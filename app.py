#!/usr/bin/env python3
"""
CPU Temperature Monitor for ESP32 Fan Controller.
Reads CPU temperature via psutil (with sysfs fallback) and sends it to an ESP32.
"""

from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import psutil
import requests


@dataclass
class Config:
    esp32_ip: str = field(
        default_factory=lambda: os.getenv("ESP32_IP", "192.168.1.103")
    )
    esp32_url: str = field(
        default_factory=lambda: os.getenv("ESP32_URL", "")
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
    log_file: str = os.getenv("LOG_FILE", "logs/fan-controller.log")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    temp_source: str = os.getenv("TEMP_SOURCE", "auto")
    api_key: str = os.getenv("API_KEY", "")
    verify_ssl: bool = os.getenv("VERIFY_SSL", "false").lower() in {"1", "true", "yes", "on", "si"}
    oneshot: bool = False

    @property
    def endpoint(self) -> str:
        if self.esp32_url:
            return self.esp32_url.rstrip("/") + "/api/cpu"
        return f"http://{self.esp32_ip}/api/cpu"

    @property
    def http_timeout(self) -> tuple[float, float]:
        return (self.http_timeout_connect, self.http_timeout_read)


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def build_config(argv: Optional[list[str]] = None) -> Config:
    parser = argparse.ArgumentParser(
        description="Monitor de temperatura CPU para ESP32 Fan Controller"
    )
    parser.add_argument("--ip", help="Dirección IP del ESP32")
    parser.add_argument("--url", help="URL base del ESP32 (alternativa a --ip)")
    parser.add_argument("--interval", type=float, help="Intervalo de envío (segundos)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Log level DEBUG")
    parser.add_argument("--log-file", help="Ruta al archivo de log")
    parser.add_argument("--delta", type=float, help="Umbral mínimo de cambio (°C)")
    parser.add_argument("--env-file", default=".env", help="Ruta del archivo .env")
    parser.add_argument("--oneshot", action="store_true", help="Ejecuta un solo ciclo y termina")
    parser.add_argument("--temp-source", choices=["auto", "psutil", "sysfs"],
                        help="Fuente de temperatura")
    parsed = parser.parse_args(argv)

    load_env_file(Path(parsed.env_file))

    config = Config()

    if parsed.ip:
        config.esp32_ip = parsed.ip
    if parsed.url:
        config.esp32_url = parsed.url
    if parsed.interval is not None:
        config.interval_seconds = parsed.interval
    if parsed.verbose:
        config.log_level = "DEBUG"
    if parsed.log_file:
        config.log_file = parsed.log_file
    if parsed.delta is not None:
        config.temp_delta_threshold = parsed.delta
    if parsed.temp_source:
        config.temp_source = parsed.temp_source
    if parsed.oneshot:
        config.oneshot = True

    return config


def setup_logging(config: Config) -> None:
    level = getattr(logging, config.log_level, logging.INFO)
    handlers: list[logging.Handler] = []

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    handlers.append(console)

    log_path = Path(config.log_file)
    ensure_parent(log_path)
    file_handler = RotatingFileHandler(
        str(log_path),
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


@dataclass
class Metrics:
    start_time: float = field(default_factory=time.time)
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    last_temp_sent: Optional[float] = None
    last_success_time: Optional[float] = None


def get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def collect_temp_candidates_from_psutil() -> list[tuple[str, float]]:
    candidates: list[tuple[str, float]] = []
    all_temps = psutil.sensors_temperatures(fahrenheit=False)
    for chip_name, entries in all_temps.items():
        for entry in entries:
            label = (entry.label or "").lower()
            current = entry.current
            if current is None:
                continue
            label_full = f"{chip_name}:{label}" if label else chip_name
            candidates.append((label_full, float(current)))
    return candidates


def select_best_temp(candidates: Iterable[tuple[str, float]]) -> Optional[tuple[str, float]]:
    items = list(candidates)
    if not items:
        return None

    priority_terms = [
        "package id 0",
        "tdie",
        "tctl",
        "cpu",
        "coretemp",
        "k10temp",
        "soc",
    ]

    for term in priority_terms:
        preferred = [item for item in items if term in item[0]]
        if preferred:
            return max(preferred, key=lambda x: x[1])

    return max(items, key=lambda x: x[1])


def read_temp_psutil() -> tuple[Optional[float], str]:
    try:
        selected = select_best_temp(collect_temp_candidates_from_psutil())
        if selected is None:
            return None, "psutil:no-data"
        source, value = selected
        return value, f"psutil:{source}"
    except Exception as exc:
        return None, f"psutil:error:{exc}"


def read_temp_sysfs() -> tuple[Optional[float], str]:
    base = Path("/sys/class/thermal")
    if not base.exists():
        return None, "sysfs:not-found"

    best_value: Optional[float] = None
    best_source = "sysfs:unknown"

    for zone in sorted(base.glob("thermal_zone*")):
        temp_file = zone / "temp"
        type_file = zone / "type"
        if not temp_file.exists():
            continue
        try:
            raw = temp_file.read_text(encoding="utf-8").strip()
            value = float(raw)
            if value > 1000:
                value = value / 1000.0
            ztype = type_file.read_text(encoding="utf-8").strip() if type_file.exists() else zone.name
            source = f"sysfs:{zone.name}:{ztype}"
            if best_value is None or value > best_value:
                best_value = value
                best_source = source
        except Exception:
            continue

    if best_value is None:
        return None, "sysfs:no-valid-zones"
    return best_value, best_source


def read_cpu_temperature(config: Config) -> tuple[Optional[float], str]:
    readers = {
        "psutil": [read_temp_psutil],
        "sysfs": [read_temp_sysfs],
        "auto": [read_temp_psutil, read_temp_sysfs],
    }
    selected_readers = readers.get(config.temp_source, readers["auto"])

    for reader in selected_readers:
        value, source = reader()
        if value is not None:
            if config.min_valid_temp <= value <= config.max_valid_temp:
                return value, source
            logging.debug("Lectura %s fuera de rango [%.1f, %.1f]: %.2f",
                          source, config.min_valid_temp, config.max_valid_temp, value)

    return None, "no-valid-reading"


def send_temperature(
    session: requests.Session,
    temp: float,
    source: str,
    config: Config,
    metrics: Metrics,
) -> bool:
    payload = {
        "temp": round(temp, 1),
        "host": get_hostname(),
        "source": source,
    }

    if config.api_key:
        payload["api_key"] = config.api_key

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

    logging.info("Enviado a ESP32: %s | fuente=%s | Respuesta: %s", payload, source, response.text.strip())
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
        "Endpoint=%s  Intervalo=%.1fs  TempSource=%s  ApiKey=%s  Umbral-delta=%.1f°C",
        config.endpoint,
        config.interval_seconds,
        config.temp_source,
        "si" if config.api_key else "no",
        config.temp_delta_threshold,
    )

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    session = requests.Session()
    session.max_redirects = 0

    last_temp: Optional[float] = None

    while not _should_exit:
        cycle_start = time.monotonic()

        temp, source = read_cpu_temperature(config)

        if temp is not None:
            if last_temp is None or abs(temp - last_temp) >= config.temp_delta_threshold:
                send_temperature(session, temp, source, config, metrics)
                last_temp = temp
            else:
                logging.debug(
                    "Delta %.2f°C < umbral %.1f°C, omitiendo envío",
                    abs(temp - last_temp),
                    config.temp_delta_threshold,
                )
        else:
            logging.debug("No se pudo leer temperatura en este ciclo: %s", source)

        if config.oneshot:
            break

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
