#!/usr/bin/env python3
"""
CPU Temperature Monitor for ESP32 Fan Controller.
Reads CPU temperature via psutil and sends it to an ESP32 via HTTP POST.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from typing import Optional

import psutil
import requests


# ======================================================
# CONFIG
# ======================================================


@dataclass
class Config:
    esp32_ip: str = field(
        default_factory=lambda: os.getenv("ESP32_IP", "192.168.1.103")
    )
    interval_seconds: float = float(os.getenv("INTERVAL_SECONDS", "5"))
    http_timeout_connect: float = float(os.getenv("HTTP_TIMEOUT_CONNECT", "2"))
    http_timeout_read: float = float(os.getenv("HTTP_TIMEOUT_READ", "3"))
    min_valid_temp: float = 1.0
    max_valid_temp: float = 120.0
    temp_delta_threshold: float = float(os.getenv("TEMP_DELTA_THRESHOLD", "0.5"))
    max_consecutive_failures: int = int(os.getenv("MAX_CONSECUTIVE_FAILURES", "5"))
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    log_file: Optional[str] = os.getenv("LOG_FILE")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    @property
    def endpoint(self) -> str:
        return f"http://{self.esp32_ip}/api/cpu"

    @property
    def http_timeout(self) -> tuple[float, float]:
        return (self.http_timeout_connect, self.http_timeout_read)


def build_config(argv: Optional[list[str]] = None) -> Config:
    parser = argparse.ArgumentParser(
        description="Monitor de temperatura CPU para ESP32 Fan Controller"
    )
    parser.add_argument("--ip", help="Dirección IP del ESP32")
    parser.add_argument("--interval", type=float, help="Intervalo de envío (segundos)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Log level DEBUG")
    parser.add_argument("--log-file", help="Ruta al archivo de log")
    parser.add_argument("--delta", type=float, help="Umbral mínimo de cambio (°C)")
    parsed = parser.parse_args(argv)

    config = Config()

    if parsed.ip:
        config.esp32_ip = parsed.ip
    if parsed.interval is not None:
        config.interval_seconds = parsed.interval
    if parsed.verbose:
        config.log_level = "DEBUG"
    if parsed.log_file:
        config.log_file = parsed.log_file
    if parsed.delta is not None:
        config.temp_delta_threshold = parsed.delta

    return config


# ======================================================
# LOGGING
# ======================================================


def setup_logging(config: Config) -> None:
    level = getattr(logging, config.log_level, logging.INFO)
    handlers: list[logging.Handler] = []

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    handlers.append(console)

    if config.log_file:
        file_handler = RotatingFileHandler(
            config.log_file,
            maxBytes=1_000_000,
            backupCount=3,
        )
        file_handler.setLevel(level)
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
    )


# ======================================================
# METRICS
# ======================================================


@dataclass
class Metrics:
    start_time: float = field(default_factory=time.time)
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    last_temp_sent: Optional[float] = None
    last_success_time: Optional[float] = None


# ======================================================
# HELPERS
# ======================================================


def get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def read_cpu_temperature(config: Config) -> Optional[float]:
    try:
        sensors = psutil.sensors_temperatures(fahrenheit=False)
    except Exception as exc:
        logging.error("No se pudo leer sensors_temperatures: %s", exc)
        return None

    if not sensors:
        logging.warning("No hay sensores de temperatura disponibles.")
        return None

    readings = []
    for sensor_name, entries in sensors.items():
        for entry in entries:
            temp = entry.current
            if temp is None:
                continue
            if config.min_valid_temp <= temp <= config.max_valid_temp:
                readings.append((sensor_name, entry.label, float(temp)))

    if not readings:
        logging.warning("No se encontraron lecturas válidas de temperatura.")
        return None

    hottest = max(readings, key=lambda x: x[2])
    sensor_name, label, temp = hottest

    logging.info(
        "Temperatura seleccionada: %.1f °C | sensor=%s | label=%s",
        temp,
        sensor_name,
        label or "-",
    )
    return temp


def send_temperature(
    session: requests.Session,
    temp: float,
    config: Config,
    metrics: Metrics,
) -> bool:
    payload = {
        "temp": round(temp, 1),
        "host": get_hostname(),
    }

    try:
        response = session.post(
            config.endpoint,
            json=payload,
            timeout=config.http_timeout,
        )
    except requests.RequestException as exc:
        logging.error("Error de conexión con ESP32: %s", exc)
        metrics.failed_requests += 1
        metrics.consecutive_failures += 1
        return False

    if response.status_code != 200:
        logging.warning(
            "ESP32 respondió HTTP %s: %s",
            response.status_code,
            response.text,
        )
        metrics.failed_requests += 1
        metrics.consecutive_failures += 1
        return False

    metrics.successful_requests += 1
    metrics.consecutive_failures = 0
    metrics.last_temp_sent = temp
    metrics.last_success_time = time.time()

    logging.info("Enviado a ESP32: %s | Respuesta: %s", payload, response.text.strip())
    return True


def compute_sleep(metrics: Metrics, config: Config) -> float:
    if metrics.consecutive_failures == 0:
        return config.interval_seconds

    if metrics.consecutive_failures >= config.max_consecutive_failures:
        logging.warning(
            "%d fallos consecutivos - esperando %.0fs antes de reintentar",
            metrics.consecutive_failures,
            config.backoff_max_seconds,
        )
        return config.backoff_max_seconds

    backoff = config.backoff_base_seconds * (2 ** (metrics.consecutive_failures - 1))
    return min(backoff, config.backoff_max_seconds)


# ======================================================
# MAIN
# ======================================================

_should_exit = False


def _handle_signal(signum: int, frame) -> None:
    global _should_exit
    signame = signal.Signals(signum).name
    logging.info("Señal %s recibida, cerrando...", signame)
    _should_exit = True


def main() -> None:
    config = build_config()
    setup_logging(config)
    metrics = Metrics()

    logging.info("Iniciando monitor de temperatura CPU para ESP32")
    logging.info(
        "Endpoint=%s  Intervalo=%.1fs  Umbral-delta=%.1f°C  Timeout=(%.1f,%.1f)s",
        config.endpoint,
        config.interval_seconds,
        config.temp_delta_threshold,
        config.http_timeout_connect,
        config.http_timeout_read,
    )

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    session = requests.Session()
    session.max_redirects = 0

    last_temp: Optional[float] = None

    while not _should_exit:
        cycle_start = time.monotonic()

        temp = read_cpu_temperature(config)

        if temp is not None:
            if last_temp is None or abs(temp - last_temp) >= config.temp_delta_threshold:
                send_temperature(session, temp, config, metrics)
                last_temp = temp
            else:
                logging.debug(
                    "Delta %.2f°C < umbral %.1f°C, omitiendo envío",
                    abs(temp - last_temp),
                    config.temp_delta_threshold,
                )
        else:
            logging.debug("No se pudo leer temperatura en este ciclo")

        sleep_time = compute_sleep(metrics, config)
        elapsed = time.monotonic() - cycle_start
        remaining = sleep_time - elapsed

        while remaining > 0 and not _should_exit:
            chunk = min(remaining, 0.5)
            time.sleep(chunk)
            remaining -= chunk

    uptime = time.time() - metrics.start_time
    logging.info(
        "Cerrando. Uptime=%.0fs  Envíos=%d  Fallos=%d  Última-temp=%s",
        uptime,
        metrics.successful_requests,
        metrics.failed_requests,
        f"{metrics.last_temp_sent:.1f}°C" if metrics.last_temp_sent is not None else "N/A",
    )

    session.close()


if __name__ == "__main__":
    main()
