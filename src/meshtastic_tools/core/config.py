"""Configuration management with multi-level support."""

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError
from pydantic import field_validator, model_validator
from croniter import croniter

from meshtastic_tools.core.exceptions import ConfigError, ValidationError


load_dotenv()


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate explicit keys while preserving YAML merge overrides."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = "<<" if key_node.tag == "tag:yaml.org,2002:merge" else self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping", node.start_mark,
                        f"duplicate key {key!r}", key_node.start_mark,
                    )
                seen.add(key)
            except TypeError as e:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    "mapping keys must be scalar values", key_node.start_mark,
                ) from e
        return super().construct_mapping(node, deep=deep)


class ConnectionType(str, Enum):
    """Supported connection types as per Meshtastic documentation."""
    HOST = "host"
    PORT = "port"
    BLE = "ble"


class PydanticConnectionConfig(BaseModel):
    """Pydantic model for connection configuration validation."""
    model_config = ConfigDict(use_enum_values=True, extra="forbid")
    
    type: ConnectionType
    address: str
    timeout: int = Field(default=30, ge=1, le=300)


class PydanticDeviceConfig(BaseModel):
    """Pydantic model for device configuration validation."""
    model_config = ConfigDict(extra="forbid")
    connection: PydanticConnectionConfig
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PydanticStorageConfig(BaseModel):
    """Pydantic model for storage configuration validation."""
    model_config = ConfigDict(extra="forbid")
    data_dir: Path
    retention_days: Optional[int] = Field(default=None, ge=1)
    max_files: Optional[int] = Field(default=None, ge=1)
    filename_format: str = "info_{device}_{timestamp}.txt"

    @field_validator("filename_format")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        try:
            filename = value.format(device="device", timestamp="timestamp")
        except (KeyError, ValueError, IndexError) as e:
            raise ValueError(f"Invalid filename_format: {e}") from e
        if not filename or Path(filename).name != filename or filename.startswith("."):
            raise ValueError("filename_format must be a filename, not a path or hidden file")
        return value


