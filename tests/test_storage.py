"""Tests for storage module."""

import tempfile
from pathlib import Path

from meshtastic_tools.core.config import StorageConfig
from meshtastic_tools.logger.storage import StorageManager


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