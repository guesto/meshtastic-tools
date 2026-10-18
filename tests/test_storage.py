"""Tests for storage module."""

import tempfile
import os
from datetime import datetime
from pathlib import Path

from meshtastic_tools.core.config import StorageConfig
from meshtastic_tools.logger.storage import StorageManager
from meshtastic_tools.core.privacy import redact_secrets


class TestStorageManager:
    """Tests for StorageManager."""
    
    def test_generate_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = StorageConfig(
                data_dir=Path(tmpdir),
                filename_format="info_{device}_{timestamp}.txt",
            )
            
            manager = StorageManager(config, "test_device")
            
            filename = manager.generate_filename()
            
            assert filename.startswith("info_test_device_")
            assert filename.endswith(".txt")
    
    def test_save_and_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = StorageConfig(
                data_dir=Path(tmpdir),
                filename_format="test_{device}.txt",
            )
            
            manager = StorageManager(config, "test_device")
            
            # Save a file
            filepath = manager.save("test content", "test_file.txt")
            assert filepath.exists()
            assert filepath.read_text() == "test content"
            
            # List files
            files = manager.list_files()
            assert len(files) == 1
            assert files[0].name == "test_file.txt"
    
    def test_cleanup_by_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = StorageConfig(
                data_dir=Path(tmpdir),
                max_files=2,
                filename_format="test_{device}.txt",
            )
            
            manager = StorageManager(config, "test_device")
            
            # Save 3 files
            manager.save("content1", "file1.txt")
            manager.save("content2", "file2.txt")
            manager.save("content3", "file3.txt")
            
            files_before = manager.list_files()
            assert len(files_before) == 3
            
            # Cleanup
            deleted, _ = manager.cleanup()
            assert deleted == 1
            
            files_after = manager.list_files()
            assert len(files_after) == 2
    
    def test_get_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = StorageConfig(
                data_dir=Path(tmpdir),
                filename_format="test_{device}.txt",
            )
            
            manager = StorageManager(config, "test_device")
            
            manager.save("content", "file1.txt")
            
            stats = manager.get_stats()
            
            assert stats["device"] == "test_device"
            assert stats["file_count"] == 1
            assert stats["total_size_bytes"] == 7  # "content"


def test_retention_uses_mtime_for_legacy_files(tmp_path):
    manager = StorageManager(StorageConfig(data_dir=tmp_path, max_files=1), "test")
    old = manager.save("legacy", "legacy.txt")
    os.utime(old, (1, 1))
    new = manager.save("new", "info_test_2026-10-16_10-00-00.txt")
    assert manager.list_files() == [new, old]
    assert manager.cleanup(dry_run=True) == (1, 6)
    assert old.exists()
    assert manager.cleanup() == (1, 6)
    assert new.exists()
    assert not old.exists()


def test_retention_days_and_custom_extension(tmp_path):
    manager = StorageManager(StorageConfig(data_dir=tmp_path, retention_days=1,
                                           filename_format="info_{device}_{timestamp}.log"), "test")
    old = manager.save("old", "info_test_2000-01-01_10-00-00.log")
    new = manager.save("new")
    assert manager.cleanup() == (1, 3)
    assert not old.exists()
    assert manager.list_files() == [new]


def test_snapshot_permissions_redaction_and_unique_names(tmp_path):
    manager = StorageManager(StorageConfig(data_dir=tmp_path), "test")
    snapshot = manager.save('{"privateKey": "private-secret", "psk": "channel-secret", "voltage": 4.1}')
    assert snapshot.stat().st_mode & 0o777 == 0o600
    assert manager.device_path.stat().st_mode & 0o777 == 0o700
    assert "secret" not in snapshot.read_text()
    assert '"voltage": 4.1' in snapshot.read_text()
    next_snapshot = manager.save("next")
    assert next_snapshot != snapshot
    assert len(manager.list_files()) == 2
    assert not list(manager.device_path.glob("*.tmp"))


def test_existing_snapshot_permissions_are_restricted(tmp_path):
    path = tmp_path / "test" / "info"
    path.mkdir(parents=True)
    snapshot = path / "legacy.txt"
    snapshot.write_text("historical snapshot")
    snapshot.chmod(0o664)
    StorageManager(StorageConfig(data_dir=tmp_path), "test")
    assert snapshot.stat().st_mode & 0o777 == 0o600
    assert snapshot.read_text() == "historical snapshot"


def test_sanitizer_removes_all_credentials_and_channel_links():
    value = '''{"private_key":"escaped\\\"secret", "psk": "abc", "password": "p", "fixedPin": 123456,
"sessionPasskey": [1, 2], "publicKey": "public", "batteryLevel": 90}
Complete URL: https://meshtastic.org/e/#secret-token
'''
    result = redact_secrets(value)
    for secret in ['escaped', '"abc"', '"p"', '123456', '[1, 2]', 'secret-token']:
        assert secret not in result
    assert '"publicKey": "public"' in result
    assert '"batteryLevel": 90' in result


def test_timestamp_roundtrip_supports_old_and_new_filenames(tmp_path):
    manager = StorageManager(StorageConfig(data_dir=tmp_path), "test")
    now = datetime(2026, 10, 16, 10, 0, 0, 123456)
    assert manager._parse_timestamp_from_filename(manager.generate_filename(now)) == now
    assert manager._parse_timestamp_from_filename("info_test_2026-10-16_10-00-00.txt") == now.replace(microsecond=0)
