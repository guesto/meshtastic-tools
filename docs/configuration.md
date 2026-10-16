# Configuration Reference

Configuration is shared across all tools in meshtastic-tools.

## Configuration File

Main configuration file: `/etc/meshtastic-tools/config.yaml`

Example file: `/opt/meshtastic-tools/config/meshtastic-tools.yaml.example`

## Full Configuration Example

```yaml
# Device connections are shared by all tools.
devices:
  home_station:
    connection:
      type: host                  # host, port, or ble
      address: 192.168.1.100
      timeout: 30                 # seconds
    metadata:
      description: "Home station"
      location: "Living Room"

  # roof_node:
  #   connection:
  #     type: port
  #     address: /dev/ttyUSB0      # Serial speed is fixed by Meshtastic CLI.
  #     timeout: 30

  # mobile_node:
  #   connection:
  #     type: ble
  #     address: "XX:XX:XX:XX:XX:XX"
  #     timeout: 60

tools:
  logger:
    enabled: true
    timezone: UTC                 # IANA name, e.g. Asia/Yekaterinburg; local = server timezone

    defaults:
      schedule: "*/5 * * * *"      # Used when a device omits schedule.

    health:
      grace_seconds: null         # Automatic: 60 seconds + device connection timeout.
                                  # Set an integer (including 0) to override.

    storage:
      data_dir: /opt/meshtastic-tools/data/logger
      retention_days: 30
      max_files: 50                # Shared by all collectors for each device.
      # Optional: filename_format: "info_{device}_{timestamp}.txt"

    devices:
      home_station:
        enabled: true             # Every device must be explicitly enabled here.

      # roof_node:
      #   enabled: true
      #   schedule: "0 * * * *"    # Overrides defaults.schedule.

      # mobile_node:
      #   enabled: false
```

## Connection Types

### host (TCP/IP)
```yaml
type: host
address: 192.168.1.100
timeout: 30
```

### port (Serial/USB)
```yaml
type: port
address: /dev/ttyUSB0
timeout: 30
```

### ble (Bluetooth)
```yaml
type: ble
address: "XX:XX:XX:XX:XX:XX"
timeout: 60
```

## Device Settings

Each device must have:
- `connection.type`: `host`, `port`, or `ble`
- `connection.address`: IP address, serial port, or MAC address
- `connection.timeout`: seconds (default: 30)
Serial connections always use the fixed 115200 baud rate selected by Meshtastic CLI.
There is no `baudrate` setting.

Optional metadata:
- `description`: Human-readable description
- `location`: Physical location
- Any other custom fields

## Logger Tool Settings

### Storage
- `data_dir`: Directory for collected data. Absolute paths are recommended; relative paths
  are resolved against the selected configuration file's directory.
- `retention_days`: Delete files older than N days
- `max_files`: Keep at most N files per device across all collectors (example: 50).
  Both retention limits apply, so this limit may shorten the history below `retention_days`.
- `filename_format`: Pattern with `{device}` and `{timestamp}`

### Defaults and device schedules

- `tools.logger.defaults.schedule`: Optional shared five-field cron expression.
- `tools.logger.devices.NAME.enabled`: Explicitly enables the device for logger; omitted means `false`.
- `tools.logger.devices.NAME.schedule`: Overrides the shared schedule when present.

An enabled device must have a schedule, directly or through `defaults.schedule`.
An explicit `schedule: null` disables inheritance and is invalid for an enabled device.
Devices omitted from `tools.logger.devices` remain excluded, even when defaults are set.
`defaults` accepts only `schedule`; there is no inherited `enabled` switch.

### Timezone

`tools.logger.timezone` accepts an IANA timezone name such as `UTC`,
`Asia/Yekaterinburg`, or `Europe/Berlin`. It controls scheduled collection, schedule
previews, and freshness checks. Invalid names are rejected during loading.

The default value `local` uses the server's timezone. An explicit IANA name makes
scheduling independent of the server's timezone. Snapshot filenames and retention
use the server's local time; collection status timestamps are stored in UTC.

### Health grace period

`tools.logger.health.grace_seconds` controls the delay allowed after a scheduled
collection time before data is overdue. `null` (the default) calculates it separately
for each device as 60 seconds plus `connection.timeout`. An integer from 0 through
86400 overrides that calculation for all logger devices. Zero means no added grace.

### Available Collectors
- `info`: Full device information (nodes, channels, config)
- `telemetry`: Battery, voltage, uptime, channel utilization
- `nodes`: List of all nodes in mesh range
- `position`: Position requested from the connected node

## Cron Schedule Format

The systemd timer triggers collection check every minute. 
The cron expression defines when collection actually happens.

Each cron time is collected at most once, with a persistent checkpoint and a per-device
lock. Collection never starts before its scheduled time. Failed attempts are retried
on subsequent timer ticks; after downtime only the latest missed time is collected.
`--force` permits an explicit extra collection. Cron times use `tools.logger.timezone`.

Examples:
- `*/5 * * * *` — Every 5 minutes (recommended minimum)
- `*/1 * * * *` — Every minute (maximum frequency)
- `0 * * * *` — Every hour
- `0 */6 * * *` — Every 6 hours
- `0 0 * * *` — Daily at midnight

## Validation

```
meshtastic-tools config-validate
meshtastic-tools config-show
meshtastic-tools config-show --effective
meshtastic-tools config-show --effective --section tools
meshtastic-tools config-show --section devices
meshtastic-tools config-show --section tools
meshtastic-tools config-path
```

## Privacy and validation

New snapshots redact credentials and channel sharing URLs. Files use mode `0600`;
storage directories use `0700`. Existing snapshots have their permissions restricted
without changing historical contents. Keep full device configuration backups separately.

Explicit `--config` takes priority over `MESHTASTIC_TOOLS_CONFIG`, then the standard
search paths apply. A missing explicitly selected file is an error. CLI commands load
configuration strictly, including device names and logger references.

Device names may contain ASCII letters, numbers, `_`, `-`, `.`, `!`, `@`, and `#`,
but cannot be `.` or `..`. Filename formats must produce a visible filename, not a path.

## Collection health

`meshtastic-tools logger status` reads the last completed collection results from
`data_dir/DEVICE/.status.json`. No additional configuration is required.
Freshness is checked against each enabled device's cron schedule in the configured
timezone, using `tools.logger.health.grace_seconds`. `logger status --json --check` returns a
nonzero exit code when enabled devices have failed, stale or unknown collection status.
Status records are independent of `max_files` and `retention_days`.


## Strict YAML validation

Only the documented fields are accepted at the root, in `tools`, and throughout the
connection, storage, defaults, health, and logger device sections. `metadata` remains
an open mapping for descriptive fields. Unknown settings, invalid timezones and cron
expressions are errors. The removed `baudrate` setting is rejected.

Duplicate keys in any YAML mapping are rejected with a line number. YAML anchors and
merge overrides remain supported, although logger schedule inheritance is usually
clearer through `defaults.schedule`.

## Effective settings

`config-show` displays the selected file's values. Add `--effective` to inspect all
applied defaults, inherited device schedules and absolute storage paths. Comments
identify the source file, selection method, schedule inheritance and calculated health
grace for each logger device. `--section devices` and `--section tools` work with both
views. Neither view edits the configuration or contacts a device.

The effective output remains valid YAML and can be loaded as configuration. Automatic
health grace stays `null` in the YAML, with its per-device calculated values explained
in comments. Explicit settings take priority over defaults; the selected file comes
from `--config`, then `MESHTASTIC_TOOLS_CONFIG`, then the documented search paths.
