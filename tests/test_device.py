"""Tests for device module."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from meshtastic_tools.core.config import ConnectionConfig, ConnectionType, DeviceConfig
from meshtastic_tools.core.device import DeviceInfo, DeviceManager
from meshtastic_tools.core.exceptions import ConnectionError


class TestDeviceInfo:
    """Tests for DeviceInfo parsing."""
    
    def test_parse_from_info_output(self):
        sample_output = """
Connected to radio

Owner: Meshtastic c3d4 (c3d4)
My info: { "myNodeNum": 111111111, "rebootCount": 48, "minAppVersion": 30200, "deviceId": "AbCdEfGhIjKlMnOpQrStUvWxYz==", "pioEnv": "test-board", "nodedbCount": 225, "firmwareEdition": "VANILLA" }
Metadata: { "firmwareVersion": "2.7.22.96dd647", "deviceStateVersion": 24, "canShutdown": true, "hasWifi": true, "hasBluetooth": true, "positionFlags": 811, "hwModel": "HELTEC_V4", "hasPKC": true, "excludedModules": 1280, "hasEthernet": false, "role": "CLIENT", "hasRemoteHardware": false }
"""
        
        info = DeviceInfo.parse_from_info_output(sample_output)
        
        assert info.my_node_num == 111111111
        assert info.long_name == "Meshtastic c3d4"
        assert info.short_name == "c3d4"
        assert info.node_id == "!c3d4"
        assert info.firmware_version == "2.7.22.96dd647"
        assert info.hw_model == "HELTEC_V4"
        assert info.role == "CLIENT"
        assert info.reboot_count == 48
        assert info.has_bluetooth is True
        assert info.has_wifi is True
    
    def test_get_identifier_formats(self):
        info = DeviceInfo(
            my_node_num=222222222,
            node_id="!c3d4",
            long_name="Test Device",
            short_name="c3d4",
            firmware_version="2.7.22.96dd647",
            hw_model="HELTEC_V4",
            role="CLIENT",
            reboot_count=0,
            has_bluetooth=False,
            has_wifi=False,
        )
        
        assert info.get_identifier("node_num") == "222222222"
        assert info.get_identifier("node_id") == "!c3d4"
        assert info.get_identifier("short_name") == "c3d4"
        assert info.get_identifier("long_name") == "Test_Device"


class TestDeviceManagerWithMocks:
    """Tests for DeviceManager with mocked subprocess calls."""
    
    @pytest.fixture
    def device_config(self):
        """Create a test device configuration."""
        conn = ConnectionConfig(
            type=ConnectionType.HOST,
            address="10.0.0.50",
            timeout=10,
        )
        return DeviceConfig(
            name="test_device",
            connection=conn,
        )
    
    @pytest.fixture
    def sample_info_output(self):
        """Sample valid --info output."""
        return """
Connected to radio

Owner: Meshtastic c3d4 (c3d4)
My info: { "myNodeNum": 111111111, "rebootCount": 48, "minAppVersion": 30200 }
Metadata: { "firmwareVersion": "2.7.22.96dd647", "hasWifi": true, "hasBluetooth": true, "hwModel": "HELTEC_V4", "role": "CLIENT" }
"""
    
    def test_fetch_info_success(self, device_config, sample_info_output):
        """Test successful info fetch with mocked subprocess."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = sample_info_output
        mock_result.stderr = ""
        
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            manager = DeviceManager(device_config)
            manager._fetch_info()
            
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "meshtastic" in call_args
            assert "--info" in call_args
            assert "--host" in call_args
            assert "10.0.0.50" in call_args
            
            assert manager.is_connected is True
            assert manager.info is not None
            assert manager.info.my_node_num == 111111111
            assert manager.info.firmware_version == "2.7.22.96dd647"
    
    def test_fetch_info_connection_refused(self, device_config):
        """Test fetch_info when device refuses connection."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Connection refused"
        
        with patch("subprocess.run", return_value=mock_result):
            manager = DeviceManager(device_config)
            
            with pytest.raises(ConnectionError, match="Failed to connect to test_device"):
                manager._fetch_info()
            
            assert manager.is_connected is False
    
    def test_fetch_info_timeout(self, device_config):
        """Test fetch_info when connection times out."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(
            cmd=["meshtastic", "--host", "10.0.0.50", "--info"],
            timeout=10,
        )):
            manager = DeviceManager(device_config)
            
            with pytest.raises(ConnectionError, match="Timeout connecting to test_device"):
                manager._fetch_info()
            
            assert manager.is_connected is False
    
    def test_fetch_info_unexpected_error(self, device_config):
        """Test fetch_info with unexpected exception."""
        with patch("subprocess.run", side_effect=OSError("Network unreachable")):
            manager = DeviceManager(device_config)
            
            with pytest.raises(ConnectionError, match="Error connecting to test_device"):
                manager._fetch_info()
            
            assert manager.is_connected is False
    
    def test_test_connection_success(self, device_config, sample_info_output):
        """Test test_connection returns success tuple."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = sample_info_output
        mock_result.stderr = ""
        
        with patch("subprocess.run", return_value=mock_result):
            manager = DeviceManager(device_config)
            success, message = manager.test_connection()
            
            assert success is True
            assert "Connected" in message
            assert "2.7.22.96dd647" in message
    
    def test_test_connection_failure(self, device_config):
        """Test test_connection returns failure tuple without raising."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "No such device"
        
        with patch("subprocess.run", return_value=mock_result):
            manager = DeviceManager(device_config)
            success, message = manager.test_connection()
            
            assert success is False
            assert "Failed to connect" in message
    
    def test_get_info_caches_result(self, device_config, sample_info_output):
        """Test get_info caches and reuses info."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = sample_info_output
        mock_result.stderr = ""
        
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            manager = DeviceManager(device_config)
            
            info1 = manager.get_info()
            info2 = manager.get_info()
            
            assert info1 is info2
            mock_run.assert_called_once()
    
    def test_get_info_force_refresh(self, device_config, sample_info_output):
        """Test get_info with force_refresh makes new call."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = sample_info_output
        mock_result.stderr = ""
        
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            manager = DeviceManager(device_config)
            
            manager.get_info()
            manager.get_info(force_refresh=True)
            
            assert mock_run.call_count == 2