"""Data collector for Meshtastic devices."""

from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from meshtastic_tools.core.device import DeviceManager
from meshtastic_tools.core.exceptions import CollectionError
from meshtastic_tools.core.logging_config import get_logger
from meshtastic_tools.logger.storage import StorageManager
from meshtastic_tools.logger.timeline import TimelineHistory


def _record_history(kind):
    def decorate(method):
        @wraps(method)
        def collect(self, *args, **kwargs):
            directory = self.storage.device_path.parent / kind
            history = TimelineHistory(directory, self.storage.config.max_files)
            try:
                path = method(self, *args, **kwargs)
            except CollectionError:
                history.append("error", datetime.now(timezone.utc))
                raise
            history.append("snapshot", datetime.now(timezone.utc))
            return path
        return collect
    return decorate


class MeshtasticCollector:
    """Collect data from Meshtastic devices."""
    
    def __init__(self, device_manager: DeviceManager, storage_manager: StorageManager):
        """
        Initialize collector for a specific device.
        
        Args:
            device_manager: Device manager instance
            storage_manager: Storage manager instance
        """
        self.device = device_manager
        self.storage = storage_manager
        self.logger = get_logger(__name__)
    
    @_record_history("info")
    def collect_info(self) -> Path:
        """
        Collect --info data from device and save to storage.
        
        Returns:
            Path to saved file
            
        Raises:
            CollectionError: If collection fails
        """
        self.logger.info(f"Collecting info from {self.device.name}")
        
        try:
            # Execute meshtastic --info command
            result = self.device.execute_command(["--info"])
            
            # Generate filename with timestamp
            filename = self.storage.generate_filename()
            
            # Save the output
            filepath = self.storage.save(result.stdout, filename)
            
            self.logger.info(
                f"Info collected from {self.device.name}",
                file=filepath.name,
                size_bytes=len(result.stdout),
            )
            
            return filepath
            
        except Exception as e:
            raise CollectionError(f"Failed to collect info from {self.device.name}: {e}") from e
    
    @_record_history("telemetry")
    def collect_telemetry(self) -> Path:
        """
        Collect --request-telemetry data from device.
        
        Returns:
            Path to saved file
            
        Raises:
            CollectionError: If collection fails
        """
        self.logger.info(f"Collecting telemetry from {self.device.name}")
        
        try:
            # Get device info to obtain node ID
            device_info = self.device.get_info()
            
            # Request telemetry using node ID as destination
            result = self.device.execute_command(
                ["--request-telemetry", "--dest", device_info.node_id]
            )
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
            filename = f"telemetry_{self.device.name}_{timestamp}.txt"
            
            filepath = self.storage.save(result.stdout, filename)
            
            self.logger.info(
                f"Telemetry collected from {self.device.name}",
                file=filepath.name,
            )
            
            return filepath
            
        except Exception as e:
            raise CollectionError(f"Failed to collect telemetry from {self.device.name}: {e}") from e
    
    @_record_history("nodes")
    def collect_nodes(self) -> Path:
        """
        Collect --nodes data from device.
        
        Returns:
            Path to saved file
            
        Raises:
            CollectionError: If collection fails
        """
        self.logger.info(f"Collecting nodes from {self.device.name}")
        
        try:
            result = self.device.execute_command(["--nodes"])
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
            filename = f"nodes_{self.device.name}_{timestamp}.txt"
            
            filepath = self.storage.save(result.stdout, filename)
            
            self.logger.info(
                f"Nodes collected from {self.device.name}",
                file=filepath.name,
            )
            
            return filepath
            
        except Exception as e:
            raise CollectionError(f"Failed to collect nodes from {self.device.name}: {e}") from e

    @_record_history("position")
    def collect_position(self) -> Path:
        """Request the connected node's position and store the response."""
        try:
            info = self.device.get_info()
            result = self.device.execute_command(["--request-position", "--dest", info.node_id])
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
            return self.storage.save(result.stdout, f"position_{self.device.name}_{timestamp}.txt")
        except Exception as e:
            raise CollectionError(f"Failed to collect position from {self.device.name}: {e}") from e
