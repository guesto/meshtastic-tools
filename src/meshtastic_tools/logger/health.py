"""Persist the latest collection results and report health without device access."""

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Dict, Literal, Optional
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from meshtastic_tools.core.exceptions import StorageError
from meshtastic_tools.logger.privacy import redact_secrets
from meshtastic_tools.logger.scheduler import DeviceSchedule


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CollectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_attempt_at: AwareDatetime
    last_finished_at: AwareDatetime
    last_outcome: Literal["success", "error"]
    source: Literal["manual", "scheduled"]
    last_duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    last_success_at: Optional[AwareDatetime] = None
    last_file: Optional[str] = None
    last_failure_at: Optional[AwareDatetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int = Field(default=0, ge=0)


class HealthDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    collectors: Dict[str, CollectionResult] = Field(default_factory=dict)


class CollectionAttempt:
    def __init__(self):
        self.started_at = utc_now()
        self.started_clock = monotonic()
        self.success_at: Optional[datetime] = None
        self.file: Optional[str] = None
        self.duration: Optional[float] = None

    def collected(self, path: Path) -> None:
        """A snapshot was saved; exclude subsequent cleanup from request duration."""
        self.success_at = utc_now()
        self.file = path.name
        self.duration = max(0, monotonic() - self.started_clock)


class DeviceHealth:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / ".status.json"

    def load(self) -> HealthDocument:
        """Read only: a status query never creates directories or modifies files."""
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return HealthDocument.model_validate(value)
        except FileNotFoundError:
            return HealthDocument()
        except (OSError, ValueError) as e:
            # Validation errors can contain credential values from the input.
            raise StorageError(f"Cannot read valid collection status from {self.path}") from e

    def _save(self, document: HealthDocument) -> None:
        temp_path = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.directory.chmod(0o700)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".status-", suffix=".tmp", delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write(document.model_dump_json(indent=2))
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, self.path)
        except OSError as e:
            raise StorageError(f"Cannot save collection status to {self.path}: {e}") from e
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @contextmanager
    def attempt(self, collector: str, source: Literal["manual", "scheduled"]):
        """Track an attempt while the caller holds the shared device lock."""
        document = self.load()
        attempt = CollectionAttempt()
        try:
            yield attempt
        except Exception as e:
            self._finish(document, collector, source, attempt, error=e)
            raise
        else:
            if attempt.success_at is None:
                raise StorageError("Collection attempt finished without a saved snapshot")
            self._finish(document, collector, source, attempt)

    def _finish(self, document: HealthDocument, collector: str, source: str,
                attempt: CollectionAttempt, error: Optional[Exception] = None) -> None:
        previous = document.collectors.get(collector)
        failed = error is not None
        finished_at = utc_now()
        duration = attempt.duration
        if duration is None:
            duration = max(0, monotonic() - attempt.started_clock)
        document.collectors[collector] = CollectionResult(
            last_attempt_at=attempt.started_at,
            last_finished_at=finished_at,
            last_outcome="error" if failed else "success",
            source=source,
            last_duration_seconds=duration,
            last_success_at=attempt.success_at or (previous.last_success_at if previous else None),
            last_file=attempt.file or (previous.last_file if previous else None),
            last_failure_at=finished_at if failed else (previous.last_failure_at if previous else None),
            last_error=redact_secrets(str(error))[:2000] if failed else (previous.last_error if previous else None),
            consecutive_failures=((previous.consecutive_failures if previous else 0) + 1) if failed else 0,
        )
        self._save(document)


def device_status(name: str, directory: Path, enabled: bool, schedule: Optional[str],
                  timeout: int, now: datetime, grace_seconds: Optional[int] = None,
                  timezone_name: str = "local") -> dict:
    """Describe the scheduled info collector; manual telemetry cannot mask its failures."""
    result = DeviceHealth(directory).load().collectors.get("info")
    if timezone_name != "local":
        now = now.astimezone(ZoneInfo(timezone_name))
    next_scheduled = croniter(schedule, now).get_next(datetime) if enabled and schedule else None
    grace = 60 + timeout if grace_seconds is None else grace_seconds
    # Check the newest slot whose grace has expired, rather than resetting a stale
    # warning at each new slot. This also works when timeout exceeds the interval.
    cutoff = datetime.fromtimestamp(now.timestamp() - grace - 0.000001, now.tzinfo)
    required_slot = DeviceSchedule.latest_slot(schedule, cutoff) if enabled and schedule else None

    last_success = result.last_success_at if result else None
    stale = False
    if required_slot is not None and last_success is not None:
        stale = last_success.timestamp() < required_slot

    if not enabled:
        state = "disabled"
    elif result is None:
        state = "unknown"
    elif result.last_outcome == "error":
        state = "error"
    elif stale:
        state = "stale"
    else:
        state = "ok"

    record = result.model_dump(mode="json") if result else {}
    # Also sanitize historical error text when reading the report.
    if record.get("last_error"):
        record["last_error"] = redact_secrets(record["last_error"])
    return {
        "device": name,
        "state": state,
        "enabled": enabled,
        "schedule": schedule,
        "timezone": timezone_name,
        "next_scheduled_at": (next_scheduled if next_scheduled.tzinfo else next_scheduled.astimezone()).isoformat() if next_scheduled else None,
        "stale": stale,
        "grace_seconds": grace,
        "last_attempt_at": record.get("last_attempt_at"),
        "last_finished_at": record.get("last_finished_at"),
        "last_outcome": record.get("last_outcome"),
        "last_success_at": record.get("last_success_at"),
        "last_failure_at": record.get("last_failure_at"),
        "last_error": record.get("last_error"),
        "consecutive_failures": record.get("consecutive_failures", 0),
        "last_duration_seconds": record.get("last_duration_seconds"),
        "last_file": record.get("last_file"),
        "source": record.get("source"),
    }
