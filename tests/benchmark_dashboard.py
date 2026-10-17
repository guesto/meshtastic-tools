"""Cold-process dashboard benchmark, separate from the unit suite.

Run: venv/bin/python tests/benchmark_dashboard.py --disk-description "disk model"
Fixtures live in a temporary directory; no live config, radio or logs are used.
The filesystem cache is not flushed. Exit 1 if any measured invocation takes >=1 s.
"""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=int, default=5, choices=range(1, 6))
    parser.add_argument("--files", type=int, default=10000)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", choices=("json", "text"), default="json")
    parser.add_argument("--directory", type=Path, help="Parent for temporary fixtures on the disk to measure")
    parser.add_argument("--disk-description", default="unspecified (use --disk-description)")
    args = parser.parse_args()
    if args.files < 1 or args.runs < 1:
        parser.error("--files and --runs must be positive")
    now = datetime.now().astimezone()
    with tempfile.TemporaryDirectory(prefix="dashboard-benchmark-", dir=args.directory) as temp:
        root = Path(temp)
        names = [f"node{i}" for i in range(args.devices)]
        config = {"devices": {name: {"connection": {"type": "host", "address": "example.invalid"}} for name in names},
                  "tools": {"logger": {"defaults": {"schedule": "*/5 * * * *"},
                                       "storage": {"data_dir": str(root / "data"), "max_files": args.files},
                                       "devices": {name: {"enabled": True} for name in names}}}}
        path = root / "config.yaml"
        path.write_text(yaml.safe_dump(config))
        content = ('My info: {"myNodeNum": 1, "nodedbCount": 1, "rebootCount": 0}\n'
                   'Metadata: {"firmwareVersion": "benchmark"}\n'
                   'Nodes in mesh: {"!00000001": {"num": 1, "deviceMetrics": {"batteryLevel": 0}}}\n')
        for name in names:
            directory = root / "data" / name / "info"
            directory.mkdir(parents=True)
            for offset in range(args.files):
                stamp = now - timedelta(seconds=offset)
                file = directory / f"info_{name}_{stamp.strftime('%Y-%m-%d_%H-%M-%S-%f')}.txt"
                file.write_text(content)
                os.utime(file, (stamp.timestamp(), stamp.timestamp()))
            record = {"version": 1, "collectors": {"info": {
                "last_attempt_at": now.isoformat(), "last_finished_at": now.isoformat(),
                "last_outcome": "success", "source": "scheduled", "last_duration_seconds": 1,
                "last_success_at": now.isoformat(), "consecutive_failures": 0}}}
            (directory.parent / ".status.json").write_text(json.dumps(record))
            (directory.parent / ".schedule.json").write_text(json.dumps(
                {"schedule": "*/5 * * * *", "timezone": "local", "last_slot": now.timestamp()}))
        command = [sys.executable, "-m", "meshtastic_tools.cli", "--config", str(path), "logger", "dashboard"]
        if args.output == "json":
            command.append("--json")
        durations = []
        for _ in range(args.runs):
            start = time.perf_counter()
            run = subprocess.run(command, capture_output=True, text=True, check=True)
            durations.append(time.perf_counter() - start)
            if args.output == "json":
                value = json.loads(run.stdout)
                assert all(device["storage"]["file_count"] == args.files for device in value["devices"])
                assert len(value["devices"]) == args.devices
        fs = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "--target", str(root)],
                            capture_output=True, text=True, check=True).stdout.strip()
        result = {"python": platform.python_version(), "platform": platform.platform(),
                  "filesystem": fs, "disk": args.disk_description,
                  "devices": args.devices, "files_per_device": args.files, "output": args.output,
                  "width": "auto (pipe)", "timeline_mode": "events", "cold_process": True, "cold_filesystem_cache": False,
                  "seconds": durations, "median_seconds": statistics.median(durations),
                  "target_met": max(durations) < 1}
        print(json.dumps(result, indent=2))
        return 0 if result["target_met"] else 1


if __name__ == "__main__":
    sys.exit(main())
