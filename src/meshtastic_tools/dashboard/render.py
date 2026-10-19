"""Dense cockpit layout. Event cells never imply a time interval."""

import math
import textwrap
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Optional

from rich.console import Console, Group
from rich.text import Text

from meshtastic_tools.dashboard.data import (
    COLORS, STATUS_PRIORITY, _duration, _safe, _size,
    timeline_capacity, timeline_symbols, timeline_view,
)
from meshtastic_tools.core.privacy import redact_secrets


LEGEND = "█ snapshot  ░ missing  ! error  · empty"


def device_page(data, page=1):
    summaries = data.device_summary or [
        {"name": row["name"], "status": row["status"], "enabled": row["enabled"]}
        for row in data.devices
    ]
    summaries = sorted(summaries, key=lambda row: (-STATUS_PRIORITY.index(row["status"]), row["name"]))
    pages = max(1, math.ceil(len(summaries) / 3))
    if not 1 <= page <= pages:
        raise ValueError(f"Page must be between 1 and {pages}")
    visible = summaries[(page - 1) * 3:page * 3]
    active = data.active_device
    if active is None:
        active = next((row["name"] for row in visible if row["enabled"]), None)
    detail = next((row for row in data.devices if row["name"] == active), None)
    return summaries, visible, pages, detail


def _time(value):
    return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S") if value else "—"


def _relative(value, now):
    if value is None:
        return "—"
    delta = datetime.fromisoformat(value).timestamp() - now.timestamp()
    return "in " + _duration(delta) if delta > 0 else _duration(-delta) + " ago"


def _float(value, suffix="", trim=False):
    if value is None:
        return "—"
    number = Decimal(str(value))
    with localcontext() as context:
        context.prec = max(28, number.adjusted() + 4)
        text = str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return (text.rstrip("0").rstrip(".") if trim else text) + suffix


def _status(value):
    return Text(("○ " if value == "unknown" else "● ") + value.upper(), style=COLORS[value])


def radio_severity(value, channel=True):
    if value is None:
        return "unknown"
    low, medium = (25, 50) if channel else (2, 5)
    return "low" if value < low else "medium" if value <= medium else "high"


def _radio(value, channel, number_width):
    state = radio_severity(value, channel)
    line = Text(_float(value).rjust(number_width) + (" %" if value is not None else "  ") + "  ")
    color = {"low": "green", "medium": "yellow", "high": "red", "unknown": COLORS["unknown"]}[state]
    line.append(("○ " if state == "unknown" else "● ") + state, style=color)
    return line


