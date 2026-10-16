# Meshtastic Tools

A framework and collection of command-line utilities for working with Meshtastic mesh network devices.

Currently implements:
- **Core** — device management, configuration, logging (shared by all tools)
- **Logger** — scheduled collection of device information with storage management

More tools planned for the future.

Designed to run on a Linux server, connecting to devices via TCP/IP, Serial, or BLE.

## Documentation

- [Installation & Setup](docs/usage.md) — full guide from clone to running service
- [Configuration Reference](docs/configuration.md) — all config options explained
- [Troubleshooting](docs/troubleshooting.md) — common problems and solutions

## Quick Install

```bash
sudo mkdir -p /opt/meshtastic-tools && sudo chown $USER:$USER /opt/meshtastic-tools
cd /opt/meshtastic-tools
git clone https://github.com/guesto/meshtastic-tools.git .

python3 -m venv venv
source venv/bin/activate
pip install "meshtastic[cli]"
pip install -e .

sudo mkdir -p /etc/meshtastic-tools
sudo cp config/meshtastic-tools.yaml.example /etc/meshtastic-tools/config.yaml
sudo chown $USER:$USER /etc/meshtastic-tools/config.yaml
nano /etc/meshtastic-tools/config.yaml
```

## Quick Usage

```bash
# Core: check device connections
meshtastic-tools devices-check --all

# Logger: collect data once
meshtastic-tools logger collect info --all

# Logger: install and enable automatic collection
sudo /opt/meshtastic-tools/venv/bin/meshtastic-tools logger service-install
sudo meshtastic-tools logger service-enable --all
```

## How Logger Works

```
Schedule "*/5 * * * *" per device
         │
         ▼
 systemd timer (every minute)
         │
         ▼
 meshtastic-tools logger run
         │
         ▼
 Checks cron time + saved checkpoint ──▶ Skips completed times
         │
         ▼
 Runs: Python Meshtastic CLI --host <addr> --info
         │
         ▼
 Saves: /opt/meshtastic-tools/data/logger/<device>/info/info_<device>_<timestamp>.txt
         │
         ▼
 Cleans up old files per retention policy
```

Snapshots redact credentials and use private file permissions. Each service collects
only its selected device; persistent checkpoints and locks prevent overlapping collection.
Service logs are available through `logger service-logs` (systemd journal).
See the usage guide for applying updated units to an existing installation.

## Project Structure

```
meshtastic-tools
├── core/           # Device management, config, logging
├── logger/         # Scheduled data collection tool
└── (future tools)  # More tools planned
```

## License

MIT