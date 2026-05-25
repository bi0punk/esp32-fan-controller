from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from app import (
    Config,
    Metrics,
    build_config,
    compute_sleep,
    get_hostname,
    read_cpu_temperature,
    send_temperature,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestBuildConfig:
    def test_defaults(self) -> None:
        cfg = build_config([])
        assert cfg.esp32_ip == "192.168.1.103"
        assert cfg.interval_seconds == 5.0
        assert cfg.temp_delta_threshold == 0.5
        assert cfg.log_level == "INFO"

    def test_overrides(self) -> None:
        cfg = build_config([
            "--ip", "10.0.0.5",
            "--interval", "10",
            "--verbose",
            "--delta", "1.0",
            "--log-file", "/tmp/test.log",
        ])
        assert cfg.esp32_ip == "10.0.0.5"
        assert cfg.interval_seconds == 10.0
        assert cfg.log_level == "DEBUG"
        assert cfg.temp_delta_threshold == 1.0
        assert cfg.log_file == "/tmp/test.log"

    def test_endpoint_property(self) -> None:
        cfg = Config(esp32_ip="10.0.0.5")
        assert cfg.endpoint == "http://10.0.0.5/api/cpu"

    def test_timeout_property(self) -> None:
        cfg = Config(http_timeout_connect=1.0, http_timeout_read=3.0)
        assert cfg.http_timeout == (1.0, 3.0)


# ---------------------------------------------------------------------------
# get_hostname
# ---------------------------------------------------------------------------

class TestGetHostname:
    @patch("app.socket.gethostname", return_value="my-pc")
    def test_returns_hostname(self, mock_gethostname: MagicMock) -> None:
        assert get_hostname() == "my-pc"

    @patch("app.socket.gethostname", side_effect=Exception("boom"))
    def test_fallback_on_error(self, mock_gethostname: MagicMock) -> None:
        assert get_hostname() == "unknown"


# ---------------------------------------------------------------------------
# read_cpu_temperature
# ---------------------------------------------------------------------------

def _make_sensor_entry(current: float, label: str = "") -> MagicMock:
    entry = MagicMock()
    entry.current = current
    entry.label = label
    return entry


class TestReadCpuTemperature:
    def test_returns_hottest_valid_temp(self) -> None:
        cfg = Config()
        fake_sensors = {
            "coretemp": [
                _make_sensor_entry(45.0, "Core 0"),
                _make_sensor_entry(52.0, "Core 1"),
            ],
            "acpitz": [_make_sensor_entry(38.0)],
        }
        with patch("app.psutil.sensors_temperatures", return_value=fake_sensors):
            temp = read_cpu_temperature(cfg)
        assert temp == 52.0

    def test_filters_out_of_range(self) -> None:
        cfg = Config(min_valid_temp=1.0, max_valid_temp=100.0)
        fake_sensors = {
            "coretemp": [
                _make_sensor_entry(-5.0),
                _make_sensor_entry(150.0),
                _make_sensor_entry(55.0),
            ],
        }
        with patch("app.psutil.sensors_temperatures", return_value=fake_sensors):
            temp = read_cpu_temperature(cfg)
        assert temp == 55.0

    def test_returns_none_when_no_valid_readings(self) -> None:
        cfg = Config()
        fake_sensors = {"coretemp": [_make_sensor_entry(999.0)]}
        with patch("app.psutil.sensors_temperatures", return_value=fake_sensors):
            temp = read_cpu_temperature(cfg)
        assert temp is None

    def test_returns_none_when_no_sensors(self) -> None:
        cfg = Config()
        with patch("app.psutil.sensors_temperatures", return_value={}):
            temp = read_cpu_temperature(cfg)
        assert temp is None

    def test_returns_none_on_exception(self) -> None:
        cfg = Config()
        with patch("app.psutil.sensors_temperatures", side_effect=OSError("denied")):
            temp = read_cpu_temperature(cfg)
        assert temp is None


# ---------------------------------------------------------------------------
# send_temperature
# ---------------------------------------------------------------------------

class TestSendTemperature:
    def test_success(self) -> None:
        cfg = Config(esp32_ip="10.0.0.1")
        metrics = Metrics()
        session = MagicMock(spec=requests.Session)
        response = MagicMock()
        response.status_code = 200
        response.text = "OK"
        session.post.return_value = response

        result = send_temperature(session, 45.3, cfg, metrics)

        assert result is True
        assert metrics.successful_requests == 1
        assert metrics.consecutive_failures == 0
        assert metrics.last_temp_sent == 45.3

        session.post.assert_called_once_with(
            "http://10.0.0.1/api/cpu",
            json={"temp": 45.3, "host": get_hostname()},
            timeout=(2.0, 3.0),
        )

    def test_http_error(self) -> None:
        cfg = Config()
        metrics = Metrics()
        session = MagicMock(spec=requests.Session)
        response = MagicMock()
        response.status_code = 500
        response.text = "Internal Error"
        session.post.return_value = response

        result = send_temperature(session, 50.0, cfg, metrics)

        assert result is False
        assert metrics.failed_requests == 1
        assert metrics.consecutive_failures == 1

    def test_connection_error(self) -> None:
        cfg = Config()
        metrics = Metrics()
        session = MagicMock(spec=requests.Session)
        session.post.side_effect = requests.ConnectionError("refused")

        result = send_temperature(session, 50.0, cfg, metrics)

        assert result is False
        assert metrics.failed_requests == 1
        assert metrics.consecutive_failures == 1


# ---------------------------------------------------------------------------
# compute_sleep
# ---------------------------------------------------------------------------

class TestComputeSleep:
    def test_no_failures_returns_interval(self) -> None:
        cfg = Config(interval_seconds=5.0)
        metrics = Metrics()
        assert compute_sleep(metrics, cfg) == 5.0

    def test_backoff_doubles(self) -> None:
        cfg = Config(backoff_base_seconds=1.0, backoff_max_seconds=60.0)
        metrics = Metrics(consecutive_failures=1)
        assert compute_sleep(metrics, cfg) == 1.0

        metrics.consecutive_failures = 2
        assert compute_sleep(metrics, cfg) == 2.0

        metrics.consecutive_failures = 3
        assert compute_sleep(metrics, cfg) == 4.0

        metrics.consecutive_failures = 4
        assert compute_sleep(metrics, cfg) == 8.0

    def test_backoff_caps_at_max(self) -> None:
        cfg = Config(backoff_base_seconds=1.0, backoff_max_seconds=10.0)
        metrics = Metrics(consecutive_failures=10)
        assert compute_sleep(metrics, cfg) == 10.0

    def test_reaches_threshold(self) -> None:
        cfg = Config(
            max_consecutive_failures=5,
            backoff_max_seconds=60.0,
            backoff_base_seconds=1.0,
        )
        metrics = Metrics(consecutive_failures=5)
        assert compute_sleep(metrics, cfg) == 60.0
