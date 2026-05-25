# ESP32 Fan Controller

Monitor de temperatura CPU que envía datos a un ESP32 para control de ventilación vía HTTP POST.

## Requisitos

- Python 3.10+
- [psutil](https://github.com/giampaolo/psutil)
- [requests](https://requests.readthedocs.io/)

## Instalación

```bash
pip install -r requirements.txt
```

## Configuración

Via variables de entorno (ver `.env.example`) o argumentos CLI.

| Variable | Flag CLI | Default | Descripción |
|---|---|---|---|
| `ESP32_IP` | `--ip` | `192.168.1.103` | Dirección IP del ESP32 |
| `INTERVAL_SECONDS` | `--interval` | `5` | Intervalo entre envíos (s) |
| `HTTP_TIMEOUT_CONNECT` | — | `2` | Timeout de conexión TCP (s) |
| `HTTP_TIMEOUT_READ` | — | `3` | Timeout de lectura HTTP (s) |
| `TEMP_DELTA_THRESHOLD` | `--delta` | `0.5` | Umbral mínimo de cambio (°C) para enviar |
| `MAX_CONSECUTIVE_FAILURES` | — | `5` | Fallos consecutivos antes de backoff máximo |
| `LOG_LEVEL` | `--verbose` | `INFO` | Nivel de logging |
| `LOG_FILE` | `--log-file` | — | Ruta a archivo de log (opcional) |

## Uso

```bash
python app.py
```

Con opciones:

```bash
python app.py --ip 10.0.0.5 --interval 10 --verbose
python app.py --delta 1.0 --log-file /var/log/esp32-monitor.log
```

## Características

- **Backoff exponencial** — ante fallos de conexión espera 1s → 2s → 4s → 8s… (tope configurable)
- **Filtro delta** — evita envíos innecesarios si la temperatura no cambió significativamente
- **Graceful shutdown** — captura SIGINT/SIGTERM, cierra el loop limpiamente y logea métricas
- **Connection pooling** — usa `requests.Session()` para reutilizar conexiones TCP
- **Config flexible** — variables de entorno + CLI + defaults sensatos
- **Métricas** — contadores de envíos exitosos/fallidos, uptime, última temperatura enviada
- **Logging rotativo** — opcionalmente escribe a archivo con rotación (1 MB, 3 backups)

## Tests

```bash
pip install pytest
pytest tests/ -v
```

## systemd

Ejemplo de servicio para correr como daemon:

```
[Unit]
Description=ESP32 CPU Temperature Monitor
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/esp32-fan-controller/app.py
Restart=on-failure
RestartSec=10
User=root
EnvironmentFile=-/etc/default/esp32-monitor

[Install]
WantedBy=multi-user.target
```

## Estructura del proyecto

```
.
├── app.py              # Monitor principal
├── requirements.txt    # Dependencias
├── .env.example        # Ejemplo de configuración
├── tests/
│   ├── __init__.py
│   └── test_app.py     # 18 tests con mocks
└── README.md
```
