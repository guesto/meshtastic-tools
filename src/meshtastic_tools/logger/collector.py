"""Data collector for Meshtastic devices."""

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from meshtastic_tools.core.device import DeviceManager
from meshtastic_tools.core.exceptions import CollectionError, ConnectionError
from meshtastic_tools.core.logging_config import get_logger
from meshtastic_tools.logger.storage import StorageManager


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
            
        except ConnectionError as e:
            raise CollectionError(f"Failed to collect info from {self.device.name}: {e}")
        except Exception as e:
            raise CollectionError(f"Unexpected error collecting from {self.device.name}: {e}")
    
    def collect_telemetry(self) -> Path:
        """
        Collect --telemetry data from device.
        
        Returns:
            Path to saved file
            
        Raises:
            CollectionError: If collection fails
        """
        self.logger.info(f"Collecting telemetry from {self.device.name}")
        
        try:
            result = self.device.execute_command(["--telemetry"])
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"telemetry_{self.device.name}_{timestamp}.txt"
            
            filepath = self.storage.save(result.stdout, filename)
            
            self.logger.info(
                f"Telemetry collected from {self.device.name}",
                file=filepath.name,
            )
            
            return filepath
            
        except ConnectionError as e:
            raise CollectionError(f"Failed to collect telemetry from {self.device.name}: {e}")
    
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
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"nodes_{self.device.name}_{timestamp}.txt"
            
            filepath = self.storage.save(result.stdout, filename)
            
            self.logger.info(
                f"Nodes collected from {self.device.name}",
                file=filepath.name,
            )
            
            return filepath
            
        except ConnectionError as e:
            raise CollectionError(f"Failed to collect nodes from {self.device.name}: {e}")
    
    def collect_position(self) -> Path:
        """
        Collect --position data from device.
        
        Returns:
            Path to saved file
            
        Raises:
            CollectionError: If collection fails
        """
        self.logger.info(f"Collecting position from {self.device.name}")
        
        try:
            result = self.device.execute_command(["--position"])
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"position_{self.device.name}_{timestamp}.txt"
            
            filepath = self.storage.save(result.stdout, filename)
            
            self.logger.info(
                f"Position collected from {self.device.name}",
                file=filepath.name,
            )
            
            return filepath
            
        except ConnectionError as e:
            raise CollectionError(f"Failed to collect position from {self.device.name}: {e}")