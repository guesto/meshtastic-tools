"""Bounded 24-hour metric history. Writers hold the device collection lock; readers never write."""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from meshtastic_tools.core.logging_config import get_logger


RETENTION = timedelta(hours=24)


@dataclass(frozen=True)
class MetricSample:
    at: datetime
    battery_level: Optional[float]
    voltage: Optional[float]

    def __post_init__(self):
        if self.at.utcoffset() is None:
            raise ValueError("Metric samples require an aware timestamp")
        for value, maximum in ((self.battery_level, 100), (self.voltage, 10)):
            if value is not None and (type(value) not in (int, float) or not 0 <= value <= maximum):
                raise ValueError("Metric samples require valid battery and voltage values")

    def to_dict(self):
        return {"at": self.at.astimezone(timezone.utc).isoformat(),
                "battery_level": self.battery_level, "voltage": self.voltage}


class MetricHistory:
    def __init__(self, directory: Path):
        self.path = directory / ".metrics.json"

    def load(self) -> list[MetricSample]:
        """Missing, unreadable or invalid history is empty, without side effects."""
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or type(value.get("version")) is not int
                    or value["version"] != 1 or not isinstance(value.get("samples"), list)):
                return []
            samples = []
            for row in value["samples"]:
                if (not isinstance(row, dict) or set(row) != {"at", "battery_level", "voltage"}
                        or not isinstance(row["at"], str)):
                    return []
                at = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
                if at.utcoffset() is None:
                    return []
                samples.append(MetricSample(at.astimezone(timezone.utc),
                                            row["battery_level"], row["voltage"]))
            samples.sort(key=lambda sample: sample.at.timestamp())
            return samples
        except (OSError, ValueError, TypeError, KeyError):
            return []

    def save(self, samples: Iterable[MetricSample]) -> None:
        """Replace atomically, retaining 24h before the newest sample; never fail collection on I/O."""
        samples = sorted(samples, key=lambda sample: sample.at.timestamp())
        if samples:
            cutoff = samples[-1].at.astimezone(timezone.utc) - RETENTION
            samples = [sample for sample in samples if sample.at >= cutoff]
        temp_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".metrics-", suffix=".tmp", delete=False) as temp:
                temp_path = Path(temp.name)
                json.dump({"version": 1, "samples": [sample.to_dict() for sample in samples]}, temp)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, self.path)
        except OSError:
            get_logger(__name__).warning("Cannot save metrics history", path=str(self.path))
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def append(self, at: datetime, battery_level: Optional[float], voltage: Optional[float]) -> None:
        """Append a sample, pruning to 24h. Skip samples with both metrics None."""
        if battery_level is None and voltage is None:
            return
        sample = MetricSample(at, battery_level, voltage)
        cutoff = sample.at.astimezone(timezone.utc) - RETENTION
        existing = [sample for sample in self.load() if sample.at >= cutoff]
        existing.append(sample)
        self.save(existing)
