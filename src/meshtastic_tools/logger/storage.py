"""Storage management for logger tool."""

import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from meshtastic_tools.core.config import StorageConfig
from meshtastic_tools.core.exceptions import StorageError
from meshtastic_tools.core.logging_config import get_logger
from meshtastic_tools.logger.privacy import redact_secrets


class StorageManager:
    """Manage storage of collected data files."""
    
    def __init__(self, config: StorageConfig, device_name: str):
        """
        Initialize storage manager for a specific device.
        
        Args:
            config: Storage configuration
            device_name: Name of the device (used for subdirectory)
        """
        self.config = config
        self.device_name = device_name
        self.logger = get_logger(__name__)
        
        # Create device-specific directory
        self.device_path = config.data_dir / device_name / "info"
        self._ensure_directory()
    
    def _ensure_directory(self) -> None:
        """Create storage directory if it doesn't exist."""
        try:
            for directory in (self.config.data_dir, self.device_path.parent, self.device_path):
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                directory.chmod(0o700)
            # Protect snapshots created by older versions as well.
            for file in self.device_path.iterdir():
                if file.is_file() and not file.is_symlink():
                    file.chmod(0o600)
        except OSError as e:
            raise StorageError(f"Failed to create storage directory {self.device_path}: {e}")
    
    @staticmethod
    def _parse_timestamp_from_filename(filename: str) -> Optional[datetime]:
        """
        Extract timestamp from filename like info_device_2026-04-25_14-30-00.txt.
        
        Returns datetime if found, None otherwise.
        """
        matches = list(re.finditer(r"(?:^|_)(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:-(\d{6}))?(?=\.|_|$)", filename))
        for match in reversed(matches):
            try:
                value = match.group(1)
                if match.group(2):
                    return datetime.strptime(value + "-" + match.group(2), "%Y-%m-%d_%H-%M-%S-%f")
                return datetime.strptime(value, "%Y-%m-%d_%H-%M-%S")
            except ValueError:
                continue
        return None
    
    def generate_filename(self, timestamp: Optional[datetime] = None) -> str:
        """
        Generate filename based on configured format.
        
        Args:
            timestamp: Optional timestamp (defaults to now)
            
        Returns:
            Generated filename
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        timestamp_str = timestamp.strftime("%Y-%m-%d_%H-%M-%S-%f")
        
        # Format filename
        filename = self.config.filename_format.format(
            device=self.device_name,
            timestamp=timestamp_str,
        )
        
        return filename
    
    def get_file_path(self, filename: str) -> Path:
        """Get full path for a filename."""
        if not filename or Path(filename).name != filename or filename.startswith("."):
            raise StorageError("Snapshot filename must not contain a path or start with a dot")
        return self.device_path / filename
    
    def save(self, content: str, filename: Optional[str] = None) -> Path:
        """
        Save content to a file atomically.
        
        Args:
            content: String content to save
            filename: Optional filename (generated if not provided)
            
        Returns:
            Path to saved file
            
        Raises:
            StorageError: If save fails
        """
        if filename is None:
            filename = self.generate_filename()
        
        filepath = self.get_file_path(filename)
        temp_path = None
        try:
            content = redact_secrets(content)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.device_path,
                                             prefix=".snapshot-", suffix=".tmp", delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write(content)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, filepath)
            
            self.logger.debug(f"Saved file: {filepath}", size_bytes=len(content))
            
            return filepath
            
        except OSError as e:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise StorageError(f"Failed to save file {filepath}: {e}")
    
    def _scan_files(self) -> List[Tuple[Path, Optional[datetime]]]:
        """
        Scan directory using os.scandir() for memory efficiency.
        
        Returns list of (path, parsed_timestamp) tuples, sorted by timestamp descending.
        Files without a timestamp use filesystem mtime.
        """
        if not self.device_path.exists():
            return []
        
        entries = []
        
        with os.scandir(self.device_path) as it:
            for entry in it:
                if self._is_snapshot(entry):
                    filepath = Path(entry.path)
                    timestamp = self._parse_timestamp_from_filename(entry.name) or datetime.fromtimestamp(entry.stat().st_mtime)
                    entries.append((filepath, timestamp))
        
        entries.sort(
            key=lambda x: (x[1], x[0].name),
            reverse=True
        )
        
        return entries

    def _is_snapshot(self, entry) -> bool:
        suffixes = {".txt", Path(self.config.filename_format).suffix}
        return (not entry.name.startswith(".") and entry.is_file(follow_symlinks=False)
                and Path(entry.name).suffix in suffixes)
    
    def list_files(self, sort_by: str = "time") -> List[Path]:
        """
        List all info files for this device.
        
        Args:
            sort_by: Sort order - "time" (parsed from filename) or "name"
            
        Returns:
            List of file paths
        """
        if sort_by == "name":
            if not self.device_path.exists():
                return []
            files = []
            with os.scandir(self.device_path) as it:
                for entry in it:
                    if self._is_snapshot(entry):
                        files.append(Path(entry.path))
            files.sort(key=lambda p: p.name, reverse=True)
            return files
        
        return [entry[0] for entry in self._scan_files()]
    
    def get_stats(self) -> dict:
        """
        Get storage statistics for this device.
        
        Returns:
            Dictionary with statistics
        """
        scanned = self._scan_files()
        
        if not scanned:
            return {
                "device": self.device_name,
                "file_count": 0,
                "total_size_bytes": 0,
                "total_size_mb": 0,
                "oldest_file": None,
                "newest_file": None,
            }
        
        total_size = sum(f[0].stat().st_size for f in scanned)
        
        oldest_path, oldest_ts = scanned[-1]
        newest_path, newest_ts = scanned[0]
        
        return {
            "device": self.device_name,
            "file_count": len(scanned),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "oldest_file": oldest_ts.isoformat() if oldest_ts else None,
            "newest_file": newest_ts.isoformat() if newest_ts else None,
        }
    
    def cleanup(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        Clean up old files based on retention policy.
        
        Uses parsed timestamps from filenames instead of filesystem mtime.
        
        Args:
            dry_run: If True, only report what would be deleted
            
        Returns:
            Tuple of (files_deleted, bytes_freed)
        """
        scanned = self._scan_files()
        
        if not scanned:
            return 0, 0
        
        to_delete: List[Path] = []
        
        if self.config.retention_days:
            cutoff = datetime.now() - timedelta(days=self.config.retention_days)
            for filepath, ts in scanned:
                if ts is not None and ts < cutoff:
                    to_delete.append(filepath)
                elif ts is None:
                    if filepath.stat().st_mtime < cutoff.timestamp():
                        to_delete.append(filepath)
        
        if self.config.max_files and len(scanned) > self.config.max_files:
            keep_paths = {entry[0] for entry in scanned[:self.config.max_files]}
            for filepath, ts in scanned[self.config.max_files:]:
                if filepath not in to_delete and filepath not in keep_paths:
                    to_delete.append(filepath)
        
        to_delete = list(set(to_delete))
        
        return self._delete_files(to_delete, dry_run)

    def _delete_files(self, files: List[Path], dry_run: bool = False) -> Tuple[int, int]:
        count = size = 0
        for file in files:
            try:
                file_size = file.stat().st_size
                if not dry_run:
                    file.unlink()
                count += 1
                size += file_size
            except FileNotFoundError:
                continue
            except OSError as e:
                raise StorageError(f"Failed to delete {file}: {e}") from e
        return count, size
    
    def purge(self) -> Tuple[int, int]:
        """
        Delete all files for this device.
        
        Returns:
            Tuple of (files_deleted, bytes_freed)
        """
        scanned = self._scan_files()
        
        return self._delete_files([path for path, _ in scanned])


