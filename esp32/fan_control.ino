#include <WiFi.h>
#include <WebServer.h>
#include "wifi_config.h"

// ======================================================
// CONFIGURACIÓN WIFI (desde wifi_config.h)
// ======================================================
const char* ssid = WIFI_SSID;
const char* password = WIFI_PASSWORD;

// ======================================================
// CONFIGURACIÓN RELÉ
// ======================================================
const int RELAY_PIN = 26;

// En tu módulo probablemente:
// LOW  = relé encendido
// HIGH = relé apagado
const bool RELAY_ACTIVE_LOW = true;

// ======================================================
// CONFIGURACIÓN TEMPERATURA
// ======================================================
float TEMP_ON = 65.0;
float TEMP_OFF = 55.0;

const unsigned long SENSOR_TIMEOUT_MS = 30000;

// ======================================================
// ESTADO GLOBAL
// ======================================================
WebServer server(80);

enum Mode {
  MODE_AUTO,
  MODE_FORCE_ON,
  MODE_FORCE_OFF
};

Mode currentMode = MODE_AUTO;

bool fanOn = false;
float lastCpuTemp = -1.0;
unsigned long lastCpuUpdate = 0;

// ======================================================
// CONTROL RELÉ
// ======================================================
void setFan(bool state) {
  fanOn = state;

  if (RELAY_ACTIVE_LOW) {
    digitalWrite(RELAY_PIN, state ? LOW : HIGH);
  } else {
    digitalWrite(RELAY_PIN, state ? HIGH : LOW);
  }

  Serial.print("Ventilador: ");
  Serial.println(state ? "ENCENDIDO" : "APAGADO");
}

// ======================================================
// LÓGICA AUTO
// ======================================================
void applyAutoLogic() {
  if (currentMode != MODE_AUTO) {
    return;
  }

  unsigned long now = millis();

  // Fail-safe: si no llegan datos de CPU, encender ventilador
  if (lastCpuTemp < 0 || (now - lastCpuUpdate > SENSOR_TIMEOUT_MS)) {
    setFan(true);
    return;
  }

  // Histéresis
  if (lastCpuTemp >= TEMP_ON) {
    setFan(true);
  } else if (lastCpuTemp <= TEMP_OFF) {
    setFan(false);
  }
}

// ======================================================
// HELPERS
// ======================================================
String modeToString() {
  if (currentMode == MODE_AUTO) return "AUTO";
  if (currentMode == MODE_FORCE_ON) return "ON";
  if (currentMode == MODE_FORCE_OFF) return "OFF";
  return "UNKNOWN";
}

bool sensorOnline() {
  return lastCpuTemp >= 0 && (millis() - lastCpuUpdate <= SENSOR_TIMEOUT_MS);
}

// ======================================================
// API JSON STATE
// ======================================================
void sendStateJson() {
  String json = "{";
  json += "\"fan_on\":";
  json += fanOn ? "true" : "false";
  json += ",";
  json += "\"fan_state\":\"";
  json += fanOn ? "ENCENDIDO" : "APAGADO";
  json += "\",";
  json += "\"mode\":\"";
  json += modeToString();
  json += "\",";
  json += "\"cpu_temp\":";
  json += String(lastCpuTemp, 1);
  json += ",";
  json += "\"sensor_online\":";
  json += sensorOnline() ? "true" : "false";
  json += ",";
  json += "\"temp_on\":";
  json += String(TEMP_ON, 1);
  json += ",";
  json += "\"temp_off\":";
  json += String(TEMP_OFF, 1);
  json += ",";
  json += "\"ip\":\"";
  json += WiFi.localIP().toString();
  json += "\"";
  json += "}";

  server.send(200, "application/json", json);
}

