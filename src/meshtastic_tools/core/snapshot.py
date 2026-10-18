"""Read the latest --info snapshot without contacting a device or changing storage."""

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class Snapshot:
    snapshot_status: str = "ok"
    snapshot_error: Optional[str] = None
    my_node_num: Optional[int] = None
    node_id: Optional[str] = None
    long_name: Optional[str] = None
    short_name: Optional[str] = None
    nodedb_count: Optional[int] = None
    reboot_count: Optional[int] = None
    battery_level: Optional[float] = None
    voltage: Optional[float] = None
    channel_utilization: Optional[float] = None
    air_util_tx: Optional[float] = None
    uptime_seconds: Optional[int] = None
    firmware_version: Optional[str] = None
    firmware_edition: Optional[str] = None
    role: Optional[str] = None
    hw_model: Optional[str] = None
    radios: dict = field(default_factory=lambda: {"wifi": None, "bluetooth": None, "ethernet": None})
    position: Optional[dict] = None
    last_heard_at: Optional[datetime] = None


def read_snapshot(path: Path) -> str:
    """FileNotFoundError is handled by the caller as a possible rotation race."""
    return path.read_text(encoding="utf-8")


_SECTIONS = re.compile(
    r"^(My info|Metadata|Nodes in mesh|Preferences|Module preferences|Config):[ \t]*",
    re.MULTILINE,
)


def parse_snapshot(text: str) -> Snapshot:
    """Parse labelled CLI JSON sections or a JSON object; never search remote metrics.

    Broken sections do not discard independently decoded sections. Diagnostic text
    contains only fixed messages, never raw JSON that could contain credentials.
    """
    result = Snapshot()
    errors = []
    decoder = json.JSONDecoder()
    sections = {}
    broken_nodes = None

    def problem(message):
        if message not in errors:
            errors.append(message)

    if text.lstrip().startswith(("{", "[")):
        try:
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError
            sections = {
                "My info": value.get("myInfo", value),
                "Metadata": value.get("metadata", {}),
                "Nodes in mesh": value.get("nodes", {}),
            }
        except ValueError:
            problem("Invalid snapshot JSON")
    else:
        matches = list(_SECTIONS.finditer(text))
        for index, match in enumerate(matches):
            label = match.group(1)
            chunk = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
            try:
                value, _ = decoder.raw_decode(chunk.lstrip())
                if not isinstance(value, dict):
                    raise ValueError
                if label in sections:
                    problem("Duplicate snapshot section")
                sections[label] = value
            except ValueError:
                problem(f"Invalid {label} section")
                if label == "Nodes in mesh":
                    broken_nodes = chunk

    def mapping(value, label):
        if not isinstance(value, dict):
            problem(f"Invalid {label}")
            return {}
        return value

    def number(value, label, integer=False, signed=False):
        if value is None:
            return None
        valid = type(value) is int if integer else type(value) in (int, float)
        if not valid or (value < 0 and not signed) or (type(value) is float and not math.isfinite(value)):
            problem(f"Invalid {label}")
            return None
        return value

    def string(value, label):
        if value is None:
            return None
        if not isinstance(value, str):
            problem(f"Invalid {label}")
            return None
        return value

    info = mapping(sections.get("My info", {}), "My info")
    result.my_node_num = number(info.get("myNodeNum"), "myNodeNum", integer=True)
    if result.my_node_num is None or result.my_node_num > 0xffffffff:
        result.my_node_num = None
        problem("Missing or invalid myNodeNum")
    else:
        result.node_id = f"!{result.my_node_num:08x}"

    result.nodedb_count = number(info.get("nodedbCount"), "nodedbCount", integer=True)
    result.reboot_count = number(info.get("rebootCount"), "rebootCount", integer=True)
    metadata = mapping(sections.get("Metadata", {}), "Metadata")
    result.firmware_version = string(metadata.get("firmwareVersion"), "firmwareVersion")
    result.firmware_edition = string(info.get("firmwareEdition"), "firmwareEdition")
    for name, key in (("wifi", "hasWifi"), ("bluetooth", "hasBluetooth"), ("ethernet", "hasEthernet")):
        value = metadata.get(key)
        if value is not None and type(value) is not bool:
            problem(f"Invalid {key}")
        else:
            result.radios[name] = value
    nodes = mapping(sections.get("Nodes in mesh", {}), "Nodes in mesh")
    local = {}
    if result.node_id is not None:
        if result.node_id in nodes:
            local = mapping(nodes[result.node_id], "local node")
        else:
            for node in nodes.values():
                if isinstance(node, dict) and type(node.get("num")) is int and node["num"] == result.my_node_num:
                    local = node
                    break
        # A complete local record may precede a truncated remote record. Recover
        # only that exact, independently valid JSON object, never partial fields.
        if broken_nodes is not None:
            match = re.search(r'^\s*"' + re.escape(result.node_id) + r'"\s*:\s*', broken_nodes, re.MULTILINE)
            if match:
                try:
                    value, _ = decoder.raw_decode(broken_nodes[match.end():])
                    local = mapping(value, "local node")
                except ValueError:
                    pass
        if "num" in local and (type(local["num"]) is not int or local["num"] != result.my_node_num):
            problem("Local node number mismatch")
            local = {}

    user = mapping(local.get("user", {}), "local user")
    result.role = string(metadata.get("role", user.get("role")), "role")
    result.hw_model = string(metadata.get("hwModel", user.get("hwModel")), "hwModel")
    result.long_name = string(user.get("longName"), "longName")
    result.short_name = string(user.get("shortName"), "shortName")
    owner = re.search(r"^Owner:[ \t]*(.*?)[ \t]*\((.*?)\)[ \t]*$", text, re.MULTILINE)
    if owner:
        if result.long_name is None:
            result.long_name = owner.group(1)
        if result.short_name is None:
            result.short_name = owner.group(2)
    metrics = mapping(local.get("deviceMetrics", {}), "local deviceMetrics")
    for field, key in (("battery_level", "batteryLevel"), ("voltage", "voltage"),
                       ("channel_utilization", "channelUtilization"), ("air_util_tx", "airUtilTx"),
                       ("uptime_seconds", "uptimeSeconds")):
        setattr(result, field, number(metrics.get(key), key, integer=field == "uptime_seconds"))

    heard = number(local.get("lastHeard"), "lastHeard")
    if heard is not None:
        try:
            result.last_heard_at = datetime.fromtimestamp(heard, timezone.utc)
        except (ValueError, OSError, OverflowError):
            problem("Invalid lastHeard")
    position = mapping(local.get("position", {}), "local position")
    coordinates = {}
    for name, integer_key, limit in (("latitude", "latitudeI", 90), ("longitude", "longitudeI", 180)):
        value = number(position.get(name), name, signed=True)
        if value is None and name not in position and integer_key in position:
            raw = number(position[integer_key], integer_key, integer=True, signed=True)
            value = raw / 10000000 if raw is not None else None
        if value is not None and not -limit <= value <= limit:
            problem(f"Invalid {name}")
            value = None
        coordinates[name] = value
    altitude = number(position.get("altitude"), "altitude", signed=True)
    if any(value is not None for value in coordinates.values()) or altitude is not None:
        result.position = {**coordinates, "altitude": altitude}

    if errors:
        result.snapshot_status = "unreadable"
        result.snapshot_error = "; ".join(errors)
    return result
