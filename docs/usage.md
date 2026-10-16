# Usage Guide

Meshtastic Tools consists of:
- Core — device management and configuration (shared by all tools)
- Logger — scheduled data collection and storage management

## Quick Start After Installation

Activate the virtual environment and explore available commands:

```
source /opt/meshtastic-tools/venv/bin/activate
meshtastic-tools --help
```

## Core Commands

### Device Management

List all configured devices:

```
meshtastic-tools devices-list
```

Check connection to all devices:

```
meshtastic-tools devices-check --all
```

Check connection to a specific device:

```
meshtastic-tools devices-check --device DEVICE_NAME
```

Show detailed device information:

```
meshtastic-tools devices-show --device DEVICE_NAME
meshtastic-tools devices-show --device DEVICE_NAME --refresh
```

Test connection with raw output:

```
meshtastic-tools devices-test --device DEVICE_NAME
```

Show device information with telemetry (battery, voltage, uptime):

```
meshtastic-tools devices-show --device DEVICE_NAME --telemetry
```

### Configuration

Show current configuration:

```
meshtastic-tools config-show
meshtastic-tools config-show --section devices
meshtastic-tools config-show --section tools
```

Validate configuration:

```
meshtastic-tools config-validate
```

Show config file search paths:

```
meshtastic-tools config-path
```

### Global Options

Use a specific config file:

```
meshtastic-tools --config /path/to/config.yaml devices-list
```

Enable debug logging:

```
meshtastic-tools --log-level DEBUG devices-check --all
```

Show version:

```
meshtastic-tools version
```

## Logger Commands

### Manual Data Collection

Collect info from a specific device:

```
meshtastic-tools logger collect info --device DEVICE_NAME
```

Collect info from all enabled devices:

```
meshtastic-tools logger collect info --all
```

Force collection ignoring schedule:

```
meshtastic-tools logger run --force
meshtastic-tools logger run --force --device DEVICE_NAME
```

Collect telemetry (battery, voltage, uptime, channel utilization):

```
meshtastic-tools logger collect telemetry --device DEVICE_NAME
```

Collect position from the connected node:

```
meshtastic-tools logger collect position --device DEVICE_NAME
```

Collect list of nodes in mesh:

```
meshtastic-tools logger collect nodes --device DEVICE_NAME
```

### Storage Management

List all stored files:

```
meshtastic-tools logger storage list
```

List files for a specific device:

```
meshtastic-tools logger storage list --device DEVICE_NAME
```

Show only the last N files:

```
meshtastic-tools logger storage list --limit 20
```

Show storage statistics:

```
meshtastic-tools logger storage stats
meshtastic-tools logger storage stats --device DEVICE_NAME
```

Preview cleanup (dry run):

```
meshtastic-tools logger storage cleanup --dry-run
```

Perform cleanup:

```
meshtastic-tools logger storage cleanup
```

Purge all data for a device:

```
meshtastic-tools logger storage purge --device DEVICE_NAME --yes
```

### Schedule Information

Show collection schedules:

```
meshtastic-tools logger schedule show
meshtastic-tools logger schedule show --device DEVICE_NAME
```

Show next collection times:

```
meshtastic-tools logger schedule next
```

### Service Management (systemd)

Service installation and management require `sudo`. Each timer collects only its own device.
Units use the Python environment and configuration selected during installation; the project
can be installed outside `/opt/meshtastic-tools`. Logs, including errors, go to the systemd journal.

For a custom installation, specify both the executable and the config explicitly:

```
sudo /path/to/project/venv/bin/meshtastic-tools --config /path/to/config.yaml logger service-install
```

After upgrading from an older version, regenerate installed units using `service-install`
and restart enabled timers with `systemctl restart meshtastic-logger@DEVICE_NAME.timer`.
This applies the device filter and journal logging to existing services.

Install services for all enabled devices:

```
sudo meshtastic-tools logger service-install
```

Install service for a specific device:

```
sudo meshtastic-tools logger service-install --device DEVICE_NAME
```

