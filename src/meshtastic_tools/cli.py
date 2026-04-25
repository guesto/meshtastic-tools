"""Main CLI entry point for meshtastic-tools."""

from pathlib import Path
from typing import Optional

import typer

from meshtastic_tools import __version__
from meshtastic_tools.core.config import ConfigManager
from meshtastic_tools.core.device import DeviceRegistry
from meshtastic_tools.core.logging_config import setup_logging
from meshtastic_tools.logger.cli import logger_app

app = typer.Typer(
    name="meshtastic-tools",
    help="Collection of tools for Meshtastic devices",
    no_args_is_help=True,
)

# Add subcommands
app.add_typer(logger_app, name="logger", help="Data logger tool")

# Global options
_config_option = typer.Option(
    None,
    "--config",
    "-c",
    help="Path to configuration file",
    exists=False,
)

_log_level_option = typer.Option(
    "WARNING",
    "--log-level",
    "-l",
    help="Log level (DEBUG, INFO, WARNING, ERROR)",
)


@app.callback()
def callback(
    config: Optional[Path] = _config_option,
    log_level: str = _log_level_option,
):
    """Meshtastic Tools - Collection of utilities for Meshtastic devices."""
    # Setup logging
    setup_logging(level=log_level)
    
    # Store config path in context for subcommands
    if config:
        import os
        os.environ["MESHTASTIC_TOOLS_CONFIG"] = str(config)


@app.command()
def version():
    """Show version information."""
    typer.echo(f"meshtastic-tools version {__version__}")


@app.command()
def config_show(
    section: Optional[str] = typer.Option(
        None, "--section", "-s", help="Show specific section (devices, tools)"
    ),
):
    """Show current configuration."""
    import yaml
    
    manager = ConfigManager()
    
    try:
        manager.load()
    except Exception as e:
        typer.echo(f"Error loading config: {e}", err=True)
        raise typer.Exit(code=1)
    
    config_data = manager.raw_config
    
    if section:
        if section in config_data:
            config_data = {section: config_data[section]}
        else:
            typer.echo(f"Section '{section}' not found", err=True)
            raise typer.Exit(code=1)
    
    typer.echo(yaml.dump(config_data, default_flow_style=False, indent=2))


@app.command()
def config_validate():
    """Validate configuration and show warnings."""
    manager = ConfigManager()
    
    try:
        manager.load()
    except Exception as e:
        typer.echo(f"✗ Config error: {e}", err=True)
        raise typer.Exit(code=1)
    
    warnings = manager.validate()
    
    if warnings:
        typer.echo("[WARN]  Configuration warnings:")
        for warning in warnings:
            typer.echo(f"  • {warning}")
    else:
        typer.echo("✓ Configuration is valid")
    
    # Show devices summary
    devices = manager.devices
    if devices:
        typer.echo(f"\n[DEVICES] Configured devices: {len(devices)}")
        for name in devices.keys():
            typer.echo(f"  • {name}")
    else:
        typer.echo("\n[WARN]  No devices configured")


@app.command()
def config_path():
    """Show configuration file path."""
    manager = ConfigManager()
    
    paths = [
        Path("/etc/meshtastic-tools/config.yaml"),
        Path.home() / ".config" / "meshtastic-tools" / "config.yaml",
        Path.cwd() / "config" / "meshtastic-tools.yaml",
    ]
    
    import os
    env_path = os.getenv("MESHTASTIC_TOOLS_CONFIG")
    if env_path:
        typer.echo(f"Environment: {env_path}")
    
    typer.echo("\nConfiguration search paths (in order):")
    for i, path in enumerate(paths, 1):
        exists = "✓" if path.exists() else "✗"
        typer.echo(f"  {i}. {exists} {path}")


@app.command()
def devices_list(
    check: bool = typer.Option(False, "--check", "-c", help="Check connection status"),
):
    """List all configured devices."""
    manager = ConfigManager()
    
    try:
        manager.load()
    except Exception as e:
        typer.echo(f"Error loading config: {e}", err=True)
        raise typer.Exit(code=1)
    
    devices = manager.devices
    
    if not devices:
        typer.echo("No devices configured")
        return
    
    registry = DeviceRegistry(devices)
    
    typer.echo(f"\n[DEVICES] Devices ({len(devices)}):\n")
    
    for device_info in registry.list_devices(include_status=check):
        name = device_info["name"]
        conn_type = device_info["connection_type"]
        address = device_info["address"]
        
        if check:
            if device_info.get("connected"):
                status = "✓"
                details = f"{device_info['node_id']} - {device_info['long_name']}"
                details += f"\n       FW: {device_info['firmware']} | HW: {device_info['hardware']}"
            else:
                status = "✗"
                details = "Not connected"
        else:
            status = "•"
            details = ""
        
        line = f"  {status} {name} ({conn_type}:{address})"
        typer.echo(line)
        
        if details:
            typer.echo(f"       {details}")
        
        if device_info.get("metadata"):
            for key, value in device_info["metadata"].items():
                if value:
                    typer.echo(f"       {key}: {value}")
        
        typer.echo()


