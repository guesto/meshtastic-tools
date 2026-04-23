"""Custom exceptions for meshtastic-tools."""


class MeshtasticToolsError(Exception):
    """Base exception for all meshtastic-tools errors."""
    pass


class ConfigError(MeshtasticToolsError):
    """Configuration related errors."""
    pass


class ConnectionError(MeshtasticToolsError):
    """Connection related errors."""
    pass


class DeviceNotFoundError(MeshtasticToolsError):
    """Device not found in configuration."""
    pass


class CollectionError(MeshtasticToolsError):
    """Data collection errors."""
    pass


class StorageError(MeshtasticToolsError):
    """Storage related errors."""
    pass


class ValidationError(MeshtasticToolsError):
    """Validation errors."""
    pass