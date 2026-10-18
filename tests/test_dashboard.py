import importlib
import io
import json
import os
import re
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from zoneinfo import ZoneInfo

import pytest
import yaml
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

from meshtastic_tools.cli import app
from meshtastic_tools.core.config import ConfigManager
from meshtastic_tools.core.device import DeviceManager
from meshtastic_tools.core.metrics import MetricHistory
from meshtastic_tools.dashboard.data import collect_dashboard_data, render_rich, render_text, timeline_symbols, to_json
from tests.test_snapshot import info_text


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from CLI output for stable assertions."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


dashboard = importlib.import_module("meshtastic_tools.dashboard.data")
NOW = datetime(2026, 10, 16, 16, 45, 32, tzinfo=ZoneInfo("Asia/Yekaterinburg"))


@pytest.fixture
def configured(tmp_path):
    value = {"devices": {name: {"connection": {"type": "host", "address": "example.invalid"}}
                         for name in ("alpha", "beta")},
             "tools": {"logger": {"timezone": "Asia/Yekaterinburg", "defaults": {"schedule": "*/5 * * * *"},
                                  "storage": {"data_dir": str(tmp_path / "data"), "max_files": 2},
                                  "devices": {"alpha": {"enabled": True}, "beta": {"enabled": True}}}}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value))
    manager = ConfigManager(path)
    manager.load()
    return manager


def record(manager, name="alpha", state="ok"):
    directory = manager.get_logger_config().storage.data_dir / name
    directory.mkdir(parents=True, exist_ok=True)
    success = NOW - timedelta(days=1) if state == "stale" else NOW - timedelta(seconds=30)
    value = {"version": 1, "collectors": {"info": {
        "last_attempt_at": NOW.astimezone(timezone.utc).isoformat(),
        "last_finished_at": NOW.astimezone(timezone.utc).isoformat(),
        "last_outcome": "error" if state == "error" else "success", "source": "scheduled",
        "last_duration_seconds": 1.5, "last_success_at": success.astimezone(timezone.utc).isoformat(),
        "last_file": "info_alpha.txt", "consecutive_failures": 3 if state == "error" else 0,
        "last_failure_at": NOW.isoformat() if state == "error" else None,
        "last_error": 'failed {"privateKey": "secret", "psk": "channel-secret"}' if state == "error" else None,
    }}}
    (directory / ".status.json").write_text(json.dumps(value))
    return directory


def checkpoint(directory, **updates):
    value = {"schedule": "*/5 * * * *", "timezone": "Asia/Yekaterinburg", "last_slot": NOW.timestamp() - 300}
    value.update(updates)
    (directory / ".schedule.json").write_text(json.dumps(value))


def snapshot(manager, stamp=NOW, content=None, kind="info", name="alpha"):
    config = manager.get_logger_config().storage
    directory = config.data_dir / name / "info"
    directory.mkdir(parents=True, exist_ok=True)
    # Filenames use the collector's server timezone, not the scheduler timezone.
    timestamp = stamp.astimezone().strftime("%Y-%m-%d_%H-%M-%S-%f")
    filename = (config.filename_format.format(device=name, timestamp=timestamp) if kind == "info"
                else f"{kind}_{name}_{timestamp}.txt")
    path = directory / filename
    path.write_text(info_text() if content is None else content)
    os.utime(path, (stamp.timestamp(), stamp.timestamp()))
    return path


def collect(manager, **kwargs):
    return collect_dashboard_data(manager, now=NOW, **kwargs)


def test_empty_storage_is_unknown_read_only_and_has_null_missing_fields(configured):
    data = collect(configured)
    assert data.overall_status == "unknown"
    assert len(data.devices) == 2
    row = data.devices[0]
    assert row["mesh"]["snapshot_status"] == "missing"
    assert row["collection"]["consecutive_failures"] is None
    assert row["schedule"]["next_slot_at"] is None
    assert row["storage"]["file_count"] == 0
    assert row["timeline"] == {"events": [], "shown": 0, "total": 0, "next_slot_at": None}
    assert row["power"] == {"battery_level": None, "voltage": None, "battery_min": None,
                            "battery_max": None, "voltage_min": None, "voltage_max": None, "samples": []}
    assert not configured.get_logger_config().storage.data_dir.exists()


def test_power_uses_snapshot_and_history_with_localized_timestamps(configured):
    path = snapshot(configured, content=info_text({"batteryLevel": 60, "voltage": 3.8}))
    history = MetricHistory(path.parent.parent)
    history.append(NOW - timedelta(hours=2), 0, None)
    history.append(NOW - timedelta(hours=1), None, 0)
    history.append(NOW, 80, 4.1)
    before = history.path.read_bytes()
    data = collect(configured)
    power = data.devices[0]["power"]
    assert power == {
        "battery_level": 60, "voltage": 3.8, "battery_min": 0, "battery_max": 80,
        "voltage_min": 0, "voltage_max": 4.1,
        "samples": [{"at": (NOW - timedelta(hours=hours)).isoformat(),
                     "battery_level": battery, "voltage": voltage}
                    for hours, battery, voltage in ((2, 0, None), (1, None, 0), (0, 80, 4.1))],
    }
    assert to_json(data)["devices"][0]["power"] == power
    assert history.path.read_bytes() == before