@app.command()
def devices_show(
    device: str = typer.Option(..., "--device", "-d", help="Device name"),
    refresh: bool = typer.Option(False, "--refresh", "-r", help="Force refresh info"),
    telemetry: bool = typer.Option(False, "--telemetry", "-t", help="Also show device telemetry"),
):
    """Show detailed information about a device."""
    manager = ConfigManager()
    
    try:
        manager.load()
    except Exception as e:
        typer.echo(f"Error loading config: {e}", err=True)
        raise typer.Exit(code=1)
    
    try:
        device_config = manager.get_device(device)
    except Exception as e:
        typer.echo(f"Device not found: {e}", err=True)
        raise typer.Exit(code=1)
    
    from meshtastic_tools.core.device import DeviceManager
    
    device_manager = DeviceManager(device_config)
    
    typer.echo(f"\n[DEVICE] Device: {device}\n")
    
    # Получаем тип подключения как строку
    conn_type = device_config.connection.type
    if hasattr(conn_type, 'value'):
        conn_type = conn_type.value
    
    typer.echo(f"  Connection: {conn_type} {device_config.connection.address}")
    
    if conn_type == "port" and device_config.connection.baudrate:
        typer.echo(f"  Baudrate: {device_config.connection.baudrate}")
    
    typer.echo(f"  Timeout: {device_config.connection.timeout}s")
    
    if device_config.metadata:
        typer.echo("\n  Metadata:")
        for key, value in device_config.metadata.items():
            typer.echo(f"    {key}: {value}")
    
    # Try to fetch device info
    if refresh:
        typer.echo("\n  Fetching device information...")
    
    try:
        info = device_manager.get_info(force_refresh=refresh)
        
        typer.echo("\n  Device Information:")
        typer.echo(f"    Node ID: {info.node_id}")
        typer.echo(f"    Node Number: {info.my_node_num}")
        typer.echo(f"    Long Name: {info.long_name}")
        typer.echo(f"    Short Name: {info.short_name}")
        typer.echo(f"    Firmware: {info.firmware_version}")
        typer.echo(f"    Hardware: {info.hw_model}")
        typer.echo(f"    Role: {info.role}")
        typer.echo(f"    Reboot Count: {info.reboot_count}")
        typer.echo(f"    Bluetooth: {'✓' if info.has_bluetooth else '✗'}")
        typer.echo(f"    WiFi: {'✓' if info.has_wifi else '✗'}")

        # Show telemetry if requested
        if telemetry:
            try:
                typer.echo("\n  Telemetry:")
                telemetry_result = device_manager.execute_command(
                    ["--request-telemetry", "--dest", info.node_id]
                )
                typer.echo(f"    {telemetry_result.stdout.strip()}")
            except Exception as e:
                typer.echo(f"    Could not fetch telemetry: {e}")

    except Exception as e:
        typer.echo(f"\n  [WARN]  Could not fetch device info: {e}", err=True)

@app.command()
def devices_check(
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Check specific device"),
    all_devices: bool = typer.Option(False, "--all", "-a", help="Check all devices"),
):
    """Check connection to device(s)."""
    if not device and not all_devices:
        typer.echo("Please specify --device or --all", err=True)
        raise typer.Exit(code=1)
    
    manager = ConfigManager()
    
    try:
        manager.load()
    except Exception as e:
        typer.echo(f"Error loading config: {e}", err=True)
        raise typer.Exit(code=1)
    
    devices = manager.devices
    
    if not devices:
        typer.echo("No devices configured")
        raise typer.Exit(code=1)
    
    registry = DeviceRegistry(devices)
    
    if device:
        if device not in devices:
            typer.echo(f"Device '{device}' not found", err=True)
            raise typer.Exit(code=1)
        
        typer.echo(f"\nChecking {device}...\n")
        
        manager = registry.get_manager(device)
        success, message = manager.test_connection()
        
        if success:
            typer.echo(f"  ✓ {message}")
        else:
            typer.echo(f"  ✗ {message}")
            raise typer.Exit(code=1)
    else:
        typer.echo(f"\nChecking {len(devices)} device(s)...\n")
        
        results = registry.check_all_devices()
        
        success_count = 0
        for name, (success, message) in results.items():
            if success:
                typer.echo(f"  ✓ {name}: {message}")
                success_count += 1
            else:
                typer.echo(f"  ✗ {name}: {message}")
        
        typer.echo(f"\n  {success_count}/{len(devices)} devices connected")

@app.command()
def devices_test(
    device: str = typer.Option(..., "--device", "-d", help="Device name"),
):
    """Test connection and show raw info output."""
    manager = ConfigManager()
    
    try:
        manager.load()
    except Exception as e:
        typer.echo(f"Error loading config: {e}", err=True)
        raise typer.Exit(code=1)
    
    try:
        device_config = manager.get_device(device)
    except Exception as e:
        typer.echo(f"Device not found: {e}", err=True)
        raise typer.Exit(code=1)
    
    from meshtastic_tools.core.device import DeviceManager
    import subprocess
    
    device_manager = DeviceManager(device_config)
    
    typer.echo(f"\nTesting connection to {device}...\n")
    
    cmd = ["meshtastic"]
    cmd.extend(device_config.connection.get_cli_args())
    cmd.append("--info")
    
    typer.echo(f"Command: {' '.join(cmd)}\n")
    typer.echo("─" * 60)
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=device_config.connection.timeout,
        )
        
        if result.returncode == 0:
            typer.echo(result.stdout)
        else:
            typer.echo(f"Error: {result.stderr}", err=True)
            raise typer.Exit(code=1)
            
    except subprocess.TimeoutExpired:
        typer.echo(f"Timeout (>{device_config.connection.timeout}s)", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
        
if __name__ == "__main__":
    app()