"""Meshtastic Tools - Collection of utilities for Meshtastic devices."""

__version__ = "0.1.1"
__author__ = "Guesto"

from meshtastic_tools.core.exceptions import (
    MeshtasticToolsError,
    ConfigError,
    ConnectionError,
    DeviceNotFoundError,
    CollectionError,
)

__all__ = [
    "__version__",
    "__author__",
    "MeshtasticToolsError",
    "ConfigError",
    "ConnectionError",
    "DeviceNotFoundError",
    "CollectionError",
]