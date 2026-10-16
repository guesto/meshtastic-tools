"""Serialize device access and persist completed cron slots across invocations."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from croniter import croniter

from meshtastic_tools.core.exceptions import CollectionError


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
        # get_prev is exclusive: include a slot that falls exactly at now.
        slot = croniter(schedule, now + timedelta(microseconds=1)).get_prev(datetime)
        return slot.timestamp()

    def is_due(self, schedule: str, now: datetime) -> bool:
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
            previous = croniter(schedule, datetime.fromtimestamp(slot)).get_prev(datetime)
            self.mark_completed(schedule, previous.timestamp())
            return True
        self.mark_completed(schedule, slot)
        return False

    def mark_completed(self, schedule: str, slot: float) -> None:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".schedule-", suffix=".tmp", delete=False) as temp:
                temp_path = Path(temp.name)
                json.dump({"schedule": schedule, "last_slot": slot}, temp)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
