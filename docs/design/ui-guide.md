# UI Guide

This document describes the general visual language used across all
Meshtastic Tools interfaces: CLI commands, dashboards, analyzers, and
any future tools. It defines principles, formatting rules, colors, and
adaptive behavior. Layer-specific documents (for example
`ui-dashboard-layer1.md`) build on top of this guide and add their own
layout and content.

The style is inspired by cockpit instrument panels (CDU / GPS monitor):
dense layout, aligned columns, minimal color, and a fixed visual grid.

## Principles

1. Density over spacing. Sections have plain uppercase headings and are
   separated by a blank line. Only the top bar has a horizontal divider;
   sections have no rules or framed boxes. Vertical space is expensive.
2. Fixed columns over stretching. Values start at a fixed offset from
   the section label. Empty space to the right is acceptable; a value
   glued to the right edge is not.
3. Color only for status. Green / yellow / red carry meaning. No
   decorative color, no colored headers, no colored labels.
4. Units always explicit. `250 known`, `2.01 s`, `2.1 MB`, not just
   numbers.
5. Alignment. Labels left-aligned, values aligned in a column. Numbers
   are right-aligned only within progress bars or tables.
6. Determinism. Given the same data and width, output is byte-for-byte
   identical. No randomness, no time-dependent layout.
7. Readability on any theme. The terminal background is never set
   explicitly. All output remains readable on both light and dark
   themes.

## Frame

The outer frame uses double-line box drawing. A horizontal divider
appears only below the top bar. Internal sections have plain uppercase
headings and a blank line between them; there are no inner rules or
boxes.

```
╔══════════════════════════════════════════════════════════════╗
║ header                                                       ║
╠══════════════════════════════════════════════════════════════╣
║ section A                                                    ║
║                                                              ║
║ section B                                                    ║
╚══════════════════════════════════════════════════════════════╝
```

Characters:

- Outer frame: `╔ ╗ ╚ ╝ ═ ║ ╠ ╣`
- Top-bar divider: a single `═` row, full inner width. No horizontal
  separators within the body or beside section headings.
- Column separator in split layouts: a single `│`.

When the frame is not appropriate (pipes, logs, narrow terminals), see
`Plain-text fallback` below.

## Sections

Each section is a label in uppercase followed by its content. Section
labels are not boxed and not centered; they sit flush at the left edge
of their column.

```
COLLECTION
  LAST SUCCESS  2026-10-16 20:40:02
                (3 m 41 s ago)
  NEXT SLOT     2026-10-16 20:45:00
                (in 1 m 16 s)
  DURATION      2.01 s
```

Section label column width: 14 characters by default. Values start after
a single space following the label column.

If a value needs a second line (for example a progress bar), the second
line aligns to the value column, not to the label column:

```
STORAGE
  FILES        14 / 50
               [███░░░░░░░░░░░░░░░░░]  28 %
  SIZE         2.1 MB
```

## Value formats

| Kind | Format | Example |
|------|--------|---------|
| Timestamp (UI) | `YYYY-MM-DD HH:MM:SS` | `2026-10-16 20:40:02` |
| Timestamp (JSON) | ISO 8601 with offset | `2026-10-16T20:40:02+05:00` |
| Relative time | compact, one or two units | `3 m 41 s ago`, `in 1 m 16 s` |
| Duration | `X.XX s`, `X m Y s`, `X h Y m`, `X d Y h` | `2.01 s`, `3 h 16 m` |
| Float | up to 2 decimal places | `12.24`, `4.14`, `0.60` |
| Percent | float + space + `%` | `12.24 %` |
| File size | B, KB, MB, GB | `2.1 MB` |
| Missing value | em dash | `—` |
| Unknown metric | em dash + gray `○ unknown` | `—    ○ unknown` |

Microseconds never appear in UI timestamps. They appear only in JSON.

### Human-readable units

File sizes:

| Range | Unit |
|-------|------|
| `< 1024 B` | `B` |
| `< 1 MB` | `KB` |
| `< 1 GB` | `MB` |
| `>= 1 GB` | `GB` |

Durations:

| Range | Unit |
|-------|------|
| `< 1 min` | `X s` |
| `< 1 h` | `X m Y s` |
| `< 1 day` | `X h Y m` |
| `>= 1 day` | `X d Y h` |

