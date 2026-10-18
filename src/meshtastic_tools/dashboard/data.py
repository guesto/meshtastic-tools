"""Read-only dashboard aggregation and terminal presentation."""

import json
import math
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from string import Formatter
from typing import List, Optional
from zoneinfo import ZoneInfo

from meshtastic_tools.core.config import ConfigManager
from meshtastic_tools.core.health import HealthDocument, describe_device_status
from meshtastic_tools.core.metrics import MetricHistory
from meshtastic_tools.core.snapshot import Snapshot, parse_snapshot, read_snapshot
from meshtastic_tools.core.timeline import EVENT_SYMBOLS, TimelineHistory


STATUS_PRIORITY = ("disabled", "ok", "unknown", "stale", "error", "unreadable")
COLORS = {"ok": "green", "stale": "yellow", "error": "red", "unreadable": "red",
          "unknown": "bright_black", "disabled": "bright_black", "warning": "yellow", "critical": "red"}
COLLECTION_FIELDS = ("last_success_at", "last_attempt_at", "last_finished_at", "last_outcome",
                     "last_duration_seconds", "consecutive_failures", "last_error",
                     "last_failure_at", "last_file", "source")
_STAMP = re.compile(r"(?:^|_)(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})(?:-(\d{6}))?(?=\.|_|$)")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass
class DashboardData:
    generated_at: datetime
    timezone: str
    overall_status: str
    devices: List[dict]
    config_source: Optional[str] = None
    device_summary: List[dict] = field(default_factory=list)
    active_device: Optional[str] = None


def _iso(timestamp: float, tz) -> str:
    value = datetime.fromtimestamp(timestamp, tz) if tz else datetime.fromtimestamp(timestamp).astimezone()
    return value.isoformat()


def _info_pattern(filename_format: str, device: str):
    """Match the configured info name literally, including custom extensions."""
    parts = []
    timestamp_seen = False
    for literal, field, spec, conversion in Formatter().parse(filename_format):
        parts.append(re.escape(literal))
        if field == "device":
            value = repr(device) if conversion == "r" else ascii(device) if conversion == "a" else device
            parts.append(re.escape(format(value, spec)))
        elif field == "timestamp":
            parts.append(r"(?P=timestamp)" if timestamp_seen else r"(?P<timestamp>.+?)")
            timestamp_seen = True
    return re.compile("".join(parts) + r"\Z")


def _file_time(name: str, mtime: float) -> float:
    matches = list(_STAMP.finditer(name))
    if not matches:
        return mtime
    match = matches[-1]
    try:
        value = datetime.fromisoformat(match[1] + "T" + match[2].replace("-", ":") + ("." + match[3] if match[3] else ""))
        # The collector writes filenames in the server's local time, independently
        # of the scheduler TZ. mtime disambiguates a repeated local hour at DST.
        stamp = value.timestamp()
        if abs(stamp - mtime) > 1800:
            other = value.replace(fold=1).timestamp()
            if abs(other - mtime) < abs(stamp - mtime):
                stamp = other
        return stamp
    except (ValueError, OSError, OverflowError):
        return mtime


def _disk(path: Path) -> Optional[dict]:
    while True:
        try:
            usage = shutil.disk_usage(path)
            if usage.total <= 0:
                return None
            percent = usage.free / usage.total * 100
            used = getattr(usage, "used", usage.total - usage.free)
            state = "critical" if percent < 10 else "warning" if percent <= 20 else "ok"
            return {"total_bytes": usage.total, "free_bytes": usage.free,
                    "free_percent": percent, "used_bytes": used,
                    "used_percent": used / usage.total * 100, "status": state}
        except FileNotFoundError:
            if path == path.parent:
                return None
            path = path.parent
        except OSError:
            return None


