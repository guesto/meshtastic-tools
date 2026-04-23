"""Device management and information parsing."""

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from meshtastic_tools.core.config import ConnectionConfig, DeviceConfig
from meshtastic_tools.core.exceptions import ConnectionError, DeviceNotFoundError
from meshtastic_tools.core.logging_config import get_logger


@dataclass
class DeviceInfo:
    """Information about a Meshtastic device parsed from --info output."""
    
    my_node_num: int
    node_id: str  # !1ba5a19c
    long_name: str
    short_name: str
    firmware_version: str
    hw_model: str
    role: str
    reboot_count: int
    has_bluetooth: bool
    has_wifi: bool
    
    @classmethod
    def parse_from_info_output(cls, output: str) -> "DeviceInfo":
        """
        Parse meshtastic --info output to extract device information.
        
        Args:
            output: Raw output from 'meshtastic --info' command
            
        Returns:
            DeviceInfo object with parsed information
            
        Raises:
            ValueError: If required fields cannot be parsed
        """
        logger = get_logger(__name__)
        
        # Parse myNodeNum
        my_node_match = re.search(r'"myNodeNum":\s*(\d+)', output)
        if not my_node_match:
            raise ValueError("Could not find 'myNodeNum' in output")
        my_node_num = int(my_node_match.group(1))
        
        # Parse rebootCount
        reboot_match = re.search(r'"rebootCount":\s*(\d+)', output)
        reboot_count = int(reboot_match.group(1)) if reboot_match else 0
        
        # Parse Owner line: "Owner: Meshtastic a19c (a19c)"
        owner_match = re.search(r'Owner:\s*(.+?)\s*\((\w+)\)', output)
        if owner_match:
            long_name = owner_match.group(1).strip()
            short_name = owner_match.group(2).strip()
        else:
            # Fallback: try to extract from JSON-like structure
            long_name = f"Meshtastic {my_node_num:x}"[:20]
            short_name = f"{my_node_num:x}"[:4]
            logger.warning(f"Could not parse owner info, using fallback: {long_name}")
        
        # Parse firmware version
        fw_match = re.search(r'"firmwareVersion":\s*"([^"]+)"', output)
        firmware_version = fw_match.group(1) if fw_match else "unknown"
        
        # Parse hwModel
        hw_match = re.search(r'"hwModel":\s*"([^"]+)"', output)
        hw_model = hw_match.group(1) if hw_match else "unknown"
        
        # Parse role
        role_match = re.search(r'"role":\s*"([^"]+)"', output)
        role = role_match.group(1) if role_match else "unknown"
        
        # Parse capabilities
        has_bluetooth = '"hasBluetooth": true' in output
        has_wifi = '"hasWifi": true' in output
        
        # Generate node_id
        node_id = f"!{short_name}"
        
        return cls(
            my_node_num=my_node_num,
            node_id=node_id,
            long_name=long_name,
            short_name=short_name,
            firmware_version=firmware_version,
            hw_model=hw_model,
            role=role,
            reboot_count=reboot_count,
            has_bluetooth=has_bluetooth,
            has_wifi=has_wifi,
        )
    
    def get_identifier(self, format_type: str = "name") -> str:
        """
        Get device identifier in specified format.
        
        Args:
            format_type: One of "node_num", "node_id", "short_name", "long_name"
            
        Returns:
            Identifier string
        """
        if format_type == "node_num":
            return str(self.my_node_num)
        elif format_type == "node_id":
            return self.node_id
        elif format_type == "short_name":
            return self.short_name
        elif format_type == "long_name":
            return self.long_name.replace(" ", "_")
        else:
            return self.long_name
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "my_node_num": self.my_node_num,
            "node_id": self.node_id,
            "long_name": self.long_name,
            "short_name": self.short_name,
            "firmware_version": self.firmware_version,
            "hw_model": self.hw_model,
            "role": self.role,
            "reboot_count": self.reboot_count,
            "has_bluetooth": self.has_bluetooth,
            "has_wifi": self.has_wifi,
        }


