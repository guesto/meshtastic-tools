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
)


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
            baudrate=115200,
        )
        
        args = config.get_cli_args()
        assert args == ["--port", "/dev/ttyUSB0", "--baudrate", "115200"]
    
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