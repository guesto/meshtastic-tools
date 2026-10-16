import importlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from typer.testing import CliRunner

from meshtastic_tools.cli import app
from meshtastic_tools.core.device import DeviceManager
from meshtastic_tools.core.exceptions import ConnectionError

logger_cli = importlib.import_module("meshtastic_tools.logger.cli")
runner = CliRunner()
INFO = '''Owner: Home (🏠)
My info: {"myNodeNum": 305419896}
Metadata: {"firmwareVersion": "test", "hwModel": "TEST"}
Config: {"security": {"privateKey": "secret"}, "psk": "channel-secret"}
'''


@pytest.fixture
def configured(tmp_path, monkeypatch):
    config = {
        "devices": {name: {"connection": {"type": "host", "address": "example.invalid"}}
                    for name in ("alpha", "beta")},
        "tools": {"logger": {"enabled": True, "storage": {"data_dir": str(tmp_path / "data"), "max_files": 50},
                             "devices": {name: {"enabled": True, "schedule": "*/5 * * * *"}
                                         for name in ("alpha", "beta")}}},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv("MESHTASTIC_TOOLS_CONFIG", str(path))
    return path, config


def test_run_device_selects_one_device_and_force_deduplicates_schedule(configured, monkeypatch):
    calls = []
    def execute(self, command, **kwargs):
        calls.append(self.name)
        return SimpleNamespace(stdout=INFO)
    monkeypatch.setattr(DeviceManager, "execute_command", execute)
    result = runner.invoke(app, ["logger", "run", "--force", "--device", "alpha"])
    assert result.exit_code == 0, result.output
    assert calls == ["alpha"]


def test_scheduled_cli_does_not_collect_early_or_twice(configured, monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 10, 16, 10, 4, 30)

        @classmethod
        def now(cls):
            return cls.current

    execute = Mock(return_value=SimpleNamespace(stdout=INFO))
    monkeypatch.setattr(logger_cli, "datetime", Clock)
    monkeypatch.setattr(DeviceManager, "execute_command", execute)
    args = ["logger", "run", "--device", "alpha"]
    assert runner.invoke(app, args).exit_code == 0
    execute.assert_not_called()
    Clock.current = datetime(2026, 10, 16, 10, 5, 30)
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, args).exit_code == 0
    assert execute.call_count == 1


def test_install_services_writes_one_device_per_unit(configured, tmp_path, monkeypatch):
    units = tmp_path / "units"
    units.mkdir()
    monkeypatch.setattr(logger_cli, "SYSTEMD_DIR", units)
    monkeypatch.setattr(logger_cli, "CLI_SYMLINK", tmp_path / "cli")
    monkeypatch.setattr("os.geteuid", lambda: 0)
    systemctl = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("subprocess.run", systemctl)
    result = runner.invoke(app, ["logger", "service-install"])
    assert result.exit_code == 0, result.output
    for name in ("alpha", "beta"):
        content = (units / f"meshtastic-logger@{name}.service").read_text()
        assert f'"--device" "{name}"' in content
        assert str(configured[0]) in content
        assert "User=" in content
    assert len(list(units.glob("*.timer"))) == 2


@pytest.mark.parametrize("args", [
    ["logger", "run", "--force"],
    ["logger", "collect", "info", "--all"],
    ["devices-check", "--all"],
    ["devices-show", "--device", "alpha"],
])
def test_all_failed_returns_error(configured, monkeypatch, args):
    def fail(*args, **kwargs):
        raise ConnectionError("simulated device failure")
    monkeypatch.setattr(DeviceManager, "execute_command", fail)
    monkeypatch.setattr(DeviceManager, "_fetch_info", fail)
    result = runner.invoke(app, args)
    assert result.exit_code == 1, result.output


def test_partial_collection_continues_and_returns_error(configured, monkeypatch):
    calls = []
    def execute(self, command, **kwargs):
        calls.append(self.name)
        if self.name == "alpha":
            raise ConnectionError("simulated failure")
        return SimpleNamespace(stdout=INFO)
    monkeypatch.setattr(DeviceManager, "execute_command", execute)
    result = runner.invoke(app, ["logger", "collect", "info", "--all"])
    assert result.exit_code == 1
    assert calls == ["alpha", "beta"]
    assert "1/2" in result.output


def test_devices_list_check_actually_checks(configured, monkeypatch):
    def fetch(self):
        from meshtastic_tools.core.device import DeviceInfo
        self._info = DeviceInfo.parse_from_info_output(INFO)
        self._is_connected = True
    check = Mock(side_effect=fetch)
    monkeypatch.setattr(DeviceManager, "_fetch_info", lambda self: check(self))
    result = runner.invoke(app, ["devices-list", "--check"])
    assert result.exit_code == 0, result.output
    assert check.call_count == 2
    assert "!12345678" in result.output
    assert "Not connected" not in result.output


@pytest.mark.parametrize("kind,flag", [("telemetry", "--request-telemetry"), ("position", "--request-position")])
def test_request_uses_full_node_id_and_redacts_snapshot(configured, monkeypatch, kind, flag):
    calls = []
    def execute(self, command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=INFO)
    monkeypatch.setattr(DeviceManager, "execute_command", execute)
    # get_info uses _fetch_info rather than execute_command.
    from meshtastic_tools.core.device import DeviceInfo
    monkeypatch.setattr(DeviceManager, "get_info", lambda self: DeviceInfo.parse_from_info_output(INFO))
    result = runner.invoke(app, ["logger", "collect", kind, "--device", "alpha"])
    assert result.exit_code == 0, result.output
    assert calls == [[flag, "--dest", "!12345678"]]
    files = list((configured[0].parent / "data").rglob("*.txt"))
    assert len(files) == 1
    assert "channel-secret" not in files[0].read_text()
    assert files[0].stat().st_mode & 0o777 == 0o600


def test_invalid_collector_fails_before_device_access(configured, monkeypatch):
    execute = Mock()
    monkeypatch.setattr(DeviceManager, "execute_command", execute)
    assert runner.invoke(app, ["logger", "collect", "unknown", "--all"]).exit_code == 2
    execute.assert_not_called()


def test_invalid_cron_is_rejected_before_collection(configured, monkeypatch):
    path, config = configured
    config["tools"]["logger"]["devices"]["alpha"]["schedule"] = "not-a-cron"
    path.write_text(yaml.safe_dump(config))
    execute = Mock()
    monkeypatch.setattr(DeviceManager, "execute_command", execute)
    assert runner.invoke(app, ["config-validate"]).exit_code == 1
    assert runner.invoke(app, ["logger", "run", "--force"]).exit_code == 1
    execute.assert_not_called()


def test_disabled_device_does_not_require_schedule(configured):
    path, config = configured
    config["tools"]["logger"]["devices"]["alpha"] = {"enabled": False}
    path.write_text(yaml.safe_dump(config))
    result = runner.invoke(app, ["config-validate"])
    assert result.exit_code == 0, result.output