def _bar(percent, width):
    width = max(1, width)
    filled = min(width, max(0, round(percent / 100 * width)))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _sparkline(samples: list[dict], width: int = 48, *, now: Optional[datetime] = None) -> str:
    """Average battery per rolling 30-minute interval in the 24h ending at now.
    Empty intervals are shown as '·' (gap)."""
    width = max(0, width)
    if not width:
        return ""
    end = (now or datetime.now(timezone.utc)).timestamp()
    start = end - 24 * 60 * 60
    buckets = {}
    for sample in samples:
        battery = sample["battery_level"]
        if battery is None:
            continue
        stamp = datetime.fromisoformat(sample["at"].replace("Z", "+00:00")).timestamp()
        if not start <= stamp <= end:
            continue
        index = min(width - 1, int((stamp - start) // (30 * 60)))
        total, count = buckets.get(index, (0, 0))
        buckets[index] = (total + battery, count + 1)
    levels = "▁▂▃▄▅▆▇█"
    cells = []
    for index in range(width):
        if index in buckets:
            total, count = buckets[index]
            cells.append(levels[min(7, max(0, int(total / count * 8 / 100)))])
        else:
            cells.append("·")
    return "".join(cells)


def _clip(value, width):
    value = value.copy() if isinstance(value, Text) else Text(_safe(value))
    value.truncate(max(0, width), overflow="ellipsis")
    return value


def _header(data, width, page, summary_only):
    summaries, visible, pages, detail = device_page(data, page)
    left_width = (width - 3) // 2
    right_width = width - 3 - left_width
    status = _status(data.overall_status)
    title = Text("MESHTASTIC LOGGER ") + status
    clock = data.generated_at.strftime("%H:%M:%S")
    timezone = Text("TZ " + _safe(data.timezone))
    if title.cell_len + 1 + len(clock) <= left_width:
        title.append(" " * (left_width - title.cell_len - len(clock)) + clock)
    else:
        timezone.append(" " + clock)
    left = [title, timezone, Text(""), Text("")]
    label = f"DEVICES ({len(summaries)})" + (f" {page}/{pages}" if pages > 1 else "")
    right = [Text(label + " ") + status]
    active = detail["name"] if detail and not summary_only else None
    for row in visible:
        state = Text(row["status"].upper(), style=COLORS[row["status"]])
        success = row.get("last_success_at")
        failures = _safe(row.get("consecutive_failures"))
        extra = " last " + (_relative(success, data.generated_at).removesuffix(" ago") if success else "—") + " fail " + failures
        if right_width >= state.cell_len + len(extra) + 16:
            state += Text(extra)
        name_width = max(1, right_width - state.cell_len - 5)
        name = _clip(row["name"], name_width)
        name.pad_right(max(0, name_width - name.cell_len))
        right.append(Text("▸ " if row["name"] == active else "  ") + name + Text("  ") + state)
    right.extend(Text("") for _ in range(4 - len(right)))
    return [_join(a, b, left_width, right_width) for a, b in zip(left, right)], detail


def _join(left, right, left_width, right_width):
    left = _clip(left, left_width)
    right = _clip(right, right_width)
    left.pad_right(max(0, left_width - left.cell_len))
    right.pad_right(max(0, right_width - right.cell_len))
    return left + Text(" │ ") + right


class _Column:
    def __init__(self, width):
        self.width = width
        self.lines = []
        self.console = Console(width=width, height=25)
        self.label_width = min(14, max(1, width // 3))
        self.value_width = max(1, width - self.label_width - 3)

    def add(self, value=""):
        value = value.copy() if isinstance(value, Text) else Text(_safe(value))
        self.lines.extend(value.wrap(self.console, self.width) or [Text("")])

    def section(self, title):
        if self.lines:
            self.add("")
        self.add(title)

    def row(self, label, value):
        label = _clip(label, self.label_width)
        label.pad_right(self.label_width - label.cell_len)
        prefix = Text("  ") + label + Text(" ")
        value = value.copy() if isinstance(value, Text) else Text(_safe(value))
        for index, line in enumerate(value.wrap(self.console, self.value_width) or [Text("")]):
            self.lines.append((prefix if index == 0 else Text(" " * prefix.cell_len)) + line)

    def time(self, label, value, now):
        self.row(label, _time(value))
        if value:
            self.row("", "(" + _relative(value, now) + ")")


def _logger_column(device, now, width, omitted, bar_width, total_width, layout):
    col = _Column(width)
    timeline, collection, storage = device["timeline"], device["collection"], device["storage"]
    capacity = timeline_capacity(total_width, layout)
    view = timeline_view(timeline, capacity)
    visible = view["events"]
    snapshots = sum(event["type"] == "snapshot" for event in visible)
    label = f"TIMELINE  {snapshots} snapshots"
    if view["total"] > view["shown"]:
        label = f"TIMELINE  last {view['shown']} of {view['total']} snapshots"
    next_slot = timeline["next_slot_at"]
    label += "  next " + (datetime.fromisoformat(next_slot).strftime("%H:%M") if next_slot else "—")
    col.section(label)
    col.row("RANGE", _time(visible[0]["at"]) + " → " + _time(visible[-1]["at"]) if visible else "—")
    col.row("EVENTS", "[" + timeline_symbols(timeline, capacity) + "]")
    col.row("LEGEND", LEGEND)
    col.section("COLLECTION")
    for error in device["errors"]:
        col.add(Text(error, style=COLORS["unreadable"]))
    col.time("LAST SUCCESS", collection["last_success_at"], now)
    col.time("NEXT SLOT", device["schedule"]["next_slot_at"], now)
    if "last_slot" not in omitted:
        col.row("LAST SLOT", _time(device["schedule"]["last_slot_at"]))
    col.row("SCHEDULE", device["schedule"]["expression"])
    duration = collection["last_duration_seconds"]
    col.row("DURATION", _duration(duration) if duration is not None and duration >= 60 else _float(duration, " s"))
    col.row("FAIL STREAK", collection["consecutive_failures"])
    col.section("STORAGE")
    count, limit = storage["file_count"], storage["max_files"]
    files = _safe(count) + (f" / {limit}" if limit else " (no limit)")
    if storage["rotation_active"]:
        files += " · rotation"
    col.row("FILES", files)
    bar_width = max(1, min(bar_width, col.value_width - 15))
    if limit and count is not None:
        percent = count / limit * 100
        col.row("", _bar(percent, bar_width) + f" {percent:.0f} %")
    col.row("SIZE", _size(storage["total_size_bytes"]))
    col.row("OLDEST", _time(storage["oldest_file_at"]))
    col.row("NEWEST", _time(storage["newest_file_at"]))
    disk = storage["disk"]
    if disk:
        used = disk["used_bytes"]
        percent = used / disk["total_bytes"] * 100
        col.row("DISK", f"{_size(used)} used of {_size(disk['total_bytes'])}")
        col.row("", Text(_bar(percent, bar_width) + f" {percent:.0f} % ") + _status(disk["status"]))
    else:
        col.row("DISK", _status("unknown"))
    if storage["error"]:
        col.row("STORAGE ERROR", storage["error"])
    return col.lines


def _node_column(device, now, width, omitted, bar_width):
    col = _Column(width)
    mesh, connection = device["mesh"], device["connection"]
    col.add(Text("NODE · " + _safe(device["name"]) + "  ") + _status(device["status"]))
    col.section("DEVICE")
    col.row("NODE ID", mesh["node_id"])
    col.row("MYNODENUM", mesh["my_node_num"])
    col.row("CONNECTION", f"{connection['type']} · {connection['address']}")
    col.row("ROLE", mesh["role"])
    col.row("HW MODEL", mesh["hw_model"])
    firmware = _safe(mesh["firmware_version"])
    if mesh["firmware_edition"] is not None:
        firmware += " · " + _safe(mesh["firmware_edition"])
    col.row("FIRMWARE", firmware)
    radios = mesh["radios"]
    radio_names = [f"[{name}]" for name, value in radios.items() if value is True]
    absent = all(value is False for value in radios.values())
    col.row("RADIOS", " ".join(radio_names) if radio_names else "none" if absent else None)
    if "position" not in omitted:
        position = mesh["position"]
        if position:
            def coordinate(key, positive, negative):
                value = position[key]
                return f"{abs(value):.4f}°{positive if value >= 0 else negative}" if value is not None else "—"
            value = coordinate("latitude", "N", "S") + " " + coordinate("longitude", "E", "W")
            if position["altitude"] is not None:
                value += " · " + _float(position["altitude"], " m", trim=True)
            col.row("POSITION", value)
        else:
            col.row("POSITION", None)
    col.section("POWER")
    power = device["power"]
    battery, voltage = power["battery_level"], power["voltage"]
    col.row("BATTERY", _float(battery, " %", trim=True) + " · " + _float(voltage, " V"))
    if battery is not None:
        col.row("", _bar(battery, 50))
    col.add("")
    col.row("", "[ " + _sparkline(power["samples"], 48, now=now) + " ]")
    col.row("", "min " + _float(power["battery_min"], "%", trim=True)
            + " · max " + _float(power["battery_max"], "%", trim=True))
    col.row("VOLTAGE", _float(voltage, " V") + "  (min " + _float(power["voltage_min"], " V")
            + " · max " + _float(power["voltage_max"], " V") + ")")
    col.section("RADIO")
    number_width = max(len(_float(mesh[key])) for key in ("channel_utilization", "air_util_tx"))
    col.row("CHANNEL", _radio(mesh["channel_utilization"], True, number_width))
    col.row("AIR TX", _radio(mesh["air_util_tx"], False, number_width))
    col.section("MESH")
    if mesh["snapshot_status"] != "ok":
        col.row("SNAPSHOT", Text("Snapshot unreadable" if mesh["snapshot_status"] == "unreadable" else "missing",
                                 style=COLORS["unreadable"] if mesh["snapshot_status"] == "unreadable" else COLORS["unknown"]))
    if mesh["snapshot_error"]:
        col.row("SNAP ERROR", redact_secrets(mesh["snapshot_error"]))
    col.row("NODES", _safe(mesh["nodedb_count"]) + (" known" if mesh["nodedb_count"] is not None else ""))
    col.row("REBOOTS", mesh["reboot_count"])
    col.row("UPTIME", _duration(mesh["uptime_seconds"]))
    if "last_heard" not in omitted:
        col.row("LAST HEARD", _relative(mesh["last_heard_at"], now))
    return col.lines


def _body(device, data, width, split, omitted, bar_width, layout):
    if split:
        left_width = (width - 3) // 2
        right_width = width - 3 - left_width
        left = _logger_column(device, data.generated_at, left_width, omitted, bar_width, width + 4, layout)
        right = _node_column(device, data.generated_at, right_width - 1, omitted, bar_width)
        return [_join(left[i] if i < len(left) else "", right[i] if i < len(right) else "", left_width, right_width)
                for i in range(max(len(left), len(right)))]
    return (_logger_column(device, data.generated_at, width, omitted, bar_width, width + 4, layout) + [Text("")]
            + _node_column(device, data.generated_at, width, omitted, bar_width))


def render_rich(data, width=120, page=1, summary_only=False, layout="auto", height=None):
    if layout not in ("auto", "single", "split"):
        raise ValueError("Layout must be auto, single or split")
    inner = max(1, width - 4)
    header, device = _header(data, inner, page, summary_only)
    content = list(header)
    if not summary_only:
        content.append(Text("═" * inner))
        if device is None:
            content.append(Text("No enabled devices" if not any(row["enabled"] for row in (data.device_summary or data.devices))
                                else "No enabled devices on this page"))
        else:
            split = layout == "split" or (layout == "auto" and width >= 120)
            bar_width = 40 if width >= 140 else 16 if width < 100 else 20
            omitted = set()
            body = _body(device, data, inner, split, omitted, bar_width, layout)
            if height is not None:
                for optional in ("last_slot", "position", "last_heard"):
                    if len(content) + len(body) + 2 <= height:
                        break
                    omitted.add(optional)
                    body = _body(device, data, inner, split, omitted, bar_width, layout)
            content.extend(body)
    frame = [Text("╔" + "═" * (width - 2) + "╗")]
    for index, line in enumerate(content):
        if index == 4 and not summary_only:
            frame.append(Text("╠" + "═" * (width - 2) + "╣"))
            continue
        line = _clip(line, inner)
        line.pad_right(max(0, inner - line.cell_len))
        frame.append(Text("║ ") + line + Text(" ║"))
    frame.append(Text("╚" + "═" * (width - 2) + "╝"))
    return Group(*frame)


def render_text(data, width=120, page=1, summary_only=False, layout="auto"):
    summaries, visible, pages, device = device_page(data, page)
    lines = [f"MESHTASTIC LOGGER · {data.overall_status.upper()} · {_time(data.generated_at.isoformat())}",
             "TZ " + _safe(data.timezone)]
    lines += [f"DEVICES ({len(summaries)}) · page {page}/{pages}"]
    active = device["name"] if device and not summary_only else None
    lines += [("▸ " if row["name"] == active else "  ") + _safe(row["name"]) + " " + row["status"].upper() for row in visible]
    if not summary_only:
        if device is None:
            lines.append("No enabled devices" if not any(row["enabled"] for row in summaries) else "No enabled devices on this page")
        else:
            # Fallback stays single-column and keeps all fields; pipes never hide
            # optional information based on an unrelated terminal's height.
            bar_width = 40 if width >= 140 else 16 if width < 100 else 20
            lines += [text.plain.replace("═", "").rstrip()
                      for text in _body(device, data, max(60, width), False, set(), bar_width, "single")]
    return "\n".join(part for line in lines for part in
                     (textwrap.wrap(_safe(line), width=max(1, width), subsequent_indent="  " if width >= 4 else "") or [""]))
