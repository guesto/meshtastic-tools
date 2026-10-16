"""Render systemd units independently of their installation location."""

import json
from pathlib import Path
from typing import Optional


def _quote(value) -> str:
    # systemd expands specifiers and ExecStart environment variables after parsing.
    return json.dumps(str(value).replace("%", "%%").replace("$", "$$"), ensure_ascii=False)


def render_service(device: str, executable: Path, config: Path, log_level: str,
                   user: Optional[str] = None) -> str:
    if log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("Invalid service log level")
    if any(char in str(config) for char in "\r\n"):
        raise ValueError("Systemd configuration paths must not contain newlines")
    args = [executable, "-m", "meshtastic_tools.cli", "--config", config,
            "--log-level", log_level.upper(), "logger", "run", "--device", device]
    return f"""[Unit]
Description=Meshtastic Logger for {device}
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
{f'User={user}' if user else ''}
ExecStart={' '.join(_quote(arg) for arg in args)}
WorkingDirectory={str(config.parent).replace('%', '%%')}
UMask=0077
StandardOutput=journal
StandardError=journal
"""


def render_timer(device: str) -> str:
    return f"""[Unit]
Description=Meshtastic Logger timer for {device}

[Timer]
OnCalendar=*:0/1
AccuracySec=1s
Persistent=true

[Install]
WantedBy=timers.target
"""
