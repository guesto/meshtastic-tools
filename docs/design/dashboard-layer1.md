# Layer 1: Logger Dashboard

## Status
Approved — ready for implementation.

## Goal

Add the command `meshtastic-tools dashboard` that displays the
current state of the logger in pseudo-graphic form — in a "cockpit
instrument" style: dense layout, aligned columns, compact timeline,
and minimal color. The command works **without contacting the device**
and **without side effects on disk** when reading.

General UI principles (formats, colors, conventions) are in
`ui-guide.md`. This document covers both the contract and the visual
layout of Layer 1.

## Background

The project already provides:

- `core/health.py` — stores the last collection result per device in
  `.status.json` (atomic writes, `0700` permissions, credential
  redaction).
- `logger/scheduler.py` — knows the last and next cron slot, keeps
  `.schedule.json`, supports locks and checkpoints.
- `logger/storage.py` — computes file statistics, knows limits and
  rotation.
- `logger/cli.py` — command `logger status` with plain text output.

What is missing is a single visual command that shows the overall
health of the logger at a glance, plus a small event history that
makes the timeline meaningful.

## Scope

In scope:

- Command `dashboard` with static output (one snapshot of state).
- Two-column layout: left — logger, right — node.
- Top bar with the tool title, timezone, and device summary.
- Pseudo-graphic panel with sections: timeline, collection, storage,
  device, radio, mesh.
- **Event history** `.timeline.json` per device: `snapshot`, `missing`,
  `error`. Written by the logger on each scheduled or manual collection.
- Parameters: `--device`, `--json`, `--no-color`, `--width`, `--page`,
  `--all`, `--layout`.
- Plain-text fallback (for pipes and narrow terminals).
- Adaptive layout by terminal width and height.
- Tests: timeline history, snapshot parser, renderer, edge cases.

Out of scope:

- Live mode (`--watch`) — Layer 2.
- Sparklines, historical charts, trends — Layer 3.
- Long-term collection journal (beyond the `.timeline.json` ring) —
  Layer 3.
- Metrics cache — Layer 3.
- Web UI, interactive navigation — out of scope.
- Export to CSV/SQLite/parquet — separate tool `analyzer` (Layer 4).
- Contacting the device from `dashboard` — out of scope.
- Side effects on disk from `dashboard` (creating directories, changing
  permissions) — out of scope. The dashboard is a pure reader.

## Data Sources (read-only for dashboard)

| Source | What we take |
|--------|--------------|
| `<device>/info/.status.json` | Last attempt, outcome, duration, file, `consecutive_failures`, last error |
| `<device>/info/.schedule.json` | Last cron slot; next slot calculated from current config schedule |
| `<device>/info/.timeline.json` | Event history: `snapshot`, `missing`, `error` |
| `<device>/info/info_*.txt` | Last snapshot — metrics of the local node only |
| Directory listing of `<device>/info/` | For storage statistics |

Snapshots are selected by `filename_format` from the config, not by
"any `.txt`". The same directory contains snapshots of other collectors
(`info_`, `telemetry_`, `nodes_`).

`.timeline.json` is per device, per collector. For the `info` collector
it lives at `<device>/info/.timeline.json`. It is written by the logger
on every scheduled or manual collection, and read (only) by the
dashboard.

## Event History (`.timeline.json`)

Schema:

```json
{
  "version": 1,
  "events": [
    { "type": "snapshot", "at": "2026-10-16T21:45:02+05:00" },
    { "type": "snapshot", "at": "2026-10-16T21:50:02+05:00" },
    { "type": "missing",  "at": "2026-10-16T21:55:02+05:00" },
    { "type": "error",    "at": "2026-10-16T22:00:02+05:00" },
    { "type": "snapshot", "at": "2026-10-16T22:05:02+05:00" }
  ]
}
```

Rules:

- One file per device, one file per collector.
- Atomic write via `NamedTemporaryFile` + `os.replace`, file mode `0600`.
- Size limit: same as `max_files` from the storage config for this
  device. When `max_files` is null, use 1000. When `max_files` is 0,
  do not write history at all.
- On overflow, the oldest events are dropped.
- If the file is corrupt or missing, it is silently restarted. It does
  not raise `UNREADABLE` and does not affect the device status.
- Event types:
  - `snapshot` — a snapshot file was successfully saved.
  - `missing` — a scheduled slot passed without an attempt (server was
    off, timer did not fire, etc.).
  - `error` — an attempt was made but failed (connection error,
    timeout, parse failure).

### Where events are written

