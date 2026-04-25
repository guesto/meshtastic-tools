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
      max_files: 1000
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
- `connection.baudrate`: for serial only (optional)

Optional metadata:
- `description`: Human-readable description
- `location`: Physical location
- Any other custom fields

## Logger Tool Settings

### Storage
- `data_dir`: Directory for collected data
- `retention_days`: Delete files older than N days
- `max_files`: Keep at most N files per device
- `filename_format`: Pattern with `{device}` and `{timestamp}`

### Device Schedule
- `enabled`: `true` or `false`
- `schedule`: Cron expression

### Available Collectors
- `info`: Full device information (nodes, channels, config)
- `telemetry`: Battery, voltage, uptime, channel utilization
- `nodes`: List of all nodes in mesh range

## Cron Schedule Format

The systemd timer triggers collection check every minute. 
The cron expression defines when collection actually happens.

Note: Collection cannot occur more than once per minute.

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