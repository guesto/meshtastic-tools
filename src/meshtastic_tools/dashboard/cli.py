"""CLI for visualizing local Meshtastic data without contacting devices."""

from typing import Optional

import typer

from meshtastic_tools.core.config import ConfigManager
from meshtastic_tools.core.exceptions import ConfigError


dashboard_app = typer.Typer(
    name="dashboard",
    help="Visualize locally collected Meshtastic data",
    invoke_without_command=True,
)


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


@dashboard_app.callback()
def dashboard(
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Show one device, including disabled devices"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colors"),
    width: Optional[int] = typer.Option(None, "--width", min=1, help="Override terminal width"),
    page: int = typer.Option(1, "--page", min=1, help="Device summary page (three devices per page)"),
    all_devices: bool = typer.Option(False, "--all", help="Show only the device summary"),
    layout: str = typer.Option("auto", "--layout", help="Layout: auto, single or split"),
):
    """Show the logger dashboard from local files, without contacting devices."""
    import json
    from rich.console import Console
    from meshtastic_tools.dashboard.data import (
        collect_dashboard_data, render_rich, render_text, to_json,
    )

    if layout not in ("auto", "single", "split"):
        raise typer.BadParameter("Choose auto, single or split", param_hint="--layout")
    if device and all_devices:
        raise typer.BadParameter("Choose either --device or --all")
    manager = _get_config()
    _get_logger_config(manager)
    try:
        data = collect_dashboard_data(manager, device=device)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    from meshtastic_tools.dashboard.render import device_page
    try:
        device_page(data, page)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--page")
    if json_output:
        typer.echo(json.dumps(to_json(data, width=width or 120, layout=layout), ensure_ascii=False, indent=2, allow_nan=False))
        return
    console = Console(width=width, no_color=no_color, highlight=False)
    render_width = width if width is not None else console.width
    if not console.is_terminal or console.is_dumb_terminal or render_width < 80:
        typer.echo(render_text(data, render_width, page, all_devices, layout))
    else:
        console.print(render_rich(data, render_width, page, all_devices, layout, height=console.height))