- `logger/collector.py`:
  - On successful `collect_info()` → `append("snapshot", now)`.
  - On `CollectionError` → `append("error", now)`.
- `logger/scheduler.py`:
  - Before collection, if missed slots are detected →
    `append("missing", slot_time)` for each missed slot, in
    chronological order. Capped at `max_files` events.
- Manual collection (`logger collect info`) also writes events, so
  manual snapshots are not lost.

## Layout overview

Target width: **120 columns**. Split 60 / 60 between two columns. The
top bar spans the full width and is exactly **4 lines tall**.

```
╔══════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║ MESHTASTIC LOGGER                                  ● OK    20:43:43  │  DEVICES (1)              ● OK    ║
║ TZ Asia/Yekaterinburg                                                │  ▸ home_station          OK       ║
║                                                                      │                                   ║
║                                                                      │                                   ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                    │                                                  ║
║ TIMELINE  ·  12 snapshots · next 22:10             │ NODE · home_station                    ● OK       ║
║   2026-10-16 20:45:02 → 2026-10-16 22:05:02        │                                                  ║
║   █████░!████░░███████!██··················        │ DEVICE                                           ║
║   █ snapshot  ░ missing  ! error  · empty          │   NODE ID      !1ba59c50                         ║
║                                                    │   MYNODENUM    463838288                         ║
║ COLLECTION                                         │   CONNECTION   host · 192.168.10.16              ║
║   LAST SUCCESS  2026-10-16 22:05:02                │   ROLE         CLIENT                            ║
║                 (3 m 33 s ago)                     │   HW MODEL     HELTEC_V4                        ║
║   NEXT SLOT     2026-10-16 22:10:00                │   FIRMWARE     2.7.22.96dd647 · VANILLA          ║
║                 (in 1 m 23 s)                      │   RADIOS       [wifi] [bluetooth]                ║
║   LAST SLOT     2026-10-16 22:05:00                │   POSITION     56.8536°N 60.6304°E · 272 m       ║
║   SCHEDULE      */5 * * * *                        │   BATTERY      96 % · 4.14 V                     ║
║   DURATION      1.64 s                             │                [█████████████████████░]         ║
║   FAIL STREAK   0                                  │                                                  ║
║                                                    │ RADIO                                            ║
║ STORAGE                                            │   CHANNEL      12.24 %    ● low                  ║
║   FILES        31 / 50                             │   AIR TX        0.60 %    ● low                  ║
║                [████████████░░░░░░░░░]  62 %       │                                                  ║
║   SIZE         4.6 MB                              │ MESH                                             ║
║   OLDEST       2026-10-16 16:29:20                 │   NODES        250 known                         ║
║   NEWEST       2026-10-16 22:05:02                 │   REBOOTS      66                                ║
║   DISK         51.8 GB used of 270.3 GB            │   UPTIME       3 h 16 m                          ║
║                [█████░░░░░░░░░░░░░░░]  19 %  ● OK  │   LAST HEARD   1 d 5 h ago                       ║
║                                                    │                                                  ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════════╝
```

## Top bar

Four lines tall, full width, split at column 60 with a `│` separator.

Left side:

- Line 1: `MESHTASTIC LOGGER`, overall status `● OK`, current time.
- Line 2: `TZ <timezone>`.
- Lines 3–4: reserved, kept empty for stable height.

Right side:

- Line 1: `DEVICES (N)` and aggregate status.
- Lines 2–4: up to three devices.

```
║ MESHTASTIC LOGGER                                  ● OK    20:43:43  │  DEVICES (3)              ● OK    ║
║ TZ Asia/Yekaterinburg                                                │  ▸ home_station          OK       ║
║                                                                      │    garage_node           OK       ║
║                                                                      │    attic_repeater      STALE      ║
```

The config source path is not shown in the UI. It remains available
via `logger config-show` and in the JSON output.

## Device summary

The right side of the top bar shows up to three devices.

Format per line:

```
  ▸ <name>  <status>  last <relative time>  fail <N>
```

Example:

```
  ▸ home_station          OK     last 3 m 41 s  fail 0
    garage_node           OK     last 2 m 12 s  fail 0
    attic_repeater      STALE    last 47 m 08 s  fail 0
```

Rules:

- Active device (whose details are shown on the right column) is marked
  with `▸` and rendered in bold.
- Status is a short label: `OK`, `STALE`, `ERROR`, `UNREADABLE`,
  `UNKNOWN`, `DISABLED`.
