"""Bounded event history. Writers hold the device collection lock; readers never write."""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from meshtastic_tools.core.logging_config import get_logger


DEFAULT_LIMIT = 1000
EVENT_SYMBOLS = {"snapshot": "█", "missing": "░", "error": "!"}


@dataclass(frozen=True)
class TimelineEvent:
    type: str
    at: datetime

    def __post_init__(self):
        if self.type not in EVENT_SYMBOLS or self.at.utcoffset() is None:
            raise ValueError("Timeline events require a known type and an aware timestamp")

    def to_dict(self):
        return {"type": self.type, "at": self.at.isoformat()}


class TimelineHistory:
    def __init__(self, directory: Path, limit: Optional[int] = None):
        self.path = directory / ".timeline.json"
        self.limit = DEFAULT_LIMIT if limit is None else max(0, limit)

    def load(self):
        """Missing, unreadable or invalid history is empty, without side effects."""
        if not self.limit:
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or type(value.get("version")) is not int
                    or value["version"] != 1 or not isinstance(value.get("events"), list)):
                return []
            events = []
            for row in value["events"]:
                if not isinstance(row, dict) or set(row) != {"type", "at"} or not isinstance(row["at"], str):
                    return []
                events.append(TimelineEvent(row["type"], datetime.fromisoformat(row["at"].replace("Z", "+00:00"))))
            return events[-self.limit:]
        except (OSError, ValueError, TypeError, KeyError):
            return []

    def read_last(self, n: int):
        return self.load()[-n:] if n > 0 else []

    def save(self, events: Iterable[TimelineEvent]):
        """Replace atomically; an auxiliary history write cannot fail a collection."""
        if not self.limit:
            return
        events = list(events)[-self.limit:]
        temp_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".timeline-", suffix=".tmp", delete=False) as temp:
                temp_path = Path(temp.name)
                json.dump({"version": 1, "events": [event.to_dict() for event in events]}, temp)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, self.path)
        except OSError:
            get_logger(__name__).warning("Cannot save timeline history", path=str(self.path))
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def append(self, event_type: str, at: datetime):
        self.extend([TimelineEvent(event_type, at)])

    def extend(self, events: Iterable[TimelineEvent]):
        if self.limit:
            incoming = list(events)
            if incoming:
                self.save(self.load() + incoming)

    def prune(self, limit: int):
        events = self.load()
        self.limit = max(0, limit)
        self.save(events)