class GlobalStorageManager:
    """Manage storage across all devices."""
    
    def __init__(self, config: StorageConfig):
        """
        Initialize global storage manager.
        
        Args:
            config: Storage configuration
        """
        self.config = config
        self.logger = get_logger(__name__)
        
        try:
            config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            config.data_dir.chmod(0o700)
        except OSError as e:
            raise StorageError(f"Failed to create storage directory {config.data_dir}: {e}")
    
    def get_device_manager(self, device_name: str) -> StorageManager:
        """Get storage manager for a specific device."""
        return StorageManager(self.config, device_name)
    
    def list_devices(self) -> List[str]:
        """
        List all devices that have data stored.
        
        Returns:
            List of device names
        """
        if not self.config.data_dir.exists():
            return []
        
        devices = []
        with os.scandir(self.config.data_dir) as it:
            for entry in it:
                if entry.is_dir():
                    info_path = Path(entry.path) / "info"
                    if info_path.exists():
                        try:
                            with os.scandir(info_path) as info_it:
                                suffixes = {".txt", Path(self.config.filename_format).suffix}
                                if any(e.is_file(follow_symlinks=False) and not e.name.startswith('.')
                                       and Path(e.name).suffix in suffixes for e in info_it):
                                    devices.append(entry.name)
                        except OSError:
                            pass
        
        return sorted(devices)
    
    def get_all_stats(self) -> List[dict]:
        """
        Get storage statistics for all devices.
        
        Returns:
            List of statistics dictionaries
        """
        stats = []
        
        for device_name in self.list_devices():
            manager = self.get_device_manager(device_name)
            stats.append(manager.get_stats())
        
        return stats
    
    def get_total_stats(self) -> dict:
        """
        Get total storage statistics across all devices.
        
        Returns:
            Dictionary with total statistics
        """
        all_stats = self.get_all_stats()
        
        total_files = sum(s["file_count"] for s in all_stats)
        total_bytes = sum(s["total_size_bytes"] for s in all_stats)
        
        return {
            "device_count": len(all_stats),
            "total_files": total_files,
            "total_size_bytes": total_bytes,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2) if total_bytes > 0 else 0,
            "devices": all_stats,
        }
    
    def cleanup_all(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        Run cleanup on all devices.
        
        Args:
            dry_run: If True, only report what would be deleted
            
        Returns:
            Tuple of (total_files_deleted, total_bytes_freed)
        """
        total_files = 0
        total_bytes = 0
        
        for device_name in self.list_devices():
            manager = self.get_device_manager(device_name)
            files, bytes_freed = manager.cleanup(dry_run=dry_run)
            total_files += files
            total_bytes += bytes_freed
            
            if files > 0:
                self.logger.info(
                    f"Cleanup for {device_name}",
                    files=files,
                    bytes_freed=bytes_freed,
                    dry_run=dry_run,
                )
        
        return total_files, total_bytes
