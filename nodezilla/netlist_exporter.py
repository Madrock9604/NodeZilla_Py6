"""Utilities for exporting a schematic scene to a lightweight netlist.

The module is intentionally structured to make the textual representation
of the netlist easy to swap out. Build the in-memory ``Netlist`` using
``NetlistBuilder`` and feed it to any callable formatter to obtain your
preferred text serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Callable, Dict, List, Tuple

from .graphics_items import ComponentItem, WireItem


@dataclass
class NetConnection:
    """A single connection between a component port and a net."""

    component_refdes: str
    component_kind: str
    port_name: str


@dataclass
class Net:
    """A collection of connections that share electrical continuity."""

    name: str
    connections: List[NetConnection]


@dataclass
class Component:
    """A component instance placed in the schematic."""

    refdes: str
    kind: str
    value: str
    pins: List["ComponentPin"] = field(default_factory=list)
    spice_type: str = ""


@dataclass
class ComponentPin:
    """A component pin and its net (or OPEN if unconnected)."""

    name: str
    net: str


@dataclass
class Netlist:
    """Container object holding components and the nets that join them."""

    components: List[Component]
    nets: List[Net]


class SimpleNetlistFormatter:
    """Turn a :class:`Netlist` into a friendly, editable text form.

    The output is designed to be human-readable and straightforward to
    extend. You can pass any callable with the same signature to
    :class:`NetlistBuilder` if you want to change the representation.
    """

    def __call__(self, netlist: Netlist) -> str:
        lines: List[str] = ["[Components]"]
        for comp in sorted(netlist.components, key=lambda c: c.refdes or c.kind):
            value = f" {comp.value}" if comp.value else ""
            lines.append(f"{comp.refdes or comp.kind}: {comp.kind}{value}")

        lines.append("\n[Nets]")
        for net in netlist.nets:
            lhs = f"{net.name}:"
            rhs = ", ".join(
                f"{c.component_refdes or c.component_kind}.{c.port_name}"
                for c in sorted(net.connections, key=lambda c: (c.component_refdes, c.port_name))
            )
            lines.append(f"{lhs} {rhs}")
        return "\n".join(lines)


class DetailedNetlistFormatter:
    """Readable netlist that includes per-pin net names and OPEN pins.

    Customize formatting by overriding the lambdas below.
    """

    def __init__(
        self,
        *,
        open_node: str = "OPEN",
        component_header: Callable[[Component], str] | None = None,
        pin_line: Callable[[Component, ComponentPin], str] | None = None,
        net_header: Callable[[], str] | None = None,
        net_line: Callable[[Net], str] | None = None,
    ) -> None:
        self._open = open_node
        self._component_header = component_header or self._default_component_header
        self._pin_line = pin_line or self._default_pin_line
        self._net_header = net_header or (lambda: "[Nets]")
        self._net_line = net_line or self._default_net_line

    def __call__(self, netlist: Netlist) -> str:
        lines: List[str] = ["[Components]"]
        for comp in sorted(netlist.components, key=lambda c: c.refdes or c.kind):
            lines.append(self._component_header(comp))
            for pin in comp.pins:
                lines.append(self._pin_line(comp, pin))

        lines.append("")
        lines.append(self._net_header())
        for net in netlist.nets:
            lines.append(self._net_line(net))
        return "\n".join(lines)

    @staticmethod
    def _default_component_header(comp: Component) -> str:
        value = f" value={comp.value}" if comp.value else ""
        name = comp.refdes or comp.kind
        return f"{name}: {comp.kind}{value}"

    @staticmethod
    def _default_pin_line(_comp: Component, pin: ComponentPin) -> str:
        return f"  {pin.name}: {pin.net}"

    @staticmethod
    def _default_net_line(net: Net) -> str:
        rhs = ", ".join(
            f"{c.component_refdes or c.component_kind}.{c.port_name}"
            for c in sorted(net.connections, key=lambda c: (c.component_refdes, c.port_name))
        )
        return f"{net.name}: {rhs}"


class MyNetlistFormatter:
    """Editable personal format. Adjust the lines below to your needs."""

    def __call__(self, netlist: Netlist) -> str:
        lines: List[str] = []
        lines.append("BEGIN NETLIST")
        for comp in sorted(netlist.components, key=lambda c: c.refdes or c.kind):
            name = comp.refdes or comp.kind
            value = f" {comp.value}" if comp.value else ""
            lines.append(f"{name} {comp.kind}{value}")
            for pin in comp.pins:
                lines.append(f"  {pin.name}: {pin.net}")
        lines.append("END NETLIST")
        return "\n".join(lines)


class SpiceNetlistFormatter:
    """Format a :class:`Netlist` into a basic SPICE-compatible netlist.

    Each component line follows:
    ``<type> <name> <net...> [value] [part]``.
    """

    def __init__(
        self,
        *,
        title: str = "NodeZilla Netlist",
        port_order: Tuple[str, str] = ("A", "B"),
        ground_node: str = "0",
        floating_node: str = "NC",
        type_resolver: Callable[[Component], str] | None = None,
        name_resolver: Callable[[Component], str] | None = None,
        value_resolver: Callable[[Component], str] | None = None,
        part_resolver: Callable[[Component], str] | None = None,
    ) -> None:
        self._title = title
        self._port_order = port_order
        self._ground_node = ground_node
        self._floating_node = floating_node
        self._type_resolver = type_resolver or self._default_component_type
        self._name_resolver = name_resolver or self._default_component_name
        self._value_resolver = value_resolver or self._default_component_value
        self._part_resolver = part_resolver or self._default_component_part

    def __call__(self, netlist: Netlist) -> str:
        """Render SPICE lines using per-pin net mapping."""
        lines: List[str] = [f"* {self._title}"]
        for comp in sorted(netlist.components, key=lambda c: c.refdes or c.kind):
            refdes = self._name_resolver(comp)
            prefix = self._type_resolver(comp)
            if getattr(comp, "pins", None):
                nodes = [self._normalize_node_name(p.net) for p in comp.pins]
            else:
                # Fallback to fixed port order if pin data is missing.
                nodes = [self._floating_node for _ in self._port_order]
            tokens = [f"{prefix}{refdes}", *nodes]
            value = self._value_resolver(comp).strip()
            value = self._normalize_rlc_value(prefix, value)
            if value:
                tokens.append(value)
            part = self._part_resolver(comp).strip()
            if part:
                tokens.append(part)
            lines.append(" ".join(tokens))

        lines.append(".end")
        return "\n".join(lines)

    def _normalize_node_name(self, name: str) -> str:
        if name.strip().upper() in {"GND", "GROUND", "0"}:
            return self._ground_node
        return name

    @staticmethod
    def _default_component_type(component: Component) -> str:
        if component.spice_type:
            return component.spice_type
        kind = component.kind.strip()
        if not kind:
            return "X"
        return kind[:1].lower()

    @staticmethod
    def _default_component_name(component: Component) -> str:
        return component.refdes or component.kind

    @staticmethod
    def _default_component_value(component: Component) -> str:
        return component.value

    @staticmethod
    def _default_component_part(component: Component) -> str:
        return ""

    @staticmethod
    def _normalize_rlc_value(prefix: str, value: str) -> str:
        """Convert engineering suffixes to scientific notation for R/C/L only.

        Examples:
        - 1k   -> 1e3
        - 4.7uF -> 4.7e-6
        - 1mH -> 1e-3
        """
        if not value:
            return value
        if (prefix or "").strip().upper() not in {"R", "C", "L"}:
            return value

        txt = value.strip().replace(" ", "")
        # Keep explicit scientific notation as-is.
        if "e" in txt.lower():
            return txt

        m = re.fullmatch(r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))([a-zA-Z]+)?", txt)
        if not m:
            return value

        number = m.group(1)
        suffix = (m.group(2) or "").strip()
        if not suffix:
            return number

        multipliers = {
            "T": 12,
            "G": 9,
            "MEG": 6,
            "K": 3,
            "M": -3,
            "U": -6,
            "N": -9,
            "P": -12,
            "F": -15,
        }

        suffix_up = suffix.upper()
        # Accept common unit tails, e.g., "uF", "mH", "kOhm".
        eng = ""
        if suffix_up.startswith("MEG"):
            eng = "MEG"
        elif suffix_up and suffix_up[0] in {"T", "G", "K", "M", "U", "N", "P", "F"}:
            eng = suffix_up[0]
        if not eng:
            # Unknown suffix; keep original value untouched.
            return value

        exp = multipliers[eng]
        return f"{number}e{exp}"


class _UnionFind:
    """Minimal disjoint-set structure for grouping connected points."""

    def __init__(self) -> None:
        self._parent: Dict[Tuple[float, float], Tuple[float, float]] = {}

    def add(self, item: Tuple[float, float]) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: Tuple[float, float]) -> Tuple[float, float]:
        self.add(item)
        if self._parent[item] != item:
            self._parent[item] = self.find(self._parent[item])
        return self._parent[item]

    def union(self, a: Tuple[float, float], b: Tuple[float, float]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def groups(self) -> Dict[Tuple[float, float], List[Tuple[float, float]]]:
        out: Dict[Tuple[float, float], List[Tuple[float, float]]] = {}
        for item in self._parent:
            root = self.find(item)
            out.setdefault(root, []).append(item)
        return out


class NetlistBuilder:
    """Convert a ``SchematicScene`` into a :class:`Netlist` structure."""

    def __init__(
        self,
        *,
        net_namer: Callable[[List[NetConnection], int], str] | None = None,
        formatter: Callable[[Netlist], str] | None = None,
    ) -> None:
        self._net_namer = net_namer or self._default_net_namer
        self._formatter = formatter or SpiceNetlistFormatter()

    def build(self, scene) -> Netlist:
        """Build a Netlist from the scene (uses scene.net_data if available)."""
        components: List[ComponentItem] = [it for it in scene.items() if isinstance(it, ComponentItem)]
        from .component_library import load_component_library
        comp_defs = load_component_library()
        def _is_net_component(kind: str) -> bool:
            cdef = comp_defs.get(kind)
            return bool(cdef and getattr(cdef, "comp_type", "component") == "net")
        def _is_chip_component(kind: str) -> bool:
            cdef = comp_defs.get(kind)
            return bool(cdef and getattr(cdef, "is_chip", False))
        if hasattr(scene, "net_data"):
            # Preferred path: use scene.net_data() for robust wiring/junction logic.
            nets_meta = scene.net_data()
            nets = [
                Net(
                    name=n.get("name", self._net_namer(n.get("connections", []), i + 1)),
                    connections=[c for c in n.get("connections", []) if not _is_net_component(c.component_kind)],
                )
                for i, n in enumerate(nets_meta)
                if n.get("connections")
            ]
            port_to_node: Dict[Tuple[str, str], str] = {}
            for net in nets:
                for connection in net.connections:
                    refdes = connection.component_refdes or connection.component_kind
                    port_to_node[(refdes, connection.port_name)] = net.name

            component_models: List[Component] = []
            for c in components:
                if _is_net_component(c.kind) or _is_chip_component(c.kind):
                    continue
                refdes = c.refdes or c.kind
                pins: List[ComponentPin] = []
                ports = [p for p in getattr(c, "ports", []) if p is not None] or [
                    p for p in (getattr(c, "port_left", None), getattr(c, "port_right", None)) if p is not None
                ]
                for port in ports:
                    net_name = port_to_node.get((refdes, port.name), "OPEN")
                    pins.append(ComponentPin(name=port.name, net=net_name))
                cdef = comp_defs.get(c.kind)
                spice_type = cdef.spice_type if cdef else ""
                component_models.append(Component(refdes=c.refdes, kind=c.kind, value=c.value, pins=pins, spice_type=spice_type))

            used_by_prefix = self._collect_used_refdes_numbers(component_models, comp_defs)
            # Flatten hierarchical chip instances: include their internal components
            # mapped onto parent nets via matching port/net-label names.
            for c in components:
                if not _is_chip_component(c.kind):
                    continue
                component_models.extend(
                    self._expand_chip_instance(c, comp_defs, port_to_node, used_by_prefix)
                )
            return Netlist(components=component_models, nets=nets)

        wires: List[WireItem] = [it for it in scene.items() if isinstance(it, WireItem)]
        components = [c for c in components if not _is_net_component(c.kind)]

        uf = _UnionFind()
        point_key = lambda p: (round(p.x(), 4), round(p.y(), 4))

        # Treat every point along every wire as part of the same conductive island.
        for wire in wires:
            pts = wire._manhattan_points()
            if not pts:
                continue
            keys = [point_key(p) for p in pts]
            for key in keys:
                uf.add(key)
            for key in keys[1:]:
                uf.union(keys[0], key)

        # Map every port to its coordinate key and capture components for reporting.
        port_connections: Dict[Tuple[float, float], List[NetConnection]] = {}
        for comp in components:
            for port in [p for p in getattr(comp, 'ports', []) if p is not None] or [p for p in (getattr(comp, 'port_left', None), getattr(comp, 'port_right', None)) if p is not None]:
                key = point_key(port.scenePos())
                uf.add(key)
                conn = NetConnection(
                    component_refdes=comp.refdes,
                    component_kind=comp.kind,
                    port_name=port.name,
                )
                port_connections.setdefault(key, []).append(conn)

        nets: List[Net] = []
        for idx, (root, members) in enumerate(sorted(uf.groups().items())):
            # Gather every connection whose coordinate collapses into this root.
            connections: List[NetConnection] = []
            for m in members:
                connections.extend(port_connections.get(m, []))
            if not connections:
                continue
            name = self._net_namer(connections, idx + 1)
            nets.append(Net(name=name, connections=connections))

        port_to_node: Dict[Tuple[str, str], str] = {}
        for net in nets:
            for connection in net.connections:
                refdes = connection.component_refdes or connection.component_kind
                port_to_node[(refdes, connection.port_name)] = net.name

        component_models: List[Component] = []
        for c in components:
            refdes = c.refdes or c.kind
            pins: List[ComponentPin] = []
            ports = [p for p in getattr(c, "ports", []) if p is not None] or [
                p for p in (getattr(c, "port_left", None), getattr(c, "port_right", None)) if p is not None
            ]
            for port in ports:
                net_name = port_to_node.get((refdes, port.name), "OPEN")
                pins.append(ComponentPin(name=port.name, net=net_name))
            cdef = comp_defs.get(c.kind)
            spice_type = cdef.spice_type if cdef else ""
            component_models.append(Component(refdes=c.refdes, kind=c.kind, value=c.value, pins=pins, spice_type=spice_type))

        return Netlist(components=component_models, nets=nets)

    def _expand_chip_instance(
        self,
        chip: ComponentItem,
        comp_defs,
        port_to_node: Dict[Tuple[str, str], str],
        used_by_prefix: Dict[str, set[int]],
    ) -> List[Component]:
        """Flatten one chip instance into internal component models."""
        chip_data = chip.chip_data() if hasattr(chip, "chip_data") else {}
        if not isinstance(chip_data, dict) or not chip_data:
            return []

        try:
            from PySide6.QtWidgets import QLabel
            from PySide6.QtGui import QUndoStack
            from .schematic_scene import SchematicScene
        except Exception:
            return []

        try:
            child_scene = SchematicScene(QLabel(""), QUndoStack())
            child_scene.load(chip_data)
        except Exception:
            return []

        chip_ref = chip.refdes or chip.kind
        ports = [p for p in getattr(chip, "ports", []) if p is not None] or [
            p for p in (getattr(chip, "port_left", None), getattr(chip, "port_right", None)) if p is not None
        ]
        port_map_cs = {}
        for p in ports:
            outer = port_to_node.get((chip_ref, p.name), "OPEN")
            port_map_cs[str(p.name).strip().casefold()] = outer

        # Build child net resolution from actual connectivity (not only net names):
        # if a child net contains numbered NetLabel (1..N), treat it as the same net
        # as the corresponding top-level chip pin.
        child_components: List[ComponentItem] = [
            it for it in child_scene.items() if isinstance(it, ComponentItem)
        ]
        child_by_key: Dict[Tuple[str, str], ComponentItem] = {}
        for c in child_components:
            ref = (c.refdes or "").strip()
            if ref:
                child_by_key[(ref, c.kind)] = c

        child_port_to_net: Dict[Tuple[str, str], str] = {}
        nets_meta = child_scene.net_data() if hasattr(child_scene, "net_data") else []
        for n in nets_meta:
            net_name = str(n.get("name", "")).strip() or "OPEN"
            resolved_net = net_name
            # Find any NetLabel on this net that maps to a chip boundary pin number.
            for conn in n.get("connections", []):
                cdef = comp_defs.get(conn.component_kind)
                if not (cdef and getattr(cdef, "comp_type", "component") == "net"):
                    continue
                comp = child_by_key.get((conn.component_refdes, conn.component_kind))
                label = (comp.value.strip() if comp is not None else "").casefold()
                if label and label in port_map_cs:
                    resolved_net = port_map_cs[label]
                    break

            for conn in n.get("connections", []):
                cdef = comp_defs.get(conn.component_kind)
                if cdef and getattr(cdef, "comp_type", "component") == "net":
                    continue
                key = (conn.component_refdes, conn.port_name)
                child_port_to_net[key] = resolved_net

        expanded: List[Component] = []
        for comp in child_components:
            cdef = comp_defs.get(comp.kind)
            if cdef and (getattr(cdef, "comp_type", "component") == "net" or getattr(cdef, "is_chip", False)):
                continue
            prefix = self._prefix_for_kind(comp.kind, comp_defs)
            merged_ref = self._next_refdes_for_prefix(prefix, used_by_prefix)
            merged_pins: List[ComponentPin] = []
            ports_local = [p for p in getattr(comp, "ports", []) if p is not None] or [
                p for p in (getattr(comp, "port_left", None), getattr(comp, "port_right", None)) if p is not None
            ]
            for p in ports_local:
                raw = child_port_to_net.get((comp.refdes, p.name), "OPEN")
                mapped = self._qualify_child_net(chip_ref, raw)
                merged_pins.append(ComponentPin(name=p.name, net=mapped))
            expanded.append(
                Component(
                    refdes=merged_ref,
                    kind=comp.kind,
                    value=comp.value,
                    pins=merged_pins,
                    spice_type=(cdef.spice_type if cdef else ""),
                )
            )
        return expanded

    def _collect_used_refdes_numbers(self, components: List[Component], comp_defs) -> Dict[str, set[int]]:
        used: Dict[str, set[int]] = {}
        for comp in components:
            prefix = self._prefix_for_kind(comp.kind, comp_defs)
            num = self._parse_refdes_num(comp.refdes, prefix)
            if num is not None:
                used.setdefault(prefix, set()).add(num)
        return used

    @staticmethod
    def _parse_refdes_num(refdes: str, prefix: str) -> int | None:
        text = (refdes or "").strip()
        if not text:
            return None
        if text.startswith(prefix):
            tail = text[len(prefix):]
            if tail.isdigit():
                try:
                    return int(tail)
                except Exception:
                    return None
        m = re.search(r"(\d+)$", text)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    @staticmethod
    def _prefix_for_kind(kind: str, comp_defs) -> str:
        cdef = comp_defs.get(kind) if comp_defs is not None else None
        if cdef is not None and getattr(cdef, "prefix", ""):
            return str(cdef.prefix)
        k = (kind or "").lower()
        if k.startswith("res"):
            return "R"
        if k.startswith("cap"):
            return "C"
        if k.startswith("ind"):
            return "L"
        if k.startswith("dio"):
            return "D"
        if k.startswith("ground") or k.startswith("gnd"):
            return "GND"
        return (kind[:1].upper() if kind else "X")

    @staticmethod
    def _next_refdes_for_prefix(prefix: str, used_by_prefix: Dict[str, set[int]]) -> str:
        used = used_by_prefix.setdefault(prefix, set())
        n = 1
        while n in used:
            n += 1
        used.add(n)
        return f"{prefix}{n}"

    @staticmethod
    def _qualify_child_net(chip_ref: str, net_name: str) -> str:
        """Keep simple net names for internal chip nets."""
        n = (net_name or "").strip()
        if not n:
            return "OPEN"
        up = n.upper()
        if up in {"0", "GND", "GROUND", "VDD", "VSS", "VEE", "OPEN", "NC"}:
            return "0" if up in {"0", "GND", "GROUND"} else n
        return n

    def format(self, netlist: Netlist) -> str:
        return self._formatter(netlist)

    def export(self, scene) -> str:
        return self.format(self.build(scene))

    @staticmethod
    def _default_net_namer(connections: List[NetConnection], sequence: int) -> str:
        if any(c.component_kind.lower().startswith(("gnd", "ground")) for c in connections):
            return "0"
        return str(sequence)


__all__ = [
    "Component",
    "ComponentPin",
    "Net",
    "NetConnection",
    "Netlist",
    "NetlistBuilder",
    "DetailedNetlistFormatter",
    "MyNetlistFormatter",
    "SimpleNetlistFormatter",
    "SpiceNetlistFormatter",
]
