"""Utilities for exporting a schematic scene to a lightweight netlist.

The module is intentionally structured to make the textual representation
of the netlist easy to swap out. Build the in-memory ``Netlist`` using
``NetlistBuilder`` and feed it to any callable formatter to obtain your
preferred text serialization.
"""

from __future__ import annotations

from dataclasses import dataclass
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
        port_to_node: Dict[Tuple[str, str], str] = {}
        for net in netlist.nets:
            node_name = self._normalize_node_name(net.name)
            for connection in net.connections:
                refdes = connection.component_refdes or connection.component_kind
                port_to_node[(refdes, connection.port_name)] = node_name

        lines: List[str] = [f"* {self._title}"]
        for comp in sorted(netlist.components, key=lambda c: c.refdes or c.kind):
            refdes = self._name_resolver(comp)
            nodes = [
                port_to_node.get((refdes, port), self._floating_node)
                for port in self._port_order
            ]
            tokens = [self._type_resolver(comp), refdes, *nodes]
            value = self._value_resolver(comp).strip()
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
        components: List[ComponentItem] = [it for it in scene.items() if isinstance(it, ComponentItem)]
        if hasattr(scene, "net_data"):
            nets_meta = scene.net_data()
            nets = [
                Net(name=n.get("name", self._net_namer(n.get("connections", []), i + 1)), connections=list(n.get("connections", [])))
                for i, n in enumerate(nets_meta)
                if n.get("connections")
            ]
            component_models = [Component(refdes=c.refdes, kind=c.kind, value=c.value) for c in components]
            return Netlist(components=component_models, nets=nets)

        wires: List[WireItem] = [it for it in scene.items() if isinstance(it, WireItem)]

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

        component_models = [
            Component(refdes=c.refdes, kind=c.kind, value=c.value) for c in components
        ]

        return Netlist(components=component_models, nets=nets)

    def format(self, netlist: Netlist) -> str:
        return self._formatter(netlist)

    def export(self, scene) -> str:
        return self.format(self.build(scene))

    @staticmethod
    def _default_net_namer(connections: List[NetConnection], sequence: int) -> str:
        if any(c.component_kind.lower().startswith(("gnd", "ground")) for c in connections):
            return "GND"
        return f"N{sequence}"


__all__ = [
    "Component",
    "Net",
    "NetConnection",
    "Netlist",
    "NetlistBuilder",
    "SimpleNetlistFormatter",
    "SpiceNetlistFormatter",
]