def _scan(directory: Path, config, name: str, tz):
    storage = {"file_count": 0, "max_files": config.max_files, "total_size_bytes": 0,
               "oldest_file_at": None, "newest_file_at": None, "rotation_active": False,
               "disk": _disk(directory), "error": None}
    matcher = _info_pattern(config.filename_format, name)
    suffixes = {".txt", Path(config.filename_format).suffix}
    oldest = newest = None
    latest = None
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.startswith(".") or os.path.splitext(entry.name)[1] not in suffixes:
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                stamp = _file_time(entry.name, stat.st_mtime)
                storage["file_count"] += 1
                storage["total_size_bytes"] += stat.st_size
                oldest = stamp if oldest is None else min(oldest, stamp)
                newest = stamp if newest is None else max(newest, stamp)
                if matcher.fullmatch(entry.name):
                    candidate = (stamp, entry.name)
                    if latest is None or candidate > latest:
                        latest = candidate
    except FileNotFoundError:
        pass
    except OSError:
        storage.update(file_count=None, total_size_bytes=None, error="Cannot read snapshot directory")
    if oldest is not None:
        storage["oldest_file_at"] = _iso(oldest, tz)
        storage["newest_file_at"] = _iso(newest, tz)
    storage["rotation_active"] = (storage["file_count"] is not None and bool(config.max_files)
                                  and storage["file_count"] >= config.max_files)
    return storage, directory / latest[1] if latest else None


def _load_health(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text)
        if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1 or "collectors" not in value:
            raise ValueError
        return HealthDocument.model_validate_json(text, strict=True).collectors.get("info"), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError):
        return None, "Cannot read valid .status.json"


def _load_schedule(path: Path, tz):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or not isinstance(value.get("schedule"), str)
                or not isinstance(value.get("timezone"), str)
                or type(value.get("last_slot")) not in (int, float)
                or not math.isfinite(value["last_slot"])):
            raise ValueError
        # Validate the saved format, not equality with the current configuration.
        from croniter import croniter
        if len(value["schedule"].split()) != 5 or not croniter.is_valid(value["schedule"]):
            raise ValueError
        if value["timezone"] != "local":
            ZoneInfo(value["timezone"])
        return _iso(value["last_slot"], tz), True, None
    except FileNotFoundError:
        return None, False, None
    except (OSError, ValueError, KeyError, OverflowError):
        return None, False, "Cannot read valid .schedule.json"


def collect_dashboard_data(manager: ConfigManager, device: Optional[str] = None,
                           now: Optional[datetime] = None) -> DashboardData:
    """Collect local records once. No StorageManager, scheduler locks or device I/O."""
    if device is not None and device not in manager.devices:
        raise ValueError(f"Device '{device}' not found")
    config = manager.get_logger_config()
    tz = ZoneInfo(config.timezone) if config.timezone != "local" else None
    now = now or datetime.now().astimezone()
    now = now.astimezone(tz) if tz else now.astimezone()
    names = [device] if device is not None else [name for name in manager.devices
                                               if config.enabled and config.is_device_enabled(name)]
    reports, summaries, records = [], [], {}
    for name in manager.devices:
        enabled = config.enabled and config.is_device_enabled(name)
        directory = config.storage.data_dir / name
        result, health_error = _load_health(directory / ".status.json")
        health_now = now if tz else now.replace(tzinfo=None)
        report = describe_device_status(name, result, enabled, config.get_device_schedule(name),
                                        manager.devices[name].connection.timeout, health_now,
                                        config.health_grace_seconds, config.timezone)
        last_slot, schedule_available, schedule_error = _load_schedule(directory / ".schedule.json", tz)
        errors = [error for error in (health_error, schedule_error) if error]
        state = "unreadable" if errors and enabled else report["state"]
        summaries.append({"name": name, "status": state, "enabled": enabled,
                          "last_success_at": (_iso(result.last_success_at.timestamp(), tz)
                                              if result and result.last_success_at else None),
                          "consecutive_failures": report["consecutive_failures"] if result else None})
        records[name] = (result, report, last_slot, schedule_available, errors, state)
    for name in names:
        enabled = config.enabled and config.is_device_enabled(name)
        directory = config.storage.data_dir / name
        result, report, last_slot, schedule_available, errors, state = records[name]
        collection = {key: report[key] if result is not None else None for key in COLLECTION_FIELDS}
        for key, value in collection.items():
            if key.endswith("_at") and value is not None:
                collection[key] = _iso(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp(), tz)
        storage, latest = _scan(directory / "info", config.storage, name, tz)
        next_slot = report["next_scheduled_at"] if schedule_available else None
        events = [{"type": event.type, "at": _iso(event.at.timestamp(), tz)}
                  for event in TimelineHistory(directory / "info", config.storage.max_files).load()]
        timeline = {"events": events, "shown": len(events), "total": len(events), "next_slot_at": next_slot}
        snapshot = Snapshot(snapshot_status="missing")
        if latest is not None:
            try:
                snapshot = parse_snapshot(read_snapshot(latest))
            except FileNotFoundError:
                pass
            except (OSError, UnicodeError):
                snapshot = Snapshot(snapshot_status="unreadable", snapshot_error="Cannot read latest snapshot")
        connection = manager.devices[name].connection
        metrics = MetricHistory(directory).load()
        power = {
            "battery_level": snapshot.battery_level,
            "voltage": snapshot.voltage,
            "battery_min": min((s.battery_level for s in metrics if s.battery_level is not None), default=None),
            "battery_max": max((s.battery_level for s in metrics if s.battery_level is not None), default=None),
            "voltage_min": min((s.voltage for s in metrics if s.voltage is not None), default=None),
            "voltage_max": max((s.voltage for s in metrics if s.voltage is not None), default=None),
            "samples": [{"at": _iso(s.at.timestamp(), tz),
                         "battery_level": s.battery_level, "voltage": s.voltage} for s in metrics],
        }
        mesh = asdict(snapshot)
        if snapshot.last_heard_at is not None:
            mesh["last_heard_at"] = _iso(snapshot.last_heard_at.timestamp(), tz)
        reports.append({"name": name, "status": state, "enabled": enabled,
                        "errors": errors,
                        "connection": {"type": getattr(connection.type, "value", connection.type), "address": connection.address},
                        "collection": collection,
                        "schedule": {"expression": report["schedule"], "last_slot_at": last_slot,
                                     "next_slot_at": next_slot},
                        "timeline": timeline, "storage": storage, "mesh": mesh, "power": power})
    overall = max((row["status"] for row in summaries), key=STATUS_PRIORITY.index, default="disabled")
    return DashboardData(now, config.timezone, overall, reports,
                         str(manager.loaded_path) if manager.loaded_path else None, summaries, device)


