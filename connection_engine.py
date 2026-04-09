"""
connection_engine.py
--------------------
Determines wiring connections between a list of components using
protocol-based rules:

  * UART  → TX  ↔  RX  (peripheral TX  → controller RX, and vice-versa)
  * I2C   → SDA ↔  SDA, SCL ↔ SCL  (bus topology: all SDA together, all SCL together)
  * SPI   → MOSI ↔ MOSI, MISO ↔ MISO, SCK ↔ SCK, each peripheral gets its own CS
  * GPIO  → peripheral SIG/IO pin → controller GPIO pin
  * Power → all VCC pins to the appropriate rail; all GND pins together

Outputs
-------
  * A human-readable wiring list  (list[str])
  * A JSON-serialisable netlist   (dict)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from components import Component, Pin


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Wire:
    """A single connection between two pins on two components."""
    from_component: str
    from_pin: str
    to_component: str
    to_pin: str
    net: str = ""

    def human_readable(self) -> str:
        return f"{self.from_component}.{self.from_pin}  →  {self.to_component}.{self.to_pin}  [{self.net}]"


@dataclass
class ConnectionResult:
    wires: list[Wire] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def to_wiring_list(self) -> list[str]:
        """Return a clean, human-readable list of connections."""
        return [w.human_readable() for w in self.wires]

    def to_netlist(self) -> dict[str, Any]:
        """
        Return a JSON-serialisable netlist dict.

        Structure::

            {
              "nets": {
                "<net_name>": [
                  {"component": "...", "pin": "..."},
                  ...
                ]
              },
              "wires": [
                {"from": {"component": "...", "pin": "..."},
                 "to":   {"component": "...", "pin": "..."},
                 "net":  "..."},
                ...
              ]
            }
        """
        nets: dict[str, list[dict[str, str]]] = {}
        for wire in self.wires:
            net = wire.net
            nets.setdefault(net, [])
            # Add endpoints if not already present
            ep_from = {"component": wire.from_component, "pin": wire.from_pin}
            ep_to   = {"component": wire.to_component,   "pin": wire.to_pin}
            if ep_from not in nets[net]:
                nets[net].append(ep_from)
            if ep_to not in nets[net]:
                nets[net].append(ep_to)

        return {
            "nets": nets,
            "wires": [
                {
                    "from": {"component": w.from_component, "pin": w.from_pin},
                    "to":   {"component": w.to_component,   "pin": w.to_pin},
                    "net":  w.net,
                }
                for w in self.wires
            ],
        }


# ---------------------------------------------------------------------------
# Connection engine
# ---------------------------------------------------------------------------

class ConnectionEngine:
    """
    Builds wiring connections for a list of components.

    The *first* component in the list that supports a given protocol is
    treated as the **bus master / controller** for that protocol.
    If no ESP32 is present, the first component with the protocol wins.
    """

    def __init__(self, components: list[Component]) -> None:
        self.components = components
        self._result = ConnectionResult()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def build(self) -> ConnectionResult:
        """Run all connection rules and return the result."""
        self._result = ConnectionResult()

        if not self.components:
            self._result.warnings.append("No components provided.")
            return self._result

        self._connect_power()
        self._connect_uart()
        self._connect_i2c()
        self._connect_spi()
        self._connect_gpio()

        return self._result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _controller(self) -> Component | None:
        """Return the ESP32 if present, otherwise the first component."""
        for c in self.components:
            if c.key == "esp32":
                return c
        return self.components[0] if self.components else None

    def _peripherals(self, exclude_key: str) -> list[Component]:
        return [c for c in self.components if c.key != exclude_key]

    # ------------------------------------------------------------------
    # Power connections
    # ------------------------------------------------------------------

    def _connect_power(self) -> None:
        """Connect all VCC pins to POWER_3V3 (or POWER_5V) and GND to GND."""
        for comp in self.components:
            for pin in comp.get_pins_by_role("vcc"):
                net = f"POWER_{comp.voltage}V".replace(".", "_")
                self._result.wires.append(Wire(
                    from_component=comp.name,
                    from_pin=pin.name,
                    to_component="POWER_RAIL",
                    to_pin=net,
                    net=net,
                ))
            for pin in comp.get_pins_by_role("gnd"):
                self._result.wires.append(Wire(
                    from_component=comp.name,
                    from_pin=pin.name,
                    to_component="POWER_RAIL",
                    to_pin="GND",
                    net="GND",
                ))

    # ------------------------------------------------------------------
    # UART connections
    # ------------------------------------------------------------------

    def _connect_uart(self) -> None:
        """
        For each UART peripheral, cross-connect its TX/RX to the
        controller's available UART TX/RX pins.
        """
        ctrl = self._controller()
        if ctrl is None:
            return

        # Pool of available UART buses on the controller
        ctrl_tx_pins = {p.bus: p for p in ctrl.get_pins_by_role("tx") if p.bus}
        ctrl_rx_pins = {p.bus: p for p in ctrl.get_pins_by_role("rx") if p.bus}
        available_buses = list(ctrl_tx_pins.keys())
        bus_index = 0

        for peri in self._peripherals(ctrl.key):
            peri_tx = peri.get_pins_by_role("tx")
            peri_rx = peri.get_pins_by_role("rx")
            if not peri_tx and not peri_rx:
                continue  # Not a UART device

            if bus_index >= len(available_buses):
                self._result.warnings.append(
                    f"No free UART bus on {ctrl.name} for {peri.name}."
                )
                continue

            bus = available_buses[bus_index]
            bus_index += 1
            net_suffix = bus.replace(" ", "_")

            if peri_tx and bus in ctrl_rx_pins:
                net = f"UART_{net_suffix}_RX"
                self._result.wires.append(Wire(
                    from_component=peri.name,
                    from_pin=peri_tx[0].name,
                    to_component=ctrl.name,
                    to_pin=ctrl_rx_pins[bus].name,
                    net=net,
                ))
            if peri_rx and bus in ctrl_tx_pins:
                net = f"UART_{net_suffix}_TX"
                self._result.wires.append(Wire(
                    from_component=ctrl.name,
                    from_pin=ctrl_tx_pins[bus].name,
                    to_component=peri.name,
                    to_pin=peri_rx[0].name,
                    net=net,
                ))

    # ------------------------------------------------------------------
    # I2C connections
    # ------------------------------------------------------------------

    def _connect_i2c(self) -> None:
        """
        All I2C devices share SDA and SCL bus lines from the controller.
        """
        ctrl = self._controller()
        if ctrl is None:
            return

        ctrl_sda_list = ctrl.get_pins_by_role("sda")
        ctrl_scl_list = ctrl.get_pins_by_role("scl")
        if not ctrl_sda_list or not ctrl_scl_list:
            return

        ctrl_sda = ctrl_sda_list[0]
        ctrl_scl = ctrl_scl_list[0]

        for peri in self._peripherals(ctrl.key):
            peri_sda = peri.get_pins_by_role("sda")
            peri_scl = peri.get_pins_by_role("scl")
            if not peri_sda and not peri_scl:
                continue  # Not an I2C device

            if peri_sda:
                self._result.wires.append(Wire(
                    from_component=ctrl.name,
                    from_pin=ctrl_sda.name,
                    to_component=peri.name,
                    to_pin=peri_sda[0].name,
                    net="I2C_SDA",
                ))
            if peri_scl:
                self._result.wires.append(Wire(
                    from_component=ctrl.name,
                    from_pin=ctrl_scl.name,
                    to_component=peri.name,
                    to_pin=peri_scl[0].name,
                    net="I2C_SCL",
                ))

    # ------------------------------------------------------------------
    # SPI connections
    # ------------------------------------------------------------------

    def _connect_spi(self) -> None:
        """
        MOSI, MISO, SCK are shared; each peripheral gets a unique CS line.
        """
        ctrl = self._controller()
        if ctrl is None:
            return

        ctrl_mosi = ctrl.get_pins_by_role("mosi")
        ctrl_miso = ctrl.get_pins_by_role("miso")
        ctrl_sck  = ctrl.get_pins_by_role("sck")
        ctrl_cs   = ctrl.get_pins_by_role("cs")
        if not ctrl_mosi and not ctrl_miso and not ctrl_sck:
            return

        cs_index = 0
        for peri in self._peripherals(ctrl.key):
            peri_mosi = peri.get_pins_by_role("mosi")
            peri_miso = peri.get_pins_by_role("miso")
            peri_sck  = peri.get_pins_by_role("sck")
            peri_cs   = peri.get_pins_by_role("cs")
            if not peri_mosi and not peri_miso and not peri_sck:
                continue  # Not an SPI device

            if ctrl_mosi and peri_mosi:
                self._result.wires.append(Wire(
                    from_component=ctrl.name,
                    from_pin=ctrl_mosi[0].name,
                    to_component=peri.name,
                    to_pin=peri_mosi[0].name,
                    net="SPI_MOSI",
                ))
            if ctrl_miso and peri_miso:
                self._result.wires.append(Wire(
                    from_component=ctrl.name,
                    from_pin=ctrl_miso[0].name,
                    to_component=peri.name,
                    to_pin=peri_miso[0].name,
                    net="SPI_MISO",
                ))
            if ctrl_sck and peri_sck:
                self._result.wires.append(Wire(
                    from_component=ctrl.name,
                    from_pin=ctrl_sck[0].name,
                    to_component=peri.name,
                    to_pin=peri_sck[0].name,
                    net="SPI_SCK",
                ))
            if ctrl_cs and peri_cs:
                net = f"SPI_CS{cs_index}"
                self._result.wires.append(Wire(
                    from_component=ctrl.name,
                    from_pin=ctrl_cs[0].name,
                    to_component=peri.name,
                    to_pin=peri_cs[0].name,
                    net=net,
                ))
                cs_index += 1

    # ------------------------------------------------------------------
    # GPIO connections
    # ------------------------------------------------------------------

    def _connect_gpio(self) -> None:
        """
        Connect peripheral GPIO/SIG pins to available GPIO pins on the
        controller.  Uses GPIO2, GPIO4, … incrementally.
        """
        ctrl = self._controller()
        if ctrl is None:
            return

        ctrl_gpio_pins = [
            p for p in ctrl.get_pins_by_type("gpio")
            if p.role == "io"
        ]
        gpio_pool = iter(ctrl_gpio_pins)

        for peri in self._peripherals(ctrl.key):
            peri_io_pins = [
                p for p in peri.get_pins_by_type("gpio")
                if p.role == "io"
            ]
            for peri_pin in peri_io_pins:
                ctrl_pin = next(gpio_pool, None)
                if ctrl_pin is None:
                    self._result.warnings.append(
                        f"No free GPIO pin on {ctrl.name} for "
                        f"{peri.name}.{peri_pin.name}."
                    )
                    continue
                net = f"GPIO_{ctrl_pin.name}_{peri.name.replace(' ', '_')}"
                self._result.wires.append(Wire(
                    from_component=ctrl.name,
                    from_pin=ctrl_pin.name,
                    to_component=peri.name,
                    to_pin=peri_pin.name,
                    net=net,
                ))


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def build_connections(components: list[Component]) -> ConnectionResult:
    """Shorthand: create an engine, run it, and return the result."""
    return ConnectionEngine(components).build()
