"""CLI commands for logger tool."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from croniter import croniter

from meshtastic_tools.core.config import ConfigManager
from meshtastic_tools.core.device import DeviceManager, DeviceRegistry
from meshtastic_tools.core.exceptions import CollectionError, ConfigError, StorageError
from meshtastic_tools.core.logging_config import get_logger
from meshtastic_tools.logger.collector import MeshtasticCollector
from meshtastic_tools.logger.storage import GlobalStorageManager
from meshtastic_tools.logger.scheduler import DeviceSchedule
from meshtastic_tools.logger.health import DeviceHealth, device_status
from meshtastic_tools.logger.services import render_service, render_timer

SYSTEMD_DIR = Path("/etc/systemd/system")
CLI_SYMLINK = Path("/usr/local/bin/meshtastic-tools")

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


def _systemctl(args):
    import subprocess
    result = subprocess.run(["systemctl", *args], capture_output=True, text=True)
    if result.returncode:
        typer.echo(f"systemctl {' '.join(args)} failed: {result.stderr.strip()}", err=True)
        raise typer.Exit(code=1)
    return result


def _get_config():
    """Load configuration."""
    manager = ConfigManager(strict=True)
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
    
    if collector_type not in ("info", "telemetry", "nodes", "position"):
        typer.echo(f"Unknown collector type: {collector_type}", err=True)
        raise typer.Exit(code=2)
    if device and all_devices:
        typer.echo("Choose either --device or --all", err=True)
        raise typer.Exit(code=2)

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
            
            schedule_state = DeviceSchedule(device_storage.device_path.parent)
            with schedule_state.locked():
                with DeviceHealth(device_storage.device_path.parent).attempt(collector_type, "manual") as attempt:
                    filepath = getattr(collector, f"collect_{collector_type}")()
                    attempt.collected(filepath)
                    device_storage.cleanup()

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
    if success_count != len(devices_to_collect):
        raise typer.Exit(code=1)

@logger_app.command()
def run(
    force: bool = typer.Option(False, "--force", "-f", help="Ignore schedule and collect immediately"),
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Run only this device's schedule"),
):
    """Run scheduled collection (for cron/systemd). Fail on any collection error."""
    logger = get_logger(__name__)
    manager = _get_config()
    logger_config = _get_logger_config(manager)
    if not logger_config.enabled:
        logger.info("Logger tool is disabled, exiting")
        return

    registry = DeviceRegistry(manager.devices)
    storage_manager = GlobalStorageManager(logger_config.storage)
    enabled_devices = registry.get_enabled_devices(logger_config)
    if device:
        if device not in enabled_devices:
            typer.echo(f"Device '{device}' is unknown or disabled in logger configuration", err=True)
            raise typer.Exit(code=1)
        enabled_devices = [device]

    failed = False
    for device_name in enabled_devices:
        try:
            device_storage = storage_manager.get_device_manager(device_name)
            state = DeviceSchedule(device_storage.device_path.parent)
            with state.locked():
                now = logger_config.schedule_time(datetime.now())
                schedule = logger_config.get_device_schedule(device_name)
                if not force and not state.is_due(schedule, now, logger_config.timezone):
                    continue
                collector = MeshtasticCollector(registry.get_manager(device_name), device_storage)
                with DeviceHealth(device_storage.device_path.parent).attempt("info", "scheduled") as attempt:
                    filepath = collector.collect_info()
                    attempt.collected(filepath)
                    state.mark_completed(schedule, state.latest_slot(schedule, now), logger_config.timezone)
                    logger.info(f"Collected from {device_name}", file=filepath.name)
                    device_storage.cleanup()
        except Exception as e:
            failed = True
            logger.error(f"Collection failed for {device_name}", error=str(e))

    if failed:
        raise typer.Exit(code=1)


@logger_app.command("status")
def status(
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Show one device"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
    check: bool = typer.Option(False, "--check", help="Exit with code 1 if an enabled device is unhealthy"),
):
    """Show collection health from local records without contacting devices."""
    import json

    manager = _get_config()
    logger_config = _get_logger_config(manager)
    if device and device not in manager.devices:
        typer.echo(f"Device '{device}' not found", err=True)
        raise typer.Exit(code=1)
    names = [device] if device else list(manager.devices)
    now = logger_config.schedule_time(datetime.now())
    reports = []
    unreadable = False
    for name in names:
        enabled = logger_config.enabled and logger_config.is_device_enabled(name)
        try:
            report = device_status(
                name, logger_config.storage.data_dir / name, enabled,
                logger_config.get_device_schedule(name),
                manager.devices[name].connection.timeout, now,
                grace_seconds=logger_config.health_grace_seconds, timezone_name=logger_config.timezone,
            )
        except StorageError as e:
            unreadable = True
            report = {"device": name, "state": "unreadable", "enabled": enabled, "error": str(e)}
        reports.append(report)

    if json_output:
        checked_at = now if now.tzinfo else now.astimezone()
        typer.echo(json.dumps({"checked_at": checked_at.isoformat(), "devices": reports}, ensure_ascii=False, indent=2))
    else:
        if not reports:
            typer.echo("No devices configured")

        def display_time(value):
            if value is None:
                return "not recorded"
            # Python 3.9/3.10 require an explicit UTC offset instead of Z.
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            localized = logger_config.schedule_time(parsed)
            if localized.tzinfo is None or logger_config.timezone == "local":
                localized = parsed.astimezone()
            return localized.isoformat(timespec="seconds")

        for report in reports:
            typer.echo(f"\n{report['device']}: {report['state'].upper()}")
            if report.get("error"):
                typer.echo(f"  {report['error']}")
                continue
            typer.echo(f"  Schedule timezone: {report['timezone']}")
            typer.echo(f"  Last successful collection: {display_time(report['last_success_at'])}")
            typer.echo(f"  Last attempt: {display_time(report['last_attempt_at'])}")
            duration = report["last_duration_seconds"]
            typer.echo(f"  Request duration: {duration:.2f}s" if duration is not None else "  Request duration: not recorded")
            typer.echo(f"  Consecutive failures: {report['consecutive_failures']}")
            error = " ".join(report["last_error"].splitlines()) if report["last_error"] else "none"
            typer.echo(f"  Last error: {error}")
            if report["last_failure_at"]:
                typer.echo(f"  Last error time: {display_time(report['last_failure_at'])}")
            next_time = display_time(report["next_scheduled_at"]) if report["enabled"] else "disabled"
            typer.echo(f"  Next scheduled collection: {next_time}")
            if report["stale"]:
                typer.echo("  WARNING: collection data is overdue")
            elif report["state"] == "unknown":
                typer.echo("  WARNING: no collection results recorded yet")

    unhealthy = any(report["enabled"] and report["state"] != "ok" for report in reports)
    if unreadable or (check and (unhealthy or not reports)):
        raise typer.Exit(code=1)


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
    
    typer.echo(f"\n[SCHEDULE] Collection schedules (timezone: {logger_config.timezone}):\n")
    
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
    now = logger_config.schedule_time(datetime.now())
    
    if device:
        if device not in devices:
            typer.echo(f"Device '{device}' not found", err=True)
            raise typer.Exit(code=1)
        devices = {device: devices[device]}
    
    typer.echo(f"\n[NEXT] Next collection times (timezone: {logger_config.timezone}, now: {now.isoformat(timespec='seconds')}):\n")
    
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
            typer.echo(f"  • {name}: {next_time.isoformat(timespec='seconds')}")
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
    
    import os
    import sys
    if os.geteuid() != 0:
        typer.echo("This command requires root privileges to write to /etc/systemd/system/", err=True)
        raise typer.Exit(code=1)
    if manager.loaded_path is None:
        typer.echo("Create a configuration file before installing services", err=True)
        raise typer.Exit(code=1)

    executable = Path(sys.executable).absolute()
    import pwd
    owner_uid = int(os.environ.get("SUDO_UID", manager.loaded_path.stat().st_uid))
    service_user = pwd.getpwuid(owner_uid).pw_name
    try:
        units = [(name, render_service(name, executable, manager.loaded_path, log_level, service_user), render_timer(name))
                 for name in devices]
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2)

    for name, service, timer in units:
        (SYSTEMD_DIR / f"meshtastic-logger@{name}.service").write_text(service)
        (SYSTEMD_DIR / f"meshtastic-logger@{name}.timer").write_text(timer)
        typer.echo(f"  + {name}")

    entrypoint = executable.parent / "meshtastic-tools"
    if entrypoint.exists() and not CLI_SYMLINK.exists() and not CLI_SYMLINK.is_symlink():
        CLI_SYMLINK.symlink_to(entrypoint)
    _systemctl(["daemon-reload"])

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
    
    if bool(device) == all_flag:
        typer.echo("Choose either --device or --all", err=True)
        raise typer.Exit(code=2)
    systemd_dir = SYSTEMD_DIR

    patterns = []
    if device:
        patterns = [f"meshtastic-logger@{device}.timer", f"meshtastic-logger@{device}.service"]
    else:
        patterns = ["meshtastic-logger@*.timer", "meshtastic-logger@*.service"]
    
    removed = []
    import subprocess
    
    for pattern in patterns:
        for file in systemd_dir.glob(pattern):
            service_name = file.name
            _systemctl(["stop", service_name])
            _systemctl(["disable", service_name])
            file.unlink()
            removed.append(file.name)
    
    _systemctl(["daemon-reload"])
    
    if removed:
        typer.echo(f"Removed {len(removed)} service(s):")
        for name in removed:
            typer.echo(f"  - {name}")
    else:
        typer.echo("No services found")
    
    # Remove symlink if no services left
    remaining_services = list(systemd_dir.glob("meshtastic-logger@*.service"))
    if not remaining_services:
        symlink_path = CLI_SYMLINK
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
        typer.echo("  - Collected data will be preserved")
        typer.echo("\nThe application in /opt/meshtastic-tools/ will NOT be removed.")
        response = typer.confirm("Are you sure?")
        if not response:
            typer.echo("Aborted")
            raise typer.Exit()
    
    import subprocess
    
    # Stop and remove all services
    systemd_dir = SYSTEMD_DIR
    removed = []
    
    for pattern in ["meshtastic-logger@*.timer", "meshtastic-logger@*.service"]:
        for file in systemd_dir.glob(pattern):
            service_name = file.name
            _systemctl(["stop", service_name])
            _systemctl(["disable", service_name])
            file.unlink()
            removed.append(file.name)
    
    _systemctl(["daemon-reload"])
    
    if removed:
        typer.echo(f"\nRemoved {len(removed)} service(s):")
        for name in removed:
            typer.echo(f"  - {name}")
    
    # Remove symlink
    symlink_path = CLI_SYMLINK
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
    
    if bool(device) == all_flag:
        typer.echo("Choose either --device or --all", err=True)
        raise typer.Exit(code=2)
    systemd_dir = SYSTEMD_DIR
    
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
        
        _systemctl(cmd[1:])
        typer.echo(f"  + Enabled meshtastic-logger@{dev_name}.timer")


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
    
    if bool(device) == all_flag:
        typer.echo("Choose either --device or --all", err=True)
        raise typer.Exit(code=2)
    systemd_dir = SYSTEMD_DIR
    
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
        
        _systemctl(cmd[1:])
        typer.echo(f"  - Disabled meshtastic-logger@{dev_name}.timer")


@logger_app.command()
def service_status():
    """Show status of installed logger services."""
    import subprocess
    
    result = _systemctl(["list-timers", "meshtastic-logger@*"])
    
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
    
    cmd = ["journalctl", "-u", f"meshtastic-logger@{device}.service", "-n", str(lines), "--no-pager"]
    if follow:
        cmd.append("--follow")
    result = subprocess.run(cmd)
    if result.returncode:
        raise typer.Exit(code=1)



if __name__ == "__main__":
    logger_app()
