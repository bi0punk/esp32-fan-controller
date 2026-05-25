#!/usr/bin/env python3
import time
import socket
import logging
from typing import Optional

import psutil
import requests


# ======================================================
# CONFIGURACIÓN
# ======================================================
ESP32_IP = "192.168.1.103"   # Cambia esto por la IP real del ESP32
ESP32_ENDPOINT = f"http://{ESP32_IP}/api/cpu"

INTERVAL_SECONDS = 5
HTTP_TIMEOUT_SECONDS = 3

# Si tu servidor tiene sensores raros, este rango filtra lecturas inválidas.
MIN_VALID_TEMP = 1.0
MAX_VALID_TEMP = 120.0


# ======================================================
# LOGGING
# ======================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def get_cpu_temperature() -> Optional[float]:
    """
    Lee temperatura CPU usando psutil.

    En Linux normalmente toma datos desde:
    /sys/class/thermal
    lm-sensors
    drivers del kernel

    Devuelve la temperatura más alta detectada, porque para ventilación
    conviene reaccionar al punto más caliente.
    """
    try:
        sensors = psutil.sensors_temperatures(fahrenheit=False)
    except Exception as exc:
        logging.error("No se pudo leer sensors_temperatures: %s", exc)
        return None

    if not sensors:
        logging.error("No hay sensores de temperatura disponibles.")
        return None

    readings = []

    for sensor_name, entries in sensors.items():
        for entry in entries:
            temp = entry.current

            if temp is None:
                continue

            if MIN_VALID_TEMP <= temp <= MAX_VALID_TEMP:
                readings.append((sensor_name, entry.label, float(temp)))

    if not readings:
        logging.error("No se encontraron lecturas válidas de temperatura.")
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


def send_temperature(temp: float) -> bool:
    payload = {
        "temp": round(temp, 1),
        "host": get_hostname(),
    }

    try:
        response = requests.post(
            ESP32_ENDPOINT,
            json=payload,
            timeout=HTTP_TIMEOUT_SECONDS,
        )

        if response.status_code != 200:
            logging.warning(
                "ESP32 respondió HTTP %s: %s",
                response.status_code,
                response.text,
            )
            return False

        logging.info("Enviado a ESP32: %s | Respuesta: %s", payload, response.text)
        return True

    except requests.RequestException as exc:
        logging.error("Error enviando temperatura al ESP32: %s", exc)
        return False


def main() -> None:
    logging.info("Iniciando envío de temperatura CPU al ESP32")
    logging.info("Endpoint: %s", ESP32_ENDPOINT)

    while True:
        temp = get_cpu_temperature()

        if temp is not None:
            send_temperature(temp)

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()