## Statuses

### Status indicator

A status indicator is a single `●` character followed by a space and a
short label. The color is set by the status.

| Status | Color | Meaning |
|--------|-------|---------|
| `OK` | green | Healthy |
| `STALE` | yellow | Expected update is overdue |
| `ERROR` | red | Last attempt failed |
| `UNREADABLE` | red | Data is corrupt or unreadable |
| `UNKNOWN` | gray | No status yet |
| `DISABLED` | gray | Feature or device disabled in config |

### Overall status

When several entities contribute to a single overall status, aggregate
by priority:

```
UNREADABLE > ERROR > STALE > UNKNOWN > OK > DISABLED
```

If all entities are `DISABLED`, the overall status is `DISABLED`.
If all entities are `UNKNOWN`, the overall status is `UNKNOWN`.

## Color rules

Color carries status only. There are no decorative colors.

| Context | Color |
|---------|-------|
| `● OK` | green |
| `● STALE`, `● WARNING`, `● medium` | yellow |
| `● ERROR`, `● UNREADABLE`, `● CRITICAL`, `● high` | red |
| `● UNKNOWN`, `● DISABLED`, `○ unknown` | gray |
| Progress bar (battery) | green by default, yellow below 30 %, red below 10 % |
| Progress bar (other) | no color |
| All other text | default terminal foreground |

The terminal background is never set explicitly. Use default foreground
for all text that does not carry status.

## Severity indicators

A severity indicator is a colored `●` followed by a short label. Used
for metrics where a numeric value alone does not convey urgency.

```
RADIO
  CHANNEL      12.24 %    ● low
  AIR TX        0.60 %    ● low
```

Labels: `low`, `medium`, `high`. If the metric is missing, show
`—    ○ unknown` with a gray empty circle.

Thresholds are defined per metric in the layer-specific document (for
example `dashboard-layer1.md`).

## Progress bars

Progress bars appear on their own line, aligned to the value column, not
to the label column. They are used for metrics where a numeric value
benefits from a visual scale.

```
BATTERY      96 % · 4.14 V
             [█████████████████████░]
```

Characters:

- Filled: `█`
- Empty: `░`
- Bar enclosed in square brackets `[` and `]`.
- Width: 20 characters by default, 16 in compact mode.

The numeric value is always authoritative. The bar is a visual aid, not
a replacement.

## Plain-text fallback

When output is redirected to a pipe, or when the terminal is too narrow
for the framed layout, the interface falls back to plain text.

The fallback preserves all information; only the frame and the split
layout are dropped.

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
  ...
```

Rules:

- No box drawing characters.
- Sections separated by a blank line.
- Status indicator `●` becomes a leading `>` or is kept as `●` if the
  terminal supports it and `--no-color` is not set.
- Indentation of two spaces under section labels is preserved.

## Adaptive behavior

### By width

The same content adapts to the available width.

| Width | Layout |
|-------|--------|
| `>= 140` | Wide layout: larger bars, wider progressions |
| `120–139` | Standard layout |
| `100–119` | Narrow layout: single column, sections stacked |
| `< 100` | Compact single column |

Exact layout rules per layer are defined in the layer-specific document.

### By height

If the terminal height is too small for the full layout, drop optional
sections in the order defined in the layer-specific document. Required
sections are never dropped.

## Timestamps

UI and JSON use different timestamp formats on purpose.

- **UI** — for humans. `YYYY-MM-DD HH:MM:SS` without microseconds and
  without offset, because the timezone is displayed once, in the header.
- **JSON** — for machines. ISO 8601 with offset, full precision
  (including microseconds when present). Missing values are `null`.

Example:

```
UI:    2026-10-16 20:43:43
JSON:  2026-10-16T20:43:43.774055+05:00
```

## JSON conventions

- All timestamps are ISO 8601 with offset.
- Missing values are `null`, not empty strings.
- Numbers are numbers, not strings.
- `schema_version` is a constant integer, bumped on breaking changes.

## Further reading

Layer-specific documents:

- `dashboard-layer1.md` — logger dashboard (Layer 1).
- Layer 2 (live mode) — planned.
- Layer 3 (trends and charts) — planned.
- Layer 4 (analyzer) — planned.

