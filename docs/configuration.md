# Configuration Reference

Configuration is shared across all tools in meshtastic-tools.

## Configuration File

Main configuration file: `/etc/meshtastic-tools/config.yaml`

Example file: `/opt/meshtastic-tools/config/meshtastic-tools.yaml.example`

## Full Configuration Example

```yaml
devices:
  home_station:
    connection:
      type: host
      address: 192.168.1.100
      timeout: 30
    metadata:
      description: "Home station in living room"
      location: "Living Room"

  roof_node:
    connection:
      type: port
      address: /dev/ttyUSB0
      baudrate: 115200
      timeout: 30
    metadata:
      description: "Node on the roof"

  mobile_node:
    connection:
      type: ble
      address: "XX:XX:XX:XX:XX:XX"
      timeout: 60
    metadata:
      description: "Mobile node"

tools:
  logger:
    enabled: true
    
    storage:
      data_dir: /opt/meshtastic-tools/data/logger
      retention_days: 30
      max_files: 50
      filename_format: "info_{device}_{timestamp}.txt"
    
    devices:
      home_station:
        enabled: true
        schedule: "*/5 * * * *"
        
      roof_node:
        enabled: true
        schedule: "0 * * * *"
        
      mobile_node:
        enabled: false
        schedule: "*/10 * * * *"
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
baudrate: 115200
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
- `connection.baudrate`: optional compatibility setting; serial uses a fixed 115200 baud.
  Other rates are rejected; no unsupported `--baudrate` argument is passed to the CLI.

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

### Device Schedule
- `enabled`: `true` or `false`
- `schedule`: Valid five-field cron expression, required when the device is enabled.
  Invalid schedules fail validation and never trigger collection.

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
`--force` permits an explicit extra collection. State uses the system's local timezone.

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
