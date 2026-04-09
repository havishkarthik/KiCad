"""
connection_engine.py
--------------------
Two-layer circuit wiring engine.

Layer 1 – Simple batch builder (backward-compatible)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  Wire, ConnectionResult, ConnectionEngine, build_connections

  Determines wiring connections between a list of components using
  protocol-based rules and returns a flat list of Wire objects plus a
  JSON-serialisable netlist dict.

Layer 2 – Full circuit model (new)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  CircuitError, Connection, Net, Circuit, export_netlist

  Provides an interactive Circuit object where components and
  connections are added one at a time.  Every connection is validated
  before being accepted:

    * VCC ↔ GND short-circuit prevention
    * Protocol role enforcement (UART TX→RX, I2C SDA↔SDA/SCL↔SCL,
      SPI MOSI↔MOSI / MISO↔MISO / SCK↔SCK / CS↔CS)
    * Voltage-level compatibility check (warns when > 0.5 V difference)

  Auto-connection helpers wire standard protocols automatically by
  querying pin roles via ``get_pins_by_role()``.

  ``generate_nets()`` groups all connections into named Net objects
  (pins sharing a net_name become a single net).

  ``export_netlist(circuit)`` formats the circuit as a readable,
  KiCad-style text netlist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from components import Component, Pin


# ===========================================================================
# Layer 1 – backward-compatible batch builder
# ===========================================================================

@dataclass
class Wire:
    """A single connection between two pins on two components."""
    from_component: str
    from_pin: str
    to_component: str
    to_pin: str
    net: str = ""

    def human_readable(self) -> str:
        return (
            f"{self.from_component}.{self.from_pin}"
            f"  →  {self.to_component}.{self.to_pin}"
            f"  [{self.net}]"
        )


@dataclass
class ConnectionResult:
    wires: list[Wire] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_wiring_list(self) -> list[str]:
        """Return a clean, human-readable list of connections."""
        return [w.human_readable() for w in self.wires]

    def to_netlist(self) -> dict[str, Any]:
        """Return a JSON-serialisable netlist dict.

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
        ctrl = self._controller()
        if ctrl is None:
            return

        ctrl_tx_pins = {p.bus: p for p in ctrl.get_pins_by_role("tx") if p.bus}
        ctrl_rx_pins = {p.bus: p for p in ctrl.get_pins_by_role("rx") if p.bus}
        available_buses = list(ctrl_tx_pins.keys())
        bus_index = 0

        for peri in self._peripherals(ctrl.key):
            peri_tx = peri.get_pins_by_role("tx")
            peri_rx = peri.get_pins_by_role("rx")
            if not peri_tx and not peri_rx:
                continue

            if bus_index >= len(available_buses):
                self._result.warnings.append(
                    f"No free UART bus on {ctrl.name} for {peri.name}."
                )
                continue

            bus = available_buses[bus_index]
            bus_index += 1
            net_suffix = bus.replace(" ", "_")

            if peri_tx and bus in ctrl_rx_pins:
                self._result.wires.append(Wire(
                    from_component=peri.name,
                    from_pin=peri_tx[0].name,
                    to_component=ctrl.name,
                    to_pin=ctrl_rx_pins[bus].name,
                    net=f"UART_{net_suffix}_RX",
                ))
            if peri_rx and bus in ctrl_tx_pins:
                self._result.wires.append(Wire(
                    from_component=ctrl.name,
                    from_pin=ctrl_tx_pins[bus].name,
                    to_component=peri.name,
                    to_pin=peri_rx[0].name,
                    net=f"UART_{net_suffix}_TX",
                ))

    # ------------------------------------------------------------------
    # I2C connections
    # ------------------------------------------------------------------

    def _connect_i2c(self) -> None:
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
                continue

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
                continue

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
                self._result.wires.append(Wire(
                    from_component=ctrl.name,
                    from_pin=ctrl_cs[0].name,
                    to_component=peri.name,
                    to_pin=peri_cs[0].name,
                    net=f"SPI_CS{cs_index}",
                ))
                cs_index += 1

    # ------------------------------------------------------------------
    # GPIO connections
    # ------------------------------------------------------------------

    def _connect_gpio(self) -> None:
        ctrl = self._controller()
        if ctrl is None:
            return

        ctrl_gpio_pins = [p for p in ctrl.get_pins_by_type("gpio") if p.role == "io"]
        gpio_pool = iter(ctrl_gpio_pins)

        for peri in self._peripherals(ctrl.key):
            peri_io_pins = [p for p in peri.get_pins_by_type("gpio") if p.role == "io"]
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