// ======================================================
// WEB SIN RECARGA
// ======================================================
String getHtmlPage() {
  String html = R"rawliteral(
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Control Ventilador CPU</title>

  <style>
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: #101010;
      color: #ffffff;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .card {
      width: 92%;
      max-width: 500px;
      background: #1c1c1c;
      border-radius: 18px;
      padding: 26px;
      text-align: center;
      box-shadow: 0 0 25px rgba(0,0,0,0.45);
    }

    h1 {
      margin-top: 0;
      font-size: 25px;
    }

    .label {
      font-size: 14px;
      color: #bbbbbb;
      margin-top: 18px;
      margin-bottom: 6px;
    }

    .value {
      font-size: 28px;
      font-weight: bold;
      margin: 8px 0;
    }

    .fan-on {
      color: #00ff88;
    }

    .fan-off {
      color: #ff5555;
    }

    .sensor-ok {
      color: #00ff88;
    }

    .sensor-bad {
      color: #ffaa00;
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 12px;
      margin-top: 24px;
    }

    button {
      width: 100%;
      border: none;
      border-radius: 12px;
      padding: 17px;
      font-size: 20px;
      font-weight: bold;
      color: #ffffff;
      cursor: pointer;
    }

    button:active {
      transform: scale(0.98);
    }

    .btn-auto {
      background: #2255aa;
    }

    .btn-on {
      background: #008f4c;
    }

    .btn-off {
      background: #aa2222;
    }

    .api {
      margin-top: 22px;
      font-size: 13px;
      color: #aaaaaa;
      text-align: left;
      background: #111111;
      padding: 12px;
      border-radius: 10px;
      word-break: break-all;
      line-height: 1.5;
    }

    .pill {
      display: inline-block;
      padding: 6px 12px;
      border-radius: 999px;
      background: #2b2b2b;
      font-size: 14px;
      margin-top: 8px;
    }

    .status-line {
      margin-top: 14px;
      color: #999999;
      font-size: 13px;
    }
  </style>
</head>

<body>
  <div class="card">
    <h1>Control Ventilador CPU</h1>

    <div class="label">Temperatura CPU</div>
    <div id="cpuTemp" class="value">-- °C</div>

    <div class="label">Estado sensor</div>
    <div id="sensorStatus" class="value sensor-bad">SIN DATOS</div>

    <div class="label">Modo actual</div>
    <div id="mode" class="value">--</div>

    <div class="label">Ventilador</div>
    <div id="fanState" class="value fan-off">--</div>

    <div class="grid">
      <button class="btn-auto" onclick="setMode('auto')">Modo AUTO</button>
      <button class="btn-on" onclick="setMode('on')">Forzar ENCENDIDO</button>
      <button class="btn-off" onclick="setMode('off')">Forzar APAGADO</button>
    </div>

    <div class="status-line" id="lastUpdate">
      Esperando datos...
    </div>

    <div class="api">
      <strong>API</strong><br>
      POST temperatura: /api/cpu<br>
      Estado JSON: /api/state<br>
      Modo AUTO: /api/mode/auto<br>
      Forzar ON: /api/mode/on<br>
      Forzar OFF: /api/mode/off
    </div>
  </div>

  <script>
    async function fetchState() {
      try {
        const response = await fetch('/api/state', { cache: 'no-store' });

        if (!response.ok) {
          throw new Error('HTTP ' + response.status);
        }

        const data = await response.json();

        updateUi(data);
      } catch (err) {
        document.getElementById('sensorStatus').textContent = 'ERROR WEB';
        document.getElementById('sensorStatus').className = 'value sensor-bad';
        document.getElementById('lastUpdate').textContent = 'Error consultando ESP32: ' + err.message;
      }
    }

    function updateUi(data) {
      const cpuTemp = document.getElementById('cpuTemp');
      const sensorStatus = document.getElementById('sensorStatus');
      const mode = document.getElementById('mode');
      const fanState = document.getElementById('fanState');
      const lastUpdate = document.getElementById('lastUpdate');

      if (data.cpu_temp >= 0) {
        cpuTemp.textContent = Number(data.cpu_temp).toFixed(1) + ' °C';
      } else {
        cpuTemp.textContent = 'Sin datos';
      }

      if (data.sensor_online) {
        sensorStatus.textContent = 'ONLINE';
        sensorStatus.className = 'value sensor-ok';
      } else {
        sensorStatus.textContent = 'SIN DATOS / TIMEOUT';
        sensorStatus.className = 'value sensor-bad';
      }

      mode.textContent = data.mode;

      if (data.fan_on) {
        fanState.textContent = 'ENCENDIDO';
        fanState.className = 'value fan-on';
      } else {
        fanState.textContent = 'APAGADO';
        fanState.className = 'value fan-off';
      }

      const now = new Date();
      lastUpdate.textContent = 'Última actualización web: ' + now.toLocaleTimeString();
    }

    async function setMode(mode) {
      try {
        const response = await fetch('/api/mode/' + mode, {
          method: 'POST',
          cache: 'no-store'
        });

        if (!response.ok) {
          throw new Error('HTTP ' + response.status);
        }

        const data = await response.json();
        updateUi(data);
      } catch (err) {
        document.getElementById('lastUpdate').textContent = 'Error cambiando modo: ' + err.message;
      }
    }

    fetchState();
    setInterval(fetchState, 1000);
  </script>