def test_corrupt_metrics_do_not_affect_current_power_or_health(configured):
    directory = record(configured)
    snapshot(configured, content=info_text({"batteryLevel": 60, "voltage": 3.8}))
    path = directory / ".metrics.json"
    path.write_text("broken")
    row = collect(configured).devices[0]
    assert row["status"] == "ok"
    assert row["power"] == {"battery_level": 60, "voltage": 3.8, "battery_min": None,
                            "battery_max": None, "voltage_min": None, "voltage_max": None, "samples": []}
    assert path.read_text() == "broken"


@pytest.mark.parametrize("state", ["ok", "stale", "error"])
def test_collection_health_and_localized_timestamps(configured, state):
    directory = record(configured, state=state)
    checkpoint(directory)
    row = collect(configured).devices[0]
    assert row["status"] == state
    assert row["collection"]["consecutive_failures"] == (3 if state == "error" else 0)
    for section in (row["collection"], row["schedule"]):
        for key, value in section.items():
            if key.endswith("_at") and value:
                assert value.endswith("+05:00")
    assert "secret" not in json.dumps(row)


@pytest.mark.parametrize("text", ['[]', 'null', 'broken', '{}', '{"collectors": {}}',
                                  '{"version": "1", "collectors": {}}', '{"version": 1}',
                                  '{"version": 2, "collectors": {}}',
                                  '{"version": 1, "collectors": {"info": []}}'])
def test_bad_health_is_unreadable_and_snapshot_still_available(configured, text):
    path = snapshot(configured)
    status = path.parent.parent / ".status.json"
    status.write_text(text)
    data = collect(configured)
    assert data.overall_status == "unreadable"
    assert data.devices[0]["mesh"]["snapshot_status"] == "ok"
    assert data.devices[1]["status"] == "unknown"
    assert status.read_text() == text


@pytest.mark.parametrize("value", [[], {}, {"last_slot": 1},
                                   {"schedule": "bad", "timezone": "local", "last_slot": 1},
                                   {"schedule": "* * * * *", "timezone": "unknown/zone", "last_slot": 1},
                                   {"schedule": "* * * * *", "timezone": "local", "last_slot": True},
                                   {"schedule": "* * * * *", "timezone": "local", "last_slot": "123"},
                                   {"schedule": "* * * * *", "timezone": "local", "last_slot": float("inf")}])
def test_bad_schedule_is_unreadable_but_valid_health_fields_survive(configured, value):
    directory = record(configured)
    (directory / ".schedule.json").write_text(json.dumps(value))
    row = collect(configured).devices[0]
    assert row["status"] == "unreadable"
    assert row["schedule"]["next_slot_at"] is None
    assert row["collection"]["last_duration_seconds"] == 1.5


def test_schedule_missing_changed_and_invalid_json(configured):
    directory = record(configured)
    assert collect(configured).devices[0]["status"] == "ok"
    assert collect(configured).devices[0]["schedule"]["next_slot_at"] is None
    checkpoint(directory, schedule="0 0 * * *", timezone="UTC")
    row = collect(configured).devices[0]
    assert row["status"] == "ok"
    assert row["schedule"]["next_slot_at"] == "2026-10-16T16:50:00+05:00"
    (directory / ".schedule.json").write_text("invalid JSON")
    assert collect(configured).devices[0]["status"] == "unreadable"


def test_snapshot_failure_does_not_change_health_or_fall_back(configured):
    record(configured)
    snapshot(configured, NOW - timedelta(minutes=5))
    snapshot(configured, content="truncated")
    row = collect(configured).devices[0]
    assert row["status"] == "ok"
    assert row["mesh"]["snapshot_status"] == "unreadable"
    assert row["mesh"]["battery_level"] is None
    assert "Snapshot unreadable" in render_text(collect(configured, device="alpha"))


def test_rotation_between_scan_and_open_does_not_fall_back(configured, monkeypatch):
    snapshot(configured, NOW - timedelta(minutes=5))
    latest = snapshot(configured)
    def removed(path):
        assert path == latest
        path.unlink()
        raise FileNotFoundError
    monkeypatch.setattr(dashboard, "read_snapshot", removed)
    assert collect(configured).devices[0]["mesh"]["snapshot_status"] == "missing"


def test_info_selected_by_custom_template_storage_counts_all_collectors(configured, monkeypatch):
    storage = configured.get_logger_config().storage
    storage.filename_format = "custom.{device}.{timestamp}.dump"
    wanted = snapshot(configured, NOW - timedelta(minutes=1))
    snapshot(configured, kind="telemetry", content='{"myNodeNum": 99}')
    snapshot(configured, kind="nodes", content="not info")
    (wanted.parent / ".temporary.txt").write_text("hidden")
    (wanted.parent / "symlink.txt").symlink_to(wanted)
    read = Mock(wraps=dashboard.read_snapshot)
    monkeypatch.setattr(dashboard, "read_snapshot", read)
    row = collect(configured).devices[0]
    assert row["storage"]["file_count"] == 3
    assert row["storage"]["total_size_bytes"] == sum(p.stat().st_size for p in wanted.parent.iterdir()
                                                     if not p.name.startswith(".") and not p.is_symlink())
    assert row["storage"]["rotation_active"] is True
    assert row["mesh"]["my_node_num"] == 1
    read.assert_called_once_with(wanted)
    assert row["timeline"]["events"] == []  # Files do not reconstruct history.








