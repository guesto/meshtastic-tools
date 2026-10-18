import json

import pytest

from meshtastic_tools.core.snapshot import parse_snapshot


def info_text(metrics=None):
    local = {"num": 1, "user": {"longName": "Local", "shortName": "L"},
             "deviceMetrics": metrics if metrics is not None else {"batteryLevel": 0, "voltage": 0,
             "channelUtilization": 0, "airUtilTx": 0, "uptimeSeconds": 0}}
    return ('Owner: Local (L)\nMy info: {"myNodeNum": 1, "nodedbCount": 0, "rebootCount": 0}\n'
            'Metadata: {"firmwareVersion": "test"}\nNodes in mesh: '
            + json.dumps({"!00000002": {"num": 2, "deviceMetrics": {"batteryLevel": 99}},
                          "!00000001": local}, indent=2) + '\n')


def test_uses_only_own_node_and_preserves_zero_values():
    snapshot = parse_snapshot(info_text())
    assert snapshot.snapshot_status == "ok"
    assert snapshot.node_id == "!00000001"
    assert snapshot.my_node_num == 1
    assert snapshot.long_name == "Local"
    assert snapshot.firmware_version == "test"
    assert snapshot.nodedb_count == snapshot.reboot_count == 0
    assert snapshot.battery_level == snapshot.voltage == snapshot.channel_utilization == 0
    assert snapshot.air_util_tx == snapshot.uptime_seconds == 0


def test_optional_metrics_missing_are_not_corruption_or_filled_from_remote_node():
    snapshot = parse_snapshot(info_text({}))
    assert snapshot.snapshot_status == "ok"
    assert snapshot.battery_level is None
    assert snapshot.uptime_seconds is None
    snapshot = parse_snapshot('My info: {"myNodeNum": 1}\nNodes in mesh: '
                              '{"!00000002": {"deviceMetrics": {"batteryLevel": 99}}}')
    assert snapshot.battery_level is None
    assert snapshot.nodedb_count is None


def test_truncated_optional_section_keeps_valid_metrics():
    snapshot = parse_snapshot(info_text() + 'Preferences: {"security": {"privateKey": "secret"')
    assert snapshot.snapshot_status == "unreadable"
    assert snapshot.battery_level == 0
    assert snapshot.my_node_num == 1
    assert "secret" not in snapshot.snapshot_error


def test_truncated_remote_node_recovers_complete_local_record():
    text = ('My info: {"myNodeNum": 1}\nMetadata: {"firmwareVersion": "test"}\n'
            'Nodes in mesh: {\n  "!00000001": {"num": 1, "deviceMetrics": {"voltage": 4.1}},\n'
            '  "!00000002": {"deviceMetrics": {"voltage": 3')
    snapshot = parse_snapshot(text)
    assert snapshot.snapshot_status == "unreadable"
    assert snapshot.voltage == 4.1
    assert snapshot.firmware_version == "test"


@pytest.mark.parametrize("value", ["", "garbage", "[]", "{}", '{"myNodeNum": true}',
                                  '{"myNodeNum": -1}', '{"myNodeNum": 4294967296}',
                                  'My info: []', 'My info: {"myNodeNum": "1"}'])
def test_unrecognizable_or_invalid_snapshot(value):
    snapshot = parse_snapshot(value)
    assert snapshot.snapshot_status == "unreadable"
    assert snapshot.my_node_num is None
    assert snapshot.battery_level is None


@pytest.mark.parametrize("value", [True, "12", -1, float("nan"), float("inf")])
def test_invalid_metric_is_null_without_losing_other_values(value):
    snapshot = parse_snapshot(info_text({"batteryLevel": value, "voltage": 4.1}))
    assert snapshot.snapshot_status == "unreadable"
    assert snapshot.battery_level is None
    assert snapshot.voltage == 4.1


def test_json_object_and_node_lookup_by_number():
    snapshot = parse_snapshot(json.dumps({"myInfo": {"myNodeNum": 0, "nodedbCount": 2},
                                        "metadata": {"firmwareVersion": "zero"},
                                        "nodes": {"0": {"num": 0, "deviceMetrics": {"batteryLevel": 101}}}}))
    assert snapshot.snapshot_status == "ok"
    assert snapshot.my_node_num == 0
    assert snapshot.node_id == "!00000000"
    assert snapshot.battery_level == 101  # Meshtastic may report external power as 101.


def test_inconsistent_local_node_is_not_used():
    snapshot = parse_snapshot('My info: {"myNodeNum": 1}\nNodes in mesh: '
                              '{"!00000001": {"num": 2, "deviceMetrics": {"batteryLevel": 99}}}')
    assert snapshot.snapshot_status == "unreadable"
    assert snapshot.battery_level is None


def test_extended_fields_come_from_metadata_and_the_local_node_only():
    value = {"myInfo": {"myNodeNum": 1, "firmwareEdition": "VANILLA"},
             "metadata": {"role": "CLIENT", "hwModel": "HELTEC_V4", "hasWifi": True,
                          "hasBluetooth": False, "hasEthernet": False},
             "nodes": {"!00000002": {"num": 2, "lastHeard": 1, "position": {"latitude": 89, "longitude": 120}},
                       "!00000001": {"num": 1, "lastHeard": 1792140000,
                                     "position": {"latitudeI": -338535465, "longitudeI": 0, "altitude": -3}}}}
    result = parse_snapshot(json.dumps(value))
    assert result.snapshot_status == "ok"
    assert result.role == "CLIENT" and result.hw_model == "HELTEC_V4"
    assert result.firmware_edition == "VANILLA"
    assert result.radios == {"wifi": True, "bluetooth": False, "ethernet": False}
    assert result.position == {"latitude": -33.8535465, "longitude": 0.0, "altitude": -3}
    assert result.last_heard_at.timestamp() == 1792140000
    assert result.last_heard_at.utcoffset().total_seconds() == 0


def test_missing_local_extended_fields_do_not_use_remote_data():
    result = parse_snapshot('My info: {"myNodeNum": 1}\nNodes in mesh: '
                            '{"!00000002": {"num": 2, "lastHeard": 1, "position": {"latitude": 89},'
                            '"user": {"role": "ROUTER", "hwModel": "REMOTE"}}}')
    assert result.snapshot_status == "ok"
    assert result.last_heard_at is None and result.position is None
    assert result.role is None and result.hw_model is None
    assert all(value is None for value in result.radios.values())


@pytest.mark.parametrize("local,metadata", [
    ({"position": {"latitude": 91}}, {}),
    ({"position": {"longitude": -181}}, {}),
    ({"lastHeard": 1e100}, {}),
    ({"lastHeard": True}, {}),
    ({}, {"hasWifi": 1}),
])
def test_invalid_extended_fields_do_not_hide_valid_metrics(local, metadata):
    local.update(num=1, deviceMetrics={"batteryLevel": 0})
    result = parse_snapshot(json.dumps({"myInfo": {"myNodeNum": 1}, "metadata": metadata,
                                        "nodes": {"!00000001": local}}))
    assert result.snapshot_status == "unreadable"
    assert result.battery_level == 0
