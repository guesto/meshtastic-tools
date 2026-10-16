import importlib
import json
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
    status_path = configured[0].parent / "data" / "alpha" / ".status.json"
    assert not status_path.exists()
    Clock.current = datetime(2026, 10, 16, 10, 5, 30)
    assert runner.invoke(app, args).exit_code == 0
    first_status = status_path.read_text()
    assert runner.invoke(app, args).exit_code == 0
    assert execute.call_count == 1
    assert status_path.read_text() == first_status
    assert json.loads(first_status)["collectors"]["info"]["source"] == "scheduled"


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


def test_status_before_first_collection_is_read_only_and_offline(configured, monkeypatch):
    execute = Mock(side_effect=AssertionError("status must not contact a device"))
    monkeypatch.setattr(DeviceManager, "execute_command", execute)
    monkeypatch.setattr(DeviceManager, "_fetch_info", execute)
    monkeypatch.setattr("subprocess.run", execute)
    result = runner.invoke(app, ["logger", "status", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert [row["device"] for row in report["devices"]] == ["alpha", "beta"]
    assert all(row["state"] == "unknown" for row in report["devices"])
    assert all(row["next_scheduled_at"] for row in report["devices"])
    assert not (configured[0].parent / "data").exists()
    execute.assert_not_called()
    assert runner.invoke(app, ["logger", "status", "--check"]).exit_code == 1


def test_status_records_manual_collection_and_recovery(configured, monkeypatch):
    execute = Mock(return_value=SimpleNamespace(stdout=INFO))
    monkeypatch.setattr(DeviceManager, "execute_command", execute)
    collect = ["logger", "collect", "info", "--device", "alpha"]
    status = ["logger", "status", "--device", "alpha", "--json", "--check"]
    assert runner.invoke(app, collect).exit_code == 0
    report = json.loads(runner.invoke(app, status).output)["devices"][0]
    assert report["last_success_at"]
    assert report["source"] == "manual"
    assert report["last_duration_seconds"] >= 0

    execute.side_effect = ConnectionError('radio unavailable: {"privateKey":"private-secret"}')
    for _ in range(2):
        assert runner.invoke(app, collect).exit_code == 1
    result = runner.invoke(app, status)
    assert result.exit_code == 1
    report = json.loads(result.output)["devices"][0]
    assert report["state"] == "error"
    assert report["consecutive_failures"] == 2
    assert "private-secret" not in result.output
    assert report["last_success_at"]

    execute.side_effect = None
    assert runner.invoke(app, collect).exit_code == 0
    result = runner.invoke(app, status)
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)["devices"][0]
    assert report["state"] == "ok"
    assert report["consecutive_failures"] == 0
    assert "radio unavailable" in report["last_error"]


def test_status_reports_disabled_devices_without_failed_check(configured):
    path, config = configured
    config["tools"]["logger"]["enabled"] = False
    path.write_text(yaml.safe_dump(config))
    result = runner.invoke(app, ["logger", "status", "--json", "--check"])
    assert result.exit_code == 0, result.output
    assert all(row["state"] == "disabled" for row in json.loads(result.output)["devices"])


def test_status_rejects_unknown_device(configured):
    result = runner.invoke(app, ["logger", "status", "--device", "missing"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_status_corrupt_device_does_not_hide_other_devices(configured):
    path = configured[0].parent / "data" / "alpha" / ".status.json"
    path.parent.mkdir(parents=True)
    path.write_text("corrupt")
    result = runner.invoke(app, ["logger", "status", "--json"])
    assert result.exit_code == 1
    reports = json.loads(result.output)["devices"]
    assert reports[0]["state"] == "unreadable"
    assert reports[1]["state"] == "unknown"
    assert path.read_text() == "corrupt"


def test_human_status_has_timestamps_error_and_request_duration(configured, monkeypatch):
    execute = Mock(side_effect=ConnectionError("radio unavailable"))
    monkeypatch.setattr(DeviceManager, "execute_command", execute)
    assert runner.invoke(app, ["logger", "run", "--force", "--device", "alpha"]).exit_code == 1
    result = runner.invoke(app, ["logger", "status", "--device", "alpha"])
    assert result.exit_code == 0, result.output
    for text in ("alpha: ERROR", "Last successful collection:", "Last attempt:", "Request duration:",
                 "Consecutive failures: 1", "radio unavailable", "Last error time:", "Next scheduled collection:"):
        assert text in result.output


def test_stale_status_warns_and_fails_check(configured, monkeypatch):
    from datetime import timedelta, timezone

    monkeypatch.setattr(DeviceManager, "execute_command", Mock(return_value=SimpleNamespace(stdout=INFO)))
    assert runner.invoke(app, ["logger", "collect", "info", "--device", "alpha"]).exit_code == 0
    path = configured[0].parent / "data" / "alpha" / ".status.json"
    value = json.loads(path.read_text())
    value["collectors"]["info"]["last_success_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    path.write_text(json.dumps(value))
    result = runner.invoke(app, ["logger", "status", "--device", "alpha", "--check"])
    assert result.exit_code == 1
    assert "alpha: STALE" in result.output
    assert "WARNING: collection data is overdue" in result.output


def test_effective_config_show_includes_sources_inherited_values_and_paths(configured):
    path, config = configured
    logger = config["tools"]["logger"]
    logger["defaults"] = {"schedule": "0 * * * *"}
    del logger["devices"]["alpha"]["schedule"]
    logger["storage"]["data_dir"] = "relative-data"
    path.write_text(yaml.safe_dump(config))
    original = path.read_text()
    result = runner.invoke(app, ["--config", str(path), "config-show", "--effective"])
    assert result.exit_code == 0, result.output
    assert f"# Source: {path} (explicit path)" in result.output
    assert "tools.logger.defaults.schedule" in result.output
    assert "90 seconds (automatic" in result.output
    value = yaml.safe_load(result.output)
    assert value["tools"]["logger"]["devices"]["alpha"]["schedule"] == "0 * * * *"
    assert value["tools"]["logger"]["storage"]["data_dir"] == str(path.parent / "relative-data")
    assert value["devices"]["alpha"]["connection"]["timeout"] == 30
    assert path.read_text() == original
    raw = runner.invoke(app, ["config-show"])
    assert yaml.safe_load(raw.output) == config
    section = runner.invoke(app, ["config-show", "--effective", "--section", "tools"])
    assert list(yaml.safe_load(section.output)) == ["tools"]
    assert "(environment)" in section.output


def test_named_timezone_controls_run_status_and_schedule_next(configured, monkeypatch):
    from datetime import timezone
    health = importlib.import_module("meshtastic_tools.logger.health")

    class Clock(datetime):
        @classmethod
        def now(cls):
            return datetime(2026, 10, 16, 7, 0, 0, tzinfo=timezone.utc)

    path, config = configured
    logger = config["tools"]["logger"]
    logger["timezone"] = "Asia/Yekaterinburg"
    logger["defaults"] = {"schedule": "0 12 * * *"}
    logger["health"] = {"grace_seconds": 42}
    del logger["devices"]["alpha"]["schedule"]
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(logger_cli, "datetime", Clock)
    monkeypatch.setattr(health, "utc_now", Clock.now)
    execute = Mock(return_value=SimpleNamespace(stdout=INFO))
    monkeypatch.setattr(DeviceManager, "execute_command", execute)
    args = ["logger", "run", "--device", "alpha"]
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, args).exit_code == 0
    assert execute.call_count == 1
    result = runner.invoke(app, ["logger", "status", "--device", "alpha", "--json", "--check"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)["devices"][0]
    assert report["timezone"] == "Asia/Yekaterinburg"
    assert report["grace_seconds"] == 42
    assert report["next_scheduled_at"] == "2026-10-17T12:00:00+05:00"
    next_run = runner.invoke(app, ["logger", "schedule", "next", "--device", "alpha"])
    assert next_run.exit_code == 0, next_run.output
    assert "Asia/Yekaterinburg" in next_run.output
    assert report["next_scheduled_at"] in next_run.output
