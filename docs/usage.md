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

### Configuration

Show current configuration:

```
meshtastic-tools config-show
meshtastic-tools devices-check --all
```

Check connection to a specific device:

```
meshtastic-tools devices-check --device DEVICE_NAME
```

Show detailed device information:

```
meshtastic-tools devices-show DEVICE_NAME
meshtastic-tools devices-show DEVICE_NAME --refresh
```

Show device information with telemetry (battery, voltage, uptime):

```
meshtastic-tools devices-show DEVICE_NAME --telemetry
```

Test connection with raw output:

```
meshtastic-tools devices-test DEVICE_NAME
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
```

Collect telemetry (battery, voltage, uptime, channel utilization):

```
meshtastic-tools logger collect telemetry --device DEVICE_NAME
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

All service commands require `sudo`.

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