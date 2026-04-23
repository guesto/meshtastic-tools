# Usage Guide

Meshtastic Tools consists of:
- Core — device management and configuration (shared by all tools)
- Logger — scheduled data collection and storage management

## Quick Start After Installation

source /opt/meshtastic-tools/venv/bin/activate
meshtastic-tools --help

## Core Commands

### Device Management

meshtastic-tools devices-list
meshtastic-tools devices-check --all
meshtastic-tools devices-check --device DEVICE_NAME
meshtastic-tools devices-show DEVICE_NAME
meshtastic-tools devices-show DEVICE_NAME --refresh
meshtastic-tools devices-test DEVICE_NAME

### Configuration

meshtastic-tools config-show
meshtastic-tools config-show --section devices
meshtastic-tools config-show --section tools
meshtastic-tools config-validate
meshtastic-tools config-path

### Global Options

meshtastic-tools --config /path/to/config.yaml devices-list
meshtastic-tools --log-level DEBUG devices-check --all
meshtastic-tools version

## Logger Commands

### Manual Data Collection

meshtastic-tools logger collect info --device DEVICE_NAME
meshtastic-tools logger collect info --all
meshtastic-tools logger run --force
meshtastic-tools logger collect telemetry --device DEVICE_NAME
meshtastic-tools logger collect nodes --device DEVICE_NAME

### Storage Management

meshtastic-tools logger storage list
meshtastic-tools logger storage list --device DEVICE_NAME
meshtastic-tools logger storage list --limit 20
meshtastic-tools logger storage stats
meshtastic-tools logger storage stats --device DEVICE_NAME
meshtastic-tools logger storage cleanup --dry-run
meshtastic-tools logger storage cleanup
meshtastic-tools logger storage purge --device DEVICE_NAME --yes

### Schedule Information

meshtastic-tools logger schedule show
meshtastic-tools logger schedule show --device DEVICE_NAME
meshtastic-tools logger schedule next

### Service Management (systemd)

All service commands require sudo:

sudo meshtastic-tools logger service-install
sudo meshtastic-tools logger service-install --device DEVICE_NAME
sudo meshtastic-tools logger service-enable --all
sudo meshtastic-tools logger service-enable --device DEVICE_NAME
sudo meshtastic-tools logger service-enable --device DEVICE_NAME --no-now
sudo meshtastic-tools logger service-status
meshtastic-tools logger service-logs DEVICE_NAME
meshtastic-tools logger service-logs DEVICE_NAME --follow
meshtastic-tools logger service-logs DEVICE_NAME --lines 100
sudo meshtastic-tools logger service-disable --device DEVICE_NAME
sudo meshtastic-tools logger service-disable --all
sudo meshtastic-tools logger service-uninstall --device DEVICE_NAME
sudo meshtastic-tools logger service-uninstall --all
sudo meshtastic-tools logger service-cleanup
sudo meshtastic-tools logger service-cleanup --yes

## Typical Workflow

### First time installation

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

### Daily monitoring

source /opt/meshtastic-tools/venv/bin/activate
meshtastic-tools devices-check --all
meshtastic-tools logger storage stats
meshtastic-tools logger service-logs DEVICE_NAME --lines 20
sudo meshtastic-tools logger storage cleanup --dry-run

### Adding new device

nano /etc/meshtastic-tools/config.yaml
meshtastic-tools config-validate
meshtastic-tools devices-check --device NEW_DEVICE
sudo meshtastic-tools logger service-install --device NEW_DEVICE
sudo meshtastic-tools logger service-enable --device NEW_DEVICE

### Complete removal

sudo meshtastic-tools logger service-cleanup --yes
sudo rm -rf /opt/meshtastic-tools