</body>
</html>
)rawliteral";

  return html;
}

// ======================================================
// HANDLERS WEB/API
// ======================================================
void handleRoot() {
  server.send(200, "text/html", getHtmlPage());
}

void handleApiState() {
  sendStateJson();
}

// Python envía:
// {"temp": 61.5}
void handleApiCpu() {
  if (!server.hasArg("plain")) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"missing_body\"}");
    return;
  }

  String body = server.arg("plain");

  int pos = body.indexOf("temp");
  if (pos < 0) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"missing_temp\"}");
    return;
  }

  int colon = body.indexOf(":", pos);
  if (colon < 0) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"bad_json\"}");
    return;
  }

  String value = body.substring(colon + 1);
  value.replace("}", "");
  value.replace("\"", "");
  value.trim();

  int comma = value.indexOf(",");
  if (comma >= 0) {
    value = value.substring(0, comma);
  }

  float temp = value.toFloat();

  if (temp <= 0 || temp > 130) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"invalid_temp\"}");
    return;
  }

  lastCpuTemp = temp;
  lastCpuUpdate = millis();

  Serial.print("Temperatura CPU recibida: ");
  Serial.print(lastCpuTemp);
  Serial.println(" C");

  applyAutoLogic();

  sendStateJson();
}

void handleModeAuto() {
  currentMode = MODE_AUTO;
  applyAutoLogic();
  sendStateJson();
}

void handleModeOn() {
  currentMode = MODE_FORCE_ON;
  setFan(true);
  sendStateJson();
}

void handleModeOff() {
  currentMode = MODE_FORCE_OFF;
  setFan(false);
  sendStateJson();
}

void handleNotFound() {
  server.send(404, "application/json", "{\"ok\":false,\"error\":\"not_found\"}");
}

// ======================================================
// SETUP
// ======================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(RELAY_PIN, OUTPUT);
  setFan(false);

  Serial.println();
  Serial.println("Iniciando ESP32 Control Fan CPU...");

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  Serial.print("Conectando a WiFi");

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    attempts++;

    if (attempts > 60) {
      Serial.println();
      Serial.println("No se pudo conectar al WiFi. Reiniciando...");
      delay(2000);
      ESP.restart();
    }
  }

  Serial.println();
  Serial.println("WiFi conectado");
  Serial.print("IP ESP32: ");
  Serial.println(WiFi.localIP());

  server.on("/", HTTP_GET, handleRoot);

  server.on("/api/state", HTTP_GET, handleApiState);
  server.on("/api/cpu", HTTP_POST, handleApiCpu);

  server.on("/api/mode/auto", HTTP_POST, handleModeAuto);
  server.on("/api/mode/on", HTTP_POST, handleModeOn);
  server.on("/api/mode/off", HTTP_POST, handleModeOff);

  // Compatibilidad con navegador directo, por si escribes la URL manualmente
  server.on("/mode/auto", HTTP_GET, []() {
    currentMode = MODE_AUTO;
    applyAutoLogic();
    server.sendHeader("Location", "/");
    server.send(303);
  });

  server.on("/mode/on", HTTP_GET, []() {
    currentMode = MODE_FORCE_ON;
    setFan(true);
    server.sendHeader("Location", "/");
    server.send(303);
  });

  server.on("/mode/off", HTTP_GET, []() {
    currentMode = MODE_FORCE_OFF;
    setFan(false);
    server.sendHeader("Location", "/");
    server.send(303);
  });

  server.onNotFound(handleNotFound);

  server.begin();

  Serial.println("Servidor web iniciado");
}

// ======================================================
// LOOP
// ======================================================
void loop() {
  server.handleClient();

  static unsigned long lastAutoCheck = 0;
  unsigned long now = millis();

  if (now - lastAutoCheck >= 2000) {
    lastAutoCheck = now;
    applyAutoLogic();
  }
}