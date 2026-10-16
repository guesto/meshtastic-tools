"""Keep tests independent of the operator's live configuration."""

import pytest


@pytest.fixture(autouse=True)
def isolate_config_environment(monkeypatch):
    monkeypatch.delenv("MESHTASTIC_TOOLS_CONFIG", raising=False)
