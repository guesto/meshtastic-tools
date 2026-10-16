"""Tests for configuration module."""

import tempfile
from pathlib import Path

import pytest
import yaml

from meshtastic_tools.core.config import (
    ConfigManager,
    ConnectionConfig,
    ConnectionType,
    DeviceConfig,
    LoggerToolConfig,
)
from meshtastic_tools.core.exceptions import ConfigError, ValidationError


class TestConnectionConfig:
    """Tests for ConnectionConfig."""
    
    def test_host_connection(self):
        config = ConnectionConfig(
            type=ConnectionType.HOST,
            address="192.168.1.1",
            timeout=30,
        )
        
        args = config.get_cli_args()
        assert args == ["--host", "192.168.1.1"]
    
    def test_port_connection(self):
        config = ConnectionConfig(
            type=ConnectionType.PORT,
            address="/dev/ttyUSB0",
        )
        
        args = config.get_cli_args()
        assert args == ["--port", "/dev/ttyUSB0"]
    
    def test_port_connection_no_baudrate(self):
        config = ConnectionConfig(
            type=ConnectionType.PORT,
            address="/dev/ttyUSB0",
        )
        
        args = config.get_cli_args()
        assert args == ["--port", "/dev/ttyUSB0"]
    
    def test_ble_connection(self):
        config = ConnectionConfig(
            type=ConnectionType.BLE,
            address="AA:BB:CC:DD:EE:FF",
        )
        
        args = config.get_cli_args()
        assert args == ["--ble", "AA:BB:CC:DD:EE:FF"]
    
    def test_from_dict_valid(self):
        data = {
            "type": "host",
            "address": "192.168.1.1",
            "timeout": 45,
        }
        
        config = ConnectionConfig.from_dict(data)
        assert config.type == ConnectionType.HOST
        assert config.address == "192.168.1.1"
        assert config.timeout == 45
    
    def test_from_dict_invalid_type(self):
        data = {
            "type": "invalid",
            "address": "192.168.1.1",
        }
        
        with pytest.raises(Exception):
            ConnectionConfig.from_dict(data)


class TestDeviceConfig:
    """Tests for DeviceConfig."""
    
    def test_from_dict_valid(self):
        data = {
            "connection": {
                "type": "host",
                "address": "192.168.1.1",
            },
            "metadata": {
                "location": "Home",
            }
        }
        
        config = DeviceConfig.from_dict("test_device", data)
        assert config.name == "test_device"
        assert config.connection.type == ConnectionType.HOST
        assert config.metadata["location"] == "Home"


class TestConfigManager:
    """Tests for ConfigManager."""
    
    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as f:
            config_data = {
                "devices": {
                    "test": {
                        "connection": {
                            "type": "host",
                            "address": "192.168.1.1",
                        }
                    }
                }
            }
            yaml.dump(config_data, f)
            f.flush()
            
            manager = ConfigManager(config_path=Path(f.name))
            manager.load()
            
            assert "test" in manager.devices
            assert manager.devices["test"].connection.address == "192.168.1.1"
    
    def test_validate_no_devices(self):
        manager = ConfigManager()
        manager._config = {"devices": {}}
        manager._parse_devices()
        
        warnings = manager.validate()
        assert "No devices configured" in warnings
    
    def test_get_device_not_found(self):
        manager = ConfigManager()
        manager._config = {"devices": {}}
        manager._parse_devices()
        
        with pytest.raises(Exception):
            manager.get_device("nonexistent")


