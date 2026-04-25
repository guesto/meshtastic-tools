"""CLI commands for logger tool."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from croniter import croniter

from meshtastic_tools.core.config import ConfigManager
from meshtastic_tools.core.device import DeviceManager, DeviceRegistry
from meshtastic_tools.core.exceptions import CollectionError, ConfigError
from meshtastic_tools.core.logging_config import get_logger
from meshtastic_tools.logger.collector import MeshtasticCollector
from meshtastic_tools.logger.storage import GlobalStorageManager

logger_app = typer.Typer(
    name="logger",
    help="Collect and store data from Meshtastic devices",
    no_args_is_help=True,
)

# Storage subcommands
storage_app = typer.Typer(help="Storage management commands")
logger_app.add_typer(storage_app, name="storage")

# Schedule subcommands
schedule_app = typer.Typer(help="Schedule management commands")
logger_app.add_typer(schedule_app, name="schedule")


def _get_config():
    """Load configuration."""
    manager = ConfigManager()
    try:
        manager.load()
    except Exception as e:
        typer.echo(f"Error loading config: {e}", err=True)
        raise typer.Exit(code=1)
    return manager


def _get_logger_config(manager: ConfigManager):
    """Get logger configuration."""
    try:
        return manager.get_logger_config()
    except ConfigError as e:
        typer.echo(f"Logger not configured: {e}", err=True)
        raise typer.Exit(code=1)


@logger_app.command()
def collect(
    collector_type: str = typer.Argument("info", help="Type of data to collect (info, telemetry, nodes, position)"),
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Device name"),
    all_devices: bool = typer.Option(False, "--all", "-a", help="Collect from all enabled devices"),
):
    """Collect data from Meshtastic devices."""
    if not device and not all_devices:
        typer.echo("Please specify --device or --all", err=True)
        raise typer.Exit(code=1)
    
    logger = get_logger(__name__)
    manager = _get_config()
    logger_config = _get_logger_config(manager)
    
    if not logger_config.enabled:
        typer.echo("Logger tool is disabled in configuration", err=True)
        raise typer.Exit(code=1)
    
    registry = DeviceRegistry(manager.devices)
    storage_manager = GlobalStorageManager(logger_config.storage)
    
    # Determine which devices to collect from
    devices_to_collect = []
    if device:
        if device not in manager.devices:
            typer.echo(f"Device '{device}' not found", err=True)
            raise typer.Exit(code=1)
        devices_to_collect = [device]
    else:
        devices_to_collect = registry.get_enabled_devices(logger_config)
        if not devices_to_collect:
            typer.echo("No enabled devices found in logger configuration", err=True)
            raise typer.Exit(code=1)
    
    typer.echo(f"\n[COLLECT] Collecting {collector_type} from {len(devices_to_collect)} device(s)...\n")
    
    success_count = 0
    for device_name in devices_to_collect:
        try:
            device_manager = registry.get_manager(device_name)
            device_storage = storage_manager.get_device_manager(device_name)
            collector = MeshtasticCollector(device_manager, device_storage)
            
            if collector_type == "info":
                filepath = collector.collect_info()
            elif collector_type == "telemetry":
                filepath = collector.collect_telemetry()
            elif collector_type == "nodes":
                filepath = collector.collect_nodes()
            elif collector_type == "position":
                filepath = collector.collect_position()
            else:
                typer.echo(f"Unknown collector type: {collector_type}", err=True)
                raise typer.Exit(code=1)
            
            size = filepath.stat().st_size
            typer.echo(f"  ✓ {device_name}: {filepath.name} ({size} bytes)")
            success_count += 1
            
        except CollectionError as e:
            typer.echo(f"  ✗ {device_name}: {e}", err=True)
            logger.error(f"Collection failed for {device_name}", error=str(e))
        except Exception as e:
            typer.echo(f"  ✗ {device_name}: Unexpected error: {e}", err=True)
            logger.error(f"Unexpected error for {device_name}", error=str(e))
    
    typer.echo(f"\n  Collected from {success_count}/{len(devices_to_collect)} devices")


@logger_app.command()
def run(
    force: bool = typer.Option(False, "--force", "-f", help="Ignore schedule and collect immediately"),
):
    """Run scheduled collection (for cron/systemd)."""
    logger = get_logger(__name__)
    manager = _get_config()
    logger_config = _get_logger_config(manager)
    
    if not logger_config.enabled:
        logger.info("Logger tool is disabled, exiting")
        return
    
    registry = DeviceRegistry(manager.devices)
    storage_manager = GlobalStorageManager(logger_config.storage)
    
    enabled_devices = registry.get_enabled_devices(logger_config)
    
    if not enabled_devices:
        logger.info("No enabled devices found")
        return
    
    now = datetime.now()
    tolerance = getattr(logger_config, 'schedule_tolerance', 55)
    devices_to_collect = []
    
    for device_name in enabled_devices:
        schedule = logger_config.get_device_schedule(device_name)
        
        if force or not schedule:
            devices_to_collect.append(device_name)
            continue
        
        try:
            cron = croniter(schedule, now)
            prev_time = cron.get_prev(datetime)
            next_time = cron.get_next(datetime)
            
            time_since_prev = (now - prev_time).total_seconds()
            
            logger.debug(
                f"Schedule check for {device_name}",
                schedule=schedule,
                now=now.strftime("%H:%M:%S"),
                prev=prev_time.strftime("%H:%M:%S"),
                next=next_time.strftime("%H:%M:%S"),
                time_since_prev=f"{time_since_prev:.0f}s",
            )
            
            if time_since_prev <= tolerance:
                devices_to_collect.append(device_name)
                logger.info(f"Device {device_name} is due for collection")
            else:
                logger.debug(
                    f"Device {device_name} not due yet",
                    next=next_time.strftime("%H:%M:%S"),
                )
                
        except Exception as e:
            logger.warning(f"Invalid schedule for {device_name}: {schedule} - {e}")
            devices_to_collect.append(device_name)
    
    if not devices_to_collect:
        logger.debug("No devices due for collection, skipping connection checks")
        return
    
    logger.info(f"Running scheduled collection for {len(devices_to_collect)} device(s)")
    
    for device_name in devices_to_collect:
        try:
            device_manager = registry.get_manager(device_name)
            
            if not force:
                success, msg = device_manager.test_connection()
                if not success:
                    logger.warning(f"Connection check failed for {device_name}: {msg}")
                    continue
            
            device_storage = storage_manager.get_device_manager(device_name)
            collector = MeshtasticCollector(device_manager, device_storage)
            
            filepath = collector.collect_info()
            logger.info(f"Collected from {device_name}", file=filepath.name)
            
        except CollectionError as e:
            logger.error(f"Collection failed for {device_name}", error=str(e))
        except Exception as e:
            logger.error(f"Unexpected error for {device_name}", error=str(e))

@storage_app.command("list")
def storage_list(
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Device name"),
    limit: int = typer.Option(10, "--limit", "-l", help="Number of files to show"),
):
    """List stored files."""
    manager = _get_config()
    logger_config = _get_logger_config(manager)
    storage_manager = GlobalStorageManager(logger_config.storage)
    
    if device:
        if device not in manager.devices:
            typer.echo(f"Device '{device}' not found", err=True)
            raise typer.Exit(code=1)
        devices = [device]
    else:
        devices = storage_manager.list_devices()
    
    if not devices:
        typer.echo("No stored data found")
        return
    
    for device_name in devices:
        device_storage = storage_manager.get_device_manager(device_name)
        files = device_storage.list_files()
        
        typer.echo(f"\n[FILES] {device_name}/info/ ({len(files)} files)\n")
        
        for file in files[:limit]:
            size = file.stat().st_size
            mtime = datetime.fromtimestamp(file.stat().st_mtime)
            typer.echo(f"  {file.name} ({size} bytes, {mtime.strftime('%Y-%m-%d %H:%M:%S')})")
        
        if len(files) > limit:
            typer.echo(f"  ... and {len(files) - limit} more files")


@storage_app.command("stats")
def storage_stats(
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Device name"),
):
    """Show storage statistics."""
    manager = _get_config()
    logger_config = _get_logger_config(manager)
    storage_manager = GlobalStorageManager(logger_config.storage)
    
    if device:
        if device not in manager.devices:
            typer.echo(f"Device '{device}' not found", err=True)
            raise typer.Exit(code=1)
        
        device_storage = storage_manager.get_device_manager(device)
        stats = device_storage.get_stats()
        
        typer.echo(f"\n[COLLECT] Storage stats for {device}:\n")
        typer.echo(f"  Files: {stats['file_count']}")
        typer.echo(f"  Total size: {stats['total_size_mb']} MB")
        
        if stats['oldest_file']:
            typer.echo(f"  Oldest: {stats['oldest_file']}")
        if stats['newest_file']:
            typer.echo(f"  Newest: {stats['newest_file']}")
    else:
        total_stats = storage_manager.get_total_stats()
        
        typer.echo(f"\n[COLLECT] Storage statistics:\n")
        typer.echo(f"  Devices with data: {total_stats['device_count']}")
        typer.echo(f"  Total files: {total_stats['total_files']}")
        typer.echo(f"  Total size: {total_stats['total_size_mb']} MB")
        
        if total_stats['devices']:
            typer.echo("\n  By device:")
            for stats in total_stats['devices']:
                typer.echo(f"    {stats['device']}: {stats['file_count']} files, {stats['total_size_mb']} MB")


@storage_app.command("cleanup")
def storage_cleanup(
    dry_run: bool = typer.Option(False, "--dry-run", help="Only show what would be deleted"),
):
    """Clean up old files based on retention policy."""
    manager = _get_config()
    logger_config = _get_logger_config(manager)
    storage_manager = GlobalStorageManager(logger_config.storage)
    
    if dry_run:
        typer.echo("\n[DRY-RUN] Dry run - no files will be deleted\n")
    
    files_deleted, bytes_freed = storage_manager.cleanup_all(dry_run=dry_run)
    
    mb_freed = bytes_freed / (1024 * 1024)
    
    if dry_run:
        typer.echo(f"\n  Would delete: {files_deleted} files ({mb_freed:.2f} MB)")
    else:
        typer.echo(f"\n  Deleted: {files_deleted} files ({mb_freed:.2f} MB)")


@storage_app.command("purge")
def storage_purge(
    device: str = typer.Option(..., "--device", "-d", help="Device name"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete all stored data for a device."""
    manager = _get_config()
    logger_config = _get_logger_config(manager)
    storage_manager = GlobalStorageManager(logger_config.storage)
    
    if device not in manager.devices:
        typer.echo(f"Device '{device}' not found", err=True)
        raise typer.Exit(code=1)
    
    device_storage = storage_manager.get_device_manager(device)
    stats = device_storage.get_stats()
    
    if stats['file_count'] == 0:
        typer.echo(f"No data stored for {device}")
        return
    
    if not confirm:
        typer.echo(f"\n[WARN] This will delete all data for {device}:")
        typer.echo(f"   {stats['file_count']} files, {stats['total_size_mb']} MB")
        response = typer.confirm("Are you sure?")
        if not response:
            typer.echo("Aborted")
            raise typer.Exit()
    
    files_deleted, bytes_freed = device_storage.purge()
    mb_freed = bytes_freed / (1024 * 1024)
    
    typer.echo(f"\n  ✓ Purged {files_deleted} files ({mb_freed:.2f} MB) from {device}")


