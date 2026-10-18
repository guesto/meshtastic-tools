"""History retention and real collection/scheduling integration, without device I/O."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from meshtastic_tools.core.config import StorageConfig, LoggerToolConfig
from meshtastic_tools.core.exceptions import CollectionError
from meshtastic_tools.logger.collector import MeshtasticCollector
from meshtastic_tools.logger.scheduler import DeviceSchedule
from meshtastic_tools.logger.storage import StorageManager
from meshtastic_tools.core.timeline import DEFAULT_LIMIT, TimelineEvent, TimelineHistory


NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("content", [None, "broken", "[]", "{}", '{"version":true,"events":[]}',
    '{"version":1,"events":[{"type":"error","at":"2026-10-17T12:00:00"}]}',
    '{"version":1,"events":[{"type":"unknown","at":"2026-10-17T12:00:00+00:00"}]}',
    '{"version":1,"events":[{"type":[],"at":"2026-10-17T12:00:00+00:00"}]}'])
def test_missing_or_corrupt_history_reads_empty_and_restarts(tmp_path, content):
    history = TimelineHistory(tmp_path)
    if content is not None:
        history.path.write_text(content)
    assert history.load() == []
    if content is not None:
        assert history.path.read_text() == content  # Reading never repairs the file.
    else:
        assert not history.path.exists()
    history.append("snapshot", NOW)
    assert history.load() == [TimelineEvent("snapshot", NOW)]
    assert history.path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_mixed_timezones_load_in_utc_time_order_and_migrate_on_save(tmp_path):
    history = TimelineHistory(tmp_path)
    history.path.write_text(json.dumps({"version": 1, "events": [
        {"type": "snapshot", "at": "2026-10-17T10:10:00+00:00"},
        {"type": "missing", "at": "2026-10-17T10:15:00+05:00"},
        {"type": "error", "at": "2026-10-17T05:05:00+00:00"},
        {"type": "snapshot", "at": "2026-10-17T10:10:00+05:00"},
    ]}))
    before = history.path.read_bytes()
    events = history.load()
    assert all(event.at.utcoffset() == timedelta(0) for event in events)
    assert [event.at for event in events] == [
        datetime(2026, 10, 17, hour, minute, tzinfo=timezone.utc)
        for hour, minute in ((5, 5), (5, 10), (5, 15), (10, 10))
    ]
    assert history.read_last(2) == events[-2:]
    assert TimelineHistory(tmp_path, 2).load() == events[-2:]
    assert history.path.read_bytes() == before

    history.save(events)
    saved = json.loads(history.path.read_text())["events"]
    assert [row["at"] for row in saved] == [event.at.isoformat() for event in events]
    assert all(row["at"].endswith("+00:00") for row in saved)


def test_retention_keeps_last_events_without_mutating_on_read(tmp_path):
    history = TimelineHistory(tmp_path, 3)
    history.extend(TimelineEvent(kind, NOW + timedelta(seconds=i))
                   for i, kind in enumerate(("snapshot", "missing", "error", "snapshot")))
    assert [e.type for e in history.load()] == ["missing", "error", "snapshot"]
    before = history.path.read_bytes()
    assert len(TimelineHistory(tmp_path, 1).read_last(5)) == 1
    assert history.path.read_bytes() == before
    history.prune(2)
    assert [e.type for e in history.load()] == ["error", "snapshot"]


def test_null_limit_is_bounded_and_zero_writes_nothing(tmp_path):
    history = TimelineHistory(tmp_path, None)
    history.extend(TimelineEvent("snapshot", NOW) for _ in range(DEFAULT_LIMIT + 2))
    assert len(history.load()) == DEFAULT_LIMIT
    disabled = TimelineHistory(tmp_path / "absent", 0)
    disabled.append("error", NOW)
    assert disabled.load() == []
    assert not disabled.path.parent.exists()
    config = LoggerToolConfig.from_dict({"storage": {"data_dir": str(tmp_path), "max_files": 0}})
    assert config.storage.max_files == 0


def test_failed_atomic_replace_preserves_previous_history(tmp_path, monkeypatch):
    history = TimelineHistory(tmp_path)
    history.append("snapshot", NOW)
    before = history.path.read_bytes()
    monkeypatch.setattr("meshtastic_tools.core.timeline.os.replace", Mock(side_effect=OSError))
    history.append("error", NOW)
    assert history.path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("kind", ["info", "nodes", "telemetry", "position"])
@pytest.mark.parametrize("fail", [False, True])
def test_collectors_write_success_or_failure_in_separate_history(tmp_path, kind, fail):
    storage = StorageManager(StorageConfig(data_dir=tmp_path, max_files=5), "alpha")
    device = SimpleNamespace(name="alpha", get_info=lambda: SimpleNamespace(node_id="!00000001"),
                             execute_command=Mock(return_value=SimpleNamespace(stdout="snapshot")))
    if fail:
        device.execute_command.side_effect = CollectionError("offline")
    collector = MeshtasticCollector(device, storage)
    if fail:
        with pytest.raises(CollectionError, match="offline"):
            getattr(collector, "collect_" + kind)()
    else:
        assert getattr(collector, "collect_" + kind)().is_file()
    history = TimelineHistory(tmp_path / "alpha" / kind).load()
    assert len(history) == 1 and history[0].type == ("error" if fail else "snapshot")
    assert history[0].at.utcoffset() is not None
    assert not (tmp_path / "alpha" / ".timeline.json").exists()


def test_snapshot_save_failure_records_error(tmp_path, monkeypatch):
    storage = StorageManager(StorageConfig(data_dir=tmp_path), "alpha")
    monkeypatch.setattr(storage, "save", Mock(side_effect=OSError("disk full")))
    device = SimpleNamespace(name="alpha", execute_command=lambda _: SimpleNamespace(stdout="data"))
    with pytest.raises(CollectionError, match="disk full"):
        MeshtasticCollector(device, storage).collect_info()
    assert [e.type for e in TimelineHistory(storage.device_path).load()] == ["error"]


def test_missed_slots_order_excludes_current_and_does_not_duplicate_on_retry(tmp_path):
    state = DeviceSchedule(tmp_path)
    history = TimelineHistory(tmp_path / "info", 10)
    schedule = "*/5 * * * *"
    state.mark_completed(schedule, (NOW - timedelta(minutes=20)).timestamp(), "UTC")
    state.record_missed(schedule, NOW, history, "UTC")
    assert [e.at for e in history.load()] == [NOW - timedelta(minutes=m) for m in (15, 10, 5)]
    history.append("error", NOW)
    before = history.path.read_bytes()
    state.record_missed(schedule, NOW + timedelta(minutes=2), history, "UTC", NOW)
    assert history.path.read_bytes() == before
    assert state.is_due(schedule, NOW + timedelta(minutes=2), "UTC")
    state.record_missed(schedule, NOW + timedelta(minutes=12), history, "UTC", NOW)
    assert [e.type for e in history.load()] == ["missing", "missing", "missing", "error", "missing"]
    assert history.load()[-1].at == NOW + timedelta(minutes=5)
    state.record_missed(schedule, NOW + timedelta(minutes=12), history, "UTC", NOW)
    assert len(history.load()) == 5


def test_attempt_is_not_missing_after_history_corruption(tmp_path):
    state = DeviceSchedule(tmp_path)
    state.mark_completed("*/5 * * * *", (NOW - timedelta(minutes=15)).timestamp(), "UTC")
    history = TimelineHistory(tmp_path / "info")
    state.record_missed("*/5 * * * *", NOW, history, "UTC", NOW - timedelta(minutes=10))
    assert [e.at for e in history.load()] == [NOW - timedelta(minutes=5)]


def test_long_outage_is_bounded_and_fresh_or_changed_schedule_has_no_history(tmp_path):
    state = DeviceSchedule(tmp_path)
    history = TimelineHistory(tmp_path / "info", 3)
    state.record_missed("* * * * *", NOW, history)
    assert not history.path.exists()
    state.mark_completed("* * * * *", (NOW - timedelta(days=3650)).timestamp())
    state.record_missed("*/5 * * * *", NOW, history)
    assert not history.path.exists()
    state.record_missed("* * * * *", NOW, history)
    assert [e.at for e in history.load()] == [NOW - timedelta(minutes=m) for m in (3, 2, 1)]


@pytest.mark.parametrize("fold", [0, 1])
def test_missing_slots_preserve_distinct_repeated_dst_minutes(tmp_path, fold):
    now = datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Berlin"), fold=fold)
    state = DeviceSchedule(tmp_path)
    history = TimelineHistory(tmp_path / "info", 4)
    state.mark_completed("* * * * *", now.timestamp() - 180, "Europe/Berlin")
    state.record_missed("* * * * *", now, history, "Europe/Berlin")
    assert [e.at.timestamp() for e in history.load()] == [now.timestamp() - 120, now.timestamp() - 60]


def test_long_attempt_does_not_hide_slot_crossed_during_collection(tmp_path):
    state = DeviceSchedule(tmp_path)
    history = TimelineHistory(tmp_path / "info")
    state.mark_completed("*/5 * * * *", (NOW - timedelta(minutes=10)).timestamp(), "UTC")
    # Attempt at 11:59 for 11:55 finishes at 12:01; the 12:00 slot was not attempted.
    history.append("snapshot", NOW + timedelta(minutes=1))
    state.record_missed("*/5 * * * *", NOW + timedelta(minutes=5), history, "UTC", NOW - timedelta(minutes=1))
    assert history.load() == [
        TimelineEvent("missing", NOW),
        TimelineEvent("snapshot", NOW + timedelta(minutes=1)),
    ]