- Sort order:
  1. By status, priority `UNREADABLE > ERROR > STALE > UNKNOWN > OK >
     DISABLED`.
  2. Then alphabetically by name.
- When more than three devices exist, show `1/N` next to `DEVICES (N)`
  and paginate via `--page N`. Statuses of hidden devices still
  contribute to the aggregate status.
- Always show the device count and summary in the upper-right area,
  including when only one device exists. Show at most three entries
  per page and mark the selected device with `▸`.

## Left column (LOGGER)

Sections in order:

1. `TIMELINE`
2. `COLLECTION`
3. `STORAGE`

### TIMELINE

```
TIMELINE  ·  12 snapshots · next 22:10
  2026-10-16 20:45:02 → 2026-10-16 22:05:02
  █████░!████░░███████!██··················
  █ snapshot  ░ missing  ! error  · empty
```

Logic:

- One cell = one event from the timeline history.
- History is read from `<device>/info/.timeline.json`.
- Only the **last N events** are shown, where N is the available width
  of the timeline in characters.
- If there are fewer events than cells, events are left-aligned, and the
  remaining cells are `·`.
- If there are more events than cells, only the most recent N are shown;
  the header reports `last N of M snapshots`.
- No time-based scaling, no interval computation, no cron parsing for
  the timeline itself.

Characters:

| Char | Meaning |
|------|---------|
| `█` | Snapshot (`type: "snapshot"`) |
| `░` | Missed slot (`type: "missing"`) |
| `!` | Failed attempt (`type: "error"`) |
| `·` | Empty cell (nothing yet) |

Header format:

- Line 1: `TIMELINE  ·  <N> snapshots · next <HH:MM>` where `<N>` is
  the number of snapshot events currently shown, and `next` is the next
  cron slot from the schedule.
- Line 2: time range from the first to the last event in the view.
- Line 3: cells.
- Line 4: legend.

When truncated:

```
TIMELINE  ·  last 40 of 200 snapshots · next 22:10
```

### COLLECTION

```
COLLECTION
  LAST SUCCESS  2026-10-16 22:05:02
                (3 m 33 s ago)
  NEXT SLOT     2026-10-16 22:10:00
                (in 1 m 23 s)
  LAST SLOT     2026-10-16 22:05:00
  SCHEDULE      */5 * * * *
  DURATION      1.64 s
  FAIL STREAK   0
```

- `LAST SUCCESS` — timestamp and relative time on a second line.
- `NEXT SLOT` — next scheduled slot from the current config schedule.
- `LAST SLOT` — the last cron slot that has already passed.
- `SCHEDULE` — cron expression.
- `DURATION` — duration of the last successful attempt.
- `FAIL STREAK` — `consecutive_failures` from `.status.json`.

If `.schedule.json` is corrupt, show `NEXT SLOT —` and mark the device
as `UNREADABLE`.

The last error is not displayed in this section in Layer 1. It is
available via `logger status` and in the JSON output.

### STORAGE

```
STORAGE
  FILES        31 / 50
               [████████████░░░░░░░░░]  62 %
  SIZE         4.6 MB
  OLDEST       2026-10-16 16:29:20
  NEWEST       2026-10-16 22:05:02
  DISK         51.8 GB used of 270.3 GB
               [█████░░░░░░░░░░░░░░░]  19 %  ● OK
```

- `FILES` — count and limit. Bar reflects used share of the limit. If
  `max_files` is null, show `31 (no limit)` and skip the bar.
- `SIZE` — total size of snapshots.
- `OLDEST` / `NEWEST` — timestamps of the oldest and newest snapshots.
- `DISK` — used space out of total. Bar reflects used space. Disk
  status indicator is appended after the bar.

## Right column (NODE)

Sections in order:

1. Header line: `NODE · <name>` and device status.
2. `DEVICE`
3. `RADIO`
4. `MESH`

### DEVICE

```
DEVICE
  NODE ID      !1ba59c50
  MYNODENUM    463838288
  CONNECTION   host · 192.168.10.16
  ROLE         CLIENT
  HW MODEL     HELTEC_V4
  FIRMWARE     2.7.22.96dd647 · VANILLA
  RADIOS       [wifi] [bluetooth]
  POSITION     56.8536°N 60.6304°E · 272 m
  BATTERY      96 % · 4.14 V
               [█████████████████████░]
```

- `RADIOS` shows capabilities as bracketed tags: `[wifi]`,
  `[bluetooth]`, `[ethernet]`. Only true capabilities are shown.
