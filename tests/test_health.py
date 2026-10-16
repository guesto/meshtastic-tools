import importlib
from datetime import datetime, timedelta, timezone

import pytest

from meshtastic_tools.core.exceptions import CollectionError, StorageError
from meshtastic_tools.logger.health import DeviceHealth, device_status

health = importlib.import_module("meshtastic_tools.logger.health")
LOCAL = timezone(timedelta(hours=5))


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = datetime(2026, 10, 16, 10, 0, 0, tzinfo=LOCAL)
        seconds = 100.0

        def advance(self, seconds):
            self.now += timedelta(seconds=seconds)
            self.seconds += seconds

    clock = Clock()
    monkeypatch.setattr(health, "utc_now", lambda: clock.now.astimezone(timezone.utc))
    monkeypatch.setattr(health, "monotonic", lambda: clock.seconds)
    return clock


def successful(health, path, collector="info", source="scheduled"):
    with health.attempt(collector, source) as attempt:
        attempt.collected(path)


def failed(health, message="connection refused", collector="info"):
    with pytest.raises(CollectionError, match="connection refused"):
        with health.attempt(collector, "scheduled"):
            raise CollectionError(message)


def test_tracks_request_duration_and_private_atomic_record(tmp_path, clock):
    tracker = DeviceHealth(tmp_path / "device")
    with tracker.attempt("info", "scheduled") as attempt:
        clock.advance(2.5)
        attempt.collected(tmp_path / "snapshot.txt")
        clock.advance(10)  # Cleanup time is excluded from request duration.
    result = DeviceHealth(tracker.directory).load().collectors["info"]
    assert result.last_outcome == "success"
    assert result.last_duration_seconds == 2.5
    assert result.last_success_at == clock.now - timedelta(seconds=10)
    assert result.last_finished_at == clock.now
    assert result.last_file == "snapshot.txt"
    assert tracker.path.stat().st_mode & 0o777 == 0o600
    assert tracker.directory.stat().st_mode & 0o777 == 0o700
    assert not list(tracker.directory.glob("*.tmp"))


def test_failures_survive_restarts_and_recovery_preserves_last_error(tmp_path, clock):
    tracker = DeviceHealth(tmp_path)
    successful(tracker, tmp_path / "first.txt")
    last_success = clock.now
    clock.advance(60)
    failed(DeviceHealth(tmp_path))
    clock.advance(60)
    failed(DeviceHealth(tmp_path))
    result = tracker.load().collectors["info"]
    assert result.consecutive_failures == 2
    assert result.last_success_at == last_success
    assert result.last_failure_at == clock.now
    clock.advance(60)
    successful(DeviceHealth(tmp_path), tmp_path / "recovered.txt", source="manual")
    result = tracker.load().collectors["info"]
    assert result.consecutive_failures == 0
    assert result.last_outcome == "success"
    assert result.last_error == "connection refused"
    assert result.last_failure_at == clock.now - timedelta(seconds=60)
    assert result.last_success_at == clock.now
    assert result.source == "manual"


def test_telemetry_cannot_mask_failed_scheduled_info(tmp_path, clock):
    tracker = DeviceHealth(tmp_path)
    failed(tracker)
    successful(tracker, tmp_path / "telemetry.txt", "telemetry", "manual")
    report = device_status("test", tmp_path, True, "*/5 * * * *", 30, clock.now)
    assert report["state"] == "error"
    assert report["consecutive_failures"] == 1
    assert report["last_success_at"] is None
    assert tracker.load().collectors["telemetry"].last_outcome == "success"


def test_error_messages_are_redacted_before_persistence(tmp_path, clock):
    tracker = DeviceHealth(tmp_path)
    failed(tracker, 'connection refused: {"privateKey": "private-secret", "psk": "channel-secret"}')
    value = tracker.path.read_text()
    assert "private-secret" not in value
    assert "channel-secret" not in value
    assert "REDACTED" in value


def test_cleanup_error_preserves_successful_snapshot_and_duration(tmp_path, clock):
    tracker = DeviceHealth(tmp_path)
    with pytest.raises(StorageError):
        with tracker.attempt("info", "scheduled") as attempt:
            clock.advance(2)
            attempt.collected(tmp_path / "saved.txt")
            clock.advance(10)
            raise StorageError("cleanup failed")
    result = tracker.load().collectors["info"]
    assert result.last_success_at == clock.now - timedelta(seconds=10)
    assert result.last_duration_seconds == 2
    assert result.last_outcome == "error"
    assert result.last_error == "cleanup failed"


