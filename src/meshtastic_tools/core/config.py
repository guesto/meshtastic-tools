"""Configuration management with multi-level support."""

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from meshtastic_tools.core.exceptions import ConfigError, ValidationError


load_dotenv()


class ConnectionType(str, Enum):
    """Supported connection types as per Meshtastic documentation."""
    HOST = "host"
    PORT = "port"
    BLE = "ble"


class PydanticConnectionConfig(BaseModel):
    """Pydantic model for connection configuration validation."""
    model_config = ConfigDict(use_enum_values=True)
    
    type: ConnectionType
    address: str
    timeout: int = Field(default=30, ge=1, le=300)
    baudrate: Optional[int] = Field(default=None, ge=1200, le=921600)


class PydanticDeviceConfig(BaseModel):
    """Pydantic model for device configuration validation."""
    connection: PydanticConnectionConfig
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PydanticStorageConfig(BaseModel):
    """Pydantic model for storage configuration validation."""
    data_dir: Path
    retention_days: Optional[int] = Field(default=None, ge=1)
    max_files: Optional[int] = Field(default=None, ge=1)
    filename_format: str = "info_{device}_{timestamp}.txt"


class PydanticLoggerToolConfig(BaseModel):
    """Pydantic model for logger tool configuration validation."""
    model_config = ConfigDict(extra="allow")
    
    enabled: bool = True
    storage: PydanticStorageConfig
    devices: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


@dataclass
class ConnectionConfig:
    """Connection configuration for a Meshtastic device."""
    type: ConnectionType
    address: str
    timeout: int = 30
    baudrate: Optional[int] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConnectionConfig":
        """Create from dictionary with validation."""
        try:
            validated = PydanticConnectionConfig(**data)
        except PydanticValidationError as e:
            raise ValidationError(f"Invalid connection configuration: {e}")
        
        if not validated.address or not validated.address.strip():
            raise ValidationError(
                f"Connection address is empty for type '{validated.type}'"
            )
        
        if validated.type == ConnectionType.HOST:
            if not validated.address:
                raise ValidationError("Host connection requires a valid IP address or hostname")
        elif validated.type == ConnectionType.PORT:
            if not validated.address:
                raise ValidationError("Serial port connection requires a valid port path")
        elif validated.type == ConnectionType.BLE:
            if not validated.address:
                raise ValidationError("BLE connection requires a valid address")
        
        return cls(
            type=validated.type,
            address=validated.address,
            timeout=validated.timeout,
            baudrate=validated.baudrate,
        )
    
    def get_cli_args(self) -> List[str]:
        """Get command line arguments for meshtastic CLI."""
        if self.type == ConnectionType.HOST:
            return ["--host", self.address]
        elif self.type == ConnectionType.PORT:
            args = ["--port", self.address]
            if self.baudrate:
                args.extend(["--baudrate", str(self.baudrate)])
            return args
        elif self.type == ConnectionType.BLE:
            return ["--ble", self.address]
        else:
            raise ValueError(f"Unsupported connection type: {self.type}")


@dataclass
class DeviceConfig:
    """Device configuration."""
    name: str
    connection: ConnectionConfig
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "DeviceConfig":
        """Create from dictionary with validation."""
        try:
            validated = PydanticDeviceConfig(**data)
        except PydanticValidationError as e:
            raise ValidationError(f"Invalid device configuration for '{name}': {e}")
        
        if 'connection' not in data:
            raise ValidationError(
                f"Device '{name}' is missing required 'connection' section. "
                f"Please specify connection type (host/port/ble) and address."
            )
        
        return cls(
            name=name,
            connection=ConnectionConfig.from_dict(validated.connection.model_dump()),
            metadata=validated.metadata,
        )


