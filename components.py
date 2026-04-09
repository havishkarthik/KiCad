"""
components.py
-------------
Loads and queries the component database (components.json).
Each component entry includes pin definitions, supported protocols,
and voltage requirements.
"""

from __future__ import annotations

import json
import os
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DB_PATH = os.path.join(os.path.dirname(__file__), "components.json")


# ---------------------------------------------------------------------------
# Data model helpers
# ---------------------------------------------------------------------------

class Pin:
    """Represents a single pin on a component."""

    def __init__(self, name: str, pin_type: str, role: str, bus: str | None = None) -> None:
        self.name = name          # e.g. "TX", "SDA"
        self.pin_type = pin_type  # e.g. "uart", "i2c", "gpio", "power"
        self.role = role          # e.g. "tx", "rx", "sda", "vcc", "gnd"
        self.bus = bus            # e.g. "UART0", "I2C0" (optional)

    def __repr__(self) -> str:
        return f"Pin({self.name}, type={self.pin_type}, role={self.role})"


class Component:
    """Represents a hardware component loaded from the database."""

    def __init__(self, key: str, data: dict[str, Any]) -> None:
        self.key = key                        # lowercase lookup key, e.g. "esp32"
        self.name: str = data["name"]
        self.description: str = data.get("description", "")
        self.voltage: float = data.get("voltage", 3.3)
        self.protocols: list[str] = data.get("protocols", [])
        self.pins: dict[str, Pin] = {}

        for pin_name, pin_data in data.get("pins", {}).items():
            self.pins[pin_name] = Pin(
                name=pin_name,
                pin_type=pin_data["type"],
                role=pin_data["role"],
                bus=pin_data.get("bus"),
            )

    def get_pins_by_type(self, pin_type: str) -> list[Pin]:
        """Return all pins of a given type (e.g. 'uart', 'i2c')."""
        return [p for p in self.pins.values() if p.pin_type == pin_type]

    def get_pins_by_role(self, role: str) -> list[Pin]:
        """Return all pins with a given role (e.g. 'tx', 'sda', 'vcc')."""
        return [p for p in self.pins.values() if p.role == role]

    def __repr__(self) -> str:
        return f"Component({self.key!r}, protocols={self.protocols})"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class ComponentDB:
    """Loads the JSON component database and provides lookup helpers."""

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self._db: dict[str, Component] = {}
        self._load(db_path)

    def _load(self, path: str) -> None:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Component database not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        for key, data in raw.get("components", {}).items():
            self._db[key.lower()] = Component(key=key.lower(), data=data)

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def get(self, key: str) -> Component | None:
        """Return a Component by its exact lowercase key, or None."""
        return self._db.get(key.lower())

    def all_keys(self) -> list[str]:
        """Return all component keys in the database."""
        return list(self._db.keys())

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._db

    def __repr__(self) -> str:
        return f"ComponentDB({list(self._db.keys())})"