def test_status_is_read_only_before_first_collection(tmp_path, clock):
    directory = tmp_path / "not-created"
    report = device_status("test", directory, True, "*/5 * * * *", 30, clock.now)
    assert report["state"] == "unknown"
    assert not directory.exists()
    assert report["next_scheduled_at"] == (clock.now + timedelta(minutes=5)).isoformat()


def test_stale_detection_respects_cron_and_timer_timeout_grace(tmp_path, clock):
    successful(DeviceHealth(tmp_path), tmp_path / "saved.txt")
    schedule = "*/5 * * * *"
    for seconds, state in [(299, "ok"), (300, "ok"), (390, "ok"), (391, "stale")]:
        now = clock.now + timedelta(seconds=seconds)
        report = device_status("test", tmp_path, True, schedule, 30, now)
        assert report["state"] == state
        assert report["stale"] == (state == "stale")


def test_daily_schedule_does_not_flag_hours_old_data(tmp_path, clock):
    clock.now = datetime(2026, 10, 16, 0, 0, 10, tzinfo=LOCAL)
    successful(DeviceHealth(tmp_path), tmp_path / "daily.txt")
    now = datetime(2026, 10, 16, 23, 59, 59, tzinfo=LOCAL)
    assert device_status("test", tmp_path, True, "0 0 * * *", 30, now)["state"] == "ok"
    now = datetime(2026, 10, 17, 0, 2, 0, tzinfo=LOCAL)
    assert device_status("test", tmp_path, True, "0 0 * * *", 30, now)["state"] == "stale"


def test_stale_warning_does_not_reset_at_next_cron_time(tmp_path, clock):
    successful(DeviceHealth(tmp_path), tmp_path / "saved.txt")
    for seconds in (391, 600, 601, 660):
        report = device_status("test", tmp_path, True, "*/5 * * * *", 30,
                               clock.now + timedelta(seconds=seconds))
        assert report["state"] == "stale"


def test_stale_detection_when_timeout_exceeds_schedule_interval(tmp_path, clock):
    successful(DeviceHealth(tmp_path), tmp_path / "saved.txt")
    report = device_status("test", tmp_path, True, "* * * * *", 300,
                           clock.now + timedelta(minutes=8))
    assert report["state"] == "stale"


def test_disabled_device_is_not_overdue(tmp_path, clock):
    failed(DeviceHealth(tmp_path))
    report = device_status("test", tmp_path, False, "*/5 * * * *", 30, clock.now)
    assert report["state"] == "disabled"
    assert report["next_scheduled_at"] is None
    assert not report["stale"]


def test_explicit_grace_overrides_timeout_including_zero(tmp_path, clock):
    successful(DeviceHealth(tmp_path), tmp_path / "saved.txt")
    now = clock.now + timedelta(minutes=5, seconds=1)
    assert device_status("test", tmp_path, True, "*/5 * * * *", 300, now,
                         grace_seconds=0)["state"] == "stale"
    assert device_status("test", tmp_path, True, "*/5 * * * *", 300, now,
                         grace_seconds=90)["state"] == "ok"


def test_freshness_and_next_time_use_configured_timezone(tmp_path, clock):
    successful(DeviceHealth(tmp_path), tmp_path / "saved.txt")
    now = datetime(2026, 10, 16, 7, 2, 0, tzinfo=timezone.utc)
    report = device_status("test", tmp_path, True, "0 12 * * *", 30, now,
                           timezone_name="Asia/Yekaterinburg")
    assert report["state"] == "stale"
    assert report["next_scheduled_at"] == "2026-10-17T12:00:00+05:00"


@pytest.mark.parametrize("value", ["broken", "[]", '{"version": 2}', '{"collectors": {"info": {"privateKey":"secret"}}}'])
def test_corrupt_state_is_reported_without_overwriting_or_leaking_input(tmp_path, clock, value):
    path = tmp_path / ".status.json"
    path.write_text(value)
    with pytest.raises(StorageError) as error:
        DeviceHealth(tmp_path).load()
    assert "secret" not in str(error.value)
    assert path.read_text() == value