@schedule_app.command("show")
def schedule_show(
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Device name"),
):
    """Show collection schedules."""
    manager = _get_config()
    logger_config = _get_logger_config(manager)
    
    devices = manager.devices
    
    if device:
        if device not in devices:
            typer.echo(f"Device '{device}' not found", err=True)
            raise typer.Exit(code=1)
        devices = {device: devices[device]}
    
    typer.echo("\n[SCHEDULE] Collection schedules:\n")
    
    for name in devices:
        enabled = logger_config.is_device_enabled(name)
        schedule = logger_config.get_device_schedule(name)
        
        status = "✓" if enabled else "✗"
        schedule_str = schedule if schedule else "not set"
        
        typer.echo(f"  {status} {name}: {schedule_str}")


@schedule_app.command("next")
def schedule_next(
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Device name"),
):
    """Show next collection times."""
    from croniter import croniter
    
    manager = _get_config()
    logger_config = _get_logger_config(manager)
    
    devices = manager.devices
    now = datetime.now()
    
    if device:
        if device not in devices:
            typer.echo(f"Device '{device}' not found", err=True)
            raise typer.Exit(code=1)
        devices = {device: devices[device]}
    
    typer.echo(f"\n[NEXT] Next collection times (now: {now.strftime('%Y-%m-%d %H:%M:%S')}):\n")
    
    for name in devices:
        enabled = logger_config.is_device_enabled(name)
        
        if not enabled:
            typer.echo(f"  ✗ {name}: disabled")
            continue
        
        schedule = logger_config.get_device_schedule(name)
        
        if not schedule:
            typer.echo(f"  ? {name}: no schedule set")
            continue
        
        try:
            cron = croniter(schedule, now)
            next_time = cron.get_next(datetime)
            typer.echo(f"  • {name}: {next_time.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            typer.echo(f"  ✗ {name}: invalid schedule '{schedule}' - {e}")

# ============================================================
# Service management commands
# ============================================================

@logger_app.command()
def service_install(
    device: str = typer.Option(None, "--device", "-d", help="Device name (omit for all enabled)"),
    log_level: str = typer.Option("WARNING", "--log-level", "-l", help="Log level for service"),
):
    """Install systemd services for automatic collection."""
    from pathlib import Path
    
    manager = _get_config()
    logger_config = _get_logger_config(manager)
    
    registry = DeviceRegistry(manager.devices)
    
    if device:
        if device not in manager.devices:
            typer.echo(f"Device '{device}' not found", err=True)
            raise typer.Exit(code=1)
        devices = [device]
    else:
        devices = registry.get_enabled_devices(logger_config)
        if not devices:
            typer.echo("No enabled devices found in configuration", err=True)
            raise typer.Exit(code=1)
    
    # System paths
    systemd_dir = Path("/etc/systemd/system")
    project_dir = Path("/opt/meshtastic-tools")
    venv_bin = project_dir / "venv" / "bin" / "meshtastic-tools"
    config_path = Path("/etc/meshtastic-tools/config.yaml")
    log_dir = project_dir / "logs"
    
    # Check permissions
    import os
    if os.geteuid() != 0:
        typer.echo("This command requires root privileges to write to /etc/systemd/system/", err=True)
        typer.echo("Please run: sudo meshtastic-tools logger service-install", err=True)
        raise typer.Exit(code=1)
    
    # Create symlink for easier sudo access
    symlink_path = Path("/usr/local/bin/meshtastic-tools")
    if not symlink_path.exists():
        try:
            symlink_path.symlink_to(venv_bin)
            typer.echo(f"  + Created symlink: {symlink_path} -> {venv_bin}")
        except OSError as e:
            typer.echo(f"  ! Could not create symlink: {e}", err=True)
    
    log_dir.mkdir(parents=True, exist_ok=True)
    
    for dev_name in devices:
        service_file = systemd_dir / f"meshtastic-logger@{dev_name}.service"
        timer_file = systemd_dir / f"meshtastic-logger@{dev_name}.timer"
        
        service_content = f"""[Unit]
Description=Meshtastic Logger for {dev_name}
After=network.target

[Service]
Type=oneshot
ExecStart={venv_bin} --log-level {log_level} logger run
WorkingDirectory={project_dir}
Environment=MESHTASTIC_TOOLS_CONFIG={config_path}
Environment=PATH={project_dir}/venv/bin:/usr/local/bin:/usr/bin
StandardOutput=append:{log_dir}/{dev_name}.log
StandardError=append:{log_dir}/{dev_name}-error.log

[Install]
WantedBy=default.target
"""
        service_file.write_text(service_content)
        
        timer_content = f"""[Unit]
Description=Meshtastic Logger timer for {dev_name}

[Timer]
OnCalendar=*:0/1
Persistent=true

[Install]
WantedBy=timers.target
"""
        timer_file.write_text(timer_content)
        
        typer.echo(f"  + {dev_name}")
    
    import subprocess
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    
    typer.echo(f"\nInstalled services for {len(devices)} device(s)")
    typer.echo("\nEnable and start with:")
    typer.echo("  sudo meshtastic-tools logger service-enable --device DEVICE_NAME")
    typer.echo("  sudo meshtastic-tools logger service-enable --all")
    typer.echo("\nCheck status:")
    typer.echo("  sudo meshtastic-tools logger service-status")


@logger_app.command()
def service_uninstall(
    device: str = typer.Option(None, "--device", "-d", help="Device name"),
    all_flag: bool = typer.Option(False, "--all", help="Remove all logger services"),
):
    """Remove systemd services."""
    from pathlib import Path
    import os
    
    if os.geteuid() != 0:
        typer.echo("This command requires root privileges.", err=True)
        typer.echo("Please run: sudo meshtastic-tools logger service-uninstall", err=True)
        raise typer.Exit(code=1)
    
    systemd_dir = Path("/etc/systemd/system")
    
    patterns = []
    if device:
        patterns = [f"meshtastic-logger@{device}.service", f"meshtastic-logger@{device}.timer"]
    else:
        patterns = ["meshtastic-logger@*.service", "meshtastic-logger@*.timer"]
    
    removed = []
    import subprocess
    
    for pattern in patterns:
        for file in systemd_dir.glob(pattern):
            service_name = file.stem
            subprocess.run(["systemctl", "stop", service_name], capture_output=True)
            subprocess.run(["systemctl", "disable", service_name], capture_output=True)
            file.unlink()
            removed.append(file.name)
    
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    
    if removed:
        typer.echo(f"Removed {len(removed)} service(s):")
        for name in removed:
            typer.echo(f"  - {name}")
    else:
        typer.echo("No services found")
    
    # Remove symlink if no services left
    remaining_services = list(systemd_dir.glob("meshtastic-logger@*.service"))
    if not remaining_services:
        symlink_path = Path("/usr/local/bin/meshtastic-tools")
        if symlink_path.exists() and symlink_path.is_symlink():
            symlink_path.unlink()
            typer.echo(f"  - Removed symlink: {symlink_path}")

@logger_app.command()
def service_cleanup(
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove all services, symlinks, and configuration."""
    from pathlib import Path
    import os
    import shutil
    
    if os.geteuid() != 0:
        typer.echo("This command requires root privileges.", err=True)
        typer.echo("Please run: sudo meshtastic-tools logger service-cleanup", err=True)
        raise typer.Exit(code=1)
    
    if not confirm:
        typer.echo("This will remove:")
        typer.echo("  - All meshtastic-logger systemd services")
        typer.echo("  - Symlink /usr/local/bin/meshtastic-tools")
        typer.echo("  - Config directory /etc/meshtastic-tools/")
        typer.echo("  - Log files in /opt/meshtastic-tools/logs/")
        typer.echo("  - Data files in /opt/meshtastic-tools/data/")
        typer.echo("\nThe application in /opt/meshtastic-tools/ will NOT be removed.")
        response = typer.confirm("Are you sure?")
        if not response:
            typer.echo("Aborted")
            raise typer.Exit()
    
    import subprocess
    
    # Stop and remove all services
    systemd_dir = Path("/etc/systemd/system")
    removed = []
    
    for pattern in ["meshtastic-logger@*.service", "meshtastic-logger@*.timer"]:
        for file in systemd_dir.glob(pattern):
            service_name = file.stem
            subprocess.run(["systemctl", "stop", service_name], capture_output=True)
            subprocess.run(["systemctl", "disable", service_name], capture_output=True)
            file.unlink()
            removed.append(file.name)
    
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    
    if removed:
        typer.echo(f"\nRemoved {len(removed)} service(s):")
        for name in removed:
            typer.echo(f"  - {name}")
    
    # Remove symlink
    symlink_path = Path("/usr/local/bin/meshtastic-tools")
    if symlink_path.exists():
        symlink_path.unlink()
        typer.echo(f"  - Removed symlink: {symlink_path}")
    
    # Remove config directory
    config_dir = Path("/etc/meshtastic-tools")
    if config_dir.exists():
        shutil.rmtree(config_dir)
        typer.echo(f"  - Removed config: {config_dir}")
    
    # Clean logs
    log_dir = Path("/opt/meshtastic-tools/logs")
    if log_dir.exists():
        for log_file in log_dir.glob("*.log"):
            log_file.unlink()
        typer.echo(f"  - Cleaned logs: {log_dir}")
    
    # Clean data (optional, ask)
    data_dir = Path("/opt/meshtastic-tools/data")
    if data_dir.exists():
        typer.echo(f"\nData directory still exists: {data_dir}")
        typer.echo("To remove all collected data run:")
        typer.echo(f"  sudo rm -rf {data_dir}")
    
    typer.echo("\nCleanup complete.")
    typer.echo("To completely remove the application:")
    typer.echo("  sudo rm -rf /opt/meshtastic-tools")

@logger_app.command()
def service_enable(
    device: str = typer.Option(None, "--device", "-d", help="Device name"),
    all_flag: bool = typer.Option(False, "--all", help="Enable all installed services"),
    now: bool = typer.Option(True, "--now/--no-now", help="Also start immediately"),
):
    """Enable systemd timers."""
    from pathlib import Path
    import os
    
    if os.geteuid() != 0:
        typer.echo("This command requires root privileges.", err=True)
        typer.echo("Please run: sudo meshtastic-tools logger service-enable", err=True)
        raise typer.Exit(code=1)
    
    systemd_dir = Path("/etc/systemd/system")
    
    devices = []
    if device:
        devices = [device]
    else:
        for file in systemd_dir.glob("meshtastic-logger@*.timer"):
            name = file.stem.replace("meshtastic-logger@", "")
            devices.append(name)
    
    if not devices:
        typer.echo("No services found. Run 'sudo meshtastic-tools logger service-install' first.")
        raise typer.Exit(code=1)
    
    import subprocess
    
    for dev_name in devices:
        cmd = ["systemctl", "enable"]
        if now:
            cmd.append("--now")
        cmd.append(f"meshtastic-logger@{dev_name}.timer")
        
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            typer.echo(f"  + Enabled meshtastic-logger@{dev_name}.timer")
        else:
            typer.echo(f"  ! Failed to enable meshtastic-logger@{dev_name}.timer: {result.stderr.decode()}", err=True)


@logger_app.command()
def service_disable(
    device: str = typer.Option(None, "--device", "-d", help="Device name"),
    all_flag: bool = typer.Option(False, "--all", help="Disable all installed services"),
    stop: bool = typer.Option(True, "--stop/--no-stop", help="Also stop immediately"),
):
    """Disable systemd timers."""
    from pathlib import Path
    import os
    
    if os.geteuid() != 0:
        typer.echo("This command requires root privileges.", err=True)
        typer.echo("Please run: sudo meshtastic-tools logger service-disable", err=True)
        raise typer.Exit(code=1)
    
    systemd_dir = Path("/etc/systemd/system")
    
    devices = []
    if device:
        devices = [device]
    else:
        for file in systemd_dir.glob("meshtastic-logger@*.timer"):
            name = file.stem.replace("meshtastic-logger@", "")
            devices.append(name)
    
    if not devices:
        typer.echo("No services found.")
        raise typer.Exit(code=1)
    
    import subprocess
    
    for dev_name in devices:
        cmd = ["systemctl", "disable"]
        if stop:
            cmd.append("--now")
        cmd.append(f"meshtastic-logger@{dev_name}.timer")
        
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0:
            typer.echo(f"  - Disabled meshtastic-logger@{dev_name}.timer")
        else:
            typer.echo(f"  ! Failed: {result.stderr.decode()}", err=True)


@logger_app.command()
def service_status():
    """Show status of installed logger services."""
    import subprocess
    
    result = subprocess.run(
        ["systemctl", "list-timers", "meshtastic-logger@*"],
        capture_output=True,
        text=True,
    )
    
    if result.stdout.strip():
        typer.echo(result.stdout)
    else:
        typer.echo("No meshtastic-logger timers found")
        typer.echo("Run 'sudo meshtastic-tools logger service-install' first")


@logger_app.command()
def service_logs(
    device: str = typer.Argument(..., help="Device name"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
):
    """View logs for a device service."""
    import subprocess
    from pathlib import Path
    
    log_file = Path("/opt/meshtastic-tools") / "logs" / f"{device}.log"
    
    if not log_file.exists():
        typer.echo(f"Log file not found: {log_file}")
        raise typer.Exit(code=1)
    
    cmd = ["tail", "-n", str(lines)]
    if follow:
        cmd.append("-f")
    cmd.append(str(log_file))
    
    subprocess.run(cmd)


if __name__ == "__main__":
    logger_app()            