def build_connections(components: list[Component]) -> ConnectionResult:
    """Shorthand: create an engine, run it, and return the result."""
    return ConnectionEngine(components).build()


# ===========================================================================
# Layer 2 – full circuit model
# ===========================================================================

# ---------------------------------------------------------------------------
# Validation tables
# ---------------------------------------------------------------------------

# Maps (pin_type, role) → the set of (pin_type, role) pairs it may connect to.
# Power pins are intentionally omitted here; they are checked separately.
_PROTOCOL_RULES: dict[tuple[str, str], set[tuple[str, str]]] = {
    ("uart", "tx"):   {("uart", "rx")},
    ("uart", "rx"):   {("uart", "tx")},
    ("i2c",  "sda"):  {("i2c",  "sda")},
    ("i2c",  "scl"):  {("i2c",  "scl")},
    ("spi",  "mosi"): {("spi",  "mosi")},
    ("spi",  "miso"): {("spi",  "miso")},
    ("spi",  "sck"):  {("spi",  "sck")},
    ("spi",  "cs"):   {("spi",  "cs")},
    ("gpio", "io"):   {("gpio", "io")},
}

# Maximum allowed voltage difference (V) between signal-level pins.
_MAX_VOLTAGE_DELTA: float = 0.5


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CircuitError(Exception):
    """Raised when a circuit connection violates an electrical rule."""


# ---------------------------------------------------------------------------
# Connection – a single two-pin electrical connection
# ---------------------------------------------------------------------------

@dataclass
class Connection:
    """Represents a single electrical connection between two component pins."""
    component_a: Component
    pin_a: str            # pin name on component_a
    component_b: Component
    pin_b: str            # pin name on component_b
    net_name: str = ""    # populated by Circuit.connect()

    def __str__(self) -> str:
        return (
            f"{self.component_a.name}.{self.pin_a}"
            f"  ─  {self.component_b.name}.{self.pin_b}"
            f"  [{self.net_name}]"
        )


# ---------------------------------------------------------------------------
# Net – a named group of connected pins
# ---------------------------------------------------------------------------

