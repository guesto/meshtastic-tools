"""Metric history validation, retention, and atomic persistence without device I/O."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from meshtastic_tools.core.metrics import MetricHistory, MetricSample, RETENTION


NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("content", [
    None, "broken", "[]", "{}", '{"version": true}', '{"version": 2}',
    '{"version": 1, "samples": [{}]}',
    '{"version": 1, "samples": [{"at": "2026-10-17T12:00:00"}]}',
    json.dumps({"version": 1, "samples": [
        {"at": "2026-10-17T12:00:00", "battery_level": 50, "voltage": 3.5}]}),
    json.dumps({"version": 1, "samples": [
        {"at": NOW.isoformat(), "battery_level": 150, "voltage": 3.5}]}),
])
def test_missing_or_corrupt_history_reads_empty_and_restarts(tmp_path, content):
    history = MetricHistory(tmp_path)
    if content is not None:
        history.path.write_text(content)
    assert history.load() == []
    if content is None:
        assert not history.path.exists()
    else:
        assert history.path.read_text() == content

    history.append(NOW, 50, 3.5)
    assert history.load() == [MetricSample(NOW, 50, 3.5)]
    assert history.path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".metrics-*.tmp"))


@pytest.mark.parametrize("field, value", [
    ("at", None), ("at", 123), ("at", "not a timestamp"),
    ("battery_level", True), ("battery_level", "50"), ("battery_level", -1),
    ("battery_level", float("nan")), ("battery_level", float("inf")),
    ("voltage", True), ("voltage", -1), ("voltage", 10.1), ("voltage", float("nan")),
])
def test_invalid_sample_does_not_hide_behind_valid_rows(tmp_path, field, value):
    history = MetricHistory(tmp_path)
    row = MetricSample(NOW, 50, 3.5).to_dict()
    bad_row = dict(row, **{field: value})
    history.path.write_text(json.dumps({"version": 1, "samples": [row, bad_row]}))
    before = history.path.read_bytes()
    assert history.load() == []
    assert history.path.read_bytes() == before


def test_append_prunes_to_24h(tmp_path):
    history = MetricHistory(tmp_path)
    samples = [MetricSample(NOW - timedelta(hours=i), 50, 3.5) for i in range(1, 31)]
    history.path.write_text(json.dumps({"version": 1, "samples": [s.to_dict() for s in samples]}))
    assert len(history.load()) == 30
    history.append(NOW, 60, 4.0)
    loaded = history.load()
    assert len(loaded) == 25
    assert loaded[0].at == NOW - RETENTION
    assert loaded[-1] == MetricSample(NOW, 60, 4.0)
    assert all(s.at >= NOW - RETENTION for s in loaded)
    assert len(json.loads(history.path.read_text())["samples"]) == 25


def test_append_skips_when_both_metrics_none(tmp_path):
    history = MetricHistory(tmp_path / "absent")
    history.append(NOW, None, None)
    assert not history.path.parent.exists()
    history.append(NOW, 0, None)
    before = history.path.read_bytes()
    history.append(NOW + timedelta(days=2), None, None)
    assert history.path.read_bytes() == before
    history.append(NOW + timedelta(minutes=1), None, 0)
    assert [(s.battery_level, s.voltage) for s in history.load()] == [(0, None), (None, 0)]


def test_samples_sorted_by_time_and_normalized_to_utc(tmp_path):
    history = MetricHistory(tmp_path)
    history.path.write_text(json.dumps({"version": 1, "samples": [
        {"at": "2026-10-17T10:10:00+00:00", "battery_level": 80, "voltage": 4.1},
        {"at": "2026-10-17T10:15:00+05:00", "battery_level": None, "voltage": 3.5},
        {"at": "2026-10-17T05:05:00Z", "battery_level": 40, "voltage": None},
    ]}))
    before = history.path.read_bytes()
    samples = history.load()
    assert all(s.at.utcoffset() == timedelta(0) for s in samples)
    assert [s.at for s in samples] == [
        datetime(2026, 10, 17, hour, minute, tzinfo=timezone.utc)
        for hour, minute in ((5, 5), (5, 15), (10, 10))
    ]
    assert history.path.read_bytes() == before
    history.save(samples)
    assert all(row["at"].endswith("+00:00") for row in json.loads(history.path.read_text())["samples"])


def test_save_prunes_and_normalizes_non_utc_samples(tmp_path):
    history = MetricHistory(tmp_path)
    local = NOW.astimezone(timezone(timedelta(hours=5)))
    history.save([MetricSample(local, 100, 10), MetricSample(local - RETENTION, 0, 0),
                  MetricSample(local - RETENTION - timedelta(seconds=1), 50, None)])
    rows = json.loads(history.path.read_text())["samples"]
    assert [row["at"] for row in rows] == [(NOW - RETENTION).isoformat(), NOW.isoformat()]
    assert history.load() == [MetricSample(NOW - RETENTION, 0, 0), MetricSample(NOW, 100, 10)]


def test_failed_atomic_replace_preserves_previous_history(tmp_path, monkeypatch):
    history = MetricHistory(tmp_path)
    history.append(NOW, 50, 3.5)
    before = history.path.read_bytes()
    warning = Mock()
    monkeypatch.setattr("meshtastic_tools.core.metrics.os.replace", Mock(side_effect=OSError("disk full")))
    monkeypatch.setattr("meshtastic_tools.core.metrics.get_logger", Mock(return_value=Mock(warning=warning)))
    history.append(NOW + timedelta(minutes=1), 60, 4.0)
    assert history.path.read_bytes() == before
    assert not list(tmp_path.glob(".metrics-*.tmp"))
    warning.assert_called_once()


@pytest.mark.parametrize("at,battery,voltage", [
    (NOW.replace(tzinfo=None), 50, 3.5), (NOW, 150, 3.5), (NOW, 50, float("nan")),
])
def test_invalid_append_preserves_previous_history(tmp_path, at, battery, voltage):
    history = MetricHistory(tmp_path)
    history.append(NOW, 50, 3.5)
    before = history.path.read_bytes()
    with pytest.raises(ValueError):
        history.append(at, battery, voltage)
    assert history.path.read_bytes() == before
