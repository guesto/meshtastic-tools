import importlib
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from typer.testing import CliRunner

from meshtastic_tools.cli import app
from meshtastic_tools.logger.services import render_service, render_timer

logger_cli = importlib.import_module("meshtastic_tools.logger.cli")


def test_render_service_selects_only_its_device_and_current_installation():
    unit = render_service("alpha", Path("/custom install/venv/bin/python"),
                          Path("/custom config/config.yaml"), "WARNING")
    command = next(line.split("=", 1)[1] for line in unit.splitlines() if line.startswith("ExecStart="))
    assert shlex.split(command) == ["/custom install/venv/bin/python", "-m", "meshtastic_tools.cli",
                                    "--config", "/custom config/config.yaml", "--log-level", "WARNING",
                                    "logger", "run", "--device", "alpha"]
    assert "UMask=0077" in unit
    assert "StandardError=journal" in unit
    assert "WorkingDirectory=/custom config\n" in unit
    assert "/opt/meshtastic-tools" not in unit
    assert "AccuracySec=1s" in render_timer("alpha")


def test_service_uninstall_stops_timer_before_service_with_full_suffix(tmp_path, monkeypatch):
    for suffix in ("service", "timer"):
        (tmp_path / f"meshtastic-logger@alpha.{suffix}").write_text("test unit")
    monkeypatch.setattr(logger_cli, "SYSTEMD_DIR", tmp_path)
    monkeypatch.setattr(logger_cli, "CLI_SYMLINK", tmp_path / "cli")
    monkeypatch.setattr("os.geteuid", lambda: 0)
    systemctl = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("subprocess.run", systemctl)
    result = CliRunner().invoke(app, ["logger", "service-uninstall", "--device", "alpha"])
    assert result.exit_code == 0, result.output
    commands = [call.args[0] for call in systemctl.call_args_list]
    assert commands[:4] == [
        ["systemctl", "stop", "meshtastic-logger@alpha.timer"],
        ["systemctl", "disable", "meshtastic-logger@alpha.timer"],
        ["systemctl", "stop", "meshtastic-logger@alpha.service"],
        ["systemctl", "disable", "meshtastic-logger@alpha.service"],
    ]
    assert not list(tmp_path.glob("meshtastic-logger@*"))


def test_systemctl_failure_returns_error_and_preserves_unit(tmp_path, monkeypatch):
    timer = tmp_path / "meshtastic-logger@alpha.timer"
    timer.write_text("test")
    monkeypatch.setattr(logger_cli, "SYSTEMD_DIR", tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    systemctl = Mock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="simulated systemctl failure"))
    monkeypatch.setattr("subprocess.run", systemctl)
    result = CliRunner().invoke(app, ["logger", "service-uninstall", "--device", "alpha"])
    assert result.exit_code == 1
    assert timer.exists()


def test_logs_read_journal_including_stderr(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr("subprocess.run", run)
    result = CliRunner().invoke(app, ["logger", "service-logs", "alpha", "--lines", "12"])
    assert result.exit_code == 0, result.output
    assert run.call_args.args[0] == ["journalctl", "-u", "meshtastic-logger@alpha.service", "-n", "12", "--no-pager"]
