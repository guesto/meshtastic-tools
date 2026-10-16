"""Remove credentials from the human-readable Meshtastic CLI output."""

import re


_SECRET = re.compile(
    r'("(?:private[_-]?key|psk|password|fixed[_-]?pin|session[_-]?passkey)"\s*:\s*)'
    r'("(?:\\.|[^"\\])*"|-?\d+|\[[^\]]*\])',
    re.IGNORECASE,
)
_CHANNEL_URL = re.compile(r"https?://(?:www\.)?meshtastic\.org/[ev]/#[^\s\"<>]+", re.IGNORECASE)


def redact_secrets(content: str) -> str:
    """Preserve diagnostics while removing keys, passwords and channel QR URLs."""
    content = _SECRET.sub(lambda match: match.group(1) + '"[REDACTED]"', content)
    return _CHANNEL_URL.sub("[REDACTED CHANNEL URL]", content)
