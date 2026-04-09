"""
main.py
-------
CLI entry-point for the KiCad circuit wiring assistant.

Usage
-----
    python main.py "ESP32 + GPS + buzzer"
    python main.py "esp32, oled, bme280, sd card"
    python main.py          # runs built-in examples + Circuit demo

Outputs
-------
  1. Parsed component list
  2. Human-readable wiring list
  3. JSON netlist (from the batch engine)
  4. Circuit demo: validated connections + KiCad-style text netlist
"""

from __future__ import annotations

import json
import sys
import textwrap

from components import ComponentDB
from parser import parse_input
from connection_engine import (
    build_connections,
    Circuit,
    CircuitError,
    export_netlist,
)


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
# Circuit demo – showcases the new Circuit / Net / Connection classes
# ---------------------------------------------------------------------------

def _run_circuit_demo(db: ComponentDB) -> None:
    """
    Demonstrate the full Circuit API:
      * Validated connections (protocol rules, VCC↔GND protection,
        voltage compatibility)
      * Auto-connect helpers for UART, I2C, SPI, and power
      * generate_nets() + export_netlist() output

    Circuit: ESP32  +  OLED (I2C)  +  GPS (UART)  +  LED + Resistor
    """
    print(textwrap.dedent("""\
        ╔══════════════════════════════════════════════════════════╗
        ║   Circuit Demo  (Circuit / Net / Connection classes)      ║
        ╚══════════════════════════════════════════════════════════╝
    """))

    esp32    = db.get("esp32")
    oled     = db.get("oled")
    gps      = db.get("gps")
    led      = db.get("led")
    resistor = db.get("resistor")

    # ── Build the circuit ────────────────────────────────────────────
    circuit = Circuit()
    for comp in (esp32, oled, gps, led, resistor):
        circuit.add_component(comp)

    # UART: ESP32 ↔ GPS
    circuit.auto_connect_uart(esp32, gps)

    # I2C: ESP32 ↔ OLED
    circuit.auto_connect_i2c(esp32, oled)

    # GPIO chain: ESP32.GPIO2 → Resistor.PIN1, Resistor.PIN2 → LED.ANODE
    circuit.connect(esp32, "GPIO2", resistor, "PIN1", net_name="GPIO_LED_SIG")
    circuit.connect(resistor, "PIN2", led, "ANODE",   net_name="GPIO_LED_SIG")

    # Power: connect all components to the same VCC and GND nets
    for peripheral in (oled, gps, resistor):
        circuit.auto_connect_power(esp32, peripheral)
    # LED CATHODE goes to GND
    circuit.connect(esp32, "GND", led, "CATHODE", net_name="GND")

    # ── Optional: show that validation catches illegal connections ────
    _section("Validation Checks")
    print("  Attempting illegal connections to verify rule enforcement…\n")

    # 1. VCC ↔ GND short-circuit
    try:
        circuit.connect(esp32, "VCC", esp32, "GND")
        print("  ✗  Short-circuit check failed (should have raised)")
    except CircuitError as exc:
        print(f"  ✓  Short-circuit prevented: {exc}")

    # 2. UART protocol mismatch (TX → TX instead of TX → RX)
    try:
        circuit.connect(esp32, "TX0", gps, "TX")
        print("  ✗  Protocol check failed (should have raised)")
    except CircuitError as exc:
        print(f"  ✓  Protocol mismatch caught: {exc}")

    # 3. I2C role mismatch (SDA → SCL)
    try:
        circuit.connect(esp32, "SDA", oled, "SCL")
        print("  ✗  I2C role check failed (should have raised)")
    except CircuitError as exc:
        print(f"  ✓  I2C mismatch caught: {exc}")

    # ── Generate nets and export netlist ─────────────────────────────
    circuit.generate_nets()

    _section("Circuit Netlist (KiCad-style text)")
    print(export_netlist(circuit))


# ---------------------------------------------------------------------------
# Built-in batch examples
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
        # ── Batch builder examples ────────────────────────────────────
        print(textwrap.dedent("""\
            ╔══════════════════════════════════════════════════════════╗
            ║   KiCad Circuit Wiring Assistant — Batch Examples        ║
            ╚══════════════════════════════════════════════════════════╝
        """))
        for example in _EXAMPLES:
            _run(example, db)
            print()

        # ── Full Circuit demo ─────────────────────────────────────────
        _run_circuit_demo(db)


if __name__ == "__main__":
    main()