@dataclass
class StorageConfig:
    """Storage configuration."""
    data_dir: Path
    retention_days: Optional[int] = None
    max_files: Optional[int] = None
    filename_format: str = "info_{device}_{timestamp}.txt"
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StorageConfig":
        """Create from dictionary with validation."""
        try:
            validated = PydanticStorageConfig(**data)
            return cls(
                data_dir=validated.data_dir,
                retention_days=validated.retention_days,
                max_files=validated.max_files,
                filename_format=validated.filename_format,
            )
        except PydanticValidationError as e:
            raise ValidationError(f"Invalid storage configuration: {e}")


@dataclass
class LoggerToolConfig:
    """Logger tool configuration."""
    enabled: bool
    storage: StorageConfig
    devices: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoggerToolConfig":
        """Create from dictionary with validation."""
        try:
            validated = PydanticLoggerToolConfig(**data)
            return cls(
                enabled=validated.enabled,
                storage=StorageConfig.from_dict(validated.storage.model_dump()),
                devices=validated.devices,
            )
        except PydanticValidationError as e:
            raise ValidationError(f"Invalid logger tool configuration: {e}")
    
    def get_device_schedule(self, device_name: str) -> Optional[str]:
        """Get schedule for a specific device."""
        device_config = self.devices.get(device_name, {})
        return device_config.get("schedule")
    
    def is_device_enabled(self, device_name: str) -> bool:
        """Check if a device is enabled in logger config."""
        device_config = self.devices.get(device_name, {})
        return device_config.get("enabled", False)