@pytest.mark.parametrize("free,state", [(0,"critical"),(9.999,"critical"),(10,"warning"),
                                      (20,"warning"),(20.001,"ok"),(100,"ok")])
def test_disk_thresholds_are_independent_of_collection_and_retention(configured, monkeypatch, free, state):
    record(configured)
    monkeypatch.setattr(dashboard.shutil, "disk_usage", lambda p: SimpleNamespace(total=100000, free=free*1000))
    row = collect(configured).devices[0]
    assert row["storage"]["disk"]["status"] == state
    assert row["status"] == "ok"


def test_disk_errors_and_scan_permission_error_are_visible_without_changing_health(configured, monkeypatch):
    record(configured)
    monkeypatch.setattr(dashboard.shutil, "disk_usage", Mock(side_effect=PermissionError))
    monkeypatch.setattr(dashboard.os, "scandir", Mock(side_effect=PermissionError))
    row = collect(configured).devices[0]
    assert row["status"] == "ok"
    assert row["storage"]["disk"] is None
    assert row["storage"]["file_count"] is None
    assert row["storage"]["error"]


def test_disabled_selection_and_status_precedence(configured):
    config = configured.get_logger_config()
    config.devices["beta"]["enabled"] = False
    record(configured, state="error")
    assert [r["name"] for r in collect(configured).devices] == ["alpha"]
    assert collect(configured).overall_status == "error"
    selected = collect(configured, device="beta")
    assert selected.overall_status == "error"  # Summary includes devices outside the detail view.
    assert selected.devices[0]["status"] == "disabled"
    config.devices["beta"]["enabled"] = True
    path = config.storage.data_dir / "beta"
    path.mkdir()
    (path / ".status.json").write_text("broken")
    assert collect(configured).overall_status == "unreadable"
    config.enabled = False
    data = collect(configured)
    assert data.overall_status == "disabled"
    assert "No enabled devices" in render_text(data)