def test_explicit_missing_config_does_not_fall_back(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        ConfigManager(config_path=tmp_path / "missing.yaml").load()


def test_explicit_config_wins_over_environment(tmp_path, monkeypatch):
    explicit = tmp_path / "config.yaml"
    explicit.write_text("devices: {}")
    monkeypatch.setenv("MESHTASTIC_TOOLS_CONFIG", str(tmp_path / "missing.yaml"))
    manager = ConfigManager(config_path=explicit)
    manager.load()
    assert manager.loaded_path == explicit


def test_config_reload_clears_removed_devices(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("devices: {test: {connection: {type: host, address: example.invalid}}}")
    manager = ConfigManager(config_path=path)
    manager.load()
    assert "test" in manager.devices
    path.write_text("devices: {}")
    manager.load()
    assert manager.devices == {}


@pytest.mark.parametrize("name", ["../escape", "/absolute", "..", "device\nExecStart=evil"])
def test_unsafe_device_names_are_rejected(name):
    with pytest.raises(ValidationError):
        DeviceConfig.from_dict(name, {"connection": {"type": "host", "address": "example.invalid"}})


def test_removed_serial_baudrate_setting_is_rejected():
    with pytest.raises(ValidationError, match="baudrate"):
        ConnectionConfig.from_dict({"type": "port", "address": "/dev/ttyUSB0", "baudrate": 9600})


@pytest.mark.parametrize("value", ["[]", "tools: []", "tools: {logger: []}"])
def test_invalid_yaml_structure_has_config_error(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(value)
    with pytest.raises(ConfigError):
        ConfigManager(config_path=path).load()


def test_relative_storage_is_anchored_to_config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("tools: {logger: {storage: {data_dir: ../snapshots}}}")
    manager = ConfigManager(config_path=path)
    manager.load()
    assert manager.logger_config.storage.data_dir == (tmp_path / "../snapshots").resolve()


def test_logger_defaults_inherit_without_implicitly_enabling_devices():
    config = LoggerToolConfig.from_dict({
        "defaults": {"schedule": "*/5 * * * *"}, "storage": {"data_dir": "data"},
        "devices": {"alpha": {"enabled": True}, "beta": {},
                    "gamma": {"enabled": True, "schedule": "0 * * * *"}},
    })
    assert config.get_device_schedule("alpha") == "*/5 * * * *"
    assert config.is_device_enabled("alpha")
    assert not config.is_device_enabled("beta")
    assert not config.is_device_enabled("not-listed")
    assert config.get_device_schedule("gamma") == "0 * * * *"


def test_explicit_null_schedule_does_not_inherit():
    with pytest.raises(ValidationError, match="requires a schedule"):
        LoggerToolConfig.from_dict({"storage": {"data_dir": "data"}, "defaults": {"schedule": "* * * * *"},
                                    "devices": {"alpha": {"enabled": True, "schedule": None}}})


@pytest.mark.parametrize("schedule", ["not-a-cron", "* * * * * *", "90 * * * *"])
def test_invalid_default_schedule_is_rejected(schedule):
    with pytest.raises(ValidationError, match="schedule"):
        LoggerToolConfig.from_dict({"storage": {"data_dir": "data"}, "defaults": {"schedule": schedule}})


@pytest.mark.parametrize("zone", ["Invalid/Timezone", "", "../etc/passwd"])
def test_invalid_timezone_is_rejected(zone):
    with pytest.raises(ValidationError, match="timezone"):
        LoggerToolConfig.from_dict({"storage": {"data_dir": "data"}, "timezone": zone})


def test_timezone_conversion_and_automatic_health_grace():
    from datetime import datetime, timezone
    config = LoggerToolConfig.from_dict({"storage": {"data_dir": "data"}, "timezone": "Asia/Yekaterinburg"})
    converted = config.schedule_time(datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc))
    assert converted.hour == 17
    assert converted.utcoffset().total_seconds() == 18000
    assert config.get_health_grace(300) == 360


@pytest.mark.parametrize("grace", [0, 90, 86400])
def test_explicit_health_grace(grace):
    config = LoggerToolConfig.from_dict({"storage": {"data_dir": "data"}, "health": {"grace_seconds": grace}})
    assert config.get_health_grace(300) == grace


@pytest.mark.parametrize("grace", [-1, 86401, "bad"])
def test_invalid_health_grace_is_rejected(grace):
    with pytest.raises(ValidationError, match="grace_seconds"):
        LoggerToolConfig.from_dict({"storage": {"data_dir": "data"}, "health": {"grace_seconds": grace}})


@pytest.mark.parametrize("document", [
    "devices: {}\ndevices: {}",
    "devices: {alpha: {connection: {type: host, address: a, timeout: 30, timeout: 60}}}",
    "devices: {alpha: {connection: {type: host, address: a}, metadata: {location: a, location: b}}}",
    "devices: {alpha: &base {connection: {type: host, address: a}}, beta: {<<: *base, <<: *base}}",
])
def test_duplicate_yaml_keys_are_rejected_with_line_number(tmp_path, document):
    path = tmp_path / "config.yaml"
    path.write_text(document)
    with pytest.raises(ConfigError, match="duplicate key") as error:
        ConfigManager(path).load()
    assert "line" in str(error.value)


def test_yaml_merge_override_is_allowed(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('''devices:
  alpha: &base
    connection: {type: host, address: alpha.invalid}
    metadata: {location: room}
  beta:
    <<: *base
    connection: {type: host, address: beta.invalid}
''')
    manager = ConfigManager(path)
    manager.load()
    assert manager.get_device("beta").connection.address == "beta.invalid"
    assert manager.get_device("beta").metadata["location"] == "room"


@pytest.mark.parametrize("field_path", [
    ("typo",), ("devices", "alpha", "typo"),
    ("devices", "alpha", "connection", "timeuot"), ("tools", "loger"),
    ("tools", "logger", "typo"), ("tools", "logger", "storage", "max_file"),
    ("tools", "logger", "defaults", "enabled"), ("tools", "logger", "health", "grace"),
    ("tools", "logger", "devices", "alpha", "enabeld"),
])
def test_unknown_fields_are_rejected_everywhere(tmp_path, field_path):
    config = {"devices": {"alpha": {"connection": {"type": "host", "address": "example.invalid"}}},
              "tools": {"logger": {"storage": {"data_dir": "data"}, "defaults": {}, "health": {},
                                    "devices": {"alpha": {"enabled": False}}}}}
    target = config
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = "unexpected"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ConfigError, match="Extra inputs"):
        ConfigManager(path).load()


def test_effective_config_is_resolved_and_can_be_loaded_again(tmp_path):
    path = tmp_path / "config.yaml"
    config = {"devices": {"alpha": {"connection": {"type": "host", "address": "example.invalid"}}},
              "tools": {"logger": {"defaults": {"schedule": "*/5 * * * *"},
                                    "storage": {"data_dir": "data"}, "devices": {"alpha": {"enabled": True}}}}}
    path.write_text(yaml.safe_dump(config))
    manager = ConfigManager(path)
    manager.load()
    effective = manager.effective_config
    assert effective["devices"]["alpha"]["connection"]["timeout"] == 30
    assert effective["tools"]["logger"]["devices"]["alpha"]["schedule"] == "*/5 * * * *"
    assert effective["tools"]["logger"]["storage"]["data_dir"] == str(tmp_path / "data")
    assert effective["tools"]["logger"]["timezone"] == "local"
    assert "defaults.schedule" in manager.effective_sources()["tools.logger.devices.alpha.schedule"]
    assert manager.raw_config == config
    path.write_text(yaml.safe_dump(effective))
    reloaded = ConfigManager(path)
    reloaded.load()
    assert reloaded.effective_config == effective