Enable and start all timers:

```
sudo meshtastic-tools logger service-enable --all
```

Enable timer for a specific device:

```
sudo meshtastic-tools logger service-enable --device DEVICE_NAME
```

Enable without starting immediately:

```
sudo meshtastic-tools logger service-enable --device DEVICE_NAME --no-now
```

Show timer status:

```
sudo meshtastic-tools logger service-status
```

View service logs:

```
meshtastic-tools logger service-logs DEVICE_NAME
meshtastic-tools logger service-logs DEVICE_NAME --follow
meshtastic-tools logger service-logs DEVICE_NAME --lines 100
```

Disable timer for a device:

```
sudo meshtastic-tools logger service-disable --device DEVICE_NAME
```

Disable all timers:

```
sudo meshtastic-tools logger service-disable --all
```

Remove services for a device:

```
sudo meshtastic-tools logger service-uninstall --device DEVICE_NAME
```

Remove all services:

```
sudo meshtastic-tools logger service-uninstall --all
```

Full cleanup (services, symlink, config, logs):

```
sudo meshtastic-tools logger service-cleanup
sudo meshtastic-tools logger service-cleanup --yes
```

## Typical Workflow

### First time installation

```
sudo mkdir -p /opt/meshtastic-tools /etc/meshtastic-tools
cd /opt/meshtastic-tools
git clone https://github.com/guesto/meshtastic-tools.git .
python3 -m venv venv
source venv/bin/activate
pip install "meshtastic[cli]" && pip install -e .
sudo cp config/meshtastic-tools.yaml.example /etc/meshtastic-tools/config.yaml
nano /etc/meshtastic-tools/config.yaml
meshtastic-tools config-validate
meshtastic-tools devices-check --all
meshtastic-tools logger collect info --all
sudo /opt/meshtastic-tools/venv/bin/meshtastic-tools logger service-install
sudo meshtastic-tools logger service-enable --all
sudo meshtastic-tools logger service-status
```

### Scheduling and errors

Only valid five-field cron expressions are accepted. Enabled logger devices require a schedule.
The logger collects at or after a cron time, never early. A checkpoint in
`DATA_DIR/DEVICE/.schedule.json` prevents repeated collection for the same cron time.
After downtime the latest missed time is collected once; a fresh installation starts
with the current minute. Failed attempts can retry on later timer ticks.
Manual and scheduled collection share a per-device lock. `--force` intentionally
collects again and advances the scheduled checkpoint after success.

Collection and connection-check commands return a nonzero exit code if any selected
device fails, while continuing to process the remaining devices.

### Snapshot privacy

Snapshot files use permissions `0600` and storage directories use `0700`. Private keys,
channel PSKs, passwords, Bluetooth PINs and channel sharing URLs are redacted from new
snapshots. These files are diagnostic logs, not complete configuration backups.
Access to existing snapshots is restricted when their storage manager is opened;
their historical contents are preserved.

Both retention limits apply: for example, `max_files: 50` with `retention_days: 30`
keeps at most 50 snapshots, each no older than 30 days. All collector types share this
limit per device. At a five-minute interval, 50 snapshots cover about four hours.
Manual collection also runs cleanup after a successful save.

### Daily monitoring

```
source /opt/meshtastic-tools/venv/bin/activate
meshtastic-tools devices-check --all
meshtastic-tools logger storage stats
meshtastic-tools logger service-logs DEVICE_NAME --lines 20
sudo meshtastic-tools logger storage cleanup --dry-run
```

### Adding a new device

```
nano /etc/meshtastic-tools/config.yaml
meshtastic-tools config-validate
meshtastic-tools devices-check --device NEW_DEVICE
sudo meshtastic-tools logger service-install --device NEW_DEVICE
sudo meshtastic-tools logger service-enable --device NEW_DEVICE
```

### Complete removal

```
sudo meshtastic-tools logger service-cleanup --yes
sudo rm -rf /opt/meshtastic-tools
```