"""Serialize device access and persist completed cron slots across invocations."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter

from meshtastic_tools.core.exceptions import CollectionError
from meshtastic_tools.core.health import latest_slot
from meshtastic_tools.core.timeline import TimelineEvent, TimelineHistory


class DeviceSchedule:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / ".schedule.json"

    @contextmanager
    def locked(self):
        """Share one lock between scheduled and manual collection for a device."""
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        fd = os.open(self.directory / ".collection.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise CollectionError("Collection for this device is already running") from e
            yield
        finally:
            os.close(fd)

    @staticmethod
    def latest_slot(schedule: str, now: datetime) -> float:
        return latest_slot(schedule, now)

    def is_due(self, schedule: str, now: datetime, timezone_name: str = "local") -> bool:
        slot = self.latest_slot(schedule, now)
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if state.get("schedule") == schedule:
                return slot > float(state["last_slot"])
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            raise CollectionError(f"Invalid scheduler state: {self.path}") from e

        # A fresh installation starts with the current minute, without collecting
        # a slot from hours ago. Afterwards, collect the latest missed slot once.
        if now.timestamp() - slot < 60:
            previous = croniter(schedule, datetime.fromtimestamp(slot, now.tzinfo)).get_prev(datetime)
            self.mark_completed(schedule, previous.timestamp(), timezone_name)
            return True
        self.mark_completed(schedule, slot, timezone_name)
        return False

    def record_missed(self, schedule: str, now: datetime, history: TimelineHistory,
                      timezone_name: str = "local", last_attempt_at=None) -> None:
        """Record unattempted slots before the current slot, bounded by retention.

        Keep the successful checkpoint unchanged so a failed collection can retry.
        The previous attempt and existing events prevent retries from becoming gaps.
        A new installation or changed schedule has no trustworthy earlier baseline.
        """
        if not history.limit:
            return
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if state["schedule"] != schedule or state["timezone"] != timezone_name:
                return
            baseline = float(state["last_slot"])
        except (OSError, ValueError, TypeError, KeyError):
            return
        current = self.latest_slot(schedule, now)
        if last_attempt_at is not None:
            attempt_slot = self.latest_slot(schedule, last_attempt_at.astimezone(now.tzinfo))
            if attempt_slot <= current:
                baseline = max(baseline, attempt_slot)
        for event in history.load():
            stamp = event.at.timestamp()
            if stamp < current and (event.type == "missing" or last_attempt_at is None):
                baseline = max(baseline, stamp if event.type == "missing" else
                               self.latest_slot(schedule, event.at.astimezone(now.tzinfo)))
        cursor = croniter(schedule, datetime.fromtimestamp(current, now.tzinfo))
        missing = []
        for _ in range(history.limit):
            slot = cursor.get_prev(datetime)
            if slot.timestamp() <= baseline:
                break
            missing.append(TimelineEvent("missing", slot.astimezone(timezone.utc)))
        history.extend(reversed(missing))

    def mark_completed(self, schedule: str, slot: float, timezone_name: str = "local") -> None:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".schedule-", suffix=".tmp", delete=False) as temp:
                temp_path = Path(temp.name)
                json.dump({"schedule": schedule, "timezone": timezone_name, "last_slot": slot}, temp)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
