# Meshtastic Tools

A framework and collection of command-line utilities for working with Meshtastic mesh network devices.

Currently implements:
- **Core** — device management, configuration, logging, health, privacy, snapshots, and timeline (shared by all tools)
- **Logger** — scheduled collection of device information with storage management
- **Dashboard** — read-only visualization of locally collected data

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

# Inspect applied defaults, inherited schedules, and resolved paths
meshtastic-tools config-show --effective

# Logger: collect data once
meshtastic-tools logger collect info --all

# Logger: inspect collection health without contacting devices
meshtastic-tools logger status

# Dashboard: view locally collected data (no device connection)
meshtastic-tools dashboard
meshtastic-tools dashboard --device home_station
meshtastic-tools dashboard --json
meshtastic-tools dashboard --width 120 --layout split
meshtastic-tools dashboard --all --page 2

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
`logger status` shows collection times, failures, request duration, and overdue data;
`logger status --json --check` supports external health checks.
`dashboard` reads local records and the latest saved info snapshot without
changing them. At 120 columns and above it shows logger and node details side by
side; `--layout auto|single|split` overrides this choice. Pipes and terminals below
80 columns use plain text. `--width N` overrides width; `--no-color` disables colors.
The four-line header summarizes all configured devices, sorted by health then name,
with three per page (`--page N`). The detail view shows the first enabled device on
the page, or the device selected with `--device NAME`. `--all` shows only the summary.
Hidden devices still contribute to the overall collection status.

Collection health, snapshot readability, disk warnings and radio utilization are
separate indicators. Timeline reads the logger's per-collector `.timeline.json`:
`█` is a saved snapshot, `░` an unattempted scheduled slot, `!` a failed attempt,
and `·` an empty cell. Each cell represents one event, with no time scaling.
The most recent events fill the column from left to right; the header reports
truncation, the displayed date range and the next scheduled slot. History starts
with new collections; existing snapshot files are not used to reconstruct events.
Missing or corrupt history is treated as empty without affecting collection health.

The logger records events for manual and scheduled collections under the device
lock. History retains up to `storage.max_files` events per collector; `null` uses
1000 events, and `0` disables history (and the snapshot count limit). Time-based
snapshot retention still applies. Unattempted slots are recorded before the next
scheduled collection; failed attempts remain retryable and are not counted as gaps.
Short terminals omit optional rows, preserving the required sections. The header
always shows the device count and list, including when only one device is configured.

JSON uses schema version 1 and timestamps with the configured timezone offset.
It includes the config source path, full recorded collection error, and all-device
summary. Config paths and collection errors are omitted from the dashboard UI;
`logger status` still shows errors. `timeline.events` contains the last events
fitting the selected width/layout, with `shown`, `total` and `next_slot_at`.
`devices` contains enabled-device details by default, or the explicitly selected
device. Page and summary flags do not remove JSON device details.
Shared schedules can be set through `tools.logger.defaults.schedule`; each device is
enabled explicitly. `tools.logger.timezone` controls cron interpretation, and
`tools.logger.health.grace_seconds` controls the freshness grace period.
See the usage guide for applying updated units to an existing installation.

## Project Structure

```
src/meshtastic_tools/
├── core/           # Shared modules
│   ├── config.py
│   ├── device.py
│   ├── exceptions.py
│   ├── logging_config.py
│   ├── health.py
│   ├── privacy.py
│   ├── snapshot.py
│   └── timeline.py
├── logger/         # Data collection and storage
│   ├── cli.py
│   ├── collector.py
│   ├── scheduler.py
│   ├── storage.py
│   └── services.py
├── dashboard/      # Read-only visualization
│   ├── __init__.py
│   ├── cli.py
│   ├── data.py
│   └── render.py
└── cli.py          # Root CLI registers logger and dashboard
```

## License

MIT
