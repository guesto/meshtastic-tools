# Troubleshooting

## Common Issues and Solutions

### Device not connecting

1. Check if device is reachable via ping (for host connections):
   ping 192.168.1.100

2. Test with raw meshtastic command:
   meshtastic --host 192.168.1.100 --info

3. For serial connections, verify the port exists:
   ls -la /dev/ttyUSB0

4. For BLE connections, verify Bluetooth is enabled:
   bluetoothctl list

5. Try increasing timeout in config:
   timeout: 60

6. Check if device firmware is up to date

### Permission denied for serial port

1. Add user to dialout group:
   sudo usermod -a -G dialout $USER

2. Log out and back in for changes to take effect

3. Check current port permissions:
   ls -la /dev/ttyUSB0

4. Expected output: crw-rw---- 1 root dialout ...

### Systemd service not starting

1. Check service status:
   systemctl --user status meshtastic-logger@DEVICE_NAME.service

2. Check timer status:
   systemctl --user status meshtastic-logger@DEVICE_NAME.timer

3. List all timers:
   systemctl --user list-timers

4. View service logs:
   meshtastic-tools logger service-logs DEVICE_NAME

5. View systemd journal:
   journalctl --user -u meshtastic-logger@DEVICE_NAME.service -n 50

6. Reload systemd after config changes:
   systemctl --user daemon-reload

### Config file not found

1. Check if environment variable is set:
   echo $MESHTASTIC_TOOLS_CONFIG

2. Show config search paths:
   meshtastic-tools config-path

3. Create config from example:
   mkdir -p ~/.config/meshtastic-tools
   cp config/meshtastic-tools.yaml.example ~/.config/meshtastic-tools/config.yaml

4. Or create in current directory:
   cp config/meshtastic-tools.yaml.example config/meshtastic-tools.yaml

### Import errors when running

1. Ensure virtual environment is activated:
   source ~/OPT/meshtastic-tools/venv/bin/activate

2. Verify meshtastic CLI is installed:
   which meshtastic
   meshtastic --version

3. Reinstall in development mode:
   pip install -e .

4. Check Python version (3.9+ required):
   python --version

### Logs not appearing

1. Run with DEBUG log level for detailed output:
   meshtastic-tools --log-level DEBUG devices-check --all

2. Check service logs:
   meshtastic-tools logger service-logs DEVICE_NAME

3. Log files location (when using systemd service):
   cat logs/DEVICE_NAME.log
   cat logs/DEVICE_NAME-error.log

4. By default logs go to stderr, redirect to file if needed:
   meshtastic-tools devices-list 2>&1 | tee output.log

### Storage cleanup not working

1. Check retention settings in config:
   meshtastic-tools config-show --section tools

2. Run with dry-run first to preview:
   meshtastic-tools logger storage cleanup --dry-run

3. Check file permissions in data directory:
   ls -la data/logger/

4. Verify storage directory exists:
   ls -la data/logger/DEVICE_NAME/info/

### Schedule not triggering collection

1. Show current schedule:
   meshtastic-tools logger schedule show

2. Check next collection time:
   meshtastic-tools logger schedule next

3. Verify device is enabled:
   meshtastic-tools logger schedule show --device DEVICE_NAME

4. Run with DEBUG to see schedule logic:
   meshtastic-tools --log-level DEBUG logger run

5. Force collection ignoring schedule:
   meshtastic-tools logger run --force

### Special characters in device names

If your device name contains special characters (!, @, #, etc.):

1. In config file, wrap the name in quotes:
   "!a1b2c3d4":
     connection:
       ...

2. In shell commands, use single quotes:
   meshtastic-tools devices-check --device '!a1b2c3d4'

3. Or use names without special characters:
   node_a19c:
     connection:
       ...

### YAML errors when loading config

1. Check for proper indentation (use spaces, not tabs)

2. Wrap values with special characters in quotes:
   name: "value with special chars"

3. Validate configuration:
   meshtastic-tools config-validate

4. Common YAML issues:
   - Missing spaces after colons
   - Incorrect indentation
   - Using tabs instead of spaces
   - Unquoted values starting with special characters

### Getting Help

1. Show all available commands:
   meshtastic-tools --help
   meshtastic-tools logger --help

2. Show version:
   meshtastic-tools version

3. Run tests to verify installation:
   pytest tests/ -v