def timeline_capacity(width: int, layout: str = "auto") -> int:
    """Return the number of timeline cells that fit in the given total width."""
    inner = max(1, width - 4)
    split = layout == "split" or (layout == "auto" and width >= 120)
    column = (inner - 3) // 2 if split else inner
    label_width = min(14, max(1, column // 3))
    value_width = max(1, column - label_width - 3)
    return min(50, max(1, value_width - 2))


def timeline_view(timeline: dict, capacity: int) -> dict:
    events = timeline["events"][-max(1, capacity):]
    return {"events": events, "shown": len(events), "total": timeline["total"],
            "next_slot_at": timeline["next_slot_at"]}


def to_json(data: DashboardData, width: int = 120, layout: str = "auto") -> dict:
    capacity = timeline_capacity(width, layout)
    devices = [dict(row, timeline=timeline_view(row["timeline"], capacity))
               for row in data.devices]
    return {"schema_version": 1, "generated_at": data.generated_at.isoformat(),
            "timezone": data.timezone, "overall_status": data.overall_status,
            "config": {"source": data.config_source}, "device_summary": data.device_summary,
            "devices": devices}


def _safe(value) -> str:
    return _CONTROL.sub(" ", str(value)) if value is not None else "—"


def _size(value) -> str:
    if value is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


def _duration(value) -> str:
    if value is None:
        return "—"
    seconds = max(0, int(value))
    if seconds >= 86400:
        return f"{seconds // 86400} d {seconds % 86400 // 3600} h"
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60} m"
    if seconds >= 60:
        return f"{seconds // 60} m {seconds % 60} s"
    return f"{seconds} s"


def timeline_symbols(timeline: dict, width: int) -> str:
    """One recorded event per cell; vacant cells carry no time semantics."""
    count = max(1, width)
    events = timeline["events"][-count:]
    return "".join(EVENT_SYMBOLS[event["type"]] for event in events) + "·" * (count - len(events))


def render_text(data: DashboardData, width: int = 120, page: int = 1,
                summary_only: bool = False, layout: str = "auto") -> str:
    from meshtastic_tools.dashboard.render import render_text as render
    return render(data, width, page, summary_only, layout)


def render_rich(data: DashboardData, width: int = 120, page: int = 1,
                summary_only: bool = False, layout: str = "auto", height: Optional[int] = None):
    from meshtastic_tools.dashboard.render import render_rich as render
    return render(data, width, page, summary_only, layout, height)
