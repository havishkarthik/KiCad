"""
main.py
-------
CLI entry-point for the KiCad circuit wiring assistant.

Usage
-----
    python main.py "ESP32 + GPS + buzzer"
    python main.py "esp32, oled, bme280, sd card"
    python main.py          # runs built-in examples

Outputs
-------
  1. Parsed component list
  2. Human-readable wiring list
  3. JSON netlist (printed to stdout; can be redirected to a file)
"""

from __future__ import annotations

import json
import sys
import textwrap

from components import ComponentDB
from parser import parse_input
from connection_engine import build_connections


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_SEPARATOR = "─" * 60


def _section(title: str) -> None:
    print(f"\n{_SEPARATOR}")
    print(f"  {title}")
    print(_SEPARATOR)


def _run(user_input: str, db: ComponentDB) -> None:
    """Parse input, build connections, and print results."""
    print(f'\nInput: "{user_input}"')

    # ── 1. Parse ──────────────────────────────────────────────────────
    result = parse_input(user_input, db)

    if result.unknown:
        print(
            "\n⚠  Unknown component(s): "
            + ", ".join(f'"{u}"' for u in result.unknown)
        )
        print(
            "   Available: " + ", ".join(db.all_keys())
        )

    if not result.components:
        print("   No known components found. Aborting.")
        return

    _section("Parsed Components")
    for comp in result.components:
        print(f"  • {comp.name:30s}  protocols={comp.protocols}  voltage={comp.voltage}V")

    # ── 2. Build connections ──────────────────────────────────────────
    conn = build_connections(result.components)

    if conn.warnings:
        _section("Warnings")
        for w in conn.warnings:
            print(f"  ⚠  {w}")

    _section("Wiring List (human-readable)")
    if conn.wires:
        for line in conn.to_wiring_list():
            print(f"  {line}")
    else:
        print("  (no connections generated)")

    _section("Netlist (JSON)")
    netlist = conn.to_netlist()
    print(json.dumps(netlist, indent=2))


# ---------------------------------------------------------------------------
# Built-in examples
# ---------------------------------------------------------------------------

_EXAMPLES = [
    "ESP32 + GPS + buzzer",
    "esp32, oled display, bme280",
    "esp32 + sd card + mpu6050 + led",
]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    db = ComponentDB()

    if len(sys.argv) > 1:
        # Treat all CLI arguments joined as one input string
        user_input = " ".join(sys.argv[1:])
        _run(user_input, db)
    else:
        # Run all built-in examples
        print(textwrap.dedent("""\
            ╔══════════════════════════════════════════════════════════╗
            ║   KiCad Circuit Wiring Assistant — Example Outputs       ║
            ╚══════════════════════════════════════════════════════════╝
        """))
        for example in _EXAMPLES:
            _run(example, db)
            print()


if __name__ == "__main__":
    main()
