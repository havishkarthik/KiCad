"""
parser.py
---------
Converts a free-text user input string (e.g. "ESP32 + GPS + buzzer")
into a list of Component objects by looking them up in the database.

Supported input separators: '+', ',', 'and' (case-insensitive).
Unknown tokens are reported as errors rather than silently ignored.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from components import Component, ComponentDB


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class ParseResult(NamedTuple):
    components: list[Component]   # successfully resolved components
    unknown: list[str]            # tokens that could not be matched


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

# Words filtered out before looking up a token (articles and common fillers
# that appear *around* component names, not as part of them)
_STOP_WORDS: set[str] = {"a", "an", "the", "with", "using"}

# Simple alias table: maps alternative names to DB keys
_ALIASES: dict[str, str] = {
    "neo6m":    "gps",
    "neo-6m":   "gps",
    "gps":      "gps",
    "neo":      "gps",
    "ssd1306":  "oled",
    "display":  "oled",
    "screen":   "oled",
    "bme":      "bme280",
    "bme280":   "bme280",
    "mpu":      "mpu6050",
    "mpu6050":  "mpu6050",
    "imu":      "mpu6050",
    "gyro":     "mpu6050",
    "sd":       "sd_card",
    "sdcard":   "sd_card",
    "sd_card":  "sd_card",
    "buzzer":   "buzzer",
    "beeper":   "buzzer",
    "led":      "led",
    "esp32":    "esp32",
    "esp":      "esp32",
}


def _normalise_token(raw: str) -> str:
    """Lowercase, strip punctuation, remove noise words, apply aliases."""
    token = raw.lower().strip()
    # Remove non-alphanumeric characters except hyphen and underscore
    token = re.sub(r"[^a-z0-9_\-]", "", token)
    # Remove filtered words if the full token is one
    if token in _STOP_WORDS:
        return ""
    return token


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_input(user_input: str, db: ComponentDB) -> ParseResult:
    """
    Parse a user description string into a list of Component objects.

    Parameters
    ----------
    user_input:
        Raw text, e.g. ``"ESP32 + GPS + buzzer"`` or
        ``"esp32, oled display, bme280"``.
    db:
        The loaded ComponentDB instance.

    Returns
    -------
    ParseResult
        Named tuple with ``.components`` (list[Component]) and
        ``.unknown`` (list[str] of unmatched tokens).
    """
    if not user_input or not user_input.strip():
        raise ValueError("Input string is empty.")

    # Split on '+', ',', or the word 'and'
    raw_tokens = re.split(r"\+|,|\band\b", user_input, flags=re.IGNORECASE)

    resolved: list[Component] = []
    unknown: list[str] = []
    seen_keys: set[str] = set()

    for raw in raw_tokens:
        # A token may contain multiple words; try the full phrase first,
        # then fall back to individual words.
        words = raw.strip().split()
        candidates = [" ".join(words).lower()] + [w.lower() for w in words]

        matched: Component | None = None
        for candidate in candidates:
            normalised = _normalise_token(candidate)
            if not normalised:
                continue
            # 1. Check alias table
            db_key = _ALIASES.get(normalised)
            # 2. Fallback: direct DB lookup
            if db_key is None and normalised in db:
                db_key = normalised
            if db_key is not None:
                component = db.get(db_key)
                if component is not None:
                    matched = component
                    break

        if matched is None:
            # Only report unknown if the original raw token is non-empty
            stripped = raw.strip()
            if stripped:
                unknown.append(stripped)
        elif matched.key not in seen_keys:
            seen_keys.add(matched.key)
            resolved.append(matched)
        # Silently drop exact duplicates

    return ParseResult(components=resolved, unknown=unknown)
