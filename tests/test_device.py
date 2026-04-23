"""Tests for device module."""

from meshtastic_tools.core.device import DeviceInfo


class TestDeviceInfo:
    """Tests for DeviceInfo parsing."""
    
    def test_parse_from_info_output(self):
        sample_output = """
Connected to radio

Owner: Meshtastic a19c (a19c)
My info: { "myNodeNum": 463839644, "rebootCount": 48, "minAppVersion": 30200, "deviceId": "Q7e/qSYbWeSSEsH2ad8HfA==", "pioEnv": "heltec-v4", "nodedbCount": 225, "firmwareEdition": "VANILLA" }
Metadata: { "firmwareVersion": "2.7.22.96dd647", "deviceStateVersion": 24, "canShutdown": true, "hasWifi": true, "hasBluetooth": true, "positionFlags": 811, "hwModel": "HELTEC_V4", "hasPKC": true, "excludedModules": 1280, "hasEthernet": false, "role": "CLIENT", "hasRemoteHardware": false }
"""
        
        info = DeviceInfo.parse_from_info_output(sample_output)
        
        assert info.my_node_num == 463839644
        assert info.long_name == "Meshtastic a19c"
        assert info.short_name == "a19c"
        assert info.node_id == "!a19c"
        assert info.firmware_version == "2.7.22.96dd647"
        assert info.hw_model == "HELTEC_V4"
        assert info.role == "CLIENT"
        assert info.reboot_count == 48
        assert info.has_bluetooth is True
        assert info.has_wifi is True
    
    def test_get_identifier_formats(self):
        info = DeviceInfo(
            my_node_num=12345,
            node_id="!abcd",
            long_name="Test Device",
            short_name="abcd",
            firmware_version="1.0",
            hw_model="TEST",
            role="CLIENT",
            reboot_count=0,
            has_bluetooth=False,
            has_wifi=False,
        )
        
        assert info.get_identifier("node_num") == "12345"
        assert info.get_identifier("node_id") == "!abcd"
        assert info.get_identifier("short_name") == "abcd"
        assert info.get_identifier("long_name") == "Test_Device"