- `POSITION` shows latitude, longitude, altitude. Use decimal degrees.
  If position is missing, show `—`.
- `BATTERY` shows level and voltage above the bar, bar below. No
  thresholds; the numeric value is authoritative.

### RADIO

```
RADIO
  CHANNEL      12.24 %    ● low
  AIR TX        0.60 %    ● low
```

Severity thresholds:

CHANNEL (channel utilization):

| Range | Indicator | Color | Label |
|-------|-----------|-------|-------|
| `< 25 %` | `●` | green | `low` |
| `25–50 %` | `●` | yellow | `medium` |
| `> 50 %` | `●` | red | `high` |

AIR TX:

| Range | Indicator | Color | Label |
|-------|-----------|-------|-------|
| `< 2 %` | `●` | green | `low` |
| `2–5 %` | `●` | yellow | `medium` |
| `> 5 %` | `●` | red | `high` |

If a metric is missing: `—    ○ unknown` with a gray empty circle.

### MESH

```
MESH
  NODES        250 known
  REBOOTS      66
  UPTIME       3 h 16 m
  LAST HEARD   1 d 5 h ago
```

- `NODES` — `nodedbCount`, number of known nodes.
- `REBOOTS` — `rebootCount`, absolute counter.
- `UPTIME` — `uptimeSeconds`, human-readable.
- `LAST HEARD` — relative time since the local node last heard itself
  in the mesh.

If the latest snapshot is `unreadable`, show `Snapshot unreadable` on
the first line and available metrics below. Do not substitute an older
snapshot.

If the latest snapshot is `missing` (no snapshots at all), show
`Snapshot missing` and `—` for all metrics.

## Disk status

Disk status is independent of the overall logger status. It is shown
inside the STORAGE section, after the disk bar.

| Free space | Status |
|------------|--------|
| `> 20 %` | `● OK` green |
| `10–20 %` | `● WARNING` yellow |
| `< 10 %` | `● CRITICAL` red |
| unknown | `○ UNKNOWN` gray |

The disk status does not affect the overall status in the top bar.

## Device statuses

Overall status priority:

```
UNREADABLE > ERROR > STALE > UNKNOWN > OK > DISABLED
```

If all devices are `DISABLED`, show `DISABLED`.

Device statuses:

- `OK` — green
- `STALE` — yellow
- `ERROR` — red
- `UNREADABLE` — red
- `UNKNOWN` — gray
- `DISABLED` — gray

`UNREADABLE` is assigned when `.status.json` or `.schedule.json` is
corrupt (invalid JSON, wrong structure, missing required fields).

Snapshot status is separate: `ok` / `missing` / `unreadable`. It
affects the `MESH` section only and does not change the device status.
If the latest snapshot cannot be parsed, show `Snapshot unreadable` and
the available metrics; do not substitute an older snapshot.

Timeline history corruption (`.timeline.json`) is not treated as
`UNREADABLE`; the history is silently restarted.

## Interface

### CLI

```
dashboard
  [--device NAME]
  [--page N]
  [--json]
  [--no-color]
  [--width N]
  [--all]
  [--layout auto|single|split]
```

- `--device NAME` — detail view for one device.
- `--page N` — page of the device summary in the top bar.
- `--json` — machine-readable output.
- `--no-color` — disable colors.
- `--width N` — force terminal width (for tests and documentation).
- `--all` — show summary only, no detail panel.
- `--layout auto|single|split` — force layout mode.

### JSON schema

