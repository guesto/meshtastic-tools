"""Metric caching follows successful snapshot storage and cannot fail collection."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from meshtastic_tools.core.config import StorageConfig
from meshtastic_tools.core.device import DeviceManager
from meshtastic_tools.core.exceptions import CollectionError
from meshtastic_tools.core.metrics import MetricHistory
from meshtastic_tools.core.timeline import TimelineHistory
from meshtastic_tools.logger.collector import MeshtasticCollector
from meshtastic_tools.logger.storage import StorageManager
from tests.test_snapshot import info_text


@pytest.fixture
def collector(tmp_path):
    device = Mock(spec=DeviceManager)
    device.name = "alpha"
    device.get_info.return_value = SimpleNamespace(node_id="!00000001")
    device.execute_command.return_value = SimpleNamespace(
        stdout=info_text({"batteryLevel": 75, "voltage": 4.1}))
    return MeshtasticCollector(device, StorageManager(StorageConfig(data_dir=tmp_path), "alpha"))


@pytest.mark.parametrize("kind", ["info"])
def test_metrics_written_on_successful_collection(collector, kind):
    before = datetime.now(timezone.utc)
    path = getattr(collector, "collect_" + kind)()
    after = datetime.now(timezone.utc)
    assert path.is_file()
    history = MetricHistory(collector.storage.device_path.parent)
    assert history.path.is_file()
    assert not (collector.storage.device_path / ".metrics.json").exists()
    samples = history.load()
    assert len(samples) == 1
    assert (samples[0].battery_level, samples[0].voltage) == (75, 4.1)
    assert samples[0].at.utcoffset() == timedelta(0)
    assert before <= samples[0].at <= after
    collector.device.execute_command.assert_called_once()


@pytest.mark.parametrize("kind", ["info"])
@pytest.mark.parametrize("failure", ["command", "storage"])
def test_metrics_not_written_on_failed_collection(collector, kind, failure, monkeypatch):
    if failure == "command":
        collector.device.execute_command.side_effect = CollectionError("offline")
    else:
        monkeypatch.setattr(collector.storage, "save", Mock(side_effect=OSError("disk full")))
    with pytest.raises(CollectionError):
        getattr(collector, "collect_" + kind)()
    assert not MetricHistory(collector.storage.device_path.parent).path.exists()


@pytest.mark.parametrize("kind", ["info"])
@pytest.mark.parametrize("failure", ["parse", "append"])
def test_metrics_failure_warns_and_keeps_collection_successful(collector, kind, failure, monkeypatch):
    target = ("meshtastic_tools.logger.collector.parse_snapshot" if failure == "parse"
              else "meshtastic_tools.logger.collector.MetricHistory.append")
    monkeypatch.setattr(target, Mock(side_effect=RuntimeError("cache unavailable")))
    collector.logger = Mock()
    assert getattr(collector, "collect_" + kind)().is_file()
    collector.logger.warning.assert_called_once()
    assert "cache unavailable" in collector.logger.warning.call_args.args[0]
    history = TimelineHistory(collector.storage.device_path.parent / kind).load()
    assert [event.type for event in history] == ["snapshot"]