class DeviceManager:
    """Manage Meshtastic devices and their connections."""
    
    def __init__(self, device_config: DeviceConfig):
        """
        Initialize device manager for a specific device.
        
        Args:
            device_config: Device configuration
        """
        self.config = device_config
        self._info: Optional[DeviceInfo] = None
        self._last_check: Optional[datetime] = None
        self._is_connected: bool = False
        self.logger = get_logger(__name__)
    
    @property
    def name(self) -> str:
        """Device name from configuration."""
        return self.config.name
    
    @property
    def info(self) -> Optional[DeviceInfo]:
        """Cached device information."""
        return self._info
    
    @property
    def is_connected(self) -> bool:
        """Check if device was connected during last check."""
        return self._is_connected
    
    def get_info(self, force_refresh: bool = False) -> DeviceInfo:
        """
        Get device information, using cache if available.
        
        Args:
            force_refresh: Force fetching new information
            
        Returns:
            DeviceInfo object
            
        Raises:
            ConnectionError: If device cannot be reached
        """
        if self._info is None or force_refresh:
            self._fetch_info()
        return self._info
    
    def _fetch_info(self) -> None:
        """
        Fetch device information by running 'meshtastic --info'.
        
        Raises:
            ConnectionError: If connection fails or timeout
        """
        cmd = ["meshtastic"]
        cmd.extend(self.config.connection.get_cli_args())
        cmd.append("--info")
        
        self.logger.debug(f"Fetching info from {self.name}", command=" ".join(cmd))
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.connection.timeout,
                check=False,
            )
            
            if result.returncode != 0:
                error_msg = result.stderr.strip() or "Unknown error"
                self._is_connected = False
                self._last_check = datetime.now()
                raise ConnectionError(
                    f"Failed to connect to {self.name}: {error_msg}\n"
                    f"Command: {' '.join(cmd)}"
                )
            
            # Parse the output
            self._info = DeviceInfo.parse_from_info_output(result.stdout)
            self._is_connected = True
            self._last_check = datetime.now()
            
            self.logger.info(
                f"Connected to {self.name}",
                node_num=self._info.my_node_num,
                name=self._info.long_name,
                firmware=self._info.firmware_version,
            )
            
        except subprocess.TimeoutExpired:
            self._is_connected = False
            self._last_check = datetime.now()
            raise ConnectionError(
                f"Timeout connecting to {self.name} "
                f"(>{self.config.connection.timeout}s)"
            )
        except Exception as e:
            self._is_connected = False
            self._last_check = datetime.now()
            raise ConnectionError(f"Error connecting to {self.name}: {e}")
    
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test connection to device without raising exceptions.
        
        Returns:
            Tuple of (success, message)
        """
        try:
            self._fetch_info()
            return True, f"Connected - {self._info.long_name} (FW: {self._info.firmware_version})"
        except ConnectionError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Unexpected error: {e}"
    
    def execute_command(self, command: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        """
        Execute a meshtastic command with device connection parameters.
        
        Args:
            command: Command arguments after connection params
            timeout: Optional timeout override
            
        Returns:
            CompletedProcess result
            
        Raises:
            ConnectionError: If command fails
        """
        full_cmd = ["meshtastic"]
        full_cmd.extend(self.config.connection.get_cli_args())
        full_cmd.extend(command)
        
        timeout = timeout or self.config.connection.timeout
        
        self.logger.debug(f"Executing command for {self.name}", command=" ".join(full_cmd))
        
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            
            if result.returncode != 0:
                raise ConnectionError(
                    f"Command failed for {self.name}: {result.stderr.strip()}"
                )
            
            return result
            
        except subprocess.TimeoutExpired:
            raise ConnectionError(f"Command timeout for {self.name} (>{timeout}s)")
        except Exception as e:
            raise ConnectionError(f"Command error for {self.name}: {e}")
    
    def get_status(self) -> Dict:
        """
        Get current device status.
        
        Returns:
            Dictionary with status information
        """
        conn_type = self.config.connection.type
        if hasattr(conn_type, 'value'):
            conn_type = conn_type.value
            
        status = {
            "name": self.name,
            "connection_type": conn_type,
            "address": self.config.connection.address,
            "is_connected": self._is_connected,
            "last_check": self._last_check.isoformat() if self._last_check else None,
        }
        
        if self._info:
            status.update({
                "node_num": self._info.my_node_num,
                "node_id": self._info.node_id,
                "long_name": self._info.long_name,
                "short_name": self._info.short_name,
                "firmware": self._info.firmware_version,
                "hardware": self._info.hw_model,
                "role": self._info.role,
                "reboot_count": self._info.reboot_count,
            })
        
        if self.config.metadata:
            status["metadata"] = self.config.metadata
        
        return status


class DeviceRegistry:
    """Registry for managing multiple devices."""
    
    def __init__(self, devices: Dict[str, DeviceConfig]):
        """
        Initialize device registry.
        
        Args:
            devices: Dictionary of device configurations
        """
        self._devices = devices
        self._managers: Dict[str, DeviceManager] = {}
        self.logger = get_logger(__name__)
        
        # Create managers for all devices
        for name, config in devices.items():
            self._managers[name] = DeviceManager(config)
    
    @property
    def device_names(self) -> List[str]:
        """List of all device names."""
        return list(self._devices.keys())
    
    def get_manager(self, device_name: str) -> DeviceManager:
        """
        Get device manager by name.
        
        Args:
            device_name: Device name
            
        Returns:
            DeviceManager instance
            
        Raises:
            DeviceNotFoundError: If device not found
        """
        if device_name not in self._managers:
            raise DeviceNotFoundError(f"Device '{device_name}' not found")
        return self._managers[device_name]
    
    def get_all_managers(self) -> Dict[str, DeviceManager]:
        """Get all device managers."""
        return self._managers
    
    def check_all_devices(self) -> Dict[str, Tuple[bool, str]]:
        """
        Check connection status for all devices.
        
        Returns:
            Dictionary mapping device name to (success, message)
        """
        results = {}
        for name, manager in self._managers.items():
            self.logger.info(f"Checking device: {name}")
            success, message = manager.test_connection()
            results[name] = (success, message)
        return results
    
    def get_enabled_devices(self, tool_config) -> List[str]:
        """
        Get list of devices enabled for a specific tool.
        
        Args:
            tool_config: Tool configuration with device settings
            
        Returns:
            List of enabled device names
        """
        enabled = []
        for name in self.device_names:
            if tool_config.is_device_enabled(name):
                enabled.append(name)
        return enabled
    
    def list_devices(self, include_status: bool = False) -> List[Dict]:
        """
        List all devices with optional status.
        
        Args:
            include_status: If True, include connection status
            
        Returns:
            List of device information dictionaries
        """
        devices = []
        for name, manager in self._managers.items():
            # Получаем тип подключения как строку
            conn_type = manager.config.connection.type
            if hasattr(conn_type, 'value'):
                conn_type = conn_type.value
                
            device_info = {
                "name": name,
                "connection_type": conn_type,
                "address": manager.config.connection.address,
            }
            
            if include_status:
                if manager.info:
                    device_info.update({
                        "connected": True,
                        "node_id": manager.info.node_id,
                        "long_name": manager.info.long_name,
                        "firmware": manager.info.firmware_version,
                        "hardware": manager.info.hw_model,
                    })
                else:
                    device_info["connected"] = False
                    
                if manager.config.metadata:
                    device_info["metadata"] = manager.config.metadata
            
            devices.append(device_info)
        
        return devices