```json
{
  "schema_version": 1,
  "generated_at": "2026-10-16T22:08:36+05:00",
  "timezone": "Asia/Yekaterinburg",
  "overall_status": "ok",
  "config": {
    "source": "./config/meshtastic-tools.yaml"
  },
  "devices": [
    {
      "name": "home_station",
      "status": "ok",
      "enabled": true,
      "connection": {
        "type": "host",
        "address": "192.168.10.16"
      },
      "collection": {
        "last_success_at": "2026-10-16T22:05:02+05:00",
        "last_attempt_at": "2026-10-16T22:05:02+05:00",
        "last_finished_at": "2026-10-16T22:05:04+05:00",
        "last_outcome": "success",
        "last_duration_seconds": 1.64,
        "consecutive_failures": 0,
        "last_error": null,
        "last_failure_at": null,
        "last_file": "info_home_station_2026-10-16_22-05-02.txt",
        "source": "scheduled"
      },
      "schedule": {
        "expression": "*/5 * * * *",
        "next_slot_at": "2026-10-16T22:10:00+05:00",
        "last_slot_at": "2026-10-16T22:05:00+05:00"
      },
      "timeline": {
        "events": [
          { "type": "snapshot", "at": "2026-10-16T21:45:02+05:00" },
          { "type": "snapshot", "at": "2026-10-16T21:50:02+05:00" },
          { "type": "missing",  "at": "2026-10-16T21:55:02+05:00" },
          { "type": "error",    "at": "2026-10-16T22:00:02+05:00" },
          { "type": "snapshot", "at": "2026-10-16T22:05:02+05:00" }
        ],
        "shown": 5,
        "total": 5,
        "next_slot_at": "2026-10-16T22:10:00+05:00"
      },
      "storage": {
        "file_count": 31,
        "max_files": 50,
        "total_size_bytes": 4823449,
        "oldest_file_at": "2026-10-16T16:29:20+05:00",
        "newest_file_at": "2026-10-16T22:05:02+05:00",
        "disk": {
          "total_bytes": 290279424000,
          "used_bytes": 55616766720,
          "used_percent": 19.16,
          "status": "ok"
        }
      },
      "mesh": {
        "snapshot_status": "ok",
        "snapshot_error": null,
        "my_node_num": 463838288,
        "node_id": "!1ba59c50",
        "long_name": "Meshtastic 9c50",
        "short_name": "9c50",
        "role": "CLIENT",
        "hw_model": "HELTEC_V4",
        "firmware_version": "2.7.22.96dd647",
        "firmware_edition": "VANILLA",
        "radios": { "wifi": true, "bluetooth": true, "ethernet": false },
        "nodedb_count": 250,
        "reboot_count": 66,
        "battery_level": 96,
        "voltage": 4.14,
        "channel_utilization": 12.24,
        "air_util_tx": 0.6,
        "uptime_seconds": 11760,
        "last_heard_at": "2026-10-15T17:08:36+05:00",
        "position": {
          "latitude": 56.8536,
          "longitude": 60.6304,
          "altitude": 272
        }
      }
    }
  ]
}
```

JSON rules:

- All timestamps are ISO 8601 with offset.
- Missing values are `null`, not empty strings.
- Numbers are numbers, not strings.
- `schema_version` is a constant `1`.
- `timeline.events` contains the last N events shown in the timeline.
  `timeline.shown` and `timeline.total` report truncation.
- `config.source` is present in JSON but not shown in the UI.
- `collection.last_error` is present in JSON but not shown in the UI in
  Layer 1.

## Architecture

The root CLI registers two independent tools: `logger` for collection and
`dashboard` for read-only visualization. Both use shared modules in `core`.

Shared modules:

- `core/config.py`, `core/device.py`, `core/exceptions.py`,
  `core/logging_config.py` — configuration, device access, errors, and logging.
- `core/health.py` — collection records, shared health/freshness rules,
  and cron-slot calculation used by the logger scheduler.
- `core/privacy.py` — credential redaction.
- `core/timeline.py` — `TimelineEvent` and bounded `TimelineHistory`
  persistence for collection events.
- `core/snapshot.py` — `Snapshot`, pure `parse_snapshot(text)`, and
  `read_snapshot(path)` for saved device metrics.

Collection tool:

- `logger/cli.py` — collection, status, storage, schedule, and service commands.
- `logger/collector.py` — collect snapshots and append `snapshot` / `error` events.
- `logger/scheduler.py` — locks, checkpoints, and `missing` events for missed slots.
- `logger/storage.py`, `logger/services.py` — storage management and systemd units.

Visualization tool:

- `dashboard/__init__.py` — package marker.
- `dashboard/cli.py` — `dashboard_app`, invoked as `meshtastic-tools dashboard`.
- `dashboard/data.py` — `DashboardData`, read-only `collect_dashboard_data()`,
  `to_json()`, timeline helpers, and rendering entry points.
- `dashboard/render.py` — Rich and plain-text layouts, device pagination,
  and radio indicators.
- `cli.py` — registers `logger_app` and `dashboard_app` at the root level.

Principles:

- Dashboard has no side effects on disk and imports shared readers from `core`.
- Neither `core` nor `dashboard` depends on the `logger` package.
- All reads via `pathlib`, with `FileNotFoundError` handling (a file
  may be removed by rotation between listing and opening).
- Statuses and freshness come from the shared `core.health` logic used
  by `logger status`; do not duplicate it.
- Snapshot parser is a pure function, testable without a filesystem.
- Timeline history is append-only and bounded by `max_files`.

## Adaptive behavior

### By width

