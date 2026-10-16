from datetime import datetime

import pytest

from meshtastic_tools.core.exceptions import CollectionError
from meshtastic_tools.logger.scheduler import DeviceSchedule


def test_no_early_collection_or_duplicate_across_process_invocations(tmp_path):
    schedule = "*/5 * * * *"
    state = DeviceSchedule(tmp_path)
    with state.locked():
        assert not state.is_due(schedule, datetime(2026, 10, 16, 10, 4, 30))
    # A new object represents a separate timer invocation.
    state = DeviceSchedule(tmp_path)
    with state.locked():
        now = datetime(2026, 10, 16, 10, 5, 30)
        assert state.is_due(schedule, now)
        state.mark_completed(schedule, state.latest_slot(schedule, now))
    with DeviceSchedule(tmp_path).locked():
        assert not DeviceSchedule(tmp_path).is_due(schedule, now)
        assert not state.is_due(schedule, datetime(2026, 10, 16, 10, 6, 30))
        assert state.is_due(schedule, datetime(2026, 10, 16, 10, 10, 0))


def test_fresh_scheduler_includes_exact_cron_boundary(tmp_path):
    state = DeviceSchedule(tmp_path)
    with state.locked():
        assert state.is_due("0 * * * *", datetime(2026, 10, 16, 10, 0, 0))


def test_first_failed_collection_can_retry_in_later_minute(tmp_path):
    with DeviceSchedule(tmp_path).locked():
        assert DeviceSchedule(tmp_path).is_due("*/5 * * * *", datetime(2026, 10, 16, 10, 5, 30))
    with DeviceSchedule(tmp_path).locked():
        assert DeviceSchedule(tmp_path).is_due("*/5 * * * *", datetime(2026, 10, 16, 10, 7, 30))


def test_late_timer_catches_up_once_and_failure_can_retry(tmp_path):
    state = DeviceSchedule(tmp_path)
    schedule = "*/5 * * * *"
    with state.locked():
        now = datetime(2026, 10, 16, 10, 0, 0)
        state.mark_completed(schedule, state.latest_slot(schedule, now))
        late = datetime(2026, 10, 16, 10, 19, 20)
        assert state.is_due(schedule, late)
        # A failed collection does not advance the checkpoint.
        assert state.is_due(schedule, late)
        state.mark_completed(schedule, state.latest_slot(schedule, late))
        assert not state.is_due(schedule, late)


def test_collection_lock_rejects_overlapping_invocations(tmp_path):
    with DeviceSchedule(tmp_path).locked():
        with pytest.raises(CollectionError, match="already running"):
            with DeviceSchedule(tmp_path).locked():
                pytest.fail("Second collector acquired the same device")
    with DeviceSchedule(tmp_path).locked():
        pass


def test_corrupt_state_fails_without_contacting_device(tmp_path):
    (tmp_path / ".schedule.json").write_text("broken")
    with DeviceSchedule(tmp_path).locked():
        with pytest.raises(CollectionError, match="Invalid scheduler state"):
            DeviceSchedule(tmp_path).is_due("* * * * *", datetime.now())


@pytest.mark.parametrize("fold", [0, 1])
def test_latest_slot_preserves_repeated_dst_hour(fold):
    from zoneinfo import ZoneInfo
    now = datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Berlin"), fold=fold)
    assert DeviceSchedule.latest_slot("* * * * *", now) == now.timestamp()


def test_naming_same_timezone_does_not_repeat_completed_slot(tmp_path):
    from zoneinfo import ZoneInfo
    schedule = "0 12 * * *"
    now = datetime(2026, 10, 16, 12, 0, tzinfo=ZoneInfo("Asia/Yekaterinburg"))
    state = DeviceSchedule(tmp_path)
    with state.locked():
        state.mark_completed(schedule, state.latest_slot(schedule, now), "local")
        assert not state.is_due(schedule, now, "Asia/Yekaterinburg")
