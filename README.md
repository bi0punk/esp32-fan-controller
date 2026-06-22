# ESP32 Fan Controller

Monitor de temperatura CPU que envía datos a un ESP32 para control de ventilación vía HTTP POST.

## Stack

- **Python 3.10+** — Monitor de CPU (app.py)
- **ESP32 (Arduino)** — Firmware de control de relé (esp32/fan_control.ino)

## Instalación — Monitor Python

```bash
pip install -r requirements.txt
```

## Configuración — Monitor Python

Via variables de entorno (ver `.env.example`) o argumentos CLI.

| Variable | Flag CLI | Default | Descripción |
|---|---|---|---|
| `ESP32_IP` | `--ip` | `192.168.1.103` | Dirección IP del ESP32 |
| `INTERVAL_SECONDS` | `--interval` | `5` | Intervalo entre envíos (s) |
| `HTTP_TIMEOUT_CONNECT` | — | `2` | Timeout de conexión TCP (s) |
| `HTTP_TIMEOUT_READ` | — | `3` | Timeout de lectura HTTP (s) |
| `TEMP_DELTA_THRESHOLD` | `--delta` | `0.5` | Umbral mínimo de cambio (°C) |
| `MAX_CONSECUTIVE_FAILURES` | — | `5` | Fallos antes de backoff máximo |
| `LOG_LEVEL` | `--verbose` | `INFO` | Nivel de logging |
| `LOG_FILE` | `--log-file` | — | Ruta a archivo de log |

## Uso — Monitor Python

```bash
python app.py --ip 10.0.0.5 --interval 10 --verbose
```

## Firmware ESP32

### Configurar WiFi

Las credenciales WiFi no están hardcodeadas en el código. Copia el archivo de ejemplo y edítalo:

```bash
cp esp32/wifi_config.example.h esp32/wifi_config.h
# Edita esp32/wifi_config.h con tu SSID y password
```

El archivo `wifi_config.h` está en `.gitignore` y no se sube al repositorio.

### Pines

| Pin | Conexión |
|---|---|
| GPIO 26 | Relé (activo en LOW) |

### API del ESP32

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` | Página web de control |
| GET | `/api/state` | Estado JSON |
| POST | `/api/cpu` | Recibe `{"temp": 61.5}` |
| POST | `/api/mode/auto` | Modo automático |
| POST | `/api/mode/on` | Forzar encendido |
| POST | `/api/mode/off` | Forzar apagado |

### Modos de operación

- **AUTO** — Enciende el ventilador cuando `temp >= TEMP_ON` (65°C), apaga cuando `temp <= TEMP_OFF` (55°C)
- **FORCE_ON** — Ventilador siempre encendido
- **FORCE_OFF** — Ventilador siempre apagado
- **Fail-safe:** Si no recibe datos del sensor por 30s, enciende el ventilador automáticamente

## Características

- **Backoff exponencial** — ante fallos de conexión espera 1s → 2s → 4s → 8s…
- **Filtro delta** — evita envíos innecesarios si la temperatura no cambió significativamente
- **Graceful shutdown** — captura SIGINT/SIGTERM, cierra el loop limpiamente
- **Connection pooling** — usa `requests.Session()` para reutilizar conexiones TCP
- **Config flexible** — variables de entorno + CLI + defaults sensatos
- **Logging rotativo** — opcionalmente escribe a archivo con rotación (1 MB, 3 backups)
- **Histéresis** — evita oscilaciones del relé cerca del umbral

## Tests

```bash
pip install pytest
pytest tests/ -v
```

## systemd

```ini
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
├── app.py                  # Monitor principal
├── requirements.txt        # Dependencias
├── .env.example            # Ejemplo de configuración
├── esp32/
│   ├── fan_control.ino     # Firmware ESP32
│   ├── wifi_config.example.h  # Template WiFi (copia a wifi_config.h)
│   └── .gitignore          # Ignora wifi_config.h
├── tests/
│   ├── __init__.py
│   └── test_app.py         # 18 tests con mocks
└── README.md
```