class Net:
    """
    Represents an electrical net: a set of pins that are all connected
    together (e.g. the I2C_SDA bus or the GND rail).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        # Each entry is (component, pin_name); duplicates are not stored.
        self.pins: list[tuple[Component, str]] = []

    def add_pin(self, component: Component, pin_name: str) -> None:
        """Add a (component, pin_name) pair to this net if not already present."""
        if (component, pin_name) not in self.pins:
            self.pins.append((component, pin_name))

    def __repr__(self) -> str:
        entries = ", ".join(f"{c.name}.{p}" for c, p in self.pins)
        return f"Net({self.name!r}, pins=[{entries}])"


# ---------------------------------------------------------------------------
# Circuit – the top-level container
# ---------------------------------------------------------------------------

class Circuit:
    """
    Interactive circuit builder.

    Workflow::

        circuit = Circuit()
        circuit.add_component(esp32)
        circuit.add_component(oled)
        circuit.auto_connect_i2c(esp32, oled)
        circuit.auto_connect_power(esp32, oled)
        circuit.generate_nets()
        print(export_netlist(circuit))
    """

    def __init__(self) -> None:
        self.components: list[Component] = []
        self.connections: list[Connection] = []
        self.nets: dict[str, Net] = {}
        self.warnings: list[str] = []
        self._net_counter: int = 0       # used for unnamed GPIO nets

    # ------------------------------------------------------------------
    # Component management
    # ------------------------------------------------------------------

    def add_component(self, component: Component) -> None:
        """Add a component to the circuit.  Duplicate adds are ignored."""
        if component not in self.components:
            self.components.append(component)

    # ------------------------------------------------------------------
    # Low-level connection primitive
    # ------------------------------------------------------------------

    def connect(
        self,
        comp_a: Component,
        pin_a_name: str,
        comp_b: Component,
        pin_b_name: str,
        net_name: str | None = None,
    ) -> Connection:
        """
        Connect ``comp_a.pin_a_name`` to ``comp_b.pin_b_name``.

        Parameters
        ----------
        comp_a, comp_b:
            Components (must already have been added via ``add_component``).
        pin_a_name, pin_b_name:
            Pin names as they appear in the component database.
        net_name:
            Optional net label.  When omitted, a name is derived
            automatically from the pin roles.

        Returns
        -------
        Connection
            The newly created (or already existing) connection.

        Raises
        ------
        CircuitError
            If the connection violates an electrical rule.
        """
        # ── resolve pins ───────────────────────────────────────────────
        pin_a = comp_a.pins.get(pin_a_name)
        if pin_a is None:
            raise CircuitError(
                f"{comp_a.name} has no pin '{pin_a_name}'. "
                f"Available: {list(comp_a.pins)}"
            )
        pin_b = comp_b.pins.get(pin_b_name)
        if pin_b is None:
            raise CircuitError(
                f"{comp_b.name} has no pin '{pin_b_name}'. "
                f"Available: {list(comp_b.pins)}"
            )

        # ── validate ───────────────────────────────────────────────────
        self._validate_connection(comp_a, pin_a, comp_b, pin_b)

        # ── duplicate check (order-independent) ────────────────────────
        for existing in self.connections:
            same = (
                existing.component_a is comp_a
                and existing.pin_a == pin_a_name
                and existing.component_b is comp_b
                and existing.pin_b == pin_b_name
            )
            reversed_ = (
                existing.component_a is comp_b
                and existing.pin_a == pin_b_name
                and existing.component_b is comp_a
                and existing.pin_b == pin_a_name
            )
            if same or reversed_:
                return existing  # already connected; return existing object

        # ── auto net name ──────────────────────────────────────────────
        if net_name is None:
            net_name = self._auto_net_name(comp_a, pin_a, comp_b, pin_b)

        conn = Connection(
            component_a=comp_a,
            pin_a=pin_a_name,
            component_b=comp_b,
            pin_b=pin_b_name,
            net_name=net_name,
        )
        self.connections.append(conn)
        return conn

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_connection(
        self,
        comp_a: Component,
        pin_a: Pin,
        comp_b: Component,
        pin_b: Pin,
    ) -> None:
        """
        Raise ``CircuitError`` if the connection is electrically illegal,
        or append to ``self.warnings`` for non-fatal issues.
        """
        # ── Rule 1: VCC ↔ GND short-circuit ───────────────────────────
        roles = {pin_a.role, pin_b.role}
        if "vcc" in roles and "gnd" in roles:
            raise CircuitError(
                f"Short circuit: cannot connect VCC to GND "
                f"({comp_a.name}.{pin_a.name} ↔ {comp_b.name}.{pin_b.name})."
            )

        # ── Rule 2: Protocol role enforcement ─────────────────────────
        key_a = (pin_a.pin_type, pin_a.role)
        key_b = (pin_b.pin_type, pin_b.role)
        allowed_for_a = _PROTOCOL_RULES.get(key_a)
        allowed_for_b = _PROTOCOL_RULES.get(key_b)

        if allowed_for_a is not None and key_b not in allowed_for_a:
            raise CircuitError(
                f"Protocol mismatch: {comp_a.name}.{pin_a.name} "
                f"(type={pin_a.pin_type}, role={pin_a.role}) cannot connect to "
                f"{comp_b.name}.{pin_b.name} "
                f"(type={pin_b.pin_type}, role={pin_b.role}). "
                f"Allowed targets: {allowed_for_a}"
            )
        if allowed_for_b is not None and key_a not in allowed_for_b:
            raise CircuitError(
                f"Protocol mismatch: {comp_b.name}.{pin_b.name} "
                f"(type={pin_b.pin_type}, role={pin_b.role}) cannot connect to "
                f"{comp_a.name}.{pin_a.name} "
                f"(type={pin_a.pin_type}, role={pin_a.role}). "
                f"Allowed targets: {allowed_for_b}"
            )

        # ── Rule 3: Voltage compatibility ─────────────────────────────
        # Only checked for signal-level pins (not power pins).
        if pin_a.pin_type != "power" and pin_b.pin_type != "power":
            delta = abs(comp_a.voltage - comp_b.voltage)
            if delta > _MAX_VOLTAGE_DELTA:
                self.warnings.append(
                    f"Voltage mismatch: {comp_a.name} ({comp_a.voltage}V) ↔ "
                    f"{comp_b.name} ({comp_b.voltage}V) — "
                    f"difference is {delta:.1f}V. "
                    "A level-shifter may be required."
                )

    # ------------------------------------------------------------------
    # Automatic net naming
    # ------------------------------------------------------------------

    def _auto_net_name(
        self,
        comp_a: Component,
        pin_a: Pin,
        comp_b: Component,
        pin_b: Pin,
    ) -> str:
        """Derive a meaningful net name from the pin roles."""
        role = pin_a.role

        if role == "vcc":
            v_str = str(comp_a.voltage).replace(".", "_")
            return f"POWER_{v_str}V"
        if role == "gnd":
            return "GND"
        if pin_a.pin_type == "uart":
            bus = pin_a.bus or pin_b.bus or "UART0"
            suffix = "TX" if role == "tx" else "RX"
            return f"UART_{bus}_{suffix}"
        if pin_a.pin_type == "i2c":
            return f"I2C_{role.upper()}"
        if pin_a.pin_type == "spi":
            return f"SPI_{role.upper()}"

        # Generic GPIO / passive net
        self._net_counter += 1
        return f"NET_{self._net_counter}"

    # ------------------------------------------------------------------
    # Auto-connection helpers
    # ------------------------------------------------------------------

    def auto_connect_uart(
        self,
        comp1: Component,
        comp2: Component,
    ) -> list[Connection]:
        """
        Cross-connect UART TX/RX pins between two components.

        ``comp1.TX → comp2.RX`` and ``comp2.TX → comp1.RX``.
        Raises ``CircuitError`` if either component has no UART pins.
        """
        tx1 = comp1.get_pins_by_role("tx")
        rx1 = comp1.get_pins_by_role("rx")
        tx2 = comp2.get_pins_by_role("tx")
        rx2 = comp2.get_pins_by_role("rx")

        if not tx1 and not rx1:
            raise CircuitError(f"{comp1.name} has no UART pins (tx/rx).")
        if not tx2 and not rx2:
            raise CircuitError(f"{comp2.name} has no UART pins (tx/rx).")

        created: list[Connection] = []

        # comp1.TX → comp2.RX
        if tx1 and rx2:
            bus = tx1[0].bus or "UART0"
            created.append(
                self.connect(comp1, tx1[0].name, comp2, rx2[0].name,
                             net_name=f"UART_{bus}_TX")
            )
        # comp2.TX → comp1.RX
        if tx2 and rx1:
            bus = tx2[0].bus or "UART0"
            created.append(
                self.connect(comp2, tx2[0].name, comp1, rx1[0].name,
                             net_name=f"UART_{bus}_RX")
            )

        if not created:
            self.warnings.append(
                f"auto_connect_uart: no TX/RX pairs found between "
                f"{comp1.name} and {comp2.name}."
            )
        return created

    def auto_connect_i2c(
        self,
        comp1: Component,
        comp2: Component,
    ) -> list[Connection]:
        """
        Connect I2C SDA and SCL lines between two components.

        Both devices share the same SDA and SCL nets (bus topology).
        Raises ``CircuitError`` if either component has no I2C pins.
        """
        sda1 = comp1.get_pins_by_role("sda")
        scl1 = comp1.get_pins_by_role("scl")
        sda2 = comp2.get_pins_by_role("sda")
        scl2 = comp2.get_pins_by_role("scl")

        if not sda1 and not scl1:
            raise CircuitError(f"{comp1.name} has no I2C pins (sda/scl).")
        if not sda2 and not scl2:
            raise CircuitError(f"{comp2.name} has no I2C pins (sda/scl).")

        created: list[Connection] = []

        if sda1 and sda2:
            created.append(
                self.connect(comp1, sda1[0].name, comp2, sda2[0].name,
                             net_name="I2C_SDA")
            )
        if scl1 and scl2:
            created.append(
                self.connect(comp1, scl1[0].name, comp2, scl2[0].name,
                             net_name="I2C_SCL")
            )

        if not created:
            self.warnings.append(
                f"auto_connect_i2c: no SDA/SCL pairs found between "
                f"{comp1.name} and {comp2.name}."
            )
        return created

    def auto_connect_spi(
        self,
        master: Component,
        slave: Component,
        cs_index: int = 0,
    ) -> list[Connection]:
        """
        Connect SPI MOSI, MISO, SCK, and CS lines.

        ``master`` provides MOSI/MISO/SCK/CS; ``slave`` mirrors them.
        Raises ``CircuitError`` if either component lacks SPI pins.
        """
        roles = ("mosi", "miso", "sck", "cs")
        master_pins = {r: master.get_pins_by_role(r) for r in roles}
        slave_pins  = {r: slave.get_pins_by_role(r)  for r in roles}

        has_master = any(master_pins[r] for r in ("mosi", "miso", "sck"))
        has_slave  = any(slave_pins[r]  for r in ("mosi", "miso", "sck"))
        if not has_master:
            raise CircuitError(f"{master.name} has no SPI data pins.")
        if not has_slave:
            raise CircuitError(f"{slave.name} has no SPI data pins.")

        created: list[Connection] = []
        net_map = {
            "mosi": "SPI_MOSI",
            "miso": "SPI_MISO",
            "sck":  "SPI_SCK",
            "cs":   f"SPI_CS{cs_index}",
        }
        for role in roles:
            if master_pins[role] and slave_pins[role]:
                created.append(
                    self.connect(
                        master, master_pins[role][0].name,
                        slave,  slave_pins[role][0].name,
                        net_name=net_map[role],
                    )
                )
        return created

    def auto_connect_power(
        self,
        comp1: Component,
        comp2: Component,
    ) -> list[Connection]:
        """
        Connect matching power pins (VCC ↔ VCC, GND ↔ GND) between
        two components so they share the same power net.
        """
        created: list[Connection] = []

        vcc1 = comp1.get_pins_by_role("vcc")
        vcc2 = comp2.get_pins_by_role("vcc")
        if vcc1 and vcc2:
            v_str = str(comp1.voltage).replace(".", "_")
            created.append(
                self.connect(comp1, vcc1[0].name, comp2, vcc2[0].name,
                             net_name=f"POWER_{v_str}V")
            )

        gnd1 = comp1.get_pins_by_role("gnd")
        gnd2 = comp2.get_pins_by_role("gnd")
        if gnd1 and gnd2:
            created.append(
                self.connect(comp1, gnd1[0].name, comp2, gnd2[0].name,
                             net_name="GND")
            )

        return created

    # ------------------------------------------------------------------
    # Net generation
    # ------------------------------------------------------------------

    def generate_nets(self) -> dict[str, Net]:
        """
        Group all connections by net name and build Net objects.

        Two connections that share a net_name contribute their pins to
        the same Net, so multi-device buses (I2C_SDA, GND, …) are
        correctly represented as a single net containing all participants.

        Returns
        -------
        dict[str, Net]
            Also stored in ``self.nets`` for later access.
        """
        nets: dict[str, Net] = {}

        for conn in self.connections:
            net = nets.setdefault(conn.net_name, Net(conn.net_name))
            net.add_pin(conn.component_a, conn.pin_a)
            net.add_pin(conn.component_b, conn.pin_b)

        self.nets = nets
        return nets


# ---------------------------------------------------------------------------
# Netlist exporter
# ---------------------------------------------------------------------------

def export_netlist(circuit: Circuit) -> str:
    """
    Format a Circuit as a readable, KiCad-inspired text netlist.

    Parameters
    ----------
    circuit:
        A ``Circuit`` instance.  Call ``generate_nets()`` first so that
        ``circuit.nets`` is populated; if not, it is called automatically.

    Returns
    -------
    str
        Multi-line text netlist.
    """
    if not circuit.nets:
        circuit.generate_nets()

    _NETLIST_LINE_WIDTH = 54
    line = "=" * _NETLIST_LINE_WIDTH
    lines: list[str] = [line, "  NETLIST", line, ""]

    # ── Components section ────────────────────────────────────────────
    lines.append("Components:")
    for idx, comp in enumerate(circuit.components, start=1):
        prefix = f"U{idx}"
        lines.append(f"  [{prefix}]  {comp.name}")
    lines.append("")

    # ── Nets section ──────────────────────────────────────────────────
    lines.append("Nets:")
    for net_name, net in sorted(circuit.nets.items()):
        n = len(net.pins)
        lines.append(f"  {net_name}  ({n} pin{'s' if n != 1 else ''}):")
        for i, (comp, pin_name) in enumerate(net.pins):
            connector = "└─" if i == n - 1 else "├─"
            lines.append(f"    {connector} {comp.name}.{pin_name}")
        lines.append("")

    # ── Warnings section ─────────────────────────────────────────────
    if circuit.warnings:
        lines.append("Warnings:")
        for w in circuit.warnings:
            lines.append(f"  ⚠  {w}")
        lines.append("")

    lines.append(line)
    return "\n".join(lines)