| Width | Layout |
|-------|--------|
| `>= 140` | Split layout, wide bars, wider timeline |
| `120–139` | Split layout, standard bars, standard timeline |
| `100–119` | Single column, sections stacked |
| `< 100` | Compact single column |

In split layout, the divider `│` is placed at the midpoint of the inner
width. In single-column layout, the divider is omitted and sections
stack vertically.

The timeline always fills the available width of its column minus the
two-space indent. There is no fixed cell count.

### By height

If the terminal is too short for the full layout, drop optional sections
in this order:

1. `LAST SLOT`
2. `POSITION`
3. `LAST HEARD`

Required sections are never dropped: `TIMELINE`, `COLLECTION`,
`STORAGE` on the left; `DEVICE`, `RADIO`, `MESH` on the right.

## Plain-text fallback

When output is redirected to a pipe, or when the terminal is too narrow
for the framed layout, the dashboard falls back to plain text.

```
MESHTASTIC LOGGER  OK  2026-10-16 22:08:36
TZ Asia/Yekaterinburg

DEVICES (1)  OK
  > home_station     OK     last 3 m 33 s  fail 0

TIMELINE  12 snapshots  next 22:10
  2026-10-16 20:45:02 - 2026-10-16 22:05:02
  █████░!████░░███████!██··················
  █ snapshot  ░ missing  ! error  · empty

COLLECTION
  LAST SUCCESS  2026-10-16 22:05:02  (3 m 33 s ago)
  NEXT SLOT     2026-10-16 22:10:00  (in 1 m 23 s)
  LAST SLOT     2026-10-16 22:05:00
  SCHEDULE      */5 * * * *
  DURATION      1.64 s
  FAIL STREAK   0

STORAGE
  FILES        31 / 50  (62 %)
  SIZE         4.6 MB
  OLDEST       2026-10-16 16:29:20
  NEWEST       2026-10-16 22:05:02
  DISK         51.8 GB used of 270.3 GB  (19 %)  OK

NODE · home_station  OK
  NODE ID      !1ba59c50
  MYNODENUM    463838288
  CONNECTION   host · 192.168.10.16
  ROLE         CLIENT
  HW MODEL     HELTEC_V4
  FIRMWARE     2.7.22.96dd647 · VANILLA
  RADIOS       [wifi] [bluetooth]
  POSITION     56.8536°N 60.6304°E · 272 m
  BATTERY      96 % · 4.14 V

RADIO
  CHANNEL      12.24 %  low
  AIR TX        0.60 %  low

MESH
  NODES        250 known
  REBOOTS      66
  UPTIME       3 h 16 m
  LAST HEARD   1 d 5 h ago
```

In plain-text mode:

- No box drawing characters.
- Progress bars are replaced by parenthesized percentages.
- Status indicators are replaced by their plain labels.
- Indentation of two spaces under section labels is preserved.

## Dependencies

- `rich` — added to `[project] dependencies` in `pyproject.toml`.

## Acceptance Criteria

1. `dashboard` runs on a Linux server without a GUI.
2. `dashboard` does not contact the device and does not create or
   modify files.
3. Handles missing `.status.json`, `.schedule.json`, `.timeline.json`,
   snapshots, and devices.
4. Correctly displays `OK / STALE / ERROR / DISABLED / UNKNOWN /
   UNREADABLE`.
5. Overall status priority:
   `UNREADABLE > ERROR > STALE > UNKNOWN > OK > DISABLED`.
   If all devices are `DISABLED`, show `DISABLED`.
6. `.timeline.json` is written by the logger on each scheduled and
   manual collection, capped at `max_files`, atomic, mode `0600`.
   Corrupt history is silently restarted.
7. Timeline reads the last N events from `.timeline.json` and shows
   `█ ░ ! ·`. No time-based scaling.
8. Mesh metrics are extracted only for the local node (`myNodeNum`).
   Missing metrics show `—`.
9. `--json` follows `schema_version: 1`, `generated_at` with offset,
   timestamps in ISO 8601 with offset, `null` for missing values.
10. Plain-text fallback without box drawing.
11. Compact mode at widths below 80 columns.
12. Config source and last error are not shown in the UI; they remain
    available in JSON and via `logger status`.
13. Covered by tests: `test_timeline.py`, `test_snapshot.py`,
    `test_dashboard.py`.
14. Benchmark: under 1 second for 10 000 files per device, up to 5
    devices, local disk, cold Python startup.

## Related documents

- `ui-guide.md` — general UI principles, formats, colors.