class ConfigManager:
    """Manage configuration from multiple sources with priority."""
    
    DEFAULT_CONFIG_PATHS = [
        Path("/etc/meshtastic-tools/config.yaml"),
        Path.home() / ".config" / "meshtastic-tools" / "config.yaml",
        Path.cwd() / "config" / "meshtastic-tools.yaml",
    ]
    
    def __init__(self, config_path: Optional[Path] = None, strict: bool = False):
        """
        Initialize configuration manager.
        
        Args:
            config_path: Optional explicit config path. If not provided,
                        searches in default locations.
            strict: If True, raise on first invalid device instead of skipping.
        """
        self.config_path = config_path
        self.strict = strict
        self._config: Dict[str, Any] = {}
        self._devices: Dict[str, DeviceConfig] = {}
        self._logger_config: Optional[LoggerToolConfig] = None
        
    def load(self) -> None:
        """Load configuration from files."""
        paths_to_try = [self.config_path] if self.config_path else self.DEFAULT_CONFIG_PATHS
        
        env_config = os.getenv("MESHTASTIC_TOOLS_CONFIG")
        if env_config:
            paths_to_try.insert(0, Path(env_config))
        
        config_loaded = False
        for path in paths_to_try:
            if path and path.exists():
                try:
                    with open(path, "r") as f:
                        self._config = yaml.safe_load(f) or {}
                    config_loaded = True
                    break
                except Exception as e:
                    raise ConfigError(f"Failed to load config from {path}: {e}")
        
        if not config_loaded:
            self._create_default_config()
        
        self._parse_devices()
        self._parse_logger_config()
    
    def _create_default_config(self) -> None:
        """Create default configuration."""
        self._config = {
            "devices": {},
            "tools": {
                "logger": {
                    "enabled": True,
                    "storage": {
                        "data_dir": "data/logger",
                        "retention_days": 30,
                        "max_files": 1000,
                        "filename_format": "info_{device}_{timestamp}.txt",
                    },
                    "devices": {},
                }
            }
        }
    
    def _parse_devices(self) -> None:
        """Parse devices section with validation."""
        devices_data = self._config.get("devices", {})
        
        if not isinstance(devices_data, dict):
            raise ConfigError("'devices' section must be a dictionary")
        
        for name, data in devices_data.items():
            if not isinstance(data, dict):
                msg = f"Device '{name}' configuration must be a dictionary"
                if self.strict:
                    raise ConfigError(msg)
                import logging
                logging.warning(msg)
                continue
            
            if 'connection' not in data:
                msg = (
                    f"Device '{name}' is missing required 'connection' section. "
                    f"Please specify connection type (host/port/ble) and address."
                )
                if self.strict:
                    raise ConfigError(msg)
                import logging
                logging.warning(msg)
                continue
            
            conn_data = data.get('connection', {})
            if not isinstance(conn_data, dict):
                msg = f"Device '{name}' connection must be a dictionary"
                if self.strict:
                    raise ConfigError(msg)
                import logging
                logging.warning(msg)
                continue
            
            conn_type = conn_data.get('type')
            if not conn_type:
                msg = (
                    f"Device '{name}' connection missing 'type'. "
                    f"Must be one of: host, port, ble"
                )
                if self.strict:
                    raise ConfigError(msg)
                import logging
                logging.warning(msg)
                continue
            
            valid_types = [t.value for t in ConnectionType]
            if conn_type not in valid_types:
                msg = (
                    f"Device '{name}' has invalid connection type '{conn_type}'. "
                    f"Must be one of: {', '.join(valid_types)}"
                )
                if self.strict:
                    raise ConfigError(msg)
                import logging
                logging.warning(msg)
                continue
            
            try:
                self._devices[name] = DeviceConfig.from_dict(name, data)
            except ValidationError as e:
                if self.strict:
                    raise ConfigError(str(e))
                import logging
                logging.warning(f"Skipping invalid device '{name}': {e}")
    
    def _parse_logger_config(self) -> None:
        """Parse logger tool configuration."""
        tools = self._config.get("tools", {})
        logger_data = tools.get("logger", {})
        
        if logger_data:
            try:
                self._logger_config = LoggerToolConfig.from_dict(logger_data)
            except ValidationError as e:
                raise ConfigError(f"Invalid logger configuration: {e}")
    
    @property
    def devices(self) -> Dict[str, DeviceConfig]:
        """Get all configured devices."""
        return self._devices
    
    def get_device(self, name: str) -> DeviceConfig:
        """Get device configuration by name."""
        if name not in self._devices:
            raise ConfigError(f"Device '{name}' not found in configuration")
        return self._devices[name]
    
    @property
    def logger_config(self) -> Optional[LoggerToolConfig]:
        """Get logger tool configuration."""
        return self._logger_config
    
    def get_logger_config(self) -> LoggerToolConfig:
        """Get logger tool configuration, raising if not configured."""
        if not self._logger_config:
            raise ConfigError("Logger tool not configured")
        return self._logger_config
    
    @property
    def raw_config(self) -> Dict[str, Any]:
        """Get raw configuration dictionary."""
        return self._config
    
    def validate(self) -> List[str]:
        """Validate entire configuration, return list of warnings/issues."""
        warnings = []
        
        if not self._devices:
            warnings.append("No devices configured")
        
        for name, device in self._devices.items():
            if not device.connection.address or not device.connection.address.strip():
                warnings.append(
                    f"Device '{name}' has empty connection address"
                )
            
            if not device.connection.type:
                warnings.append(
                    f"Device '{name}' has no connection type specified"
                )
            
            if device.connection.type == ConnectionType.PORT:
                port = device.connection.address
                if not port.startswith('/') and not port.startswith('COM'):
                    warnings.append(
                        f"Device '{name}' port '{port}' doesn't look like a valid serial port"
                    )
            
            if self._logger_config and self._logger_config.enabled:
                if name in self._logger_config.devices:
                    if not self._logger_config.devices[name].get("schedule"):
                        warnings.append(f"Device '{name}' has no schedule configured")
                else:
                    warnings.append(f"Device '{name}' not configured in logger tool")
        
        if self._logger_config and self._logger_config.enabled:
            if not self._logger_config.storage.data_dir:
                warnings.append("Logger storage data_dir is not set")
            
            for dev_name in self._logger_config.devices:
                if dev_name not in self._devices:
                    warnings.append(
                        f"Logger references device '{dev_name}' which is not defined in devices section"
                    )
        
        return warnings