def test_read_only_offline_with_existing_artifacts(configured, monkeypatch):
    directory = record(configured)
    checkpoint(directory)
    snapshot(configured)
    from meshtastic_tools.core.timeline import TimelineHistory
    TimelineHistory(directory / "info", 2).append("snapshot", NOW)
    paths = [p for p in directory.rglob("*") if p.is_file()]
    before = {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in paths}
    forbidden = Mock(side_effect=AssertionError("dashboard must be read only and offline"))
    monkeypatch.setattr(DeviceManager, "execute_command", forbidden)
    monkeypatch.setattr(DeviceManager, "_fetch_info", forbidden)
    monkeypatch.setattr("meshtastic_tools.logger.storage.StorageManager.__init__", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(Path, "chmod", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    result = CliRunner().invoke(app, ["--config", str(configured.loaded_path), "dashboard", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["schema_version"] == 1
    forbidden.assert_not_called()
    assert {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in paths} == before


@pytest.mark.parametrize("width", [1, 20, 79, 80, 110])
def test_text_and_rich_rendering_with_fake_data_preserve_json(configured, width):
    record(configured)
    snapshot(configured)
    data = collect(configured)
    original = deepcopy(to_json(data))
    plain = render_text(data, width=width)
    assert "╔" not in plain and "\x1b" not in plain
    if width >= 20:
        assert "MESH" in plain
    output = io.StringIO()
    console = Console(file=output, width=max(80,width), height=25, force_terminal=True, no_color=True)
    console.print(render_rich(data, max(80,width)))
    rich = output.getvalue()
    assert "MESHTASTIC LOGGER" in rich and "MESH" in rich
    assert "\x1b" not in rich
    assert max(len(line) for line in rich.splitlines()) == max(80, width)
    assert to_json(data) == original


@pytest.mark.parametrize("args,code", [(["--window","bad"],2),(["--width","0"],2),
                                      (["--device","missing"],1),(["--no-color","--width","40"],0)])
def test_cli_arguments(configured, args, code):
    result = CliRunner().invoke(app, ["--config", str(configured.loaded_path), "dashboard", *args])
    assert result.exit_code == code, result.output
    if "--width" in args and code == 0:
        assert max(len(line) for line in result.output.splitlines()) <= int(args[args.index("--width") + 1])


def test_cli_json_is_width_independent_and_local_timezone_has_offset(configured):
    configured.get_logger_config().timezone = "local"
    data = collect(configured)
    assert data.generated_at.utcoffset() is not None
    assert data.timezone == "local"
    runner = CliRunner()
    for width in (20, 110):
        result = runner.invoke(app, ["--config", str(configured.loaded_path), "dashboard", "--json", "--width", str(width)])
        assert result.exit_code == 0, result.output
        value = json.loads(result.output)
        assert value["schema_version"] == 1
        assert value["devices"][0]["timeline"]["events"] == []


@pytest.mark.parametrize("no_color", [False, True])
def test_cli_uses_rich_in_a_terminal_and_honors_no_color(configured, monkeypatch, no_color):
    monkeypatch.setenv("TERM", "xterm")
    def terminal_console(**kwargs):
        return Console(**kwargs, height=25, force_terminal=True)
    monkeypatch.setattr("rich.console.Console", terminal_console)
    args = ["--config", str(configured.loaded_path), "dashboard", "--width", "110"]
    if no_color:
        args.append("--no-color")
    result = CliRunner().invoke(app, args, color=True)
    assert result.exit_code == 0, result.output
    assert "╔" in result.output
    assert ("\x1b" in result.output) is not no_color
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert max(len(line) for line in plain.splitlines()) == 110


def test_watch_with_json_is_rejected():
    result = CliRunner().invoke(app, ["dashboard", "--watch", "--json"])
    assert result.exit_code == 2, result.output
    assert "--watch cannot be combined with --json" in _strip_ansi(result.output)


def test_watch_requires_terminal():
    result = CliRunner().invoke(app, ["dashboard", "--watch"])
    assert result.exit_code == 2, result.output
    assert "--watch requires a terminal" in _strip_ansi(result.output)


@pytest.fixture
def watch_terminal(monkeypatch):
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr("rich.console.Console", lambda **kwargs: Console(
        **kwargs, height=25, force_terminal=True,
    ))


@pytest.mark.parametrize("args,interval", [(["--watch"], 5.0), (["-w", "--interval", "0.1"], 0.1)])
def test_watch_renders_at_least_once_and_handles_ctrl_c(configured, monkeypatch, watch_terminal, args, interval):
    sleep = Mock(side_effect=[None, KeyboardInterrupt])
    monkeypatch.setattr(time, "sleep", sleep)
    collect_spy = Mock(wraps=dashboard.collect_dashboard_data)
    render_spy = Mock(wraps=dashboard.render_rich)
    monkeypatch.setattr(dashboard, "collect_dashboard_data", collect_spy)
    monkeypatch.setattr(dashboard, "render_rich", render_spy)

    result = CliRunner().invoke(app, ["--config", str(configured.loaded_path), "dashboard",
                                      "--width", "120", "--no-color", *args], color=True)

    assert result.exit_code == 0, result.output
    assert collect_spy.call_count == 2  # Initial snapshot and one refresh.
    assert render_spy.call_count == 2
    assert "MESHTASTIC LOGGER" in result.output
    assert "\x1b[?1049h" in result.output  # Alternate screen entered and restored.
    assert "\x1b[?1049l" in result.output
    assert "\x1b[?25h" in result.output  # Cursor restored.
    assert not re.search(r"\x1b\[[0-9;]*m", result.output)  # --no-color applies to Live.
    assert [call.args for call in sleep.call_args_list] == [(interval,), (interval,)]


@pytest.mark.parametrize("error_source", ["collect", "render"])
def test_watch_displays_update_errors_and_recovers(configured, monkeypatch, watch_terminal, error_source):
    initial = collect(configured, device="alpha")
    recovered = collect(configured, device="alpha")
    collect_spy = Mock(side_effect=[initial, RuntimeError("temporary failure"), recovered]
                       if error_source == "collect" else [initial, initial, recovered])
    render_spy = Mock(side_effect=[Text("initial"), RuntimeError("temporary failure"), Text("recovered")]
                      if error_source == "render" else [Text("initial"), Text("recovered")])
    monkeypatch.setattr(dashboard, "collect_dashboard_data", collect_spy)
    monkeypatch.setattr(dashboard, "render_rich", render_spy)
    monkeypatch.setattr(time, "sleep", Mock(side_effect=[None, None, KeyboardInterrupt]))
    live_factory = MagicMock()
    monkeypatch.setattr("rich.live.Live", live_factory)

    result = CliRunner().invoke(app, ["--config", str(configured.loaded_path), "dashboard", "--watch",
                                      "--device", "alpha", "--width", "100", "--page", "1", "--layout", "single"])

    assert result.exit_code == 0, result.output
    assert collect_spy.call_count == 3
    assert all(call.kwargs == {"device": "alpha"} for call in collect_spy.call_args_list)
    assert render_spy.call_args.args == (recovered, 100, 1, False, "single")
    assert render_spy.call_args.kwargs == {"height": 25}
    live_kwargs = live_factory.call_args.kwargs
    assert live_kwargs["screen"] is True
    assert live_kwargs["refresh_per_second"] == 4
    assert live_kwargs["console"].width == 100
    updates = live_factory.return_value.__enter__.return_value.update.call_args_list
    assert len(updates) == 2
    assert updates[0].args[0].plain == "Error: temporary failure"
    assert updates[0].args[0].style == "red"
    assert updates[1].args[0].plain == "recovered"
    live_factory.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("interval", ["0", "-1", "0.09"])
def test_watch_interval_must_be_at_least_point_one(interval):
    result = CliRunner().invoke(app, ["dashboard", "--watch", "--interval", interval])
    assert result.exit_code == 2, result.output
    assert "--interval" in _strip_ansi(result.output)


@pytest.mark.parametrize("terminal", [False, True])
def test_static_mode_still_works(configured, monkeypatch, terminal):
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr("rich.console.Console", lambda **kwargs: Console(
        **kwargs, height=25, force_terminal=terminal,
    ))
    collect_spy = Mock(wraps=dashboard.collect_dashboard_data)
    render_spy = Mock(wraps=dashboard.render_rich)
    text_spy = Mock(wraps=dashboard.render_text)
    live = Mock(side_effect=AssertionError("static mode must not use Live"))
    sleep = Mock(side_effect=AssertionError("static mode must not sleep"))
    monkeypatch.setattr(dashboard, "collect_dashboard_data", collect_spy)
    monkeypatch.setattr(dashboard, "render_rich", render_spy)
    monkeypatch.setattr(dashboard, "render_text", text_spy)
    monkeypatch.setattr("rich.live.Live", live)
    monkeypatch.setattr(time, "sleep", sleep)
    args = ["--config", str(configured.loaded_path), "dashboard", "--width", "120"]

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    assert "--watch" not in args and "-w" not in args
    collect_spy.assert_called_once()
    assert render_spy.call_count == int(terminal)
    assert text_spy.call_count == int(not terminal)
    assert result.output.count("MESHTASTIC LOGGER") == 1
    live.assert_not_called()
    sleep.assert_not_called()


@pytest.mark.parametrize("timezone_name", ["local", "America/New_York"])
def test_next_slot_and_timeline_across_daylight_saving_change(configured, monkeypatch, timezone_name):
    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        config = configured.get_logger_config()
        config.timezone = timezone_name
        config.devices["alpha"]["schedule"] = "0 12 * * *"
        directory = record(configured)
        checkpoint(directory)
        # At noon on the day before the spring transition, next noon is 23h away.
        now = datetime(2026, 3, 7, 13, tzinfo=ZoneInfo("America/New_York"))
        data = collect_dashboard_data(configured, now=now)
        row = data.devices[0]
        assert row["schedule"]["next_slot_at"] == "2026-03-08T12:00:00-04:00"
        assert data.generated_at.isoformat().endswith("-05:00")
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()


def rich_output(data, width=120, **kwargs):
    output = io.StringIO()
    Console(file=output, width=width, height=100, no_color=True).print(render_rich(data, width, **kwargs))
    return output.getvalue()


def many_devices(manager):
    value = yaml.safe_load(manager.loaded_path.read_text())
    for name in ("gamma", "delta", "epsilon"):
        value["devices"][name] = {"connection": {"type": "host", "address": "example.invalid"}}
        value["tools"]["logger"]["devices"][name] = {"enabled": True}
    manager.loaded_path.write_text(yaml.safe_dump(value))
    manager.load()
    record(manager, name="alpha", state="ok")
    record(manager, name="beta", state="stale")
    record(manager, name="gamma", state="error")
    directory = record(manager, name="delta")
    (directory / ".status.json").write_text("broken")


def test_summary_pagination_sorting_and_hidden_devices_contribute_to_status(configured):
    from meshtastic_tools.dashboard.render import device_page
    many_devices(configured)
    data = collect(configured, device="alpha")
    assert data.overall_status == "unreadable"
    _, visible, pages, detail = device_page(data, 1)
    assert [row["name"] for row in visible] == ["delta", "gamma", "beta"]
    assert pages == 2 and detail["name"] == "alpha"
    result = rich_output(data, page=2)
    top = "\n".join(result.splitlines()[1:5])
    assert "DEVICES (5) 2/2" in top
    assert "UNREADABLE" in top and "delta" not in top
    assert "▸ alpha" in top and "epsilon" in top
    assert "NODE · alpha" in result
    value = to_json(data)
    assert len(value["device_summary"]) == 5
    assert [row["name"] for row in value["devices"]] == ["alpha"]


def test_default_detail_is_first_enabled_device_on_requested_page(configured):
    from meshtastic_tools.dashboard.render import device_page
    many_devices(configured)
    data = collect(configured)
    assert device_page(data, 1)[3]["name"] == "delta"
    assert device_page(data, 2)[3]["name"] == "epsilon"


def test_top_bar_is_exactly_four_rows_and_summary_mode_omits_detail(configured):
    many_devices(configured)
    data = collect(configured)
    result = rich_output(data, summary_only=True)
    lines = result.splitlines()
    assert len(lines) == 6  # Four content rows plus the outer frame.
    assert "DEVICES (5) 1/2" in lines[1]
    assert "COLLECTION" not in result and "NODE ·" not in result
    assert all(len(line) == 120 for line in lines)
    assert "COLLECTION" not in render_text(data, summary_only=True)


@pytest.mark.parametrize("width,layout,split", [(80,"auto",False),(99,"auto",False),
    (100,"auto",False),(119,"auto",False),(120,"auto",True),(140,"auto",True),
    (160,"single",False),(100,"split",True)])
def test_adaptive_layout_has_one_frame_and_a_fixed_center_divider(configured, width, layout, split):
    data = collect(configured, device="alpha")
    result = rich_output(data, width=width, layout=layout)
    lines = result.splitlines()
    assert result.count("╔") == result.count("╚") == 1
    assert result.count("╠") == 1
    assert all(len(line) == width for line in lines)
    if split:
        divider = 2 + (width - 5) // 2
        assert all(line[divider] == "│" for line in lines[6:-1])
        assert "TIMELINE" in lines[6] and "NODE · alpha" in lines[6]
    else:
        assert all("│" not in line for line in lines[6:-1])


@pytest.mark.parametrize("value,channel,state", [(None,True,"unknown"),(0,True,"low"),
    (24.999,True,"low"),(25,True,"medium"),(50,True,"medium"),(50.001,True,"high"),
    (1.999,False,"low"),(2,False,"medium"),(5,False,"medium"),(5.001,False,"high")])
def test_radio_thresholds_do_not_change_health(value, channel, state):
    from meshtastic_tools.dashboard.render import radio_severity
    assert radio_severity(value, channel) == state


def test_ui_rounds_without_changing_json_and_shows_extended_node_data(configured):
    content = {'myInfo': {'myNodeNum': 1, 'firmwareEdition': 'VANILLA'},
               'metadata': {'firmwareVersion': 'test', 'hwModel': 'HELTEC_V4', 'role': 'CLIENT',
                            'hasWifi': True, 'hasBluetooth': True, 'hasEthernet': False},
               'nodes': {'!00000001': {'num': 1, 'lastHeard': NOW.timestamp() - 20,
                    'position': {'latitude': -33.8535465, 'longitude': 60.6305125, 'altitude': 0},
                    'deviceMetrics': {'channelUtilization': 12.235, 'airUtilTx': 0.59855556,
                                      'batteryLevel': 96, 'voltage': 4.143, 'uptimeSeconds': 0}}}}
    directory = record(configured)
    checkpoint(directory)
    snapshot(configured, content=json.dumps(content))
    data = collect(configured, device="alpha")
    value = deepcopy(to_json(data))
    result = rich_output(data)
    for text in ("12.24 %", "0.60 %", "96 % · 4.14 V", "HELTEC_V4", "CLIENT",
                 "test · VANILLA", "[wifi] [bluetooth]", "33.8535°S", "60.6305°E", "0 m", "20 s ago"):
        assert text in result
    assert "2026-10-16" not in result.splitlines()[1]  # Top bar only shows HH:MM:SS.
    assert "2026-10-16T" not in result
    assert "HEALTH (24h)" not in result
    assert to_json(data) == value
    assert value["devices"][0]["mesh"]["channel_utilization"] == 12.235
    assert value["devices"][0]["mesh"]["last_heard_at"].endswith("+05:00")
    assert value["config"]["source"] == str(configured.loaded_path)


def test_disk_used_does_not_count_reserved_space_as_used(configured, monkeypatch):
    monkeypatch.setattr(dashboard.shutil, "disk_usage", lambda p: SimpleNamespace(total=100, used=40, free=40))
    data = collect(configured, device="alpha")
    disk = data.devices[0]["storage"]["disk"]
    assert disk["used_bytes"] == 40 and disk["used_percent"] == 40
    assert disk["free_percent"] == 40 and disk["status"] == "ok"
    assert "40.0 B used of 100.0 B" in rich_output(data)


def test_progress_bars_are_neutral_clamped_and_optional_without_limit(configured):
    for seconds in (0, 1, 2):
        snapshot(configured, NOW - timedelta(seconds=seconds))
    data = collect(configured, device="alpha")
    row = data.devices[0]
    assert row["storage"]["rotation_active"] is True
    result = render_text(data)
    assert "3 / 2 · rotation" in result and "150 %" in result
    configured.get_logger_config().storage.max_files = None
    result = render_text(collect(configured, device="alpha"))
    assert "3 (no limit)" in result
    lines = result.splitlines()
    file_line = next(i for i, line in enumerate(lines) if "FILES" in line)
    assert "SIZE" in lines[file_line + 1]  # No file-limit bar.


def test_short_terminal_drops_only_optional_fields_in_priority_order(configured):
    data = collect(configured, device="alpha")
    full = rich_output(data)
    full_height = len(full.splitlines())
    first = rich_output(data, height=full_height - 1)
    assert "HEALTH (24h)" not in first
    assert "LAST SLOT" not in first
    if "LAST HEARD" not in first:
        assert "POSITION" not in first
    short = rich_output(data, height=1)
    for field in ("HEALTH (24h)", "LAST SLOT", "POSITION", "LAST HEARD"):
        assert field not in short
    for field in ("LAST SUCCESS", "NEXT SLOT", "STORAGE", "BATTERY", "CHANNEL", "REBOOTS"):
        assert field in short


def test_source_path_is_json_only_and_untrusted_strings_are_literal(configured):
    data = collect(configured, device="alpha")
    data.config_source = "/very/" + "long/" * 40 + "config.yaml"
    data.devices[0]["connection"]["address"] = "[red]literal[/red]\x1b[31m"
    result = rich_output(data)
    assert "SOURCE" not in result and "config.yaml" not in result
    assert "SOURCE" not in render_text(data)
    assert to_json(data)["config"]["source"] == data.config_source
    assert "[red]literal[/red]" in result
    assert "\x1b" not in result


@pytest.mark.parametrize("args,code", [(["--page","0"],2),(["--page","2"],2),
    (["--layout","bad"],2),(["--device","alpha","--all"],2),
    (["--all"],0),(["--layout","split","--width","80"],0)])
def test_new_cli_flags(configured, args, code):
    result = CliRunner().invoke(app, ["--config", str(configured.loaded_path), "dashboard", *args])
    assert result.exit_code == code, result.output
    if args == ["--all"]:
        assert "COLLECTION" not in result.output
    if code == 0:
        assert "DEVICES (2)" in result.output


@pytest.mark.parametrize("width,bar_width", [(80,16),(100,20),(120,20),(140,34)])
def test_storage_bar_width_follows_the_outer_terminal_width(configured, width, bar_width):
    data = collect(configured, device="alpha")
    storage = data.devices[0]["storage"]
    storage["file_count"] = storage["max_files"]
    text = rich_output(data, width=width)
    assert "[" + "█" * bar_width + "]" in text


def power_samples():
    start = NOW - timedelta(hours=24)
    return [{"at": (start + timedelta(minutes=30 * i + 15)).isoformat(),
             "battery_level": (i % 8) * 12.5, "voltage": None} for i in range(48)]


@pytest.mark.parametrize("renderer", ["rich", "text"])
def test_power_block_shows_battery_bar_sparkline_and_minmax(configured, renderer):
    data = collect(configured, device="alpha")
    data.devices[0]["power"] = {
        "battery_level": 27, "voltage": 3.5, "battery_min": 12, "battery_max": 100,
        "voltage_min": 3.21, "voltage_max": 4.18, "samples": power_samples(),
    }
    original = deepcopy(to_json(data))
    output = rich_output(data, width=160) if renderer == "rich" else render_text(data, width=160)
    assert output.index("DEVICE") < output.index("POWER") < output.index("RADIO\n" if renderer == "text" else "RADIO ")
    assert "27 % · 3.50 V" in output
    assert "min 12% · max 100%" in output
    assert "3.50 V  (min 3.21 V · max 4.18 V)" in output
    progress = re.search(r"\[([█░]{50})\]", output)
    sparkline = re.search(r"\[ ([▁▂▃▄▅▆▇█]{48}) \]", output)
    assert progress and sparkline
    assert len(progress.group(0)) == len(sparkline.group(0)) == 52
    assert sparkline.group(1) == "▁▂▃▄▅▆▇█" * 6
    assert "[" + "·" * 50 + "]" in output
    assert to_json(data) == original
    assert "sparkline" not in to_json(data)["devices"][0]["power"]


def test_sparkline_averages_per_30min():
    from meshtastic_tools.dashboard.render import _sparkline
    samples = power_samples()
    # Three values in the oldest interval average to 50%, the fifth level.
    start = NOW - timedelta(hours=24)
    samples.extend({"at": (start + timedelta(minutes=minute)).isoformat(),
                    "battery_level": value} for minute, value in ((1, 50), (29, 100)))
    line = _sparkline(list(reversed(samples)), now=NOW)
    assert line == "▅" + ("▁▂▃▄▅▆▇█" * 6)[1:]
    assert len(line) == 48


def test_sparkline_skips_gaps_none_and_samples_outside_24h():
    from meshtastic_tools.dashboard.render import _sparkline
    samples = [
        {"at": (NOW - timedelta(hours=25)).isoformat(), "battery_level": 100},
        {"at": (NOW - timedelta(hours=24)).isoformat(), "battery_level": 0},
        {"at": (NOW - timedelta(hours=23)).isoformat(), "battery_level": None},
        {"at": NOW.astimezone(timezone.utc).isoformat(), "battery_level": 100},
        {"at": (NOW + timedelta(seconds=1)).isoformat(), "battery_level": 50},
    ]
    assert _sparkline(samples, now=NOW) == "▁█" + " " * 46
    assert _sparkline(samples, 1, now=NOW) == "█"
    assert _sparkline(samples, 0, now=NOW) == ""


def test_sparkline_separates_half_hour_boundary_and_handles_timezones():
    from meshtastic_tools.dashboard.render import _sparkline
    start = NOW - timedelta(hours=24)
    boundary = start + timedelta(minutes=30)
    samples = [
        {"at": (boundary - timedelta(seconds=1)).isoformat(), "battery_level": 0},
        {"at": boundary.astimezone(timezone.utc).isoformat(), "battery_level": 100},
    ]
    assert _sparkline(samples, now=NOW) == "▁█" + " " * 46


def test_sparkline_empty_when_no_samples():
    from meshtastic_tools.dashboard.render import _sparkline
    assert _sparkline([]) == " " * 48
    assert _sparkline([], 12) == " " * 12


@pytest.mark.parametrize("renderer", ["rich", "text"])
def test_power_minmax_none_shows_dash(configured, renderer):
    data = collect(configured, device="alpha")
    output = rich_output(data, width=160) if renderer == "rich" else render_text(data, width=160)
    assert "min — · max —" in output
    assert "—  (min — · max —)" in output
    assert "[ " + " " * 48 + " ]" in output
    assert not re.search(r"\[[█░]{50}\]", output)


@pytest.mark.parametrize("renderer", ["rich", "text"])
def test_battery_removed_from_device(configured, renderer):
    data = collect(configured, device="alpha")
    output = rich_output(data, width=160) if renderer == "rich" else render_text(data, width=160)
    device = output.split("NODE · alpha", 1)[1].split("POWER", 1)[0]
    assert "DEVICE" in device
    assert "BATTERY" not in device
    assert output.count("BATTERY") == 1


@pytest.mark.parametrize("seconds,display", [(2.01,"2.01 s"),(75.2,"1 m 15 s"),(3601,"1 h 0 m")])
def test_request_duration_uses_human_readable_units(configured, seconds, display):
    data = collect(configured, device="alpha")
    data.devices[0]["collection"]["last_duration_seconds"] = seconds
    assert display in rich_output(data)




@pytest.mark.parametrize("channel,air", [(12.235,0.5985),(100,2),(0,12.24)])
def test_radio_decimal_points_percent_and_severity_are_aligned(configured, channel, air):
    data = collect(configured, device="alpha")
    data.devices[0]["mesh"].update(channel_utilization=channel,air_util_tx=air)
    for text in (rich_output(data),render_text(data)):
        rows = [next(line for line in text.splitlines() if label in line).split("│")[-1]
                for label in ("CHANNEL","AIR TX")]
        assert rows[0].index(".") == rows[1].index(".")
        assert rows[0].index("%") == rows[1].index("%")
        assert rows[0].index("●") == rows[1].index("●")


@pytest.mark.parametrize("active", [False,True])
def test_error_display_removes_wrappers_and_identifies_recovered_failures(configured, active):
    record(configured, state="error" if active else "ok")
    data = collect(configured, device="alpha")
    collection = data.devices[0]["collection"]
    raw = 'Failed to collect info from alpha: Command error for alpha: Command failed for alpha: '
    collection["last_error"] = raw
    text = rich_output(data)
    assert "Command failed; details not recorded" not in text
    assert "Failed to collect info" not in text and "Command error for" not in text
    for output in (text, render_text(data)):
        assert "PREV ERROR" not in output and "LAST ERROR" not in output and "ERROR AT" not in output
    assert to_json(data)["devices"][0]["collection"]["last_error"] == raw








@pytest.mark.parametrize("width", [80,120,160])
def test_timeline_brackets_fill_column_and_legend_is_separated(configured, width):
    data = collect(configured, device="alpha")
    original = deepcopy(to_json(data))
    lines = rich_output(data, width=width).splitlines()
    index = next(i for i, line in enumerate(lines) if re.search(r'\[[█░?·]+\]', line))
    bar = re.search(r'\[([█░?·]+)\]', lines[index])
    from meshtastic_tools.dashboard.data import timeline_capacity
    assert len(bar.group(1)) == timeline_capacity(width)
    assert "█ snapshot" in lines[index + 1]
    assert "! error" in lines[index + 1] and "· empty" in lines[index + 1]
    assert "/ cell" not in "\n".join(lines[:index])
    assert to_json(data) == original


def event_history(manager, kinds, name="alpha"):
    from meshtastic_tools.core.timeline import TimelineHistory, TimelineEvent
    storage = manager.get_logger_config().storage
    storage.max_files = None
    history = TimelineHistory(storage.data_dir / name / "info")
    events = [TimelineEvent(kind, NOW - timedelta(minutes=len(kinds)-i)) for i, kind in enumerate(kinds)]
    history.extend(events)
    return history


@pytest.mark.parametrize("width", [80,120,160])
def test_timeline_events_fill_available_cells_and_json_reports_same_truncation(configured, width):
    from meshtastic_tools.dashboard.data import timeline_capacity
    directory = record(configured)
    checkpoint(directory)
    kinds = (["snapshot", "missing", "error"] * 70)[:200]
    history = event_history(configured, kinds)
    data = collect(configured, device="alpha")
    capacity = timeline_capacity(width)
    expected = "".join({"snapshot":"█", "missing":"░", "error":"!"}[k] for k in kinds[-capacity:])
    lines = rich_output(data, width).splitlines()
    index = next(i for i, line in enumerate(lines) if re.search(r'\[[█░!·]+\]', line))
    assert expected in lines[index]
    assert f"last {capacity} of 200 snapshots" in "\n".join(lines[:index])
    assert "next 16:50" in "\n".join(lines[:index])
    assert "█ snapshot" in lines[index + 1] and "! error" in lines[index + 1]
    assert "future" not in "\n".join(lines)
    value = to_json(data, width=width)["devices"][0]["timeline"]
    assert value["shown"] == capacity and value["total"] == 200
    assert [e["type"] for e in value["events"]] == kinds[-capacity:]
    assert all(e["at"].endswith("+05:00") for e in value["events"])
    assert value["next_slot_at"] == "2026-10-16T16:50:00+05:00"
    before = history.path.read_bytes()
    render_text(data, width)
    assert history.path.read_bytes() == before


def test_sparse_timeline_is_left_aligned_without_file_or_time_inference(configured):
    event_history(configured, ["snapshot", "missing", "error"])
    snapshot(configured, NOW - timedelta(days=30))
    timeline = collect(configured).devices[0]["timeline"]
    assert timeline_symbols(timeline, 8) == "█░!·····"
    assert timeline_symbols(timeline, 2) == "░!"
    assert timeline["total"] == 3


@pytest.mark.parametrize("content", [None, "broken", "[]", '{"version":1,"events":[{}]}'])
def test_bad_history_is_silently_empty_without_affecting_health(configured, content):
    directory = record(configured)
    path = snapshot(configured).parent / ".timeline.json"
    if content is not None:
        path.write_text(content)
    row = collect(configured).devices[0]
    assert row["status"] == "ok" and row["errors"] == []
    assert row["timeline"]["events"] == []
    assert path.read_text() == content if content is not None else not path.exists()


def test_single_configured_device_keeps_summary_and_four_line_header(configured):
    configured.devices.pop("beta")
    data = collect(configured)
    text = rich_output(data)
    lines = text.splitlines()
    assert "DEVICES (1)" in lines[1] and "▸ alpha" in lines[2]
    assert "DEVICES (1)" in render_text(data) and "▸ alpha" in render_text(data)
    assert all("│" in line for line in lines[1:5])
    assert lines[5].startswith("╠")
    assert "16:45:32" in lines[1] and "TZ Asia/Yekaterinburg" in lines[2]
    assert "COLLECTION" in text and "NODE · alpha" in text
    summary = rich_output(data, summary_only=True)
    assert "DEVICES (1)" in summary and "alpha" in summary
    assert len(summary.splitlines()) == 6