class PydanticLoggerDeviceConfig(BaseModel):
    """Validate schedules before a collector can contact any device."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    schedule: Optional[str] = None

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and (len(value.split()) != 5 or not croniter.is_valid(value)):
            raise ValueError("schedule must be a valid five-field cron expression")
        return value


class PydanticLoggerDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule: Optional[str] = None

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, value: Optional[str]) -> Optional[str]:
        return PydanticLoggerDeviceConfig.validate_schedule(value)


class PydanticLoggerHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # null means automatic: timer interval (60 seconds) + device timeout.
    grace_seconds: Optional[int] = Field(default=None, ge=0, le=86400)


class PydanticLoggerToolConfig(BaseModel):
    """Pydantic model for logger tool configuration validation."""
    model_config = ConfigDict(extra="forbid")
    
    enabled: bool = True
    timezone: str = "local"
    defaults: PydanticLoggerDefaults = Field(default_factory=PydanticLoggerDefaults)
    health: PydanticLoggerHealth = Field(default_factory=PydanticLoggerHealth)
    storage: PydanticStorageConfig
    devices: Dict[str, PydanticLoggerDeviceConfig] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        if value != "local":
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as e:
                raise ValueError(f"Unknown IANA timezone: {value}") from e
        return value

    @model_validator(mode="after")
    def resolve_schedules(self):
        for name, device in self.devices.items():
            if "schedule" not in device.model_fields_set:
                device.schedule = self.defaults.schedule
            if device.enabled and not device.schedule:
                raise ValueError(f"Enabled logger device '{name}' requires a schedule or defaults.schedule")
        return self


class PydanticToolsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logger: Optional[PydanticLoggerToolConfig] = None


class PydanticAppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    devices: Dict[str, PydanticDeviceConfig] = Field(default_factory=dict)
    tools: PydanticToolsConfig = Field(default_factory=PydanticToolsConfig)


@dataclass
class ConnectionConfig:
    """Connection configuration for a Meshtastic device."""
    type: ConnectionType
    address: str
    timeout: int = 30
    
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
        )
    
    def get_cli_args(self) -> List[str]:
        """Get command line arguments for meshtastic CLI."""
        if self.type == ConnectionType.HOST:
            return ["--host", self.address]
        elif self.type == ConnectionType.PORT:
            return ["--port", self.address]
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
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_!@#.-]+", name) or name in (".", ".."):
            raise ValidationError("Device names must be safe directory and systemd instance names")
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
    timezone: str = "local"
    default_schedule: Optional[str] = None
    health_grace_seconds: Optional[int] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoggerToolConfig":
        """Create from dictionary with validation."""
        try:
            validated = PydanticLoggerToolConfig(**data)
            return cls(
                enabled=validated.enabled,
                storage=StorageConfig.from_dict(validated.storage.model_dump()),
                devices={name: config.model_dump() for name, config in validated.devices.items()},
                timezone=validated.timezone,
                default_schedule=validated.defaults.schedule,
                health_grace_seconds=validated.health.grace_seconds,
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

    def schedule_time(self, now: datetime) -> datetime:
        """Convert a reference instant to the configured scheduling timezone."""
        return now if self.timezone == "local" else now.astimezone(ZoneInfo(self.timezone))

    def get_health_grace(self, timeout: int) -> int:
        return 60 + timeout if self.health_grace_seconds is None else self.health_grace_seconds


class ConfigManager:
    """Manage configuration from multiple sources with priority."""
    
    DEFAULT_CONFIG_PATHS = [
        Path("/etc/meshtastic-tools/config.yaml"),
        Path.home() / ".config" / "meshtastic-tools" / "config.yaml",
        Path.cwd() / "config" / "meshtastic-tools.yaml",
    ]
    
    def __init__(self, config_path: Optional[Path] = None, strict: bool = True):
        """
        Initialize configuration manager.
        
        Args:
            config_path: Optional explicit config path. If not provided,
                        searches in default locations.
            strict: If True, raise on first invalid device instead of skipping.
        """
        self.config_path = Path(config_path) if config_path is not None else None
        self.strict = strict
        self._config: Dict[str, Any] = {}
        self._devices: Dict[str, DeviceConfig] = {}
        self._logger_config: Optional[LoggerToolConfig] = None
        self.loaded_path: Optional[Path] = None
        self.config_source = "built-in defaults"
        self._validated: Optional[PydanticAppConfig] = None
        
    def load(self) -> None:
        """Load configuration from files."""
        paths_to_try = list(self.DEFAULT_CONFIG_PATHS)
        env_config = os.getenv("MESHTASTIC_TOOLS_CONFIG")
        explicit_path = self.config_path or (Path(env_config) if env_config else None)
        if explicit_path:
            paths_to_try = [explicit_path]
            if not explicit_path.is_file():
                raise ConfigError(f"Configuration file not found: {explicit_path}")

        self._devices = {}
        self._logger_config = None
        self.loaded_path = None
        self._validated = None
        self.config_source = "built-in defaults"
        
        config_loaded = False
        for path in paths_to_try:
            if path and path.exists():
                try:
                    with open(path, "r") as f:
                        loaded = yaml.load(f, Loader=UniqueKeyLoader)
                        self._config = {} if loaded is None else loaded
                    self.loaded_path = path.resolve()
                    self.config_source = "explicit path" if self.config_path else ("environment" if env_config else "search paths")
                    config_loaded = True
                    break
                except Exception as e:
                    raise ConfigError(f"Failed to load config from {path}: {e}")
        
        if not config_loaded:
            self._create_default_config()

        if not isinstance(self._config, dict):
            raise ConfigError("Configuration must be a YAML mapping")

        try:
            self._validated = PydanticAppConfig.model_validate(self._config)
        except PydanticValidationError as e:
            raise ConfigError(f"Invalid configuration: {e}") from e
        
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
                        "max_files": 50,
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
        if not isinstance(tools, dict):
            raise ConfigError("'tools' section must be a dictionary")
        logger_data = tools.get("logger", {})
        if not isinstance(logger_data, dict):
            raise ConfigError("'tools.logger' section must be a dictionary")
        
        if logger_data:
            try:
                self._logger_config = LoggerToolConfig.from_dict(logger_data)
                storage = self._logger_config.storage
                if not storage.data_dir.is_absolute():
                    base = self.loaded_path.parent if self.loaded_path else Path.cwd()
                    storage.data_dir = (base / storage.data_dir).resolve()
                unknown = set(self._logger_config.devices) - set(self._devices)
                if unknown:
                    raise ConfigError(f"Logger references undefined devices: {', '.join(sorted(unknown))}")
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

    @property
    def effective_config(self) -> Dict[str, Any]:
        """Return normalized settings with inherited values and resolved paths."""
        if self._validated is None:
            raise ConfigError("Load configuration before requesting effective settings")
        result = self._validated.model_dump(mode="json")
        if self._logger_config:
            result["tools"]["logger"]["storage"]["data_dir"] = str(self._logger_config.storage.data_dir)
        elif result["tools"]["logger"] is None:
            result["tools"] = {}
        return result

    def effective_sources(self) -> Dict[str, str]:
        """Explain inheritance and automatic values without modifying YAML settings."""
        sources = {}
        if self._logger_config:
            raw_logger = self._config.get("tools", {}).get("logger", {})
            sources["tools.logger.timezone"] = "configured" if "timezone" in raw_logger else "default: local"
            raw_dir = Path(raw_logger["storage"]["data_dir"])
            sources["tools.logger.storage.data_dir"] = (
                "configured absolute path" if raw_dir.is_absolute() else "resolved relative to the configuration file"
            )
            for name in self._logger_config.devices:
                raw_device = raw_logger.get("devices", {}).get(name, {})
                sources[f"tools.logger.devices.{name}.schedule"] = (
                    "device setting" if "schedule" in raw_device else "tools.logger.defaults.schedule"
                )
                timeout = self._devices[name].connection.timeout
                grace = self._logger_config.get_health_grace(timeout)
                origin = "automatic: 60 + connection.timeout" if self._logger_config.health_grace_seconds is None else "tools.logger.health.grace_seconds"
                sources[f"health grace for {name}"] = f"{grace} seconds ({origin})"
        return sources
    
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
                    if self._logger_config.is_device_enabled(name) and not self._logger_config.get_device_schedule(name):
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
