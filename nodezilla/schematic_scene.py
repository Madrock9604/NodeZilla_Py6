# ========================================
# File: nodezilla/schematic_scene.py
# ========================================
from __future__ import annotations
from typing import Optional, List, Dict, Tuple
import heapq
import json
import zlib
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QTimer
from PySide6.QtGui import QPen, QPainterPath, QTransform, QPainter, QBrush, QColor
from PySide6.QtWidgets import QGraphicsScene, QLabel, QGraphicsView, QGraphicsItem, QGraphicsEllipseItem
from .graphics_items import ComponentItem, PortItem, CommentTextItem
from .commands import SetWirePointsCommand
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .graphics_items import WireItem  # for type hints only
from .commands import (
    AddComponentCommand, AddWireCommand, MoveComponentCommand,
    RotateComponentCommand, DeleteItemsCommand
)
from .theme import Theme
from .component_library import load_component_library
try:
    from .graphics_items import WireItem as _WireItem  # runtime check
except Exception:
    _WireItem = None

class SchematicScene(QGraphicsScene):
    nets_changed = Signal()
    component_placed = Signal(object)

    class Mode:
        SELECT = 0; PLACE = 1; WIRE = 2

    def __init__(self, status_label: QLabel, undo_stack):
        super().__init__()
        self.mode = SchematicScene.Mode.SELECT
        self.component_to_place: Optional[str] = None
        self.status_label = status_label
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self._pending_port: Optional[PortItem] = None
        self.grid_on = True; self.snap_on = True; self.grid_size = 20; self.grid_style = 'dots'
        self._ghost_kind: Optional[str] = None; self._ghost_item: Optional[ComponentItem] = None
        self._view = None
        self.request_properties = None
        self.request_open_chip = None
        self.undo_stack = undo_stack
        self._refseq: Dict[str, int] = {}
        self.theme: Theme | None = None
        # default grid pen; will be set in apply_theme
        self._grid_pen_lines = QPen(Qt.lightGray, 0)
        self._grid_pen_dots  = QPen(Qt.lightGray, 2)
        self._routing = False
        # Wire routing state
        self._route_pts = []          # waypoints between endpoints (QPointF)
        self._route_start_port = None # PortItem or None if starting from free/junction point
        self._route_start_point: Optional[QPointF] = None
        self._temp_dash = None        # QGraphicsPathItem (you already use one)
        # Explicit wire junctions (only when user connects wires)
        self._junction_markers: list[QGraphicsItem] = []
        self._wire_junctions: set[Tuple[float, float]] = set()
        self._junction_owners: Dict[Tuple[float, float], set] = {}
        self._net_name_overrides: Dict[Tuple[float, float], str] = {}
        self._net_label_overrides: Dict[Tuple[float, float], str] = {}
        self._net_update_pending = False
        self.changed.connect(self._schedule_nets_changed)
        self.wire_route_mode = "orth"  # orth | free | 45
        self._place_refdes_override: str = ""
        self._place_value_override: str = ""
        self._place_chip_io: dict | None = None

    def _reset_wire_session(self):
        self._clear_temp_wire()
        self._routing = False
        self._route_pts = []
        self._route_start_port = None
        self._route_start_point = None
        self._junction_start_key = None


    def apply_theme(self, theme: "Theme"):
        """Remember theme, recolor bg/grid, and propagate to items."""
        self.theme = theme
        self.setBackgroundBrush(theme.bg)
        # Keep grid dots visible in both light/dark modes.
        grid_col = self._grid_color_for_background(theme.bg)
        self._grid_pen_lines = QPen(grid_col, 0)
        self._grid_pen_lines.setCosmetic(True)
        self._grid_pen_dots = QPen(grid_col, 2)
        self._grid_pen_dots.setCosmetic(True)

        # (grid pens, if you have them — keep as-is or derive from theme)
        # self._grid_pen_lines = QPen(theme.component_stroke, 0); self._grid_pen_lines.setCosmetic(True)
        # self._grid_pen_dots  = QPen(theme.component_stroke, 2); self._grid_pen_dots.setCosmetic(True)

        for it in self.items():
            if hasattr(it, "apply_theme"):
                it.apply_theme(theme)
        if self._temp_dash is not None:
            pen = self._temp_dash.pen()
            pen.setColor(theme.wire_selected)
            self._temp_dash.setPen(pen)
        self.update()
        self._rebuild_junction_markers()

    @staticmethod
    def _grid_color_for_background(bg: QColor) -> QColor:
        luma = 0.2126 * bg.redF() + 0.7152 * bg.greenF() + 0.0722 * bg.blueF()
        # Darker grid on light backgrounds, lighter grid on dark backgrounds.
        return QColor(96, 96, 96) if luma > 0.5 else QColor(175, 175, 175)

    def _schedule_nets_changed(self):
        if self._net_update_pending:
            return
        self._net_update_pending = True
        QTimer.singleShot(0, self._emit_nets_changed)

    def _emit_nets_changed(self):
        self._net_update_pending = False
        self._update_net_labels()
        self.nets_changed.emit()

    def _update_net_labels(self):
        # Compute label-based net names from net-type components.
        saved = dict(self._net_label_overrides)
        self._net_label_overrides = {}
        try:
            nets = self.net_data()
            comp_defs = load_component_library()
            comp_items = [it for it in self.items() if isinstance(it, ComponentItem)]
            comp_by_key: Dict[Tuple[str, str], ComponentItem] = {}
            for comp in comp_items:
                ref = (comp.refdes or "").strip()
                if ref:
                    comp_by_key[(ref, comp.kind)] = comp
            for net in nets:
                label = ""
                for conn in net.get("connections", []):
                    cdef = comp_defs.get(conn.component_kind)
                    if cdef and getattr(cdef, "comp_type", "component") == "net":
                        if cdef.net_name:
                            label = cdef.net_name
                            break
                        comp = comp_by_key.get((conn.component_refdes, conn.component_kind))
                        if comp is not None:
                            label = comp.value.strip() or comp.refdes.strip()
                            if label:
                                break
                if label:
                    if label.strip().upper() in {"GND", "GROUND"}:
                        label = "0"
                    self._net_label_overrides[net["id"]] = label
        except Exception:
            self._net_label_overrides = saved

    def drawBackground(self, p: 'QPainter', rect: QRectF):
        if not self.grid_on: return
        g = self.grid_size
        left = int((rect.left()//g)*g); top = int((rect.top()//g)*g)
        p.save()
        # Dotted grid with minor/major emphasis (major every 5 cells)
        major_step = g * 5
        minor_pen = QPen(self._grid_pen_dots.color(), 1)
        minor_pen.setCosmetic(True)
        minor_pen.setColor(self._grid_pen_dots.color())
        major_pen = QPen(self._grid_pen_dots.color(), 2)
        major_pen.setCosmetic(True)
        major_pen.setColor(self._grid_pen_dots.color())
        # Slightly dim minors
        minor_color = minor_pen.color()
        minor_color.setAlphaF(0.55)
        minor_pen.setColor(minor_color)
        major_color = major_pen.color()
        major_color.setAlphaF(0.85)
        major_pen.setColor(major_color)
        x = left
        while x < rect.right():
            y = top
            is_major_x = (int(round(x)) % int(major_step) == 0)
            while y < rect.bottom():
                is_major = is_major_x and (int(round(y)) % int(major_step) == 0)
                p.setPen(major_pen if is_major else minor_pen)
                p.drawPoint(int(x), int(y))
                y += g
            x += g
        p.restore()

    def attach_view(self, view):
        self._view = view

    # callbacks from items
    def on_component_moved(self, comp: ComponentItem, old_pos: QPointF, new_pos: QPointF):
        self._reroute_wires_blocked_by_component(comp)
        self.undo_stack.push(MoveComponentCommand(comp, old_pos, new_pos))

    # modes
    def set_mode_select(self):
        self.mode = SchematicScene.Mode.SELECT
        self.component_to_place = None
        self._place_chip_io = None
        self.clear_place_overrides()
        self._remove_ghost()
        self._reset_wire_session()
        if self._view: self._view.setDragMode(QGraphicsView.RubberBandDrag)
        self.status_label.setText("Arrow: select/move")

    def set_mode_place(self, kind: str):
        self.mode = SchematicScene.Mode.PLACE
        self.component_to_place = kind
        self._place_chip_io = None
        self._remove_ghost(); self._ghost_kind = kind; self._ensure_ghost()
        if self._view: self._view.setDragMode(QGraphicsView.NoDrag)
        comp_def = load_component_library().get(kind)
        if comp_def and getattr(comp_def, "comp_type", "component") == "net":
            self.status_label.setText(
                f"Place: {kind} – click a wire to attach (Esc to cancel, [ / ] to rotate)"
            )
        else:
            self.status_label.setText(
                f"Place: {kind} (next {self._display_refdes_for_kind(kind, self._next_refdes(kind))}) – click to place, ESC to cancel, [ / ] to rotate"
            )

    def set_mode_place_chip(self, pins: int):
        self.set_mode_place("Chip")
        self._place_chip_io = {"pins": max(2, int(pins))}
        if self._ghost_item is not None and hasattr(self._ghost_item, "configure_chip_pins"):
            self._ghost_item.configure_chip_pins(self._place_chip_io["pins"])
        self.status_label.setText(
            f"Place Chip ({self._place_chip_io['pins']} pins) – click to place, ESC to cancel"
        )

    def set_place_overrides(self, *, refdes: str = "", value: str = ""):
        self._place_refdes_override = str(refdes or "").strip()
        self._place_value_override = str(value or "").strip()

    def clear_place_overrides(self):
        self._place_refdes_override = ""
        self._place_value_override = ""

    def set_mode_wire(self):
        self.mode = SchematicScene.Mode.WIRE
        self.component_to_place = None
        self._remove_ghost()
        self._reset_wire_session()
        if self._view: self._view.setDragMode(QGraphicsView.RubberBandDrag)
        self.status_label.setText(f"Wire ({self._wire_mode_label()}): click ports or wires, double click to finish (Esc/right-click to cancel)")

    def set_mode_text(self):
        self.mode = SchematicScene.Mode.PLACE
        self.component_to_place = "__TEXT__"
        self._remove_ghost()
        if self._view:
            self._view.setDragMode(QGraphicsView.NoDrag)
        self.status_label.setText("Text: click to place comment, Esc to cancel")

    # helpers
    def _ensure_ghost(self):
        if self._ghost_item is None and self._ghost_kind:
            self._ghost_item = ComponentItem(self._ghost_kind, QPointF(0, 0))
            self._ghost_item.setOpacity(0.35)
            self._ghost_item.setAcceptedMouseButtons(Qt.NoButton)
            if getattr(self, "theme", None):
                self._ghost_item.apply_theme(self.theme)

            self._ghost_item.setFlag(QGraphicsItem.ItemIsMovable, False)
            self._ghost_item.setFlag(QGraphicsItem.ItemIsSelectable, False)

            self._ghost_item.setZValue(3)
            self.addItem(self._ghost_item)
            if getattr(self, "_view", None):
                try:
                    center = self._view.mapToScene(self._view.viewport().rect().center())
                    self._ghost_item.setPos(self._snap_point(center))
                except Exception:
                    pass


    def _remove_ghost(self):
        if self._ghost_item is not None:
            self.removeItem(self._ghost_item); self._ghost_item = None; self._ghost_kind = None

    def _point_on_orthogonal_segment(self, p: QPointF, a: QPointF, b: QPointF, tol: float = 1e-4) -> bool:
        if abs(a.x() - b.x()) < tol:
            if abs(p.x() - a.x()) > tol:
                return False
            lo, hi = (a.y(), b.y()) if a.y() <= b.y() else (b.y(), a.y())
            return lo - tol <= p.y() <= hi + tol
        if abs(a.y() - b.y()) < tol:
            if abs(p.y() - a.y()) > tol:
                return False
            lo, hi = (a.x(), b.x()) if a.x() <= b.x() else (b.x(), a.x())
            return lo - tol <= p.x() <= hi + tol
        return False

    def _wire_contains_point(self, wire, p: QPointF, tol: float | None = None) -> bool:
        """Return True if point hits a wire segment (with tolerance)."""
        pts = wire.render_points() if hasattr(wire, "render_points") else wire._manhattan_points()
        if not pts:
            return False
        if tol is None:
            # Connectivity should be much stricter than cursor picking.
            # Ports are expected to land exactly on-grid, so only near-exact
            # geometric coincidence should count as "on the wire".
            tol = 0.25
        for i in range(len(pts) - 1):
            if self._point_on_segment(p, pts[i], pts[i + 1], tol):
                return True
        return False

    def _point_on_segment(self, p: QPointF, a: QPointF, b: QPointF, tol: float = 1.0) -> bool:
        ax, ay = a.x(), a.y()
        bx, by = b.x(), b.y()
        px, py = p.x(), p.y()
        abx = bx - ax
        aby = by - ay
        ab2 = abx * abx + aby * aby
        if ab2 < 1e-12:
            return (px - ax) ** 2 + (py - ay) ** 2 <= tol * tol
        t = ((px - ax) * abx + (py - ay) * aby) / ab2
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        qx = ax + t * abx
        qy = ay + t * aby
        dx = px - qx
        dy = py - qy
        return (dx * dx + dy * dy) <= tol * tol

    def _closest_point_on_segment(self, p: QPointF, a: QPointF, b: QPointF) -> QPointF:
        ax, ay = a.x(), a.y()
        bx, by = b.x(), b.y()
        px, py = p.x(), p.y()
        abx = bx - ax
        aby = by - ay
        ab2 = abx * abx + aby * aby
        if ab2 < 1e-12:
            return QPointF(ax, ay)
        t = ((px - ax) * abx + (py - ay) * aby) / ab2
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        return QPointF(ax + t * abx, ay + t * aby)

    def _snap_to_wire(self, scene_pos: QPointF) -> QPointF | None:
        if _WireItem is None:
            return None
        wires = [it for it in self.items() if isinstance(it, _WireItem)]
        if not wires:
            return None
        gs = float(getattr(self, "grid_size", 20) or 20)
        tol = max(6.0, gs * 0.6)
        best = None
        best_d2 = tol * tol
        for w in wires:
            pts = w.render_points() if hasattr(w, "render_points") else w._manhattan_points()
            for i in range(len(pts) - 1):
                q = self._closest_point_on_segment(scene_pos, pts[i], pts[i + 1])
                d2 = (q.x() - scene_pos.x()) ** 2 + (q.y() - scene_pos.y()) ** 2
                if d2 <= best_d2:
                    best_d2 = d2
                    best = q
        return best

    def _wires_at_key(self, key: Tuple[float, float]):
        if _WireItem is None:
            return []
        p = QPointF(float(key[0]), float(key[1]))
        wires = [it for it in self.items() if isinstance(it, _WireItem)]
        return [w for w in wires if self._wire_contains_point(w, p)]

    def _implicit_wire_join_keys(
        self,
        wire_endpoint_keys: Dict[object, tuple[Tuple[float, float], Tuple[float, float]] | None],
    ) -> set[Tuple[float, float]]:
        """Return endpoint keys that should behave like intentional T-junctions.

        This makes wiring more robust after save/load and when a wire endpoint
        lands exactly on another wire segment, even if the explicit junction
        bookkeeping was missed on creation.
        """
        if _WireItem is None:
            return set()
        out: set[Tuple[float, float]] = set()
        for wire, endpoints in wire_endpoint_keys.items():
            if not endpoints:
                continue
            for key in endpoints:
                owners = [w for w in self._wires_at_key(key) if w is not None]
                if len(owners) >= 2:
                    out.add(key)
        return out

    def _register_wire_junction(self, p: QPointF):
        """Create an explicit junction marker (wire-to-wire connection)."""
        key = self._net_point_key(p)
        self._wire_junctions.add(key)
        self._rebuild_junction_markers()

    def _add_junction_owner(self, key: Tuple[float, float], wire):
        owners = self._junction_owners.setdefault(key, set())
        owners.add(wire)

    def _rebuild_junction_markers(self):
        """Show explicit wire junction nodes where wires are intentionally connected."""

        # Clear existing markers first
        for dot in list(self._junction_markers):
            try:
                self.removeItem(dot)
            except Exception:
                pass
        self._junction_markers.clear()

        if _WireItem is None:
            return

        wires = [it for it in self.items() if isinstance(it, _WireItem)]
        wire_endpoint_keys: Dict[object, tuple[Tuple[float, float], Tuple[float, float]] | None] = {}
        for w in wires:
            pts = w.render_points() if hasattr(w, "render_points") else w._manhattan_points()
            if pts and len(pts) >= 2:
                wire_endpoint_keys[w] = (self._net_point_key(pts[0]), self._net_point_key(pts[-1]))
            else:
                wire_endpoint_keys[w] = None

        live_junctions: set[Tuple[float, float]] = set()
        marker_keys = set(self._wire_junctions)
        marker_keys.update(self._implicit_wire_join_keys(wire_endpoint_keys))
        for (x, y) in sorted(marker_keys):
            owners = self._wires_at_key((x, y))
            if len(owners) < 2:
                continue
            live_junctions.add((x, y))
            r = 4.0
            dot = QGraphicsEllipseItem(x - r, y - r, 2 * r, 2 * r)
            pen_color = self.theme.wire if self.theme else Qt.black
            pen = QPen(pen_color, 2)
            pen.setCosmetic(True)
            dot.setPen(pen)
            dot.setBrush(QBrush(pen_color))
            dot.setZValue(2)
            dot.setAcceptedMouseButtons(Qt.NoButton)
            self.addItem(dot)
            self._junction_markers.append(dot)
        self._wire_junctions.update(live_junctions)

    def _snap_point(self, p: QPointF) -> QPointF:
        if not self.snap_on: return p
        g = self.grid_size; return QPointF(round(p.x()/g)*g, round(p.y()/g)*g)

    def _wire_mode_label(self) -> str:
        return {"orth": "Orthogonal", "free": "Free", "45": "45°"} .get(self.wire_route_mode, "Orthogonal")

    def _snap_for_mode(self, last: QPointF, raw: QPointF) -> QPointF:
        """Mode-aware snap (orth / 45 / free)."""
        if self.wire_route_mode == "free":
            return self._snap_point(raw) if self.snap_on else raw
        if self.wire_route_mode == "orth":
            dx = raw.x() - last.x()
            dy = raw.y() - last.y()
            if abs(dx) >= abs(dy):
                pt = QPointF(raw.x(), last.y())
            else:
                pt = QPointF(last.x(), raw.y())
            return self._snap_point(pt) if self.snap_on else pt
        # 45° mode: snap to horizontal/vertical/45 based on angle
        dx = raw.x() - last.x()
        dy = raw.y() - last.y()
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return self._snap_point(raw) if self.snap_on else raw
        adx = abs(dx)
        ady = abs(dy)
        if adx > 2 * ady:
            pt = QPointF(raw.x(), last.y())
        elif ady > 2 * adx:
            pt = QPointF(last.x(), raw.y())
        else:
            d = max(adx, ady)
            pt = QPointF(last.x() + (d if dx >= 0 else -d), last.y() + (d if dy >= 0 else -d))
        return self._snap_point(pt) if self.snap_on else pt

    def _preview_point_for_mode(self, last: QPointF, raw: QPointF) -> QPointF:
        """Mode-aware preview point that follows the mouse more naturally."""
        if self.wire_route_mode == "free":
            return raw
        if self.wire_route_mode == "orth":
            dx = raw.x() - last.x()
            dy = raw.y() - last.y()
            if abs(dx) >= abs(dy):
                return QPointF(raw.x(), last.y())
            return QPointF(last.x(), raw.y())
        dx = raw.x() - last.x()
        dy = raw.y() - last.y()
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return raw
        adx = abs(dx)
        ady = abs(dy)
        if adx > 2 * ady:
            return QPointF(raw.x(), last.y())
        if ady > 2 * adx:
            return QPointF(last.x(), raw.y())
        d = max(adx, ady)
        return QPointF(last.x() + (d if dx >= 0 else -d), last.y() + (d if dy >= 0 else -d))

    def _legalize_route_point(self, p: QPointF) -> QPointF:
        """In schematic mode, user-defined corners stay exactly where the user puts them."""
        return self._snap_point(p)

    def _build_preview_points(self, anchors: list[QPointF], raw_end: QPointF) -> list[QPointF]:
        """Preview path that always terminates exactly at the current mouse position."""
        if not anchors:
            return [QPointF(raw_end)]
        if len(anchors) == 1:
            start = QPointF(anchors[0])
            if self.wire_route_mode == "free":
                return [start, QPointF(raw_end)]
            if self.wire_route_mode == "orth":
                last = start
                bend = self._legalize_route_point(self._preview_point_for_mode(last, raw_end))
                return self._simplify_points([start, bend, QPointF(raw_end)])
            last = start
            bend = self._legalize_route_point(self._preview_point_for_mode(last, raw_end))
            return self._simplify_points([start, bend, QPointF(raw_end)])

        pts: list[QPointF] = [QPointF(anchors[0])]
        for a in anchors[1:]:
            pts.append(QPointF(a))
        last = QPointF(anchors[-1])
        if self.wire_route_mode == "free":
            pts.append(QPointF(raw_end))
            return self._simplify_points(pts)
        bend = self._legalize_route_point(self._preview_point_for_mode(last, raw_end))
        pts.append(bend)
        pts.append(QPointF(raw_end))
        return self._simplify_points(pts)

    def _is_45_or_orth(self, a: QPointF, b: QPointF) -> bool:
        dx = abs(a.x() - b.x())
        dy = abs(a.y() - b.y())
        return dx < 1e-6 or dy < 1e-6 or abs(dx - dy) < 1e-6

    def _nearest_port(self, comp: ComponentItem, to_scene: QPointF) -> PortItem:
        ports = [p for p in getattr(comp, 'ports', []) if p is not None]
        if not ports:
            # Fallback for legacy components
            ports = [p for p in (getattr(comp, 'port_left', None), getattr(comp, 'port_right', None)) if p is not None]
        dists = [(p, (p.scenePos() - to_scene).manhattanLength()) for p in ports]
        return min(dists, key=lambda t: t[1])[0]

    def _port_escape_point(self, port: PortItem) -> QPointF:
        comp = port.parentItem()
        p = port.scenePos()
        if not isinstance(comp, ComponentItem):
            return self._snap_point(p)
        try:
            if hasattr(comp, "forbidden_local_rect"):
                rect = comp.forbidden_local_rect().normalized()
            else:
                rect = comp.routing_local_rect().normalized()
            lp = QPointF(port.pos())
            grid = float(getattr(self, "grid_size", 20) or 20)
            tol = max(2.0, grid * 0.2)
            if abs(lp.x() - rect.left()) <= tol:
                local_escape = QPointF(rect.left() - grid, lp.y())
            elif abs(lp.x() - rect.right()) <= tol:
                local_escape = QPointF(rect.right() + grid, lp.y())
            elif abs(lp.y() - rect.top()) <= tol:
                local_escape = QPointF(lp.x(), rect.top() - grid)
            elif abs(lp.y() - rect.bottom()) <= tol:
                local_escape = QPointF(lp.x(), rect.bottom() + grid)
            else:
                left_d = abs(lp.x() - rect.left())
                right_d = abs(lp.x() - rect.right())
                top_d = abs(lp.y() - rect.top())
                bot_d = abs(lp.y() - rect.bottom())
                side = min(
                    (left_d, "left"),
                    (right_d, "right"),
                    (top_d, "top"),
                    (bot_d, "bottom"),
                    key=lambda item: item[0],
                )[1]
                if side == "left":
                    local_escape = QPointF(rect.left() - grid, lp.y())
                elif side == "right":
                    local_escape = QPointF(rect.right() + grid, lp.y())
                elif side == "top":
                    local_escape = QPointF(lp.x(), rect.top() - grid)
                else:
                    local_escape = QPointF(lp.x(), rect.bottom() + grid)
            return self._snap_point(comp.mapToScene(local_escape))
        except Exception:
            return self._snap_point(p)

    def _compose_routed_spine(
        self,
        start_port: PortItem | None,
        start_point: QPointF | None,
        end_port: PortItem | None,
        end_point: QPointF | None,
        mid_points: list[QPointF] | None = None,
        *,
        route_mode: str | None = None,
        exclude_wires: set[object] | None = None,
    ) -> list[QPointF]:
        """Build a wire spine with explicit port escape stubs outside the router.

        The router only operates between already-safe outside anchors. This keeps
        A* from reasoning inside endpoint component bodies.
        """
        a_end = start_port.scenePos() if start_port is not None else (start_point or QPointF(0, 0))
        b_end = end_port.scenePos() if end_port is not None else (end_point or QPointF(0, 0))
        a_end = self._snap_point(a_end)
        b_end = self._snap_point(b_end)

        start_escape = self._port_escape_point(start_port) if start_port is not None else None
        end_escape = self._port_escape_point(end_port) if end_port is not None else None

        route_anchors: list[QPointF] = [QPointF(start_escape or a_end)]
        for p in (mid_points or []):
            route_anchors.append(self._snap_point(p))
        route_anchors.append(QPointF(end_escape or b_end))

        exclude_components: set[ComponentItem] = set()
        for port in (start_port, end_port):
            comp = port.parentItem() if port is not None else None
            if isinstance(comp, ComponentItem):
                exclude_components.add(comp)

        middle = self._build_routed_points_from_anchors(
            route_anchors,
            exclude_components=exclude_components,
            route_mode=route_mode,
            exclude_wires=exclude_wires,
            exclude_route_start_component=False,
        )
        if not middle:
            middle = route_anchors

        spine: list[QPointF] = [QPointF(a_end)]
        if start_escape is not None:
            spine.append(QPointF(start_escape))
        if middle:
            if spine[-1] == middle[0]:
                spine.extend(middle[1:])
            else:
                spine.extend(middle)
        if end_escape is not None and (not spine or spine[-1] != end_escape):
            spine.append(QPointF(end_escape))
        if not spine or spine[-1] != b_end:
            spine.append(QPointF(b_end))
        return self._simplify_points(spine)

    def _pick_port_or_wire(self, scene_pos: QPointF):
        """Hit test for wire mode.

        Prefer the *nearest* PortItem within a small radius (grid-scaled). This solves:
        - ports covered by the SVG/body (itemAt misses)
        - clicks that are slightly off-center even though the port is highlighted
        - accidentally picking the wrong terminal when two are close

        Then allow selecting an existing wire, then fall back to component->nearest port.
        """
        gs = float(getattr(self, "grid_size", 20) or 20)
        r = max(8.0, gs * 0.65)  # pick radius in scene units
        pick_rect = QRectF(scene_pos.x() - r, scene_pos.y() - r, 2*r, 2*r)
        items = self.items(pick_rect)

        # 1) Nearest PortItem within radius
        best_port = None
        best_d = 1e18
        for it in items:
            if isinstance(it, PortItem):
                d = (it.scenePos() - scene_pos).manhattanLength()
                if d < best_d:
                    best_d = d
                    best_port = it
        if best_port is not None:
            return best_port, None

        # 2) Wire hit (allow starting from an existing wire)
        if _WireItem is not None:
            for it in items:
                if isinstance(it, _WireItem):
                    return None, it

        # 3) If we clicked on a child of a ComponentItem (e.g., SVG), climb parents and choose nearest port
        comp = None
        for it in items:
            cur = it
            while cur is not None:
                if isinstance(cur, ComponentItem):
                    comp = cur
                    break
                cur = cur.parentItem()
            if comp is not None:
                break
        if comp is not None:
            return self._nearest_port(comp, scene_pos), None

        return None, None


    def _obstacle_rects(self, *, pad: float | None = None, exclude: set[ComponentItem] | None = None) -> list[QRectF]:
        """Schematic wiring does not treat components as hard routing obstacles."""
        return []


    def _segment_clear(self, a: QPointF, b: QPointF, rects: list[QRectF]) -> bool:
        """Return True if an orthogonal segment a->b does not cross any obstacle rect.

        This uses a stroked QPainterPath intersection test, which is much more robust than
        thin-rectangle intersects checks for exact-on-grid segments.
        """
        if a == b:
            return True

        ax = a.x()
        ay = a.y()
        bx = b.x()
        by = b.y()
        tol = 1e-6
        if abs(ax - bx) < tol or abs(ay - by) < tol:
            if abs(ax - bx) < tol:
                x = ax
                y1, y2 = (ay, by) if ay <= by else (by, ay)
                for r in rects:
                    rr = r.normalized().adjusted(-0.5, -0.5, 0.5, 0.5)
                    if rr.left() - tol <= x <= rr.right() + tol:
                        if not (y2 < rr.top() - tol or y1 > rr.bottom() + tol):
                            return False
            else:
                y = ay
                x1, x2 = (ax, bx) if ax <= bx else (bx, ax)
                for r in rects:
                    rr = r.normalized().adjusted(-0.5, -0.5, 0.5, 0.5)
                    if rr.top() - tol <= y <= rr.bottom() + tol:
                        if not (x2 < rr.left() - tol or x1 > rr.right() + tol):
                            return False
            return True

        # Non-orthogonal segments are not allowed in this router.
        return False

    def _segments_from_points(self, pts: list[QPointF]) -> list[tuple[QPointF, QPointF]]:
        out: list[tuple[QPointF, QPointF]] = []
        for i in range(len(pts) - 1):
            a = pts[i]
            b = pts[i + 1]
            if (b - a).manhattanLength() > 1e-6:
                out.append((a, b))
        return out

    def _segment_crosses_existing(
        self,
        a: QPointF,
        b: QPointF,
        existing: list[tuple[QPointF, QPointF]],
        *,
        allow_touch_at: set[Tuple[float, float]] | None = None,
    ) -> bool:
        """Return True when segment a->b intersects or retraces any existing orthogonal segment."""
        allow = allow_touch_at or set()
        tol = 1e-6

        def _key(p: QPointF) -> Tuple[float, float]:
            return (round(p.x(), 4), round(p.y(), 4))

        def _between(v: float, lo: float, hi: float) -> bool:
            return lo - tol <= v <= hi + tol

        ax, ay = a.x(), a.y()
        bx, by = b.x(), b.y()
        a_vert = abs(ax - bx) < tol
        a_horz = abs(ay - by) < tol
        if not (a_vert or a_horz):
            return True

        for p, q in existing:
            px, py = p.x(), p.y()
            qx, qy = q.x(), q.y()
            b_vert = abs(px - qx) < tol
            b_horz = abs(py - qy) < tol
            if not (b_vert or b_horz):
                continue

            # Same orientation: reject overlap/retrace on the same axis.
            if a_vert and b_vert and abs(ax - px) < tol:
                lo1, hi1 = sorted((ay, by))
                lo2, hi2 = sorted((py, qy))
                if max(lo1, lo2) < min(hi1, hi2) - tol:
                    return True
            elif a_horz and b_horz and abs(ay - py) < tol:
                lo1, hi1 = sorted((ax, bx))
                lo2, hi2 = sorted((px, qx))
                if max(lo1, lo2) < min(hi1, hi2) - tol:
                    return True
            else:
                # Perpendicular intersection.
                if a_vert:
                    ix = ax
                    iy = py
                    hit = _between(ix, min(px, qx), max(px, qx)) and _between(iy, min(ay, by), max(ay, by))
                else:
                    ix = px
                    iy = ay
                    hit = _between(ix, min(ax, bx), max(ax, bx)) and _between(iy, min(py, qy), max(py, qy))
                if hit:
                    k = (round(ix, 4), round(iy, 4))
                    if k not in allow:
                        return True
        return False

    def _polyline_clear(
        self,
        pts: list[QPointF],
        rects: list[QRectF],
        existing: list[tuple[QPointF, QPointF]] | None = None,
        *,
        allow_touch_at: set[Tuple[float, float]] | None = None,
    ) -> bool:
        segs = self._segments_from_points(self._simplify_points(pts))
        prior = existing or []
        for i, (a, b) in enumerate(segs):
            if not self._segment_clear(a, b, rects):
                return False
            if self._segment_crosses_existing(a, b, prior + segs[:i], allow_touch_at=allow_touch_at):
                return False
        return True

    def _route_direct_manhattan(
        self,
        a: QPointF,
        b: QPointF,
        *,
        rects: list[QRectF] | None = None,
        existing: list[tuple[QPointF, QPointF]] | None = None,
    ) -> list[QPointF]:
        """Return a Manhattan path from a to b, preferring obstacle- and self-clear routes."""
        if getattr(self, "snap_on", True):
            a = self._snap_point(a)
            b = self._snap_point(b)
        rects = rects or []
        existing = existing or []
        allow_touch_at = {
            (round(a.x(), 4), round(a.y(), 4)),
            (round(b.x(), 4), round(b.y(), 4)),
        }
        if abs(a.x() - b.x()) < 1e-6 or abs(a.y() - b.y()) < 1e-6:
            direct = [a, b]
            if self._polyline_clear(direct, rects, existing, allow_touch_at=allow_touch_at):
                return direct

        candidates: list[list[QPointF]] = []
        mid1 = QPointF(b.x(), a.y())
        mid2 = QPointF(a.x(), b.y())
        candidates.append([a, mid1, b])
        candidates.append([a, mid2, b])

        pad = float(getattr(self, "grid_size", 20) or 20)
        for r in rects:
            x_left = self._snap_point(QPointF(r.left() - pad, a.y())).x()
            x_right = self._snap_point(QPointF(r.right() + pad, a.y())).x()
            y_top = self._snap_point(QPointF(a.x(), r.top() - pad)).y()
            y_bottom = self._snap_point(QPointF(a.x(), r.bottom() + pad)).y()
            candidates.append([a, QPointF(x_left, a.y()), QPointF(x_left, b.y()), b])
            candidates.append([a, QPointF(x_right, a.y()), QPointF(x_right, b.y()), b])
            candidates.append([a, QPointF(a.x(), y_top), QPointF(b.x(), y_top), b])
            candidates.append([a, QPointF(a.x(), y_bottom), QPointF(b.x(), y_bottom), b])
            candidates.append([a, QPointF(x_left, a.y()), QPointF(x_left, y_top), QPointF(b.x(), y_top), b])
            candidates.append([a, QPointF(x_left, a.y()), QPointF(x_left, y_bottom), QPointF(b.x(), y_bottom), b])
            candidates.append([a, QPointF(x_right, a.y()), QPointF(x_right, y_top), QPointF(b.x(), y_top), b])
            candidates.append([a, QPointF(x_right, a.y()), QPointF(x_right, y_bottom), QPointF(b.x(), y_bottom), b])

        best: list[QPointF] | None = None
        best_score: tuple[float, int] | None = None
        for cand in candidates:
            simp = self._simplify_points(cand)
            if not self._polyline_clear(simp, rects, existing, allow_touch_at=allow_touch_at):
                continue
            manhattan = 0.0
            for p, q in self._segments_from_points(simp):
                manhattan += abs(q.x() - p.x()) + abs(q.y() - p.y())
            score = (manhattan, len(simp))
            if best_score is None or score < best_score:
                best = simp
                best_score = score
        if best is not None:
            return best

        mid = QPointF(b.x(), a.y())
        return self._simplify_points([a, mid, b])

    def _grid_node(self, p: QPointF) -> Tuple[int, int]:
        q = self._snap_point(p) if getattr(self, "snap_on", True) else p
        g = float(getattr(self, "grid_size", 20) or 20)
        return (int(round(q.x() / g)), int(round(q.y() / g)))

    def _grid_point(self, node: Tuple[int, int]) -> QPointF:
        g = float(getattr(self, "grid_size", 20) or 20)
        return QPointF(float(node[0]) * g, float(node[1]) * g)

    def _route_astar_orth(
        self,
        a: QPointF,
        b: QPointF,
        *,
        rects: list[QRectF],
        existing: list[tuple[QPointF, QPointF]] | None = None,
    ) -> list[QPointF]:
        existing = existing or []
        start = self._grid_node(a)
        goal = self._grid_node(b)
        allow_touch_at = {
            (round(a.x(), 4), round(a.y(), 4)),
            (round(b.x(), 4), round(b.y(), 4)),
        }
        if start == goal:
            return [self._grid_point(start)]

        def _heur(n: Tuple[int, int]) -> float:
            return abs(goal[0] - n[0]) + abs(goal[1] - n[1])

        dirs = [
            ((1, 0), "h"),
            ((-1, 0), "h"),
            ((0, 1), "v"),
            ((0, -1), "v"),
        ]
        sx0, sy0 = start
        gx0, gy0 = goal
        for expand in (6, 10, 16, 24, 36):
            minx = min(sx0, gx0) - expand
            maxx = max(sx0, gx0) + expand
            miny = min(sy0, gy0) - expand
            maxy = max(sy0, gy0) + expand
            scene_box = QRectF(
                self._grid_point((minx, miny)),
                self._grid_point((maxx, maxy)),
            ).normalized()
            local_rects = [r for r in rects if r.intersects(scene_box.adjusted(-self.grid_size, -self.grid_size, self.grid_size, self.grid_size))]

            open_heap: list[tuple[float, float, Tuple[int, int], Optional[str]]] = []
            start_state = (start, None)
            heapq.heappush(open_heap, (_heur(start), 0.0, start, None))
            best_cost: dict[tuple[Tuple[int, int], Optional[str]], float] = {start_state: 0.0}
            came: dict[tuple[Tuple[int, int], Optional[str]], tuple[Tuple[int, int], Optional[str]] | None] = {start_state: None}
            found_state: tuple[Tuple[int, int], Optional[str]] | None = None
            steps = 0
            max_steps = 50000

            while open_heap and steps < max_steps:
                _f, g_cost, node, prev_axis = heapq.heappop(open_heap)
                state = (node, prev_axis)
                if g_cost > best_cost.get(state, float("inf")) + 1e-9:
                    continue
                if node == goal:
                    found_state = state
                    break
                steps += 1
                for (dx, dy), axis in dirs:
                    nxt = (node[0] + dx, node[1] + dy)
                    if nxt[0] < minx or nxt[0] > maxx or nxt[1] < miny or nxt[1] > maxy:
                        continue
                    pa = self._grid_point(node)
                    pb = self._grid_point(nxt)
                    if not self._segment_clear(pa, pb, local_rects):
                        continue
                    if self._segment_crosses_existing(pa, pb, existing, allow_touch_at=allow_touch_at):
                        continue
                    bend_penalty = 0.35 if (prev_axis is not None and prev_axis != axis) else 0.0
                    new_cost = g_cost + 1.0 + bend_penalty
                    nxt_state = (nxt, axis)
                    if new_cost + 1e-9 < best_cost.get(nxt_state, float("inf")):
                        best_cost[nxt_state] = new_cost
                        came[nxt_state] = state
                        heapq.heappush(open_heap, (new_cost + _heur(nxt), new_cost, nxt, axis))

            if found_state is None:
                continue

            rev_nodes: list[Tuple[int, int]] = []
            cur = found_state
            while cur is not None:
                rev_nodes.append(cur[0])
                cur = came.get(cur)
            pts = [self._grid_point(n) for n in reversed(rev_nodes)]
            pts = self._simplify_points(pts)
            if self._polyline_clear(pts, local_rects, existing, allow_touch_at=allow_touch_at):
                return pts
        return []

    def _turn_anchors_from_spine(self, spine: list[QPointF]) -> list[QPointF]:
        if len(spine) <= 2:
            return [QPointF(p) for p in spine]
        anchors: list[QPointF] = [QPointF(spine[0])]
        tol = 1e-6
        for i in range(1, len(spine) - 1):
            a = spine[i - 1]
            b = spine[i]
            c = spine[i + 1]
            same_x = abs(a.x() - b.x()) < tol and abs(b.x() - c.x()) < tol
            same_y = abs(a.y() - b.y()) < tol and abs(b.y() - c.y()) < tol
            if not (same_x or same_y):
                anchors.append(QPointF(b))
        anchors.append(QPointF(spine[-1]))
        return self._simplify_points(anchors)

    def reroute_wire_points(self, wire: "_WireItem") -> list[QPointF]:
        return wire.render_points()

    def _wire_is_attached_to_component(self, wire: "_WireItem", comp: ComponentItem) -> bool:
        for port in (getattr(wire, "port_a", None), getattr(wire, "port_b", None)):
            if port is not None and port.parentItem() is comp:
                return True
        return False

    def _wire_hits_rect(self, wire: "_WireItem", rect: QRectF) -> bool:
        pts = wire.render_points() if hasattr(wire, "render_points") else []
        if len(pts) < 2:
            return False
        for a, b in self._segments_from_points(pts):
            if not self._segment_clear(a, b, [rect]):
                return True
        return False

    def _scene_wire_segments(self, exclude_wires: set[object] | None = None) -> list[tuple[QPointF, QPointF]]:
        out: list[tuple[QPointF, QPointF]] = []
        ex = exclude_wires or set()
        if _WireItem is None:
            return out
        for it in self.items():
            if not isinstance(it, _WireItem) or it in ex:
                continue
            pts = it.render_points() if hasattr(it, "render_points") else it._manhattan_points()
            out.extend(self._segments_from_points(pts))
        return out

    def _reroute_wires_blocked_by_component(self, comp: ComponentItem):
        # In the simplified schematic-style wire model, existing wires keep
        # their explicit geometry when components move. Electrical topology
        # comes from endpoints/junctions, not from auto-rerouting.
        return

    def _reroute_all_orth_wires(self):
        return

    def _build_routed_points_from_anchors(
        self,
        anchors: list[QPointF],
        exclude_components: set[ComponentItem] | None = None,
        route_mode: str | None = None,
        exclude_wires: set[object] | None = None,
        exclude_route_start_component: bool = True,
    ) -> list[QPointF]:
        """Build the exact routed spine used by both preview and final wire creation."""
        if not anchors:
            return []
        if len(anchors) == 1:
            return [QPointF(anchors[0])]

        active_mode = route_mode or self.wire_route_mode
        ex = set(exclude_components or set())
        wire_ex = set(exclude_wires or set())
        if exclude_route_start_component and self._route_start_port is not None:
            comp = self._route_start_port.parentItem()
            if isinstance(comp, ComponentItem):
                ex.add(comp)
        rects = self._obstacle_rects(exclude=ex) if active_mode == "orth" else []
        routed: list[QPointF] = []
        for i in range(len(anchors) - 1):
            a = self._snap_point(anchors[i])
            b = self._snap_point(anchors[i + 1])
            existing = self._segments_from_points(routed)
            if active_mode == "orth":
                seg = self._route_direct_manhattan(a, b, rects=rects, existing=existing)
                if not seg:
                    return []
            elif active_mode == "free":
                seg = [a, b]
            else:  # 45°
                if self._is_45_or_orth(a, b):
                    seg = [a, b]
                else:
                    mid = QPointF(b.x(), a.y())
                    seg = self._simplify_points([a, mid, b])
            if not routed:
                routed.extend(seg)
            else:
                routed.extend(seg[1:])
        return self._simplify_points(routed)

    def _simplify_points(self, pts: list[QPointF]) -> list[QPointF]:
        """Drop duplicates, collinear midpoints, and immediate backtracking."""
        if not pts:
            return []
        tol = 1e-6
        out: list[QPointF] = []

        def _same(a: QPointF, b: QPointF) -> bool:
            return abs(a.x() - b.x()) < tol and abs(a.y() - b.y()) < tol

        def _collinear(a: QPointF, b: QPointF, c: QPointF) -> bool:
            return (abs(a.x() - b.x()) < tol and abs(b.x() - c.x()) < tol) or (
                abs(a.y() - b.y()) < tol and abs(b.y() - c.y()) < tol
            )

        for p in pts:
            out.append(QPointF(p))

            # Remove duplicate points as they appear.
            while len(out) >= 2 and _same(out[-1], out[-2]):
                out.pop()

            # Remove immediate U-turns: A -> B -> A.
            while len(out) >= 3 and _same(out[-1], out[-3]) and _collinear(out[-3], out[-2], out[-1]):
                out.pop()  # last A
                out.pop()  # middle B

            # Remove unnecessary collinear middle points: A -> B -> C.
            while len(out) >= 3 and _collinear(out[-3], out[-2], out[-1]):
                out.pop(-2)

        return out

    def _prepare_wire_anchor(self, wire: "_WireItem", raw_pos: QPointF) -> QPointF:
        """Snap to the nearest point on the drawn wire and insert a junction waypoint."""
        snapped, _insert_idx, new_spine = self._compute_wire_anchor_preview(wire, raw_pos)
        if len(new_spine) < 2:
            wire.set_points([snapped])
            return snapped

        # Strip endpoints before storing back on the wire
        wire.set_points(new_spine[1:-1])
        self._register_wire_junction(snapped)
        self._add_junction_owner(self._net_point_key(snapped), wire)
        return snapped

    def _compute_wire_anchor_preview(self, wire: "_WireItem", raw_pos: QPointF) -> tuple[QPointF, int, list[QPointF]]:
        """Compute the snapped anchor on a wire without mutating scene state."""
        spine = wire.render_points() if hasattr(wire, "render_points") else wire._manhattan_points()
        if len(spine) < 2:
            snapped = self._snap_point(raw_pos)
            return snapped, 0, [snapped]

        # Find the closest point on any rendered segment (already orthogonal)
        best_d2 = float("inf"); insert_idx = 0; best_q = spine[0]; best_a = spine[0]; best_b = spine[1]
        for i in range(len(spine) - 1):
            a, b = spine[i], spine[i + 1]
            abx, aby = (b.x() - a.x()), (b.y() - a.y())
            ab2 = abx * abx + aby * aby
            if ab2 == 0:
                q = a
            else:
                t = ((raw_pos.x() - a.x()) * abx + (raw_pos.y() - a.y()) * aby) / ab2
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                q = QPointF(a.x() + t * abx, a.y() + t * aby)
            d2 = (q.x() - raw_pos.x()) ** 2 + (q.y() - raw_pos.y()) ** 2
            if d2 < best_d2:
                best_d2, best_q, insert_idx, best_a, best_b = d2, q, i, a, b

        # Keep snapped anchor on the original segment axis to avoid off-wire anchors.
        if abs(best_a.x() - best_b.x()) < 1e-6:
            x = best_a.x()
            if self.snap_on:
                y = round(best_q.y() / self.grid_size) * self.grid_size
            else:
                y = best_q.y()
            lo = min(best_a.y(), best_b.y()); hi = max(best_a.y(), best_b.y())
            snapped = QPointF(x, min(max(y, lo), hi))
        elif abs(best_a.y() - best_b.y()) < 1e-6:
            y = best_a.y()
            if self.snap_on:
                x = round(best_q.x() / self.grid_size) * self.grid_size
            else:
                x = best_q.x()
            lo = min(best_a.x(), best_b.x()); hi = max(best_a.x(), best_b.x())
            snapped = QPointF(min(max(x, lo), hi), y)
        else:
            snapped = best_q

        # Snap junction to grid if enabled for consistent net keys.
        if self.snap_on:
            snapped = self._snap_point(snapped)

        # Insert the anchor into the spine (between insert_idx and insert_idx+1)
        new_spine = list(spine)
        if snapped != new_spine[insert_idx] and snapped != new_spine[insert_idx + 1]:
            new_spine.insert(insert_idx + 1, snapped)

        # Keep collinear points so the inserted anchor remains an explicit junction.
        dedup = [new_spine[0]]
        for p in new_spine[1:]:
            if (p - dedup[-1]).manhattanLength() > 1e-6:
                dedup.append(p)
        return snapped, insert_idx, dedup

    def _wire_anchor_candidates(self, wire: "_WireItem", raw_pos: QPointF) -> list[tuple[QPointF, list[QPointF]]]:
        spine = wire.render_points() if hasattr(wire, "render_points") else wire._manhattan_points()
        if len(spine) < 2:
            snapped = self._snap_point(raw_pos)
            return [(snapped, [snapped])]

        grid = float(getattr(self, "grid_size", 20) or 20)
        candidates: dict[Tuple[float, float], tuple[QPointF, list[QPointF], float]] = {}

        def _add_candidate(anchor: QPointF):
            snapped, _idx, new_spine = self._compute_wire_anchor_preview(wire, anchor)
            key = self._net_point_key(snapped)
            dist = abs(snapped.x() - raw_pos.x()) + abs(snapped.y() - raw_pos.y())
            prev = candidates.get(key)
            if prev is None or dist < prev[2]:
                candidates[key] = (snapped, new_spine, dist)

        _add_candidate(raw_pos)
        for p in spine:
            _add_candidate(p)
        for i in range(len(spine) - 1):
            a = spine[i]
            b = spine[i + 1]
            if abs(a.x() - b.x()) < 1e-6:
                x = a.x()
                lo = min(a.y(), b.y())
                hi = max(a.y(), b.y())
                y = lo
                while y <= hi + 1e-6:
                    _add_candidate(QPointF(x, y))
                    y += grid
            elif abs(a.y() - b.y()) < 1e-6:
                y = a.y()
                lo = min(a.x(), b.x())
                hi = max(a.x(), b.x())
                x = lo
                while x <= hi + 1e-6:
                    _add_candidate(QPointF(x, y))
                    x += grid

        ordered = sorted(candidates.values(), key=lambda item: item[2])
        return [(anchor, new_spine) for anchor, new_spine, _dist in ordered]

    def _finish_routed_wire_to_wire(self, wire: "_WireItem", raw_pos: QPointF) -> bool:
        start_port = self._route_start_port
        start_point = self._route_start_point
        best: tuple[float, QPointF, list[QPointF], list[QPointF]] | None = None
        for anchor, new_spine in self._wire_anchor_candidates(wire, raw_pos):
            routed = self._compose_routed_spine(
                start_port,
                start_point,
                None,
                anchor,
                list(self._route_pts),
                route_mode=self.wire_route_mode,
                exclude_wires={wire},
            )
            if self.wire_route_mode == "orth" and not routed:
                continue
            path_len = 0.0
            for p, q in self._segments_from_points(routed):
                path_len += abs(q.x() - p.x()) + abs(q.y() - p.y())
            click_penalty = abs(anchor.x() - raw_pos.x()) + abs(anchor.y() - raw_pos.y())
            score = path_len + click_penalty * 0.15
            if best is None or score < best[0]:
                best = (score, anchor, new_spine, routed)

        if best is None:
            anchor = self._prepare_wire_anchor(wire, raw_pos)
            self._finish_routed_wire(end_port=None, end_point=anchor, join_key=self._net_point_key(anchor), end_wire=wire)
            return True

        _score, anchor, new_spine, _routed = best
        if len(new_spine) >= 2:
            wire.set_points(new_spine[1:-1])
        join_key = self._net_point_key(anchor)
        self._register_wire_junction(anchor)
        self._add_junction_owner(join_key, wire)
        self._finish_routed_wire(end_port=None, end_point=anchor, join_key=join_key, end_wire=wire)
        return True

    def _preview_routed_spine(self, scene_pos: QPointF) -> list[QPointF]:
        start_port = self._route_start_port
        start_point = self._route_start_point
        port, wire = self._pick_port_or_wire(scene_pos)

        if port is not None and port is not self._route_start_port:
            return self._compose_routed_spine(
                start_port,
                start_point,
                port,
                None,
                list(self._route_pts),
                route_mode=self.wire_route_mode,
            )

        if wire is not None:
            best: tuple[float, list[QPointF]] | None = None
            for anchor, _new_spine in self._wire_anchor_candidates(wire, scene_pos):
                routed = self._compose_routed_spine(
                    start_port,
                    start_point,
                    None,
                    anchor,
                    list(self._route_pts),
                    route_mode=self.wire_route_mode,
                    exclude_wires={wire},
                )
                if not routed:
                    continue
                path_len = 0.0
                for p, q in self._segments_from_points(routed):
                    path_len += abs(q.x() - p.x()) + abs(q.y() - p.y())
                click_penalty = abs(anchor.x() - scene_pos.x()) + abs(anchor.y() - scene_pos.y())
                score = path_len + click_penalty * 0.15
                if best is None or score < best[0]:
                    best = (score, routed)
            if best is not None:
                return best[1]

        last = start_port.scenePos() if start_port else (start_point or QPointF())
        if self._route_pts:
            last = self._route_pts[-1]
        endp = self._snap_for_mode(last, scene_pos)
        return self._compose_routed_spine(
            start_port,
            start_point,
            None,
            endp,
            list(self._route_pts),
            route_mode=self.wire_route_mode,
        )

    def _start_temp_wire(self):
        from PySide6.QtWidgets import QGraphicsPathItem
        self._temp_dash = QGraphicsPathItem()
        pen = QPen(Qt.black, 1.2)
        pen.setCosmetic(True)
        pen.setStyle(Qt.DashLine)
        # theme-aware color
        if getattr(self, "theme", None):
            pen.setColor(self.theme.wire_selected if self.theme else pen.color())
        else:
            pen.setColor(QColor(255, 255, 0))
        self._temp_dash.setPen(pen)
        self._temp_dash.setZValue(4) # keep above grid for visibility
        self._temp_dash.setAcceptedMouseButtons(Qt.NoButton)
        self.addItem(self._temp_dash)

    def _update_temp_wire_preview(self, scene_pos: QPointF):
        """Update the dashed preview path while routing."""
        if self._temp_dash is None:
            return
        preview_pts = self._preview_routed_spine(scene_pos)
        if not preview_pts:
            return

        path = QPainterPath(preview_pts[0])
        for p in preview_pts[1:]:
            path.lineTo(p)
        self._temp_dash.setPath(path)

    def _clear_temp_wire(self):
        if getattr(self, "_temp_dash", None) is not None:
            self.removeItem(self._temp_dash)
            self._temp_dash = None

    # events
    def mousePressEvent(self, e):
        """Main interaction handler for place/wire/selection modes."""
        scene_pos = e.scenePos()
        if self.mode == SchematicScene.Mode.PLACE and self.component_to_place:
            if e.button() == Qt.LeftButton:
                if self.component_to_place == "__TEXT__":
                    pos = self._snap_point(scene_pos)
                    txt = CommentTextItem("Comment")
                    txt.setPos(pos)
                    if self.theme and hasattr(txt, "apply_theme"):
                        txt.apply_theme(self.theme)
                    self.addItem(txt)
                    self.clearSelection()
                    txt.setSelected(True)
                    txt.setTextInteractionFlags(Qt.TextEditorInteraction)
                    txt.setFocus(Qt.MouseFocusReason)
                    self.status_label.setText(f"Placed text at {pos.x():.0f},{pos.y():.0f}")
                    e.accept(); return
                comp_def = load_component_library().get(self.component_to_place)
                pos = None
                if comp_def and getattr(comp_def, "comp_type", "component") == "net":
                    pos = self._snap_to_wire(scene_pos)
                if pos is None:
                    if self._ghost_item is not None:
                        pos = self._snap_point(self._ghost_item.scenePos())
                    else:
                        pos = self._snap_point(scene_pos)
                comp = ComponentItem(self.component_to_place, pos)
                if (
                    self.component_to_place == "Chip"
                    and isinstance(self._place_chip_io, dict)
                    and hasattr(comp, "configure_chip_pins")
                ):
                    comp.configure_chip_pins(int(self._place_chip_io.get("pins", 2)))
                theme = getattr(self, "theme", None)
                if theme and hasattr(comp, "apply_theme"):
                    comp.apply_theme(theme)
                forced_refdes = (self._place_refdes_override or "").strip()
                comp.set_refdes(forced_refdes if forced_refdes else self._next_refdes(self.component_to_place))
                comp_def = load_component_library().get(self.component_to_place)
                if comp_def and getattr(comp_def, "comp_type", "component") == "net":
                    if comp_def.net_name:
                        comp.set_value(comp_def.net_name)
                if comp_def and not comp.value:
                    if comp_def.default_value:
                        comp.set_value(comp_def.default_value)
                    else:
                        label = comp_def.value_label.lower()
                        if "part" in label or comp_def.spice_type.upper() in {"D", "Q", "U", "X"}:
                            if comp_def.display_name:
                                comp.set_value(comp_def.display_name)
                forced_value = (self._place_value_override or "").strip()
                if forced_value:
                    comp.set_value(forced_value)
                self.undo_stack.push(AddComponentCommand(self, comp))
                self._bump_refseq(self.component_to_place)
                if self._ghost_item:
                    comp.setRotation(self._ghost_item.rotation())
                    if hasattr(self._ghost_item, "mirror_state") and hasattr(comp, "set_mirror"):
                        ms = self._ghost_item.mirror_state()
                        comp.set_mirror(ms.get("mx", 1.0), ms.get("my", 1.0))
                shown_ref = comp.display_refdes() if hasattr(comp, "display_refdes") else comp.refdes
                self.status_label.setText(f"Placed {shown_ref} ({self.component_to_place}) at {pos.x():.0f},{pos.y():.0f}")
                self.component_placed.emit(comp)
                self.clear_place_overrides()
                self._place_chip_io = None
                e.accept(); return
            elif e.button() == Qt.RightButton:
                self.set_mode_select(); e.accept(); return

        if self.mode == SchematicScene.Mode.WIRE:
            # Right-click cancels temp wire (keep your existing code)
            if e.button() == Qt.RightButton:
                if self._pending_port or getattr(self, '_temp_dash', None) or self._routing:
                    self._reset_wire_session()
                # Always leave wire mode on right-click cancel.
                self.set_mode_select()
                e.accept(); return

            if e.button() == Qt.LeftButton:
                port, wire = self._pick_port_or_wire(scene_pos)

                if not self._routing:
                    #Start from a port or an existing wire anchor
                    if port is None and wire is None:
                        # Start a free wire at the clicked point
                        self._routing = True
                        self._route_pts = []
                        self._route_start_port = None
                        self._route_start_point = self._snap_point(scene_pos)
                        self._start_temp_wire()
                        self._update_temp_wire_preview(scene_pos)
                        e.accept(); return
                    self._routing = True
                    self._route_pts = []                 # clear waypoints
                    if wire is not None:
                        anchor = self._prepare_wire_anchor(wire, scene_pos)
                        self._route_start_port = None
                        self._route_start_point = anchor
                        self._junction_start_key = self._net_point_key(anchor)
                    else:
                        self._route_start_port = port
                        self._route_start_point = None
                        self._junction_start_key = None
                    self._start_temp_wire()              # create dashed preview path
                    self._update_temp_wire_preview(scene_pos)
                    e.accept(); return
                else:
                    # we are routing
                    # finish if we clicked a different port
                    if port is not None and port is not self._route_start_port:
                        self._finish_routed_wire(end_port=port)
                        e.accept(); return
                    if wire is not None:
                        self._finish_routed_wire_to_wire(wire, scene_pos)
                        e.accept(); return

                    # otherwise, drop a corner based on the route mode
                    last = self._route_start_port.scenePos() if self._route_start_port else (self._route_start_point or QPointF())
                    if self._route_pts:
                        last = self._route_pts[-1]
                    pt = self._legalize_route_point(self._snap_for_mode(last, scene_pos))
                    self._route_pts.append(pt)
                    e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.mode == SchematicScene.Mode.PLACE and self._ghost_item is not None:
            comp_def = load_component_library().get(self._ghost_kind or "")
            if comp_def and getattr(comp_def, "comp_type", "component") == "net":
                snap = self._snap_to_wire(e.scenePos())
                if snap is not None:
                    self._ghost_item.setPos(snap)
                else:
                    self._ghost_item.setPos(self._snap_point(e.scenePos()))
            else:
                self._ghost_item.setPos(self._snap_point(e.scenePos()))
        if self.mode == SchematicScene.Mode.WIRE and self._routing and self._temp_dash is not None:
            self._update_temp_wire_preview(e.scenePos())
        super().mouseMoveEvent(e)

    def keyPressEvent(self, e):
        fi = self.focusItem()
        from PySide6.QtWidgets import QGraphicsTextItem as _QGT
        if isinstance(fi, _QGT) and fi.textInteractionFlags() != Qt.NoTextInteraction:
            return super().keyPressEvent(e)
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            sel = self.selectedItems()
            if sel:
                self.undo_stack.push(DeleteItemsCommand(self, sel))
            e.accept(); return
        if e.key() == Qt.Key_G:
            self.grid_on = not self.grid_on; self.update(); e.accept(); return
        ctrl_meta = Qt.ControlModifier | Qt.MetaModifier
        if e.key() == Qt.Key_S and (e.modifiers() & Qt.ShiftModifier) and (e.modifiers() & ctrl_meta):
            self.snap_on = not self.snap_on; self.status_label.setText(f"Snap: {'ON' if self.snap_on else 'OFF'} (grid {self.grid_size})"); e.accept(); return
        if e.key() == Qt.Key_W and (e.modifiers() & Qt.ShiftModifier):
            order = ["orth", "45", "free"]
            idx = order.index(self.wire_route_mode) if self.wire_route_mode in order else 0
            self.wire_route_mode = order[(idx + 1) % len(order)]
            self.status_label.setText(f"Wire mode: {self._wire_mode_label()} (Shift+W to cycle)")
            e.accept(); return
        if self.mode == SchematicScene.Mode.PLACE and self._ghost_item is not None:
            if e.key() in (Qt.Key_BracketRight, Qt.Key_R) and not (e.modifiers() & Qt.ShiftModifier):
                self._ghost_item.setRotation((self._ghost_item.rotation()+90)%360); e.accept(); return
            if e.key() in (Qt.Key_BracketLeft, Qt.Key_R) and (e.modifiers() & Qt.ShiftModifier):
                self._ghost_item.setRotation((self._ghost_item.rotation()-90)%360); e.accept(); return
            if e.key() == Qt.Key_X and hasattr(self._ghost_item, "toggle_mirror_x"):
                self._ghost_item.toggle_mirror_x(); e.accept(); return
            if e.key() == Qt.Key_Y and hasattr(self._ghost_item, "toggle_mirror_y"):
                self._ghost_item.toggle_mirror_y(); e.accept(); return
            if e.key() == Qt.Key_Escape:
                self.set_mode_select(); e.accept(); return
        if self.mode == SchematicScene.Mode.WIRE and e.key() == Qt.Key_Escape:
            if self._pending_port or getattr(self, '_temp_dash', None) or self._routing:
                self._reset_wire_session()
            self.set_mode_select()
            e.accept(); return
        if e.key() == Qt.Key_Escape:
            self.clearSelection()
            self.set_mode_select()
            e.accept(); return
        super().keyPressEvent(e)

    # refdes helpers
    def _prefix_for_kind(self, kind: str) -> str:
        comp_def = load_component_library().get(kind)
        if comp_def is not None and comp_def.prefix:
            return comp_def.prefix
        k = kind.lower()
        if k.startswith('res'): return 'R'
        if k.startswith('cap'): return 'C'
        if k.startswith('vsource') or k.startswith('vs'): return 'V'
        if k.startswith('ind'): return 'L'
        if k.startswith('dio'): return 'D'
        if k.startswith('isource') or k.startswith('current'): return 'I'
        if k.startswith('ground') or k.startswith('gnd'): return 'GND'
        return kind[:1].upper() if kind else 'X'

    def _multipart_meta_for_kind(self, kind: str) -> tuple[str, str]:
        comp_def = load_component_library().get(kind)
        if comp_def is None:
            return "", ""
        return (
            str(getattr(comp_def, "multipart_family", "") or "").strip(),
            str(getattr(comp_def, "unit_name", "") or "").strip(),
        )

    def _display_refdes_for_kind(self, kind: str, base_refdes: str) -> str:
        _, unit = self._multipart_meta_for_kind(kind)
        text = (base_refdes or "").strip()
        if text and unit:
            return f"{text}{unit}"
        return text

    def _used_units_by_refnum(self, prefix: str, family: str) -> Dict[int, set[str]]:
        used: Dict[int, set[str]] = {}
        for it in self.items():
            if not isinstance(it, ComponentItem):
                continue
            if not it.refdes:
                continue
            comp_def = getattr(it, "_comp_def", None) or load_component_library().get(it.kind)
            if comp_def is None:
                continue
            if str(getattr(comp_def, "multipart_family", "") or "").strip() != family:
                continue
            if self._prefix_for_kind(it.kind) != prefix:
                continue
            num = self._extract_refdes_number(it.refdes, prefix)
            if num is None or num <= 0:
                continue
            unit = str(getattr(comp_def, "unit_name", "") or "").strip()
            used.setdefault(num, set()).add(unit)
        return used

    def _next_refdes(self, kind: str) -> str:
        p = self._prefix_for_kind(kind)
        if p == 'GND':
            return 'GND'
        family, unit = self._multipart_meta_for_kind(kind)
        if family and unit:
            used_by_ref = self._used_units_by_refnum(p, family)
            n = 1
            while unit in used_by_ref.get(n, set()):
                n += 1
            return f"{p}{n}"
        used = self._used_refdes_numbers(p)
        n = 1
        while n in used:
            n += 1
        return f"{p}{n}"

    def _bump_refseq(self, kind: str):
        # Refdes assignment is now derived from current scene occupancy.
        # Keep method for compatibility with existing call sites.
        p = self._prefix_for_kind(kind)
        if p == 'GND':
            return
        used = self._used_refdes_numbers(p)
        self._refseq[p] = (max(used) + 1) if used else 1

    def _extract_refdes_number(self, refdes: str, prefix: str) -> int | None:
        text = (refdes or "").strip()
        if not text:
            return None
        if text.startswith(prefix):
            digits = text[len(prefix):]
        else:
            # Fallback for manually edited refs: trailing digits only.
            i = len(text)
            while i > 0 and text[i - 1].isdigit():
                i -= 1
            digits = text[i:]
        if not digits.isdigit():
            return None
        try:
            return int(digits)
        except Exception:
            return None

    def _used_refdes_numbers(self, prefix: str) -> set[int]:
        used: set[int] = set()
        for it in self.items():
            if not isinstance(it, ComponentItem):
                continue
            if not it.refdes:
                continue
            if self._prefix_for_kind(it.kind) != prefix:
                continue
            num = self._extract_refdes_number(it.refdes, prefix)
            if num is not None and num > 0:
                used.add(num)
        return used

    def _reseed_refseq(self):
        counters: Dict[str, int] = {}
        for it in self.items():
            if isinstance(it, ComponentItem) and it.refdes:
                prefix = self._prefix_for_kind(it.kind)
                if prefix == 'GND':
                    continue
                num = self._extract_refdes_number(it.refdes, prefix)
                if num is None:
                    continue
                counters[prefix] = max(counters.get(prefix, 0), num)
        self._refseq = {p: n + 1 for p, n in counters.items()}

    def _net_point_key(self, p: QPointF) -> Tuple[float, float]:
        return (round(p.x(), 4), round(p.y(), 4))

    def _label_key(self, label: str) -> Tuple[float, float]:
        data = label.encode("utf-8")
        h = zlib.adler32(data) & 0xFFFFFFFF
        hi = (h >> 16) & 0xFFFF
        lo = h & 0xFFFF
        return (float(hi), float(lo))

    def net_data(self) -> List[Dict]:
        """Return computed net metadata for panels and highlighting."""
        from .netlist_exporter import NetConnection, NetlistBuilder, _UnionFind

        components: List[ComponentItem] = [it for it in self.items() if isinstance(it, ComponentItem)]
        wires = [it for it in self.items() if (_WireItem is not None and isinstance(it, _WireItem))]

        uf = _UnionFind()
        wire_nodes: Dict[object, Tuple[str, int]] = {}
        node_to_wire: Dict[Tuple[str, int], object] = {}
        wire_points: Dict[object, List[Tuple[float, float]]] = {}
        wire_endpoint_keys: Dict[object, tuple[Tuple[float, float], Tuple[float, float]] | None] = {}
        endpoint_to_wires: Dict[Tuple[float, float], List[object]] = {}
        for w in wires:
            key = ("wire", id(w))
            wire_nodes[w] = key
            node_to_wire[key] = w
            uf.add(key)
            pts = w.render_points() if hasattr(w, "render_points") else w._manhattan_points()
            wire_points[w] = [self._net_point_key(p) for p in pts] if pts else []
            if pts and len(pts) >= 2:
                start_key = self._net_point_key(pts[0])
                end_key = self._net_point_key(pts[-1])
                wire_endpoint_keys[w] = (start_key, end_key)
                endpoint_to_wires.setdefault(start_key, []).append(w)
                endpoint_to_wires.setdefault(end_key, []).append(w)
            else:
                wire_endpoint_keys[w] = None

        port_connections: Dict[Tuple[str, int], List[NetConnection]] = {}
        port_points: Dict[Tuple[str, int], Tuple[float, float]] = {}
        for comp in components:
            ports = [p for p in getattr(comp, 'ports', []) if p is not None] or [
                p for p in (getattr(comp, 'port_left', None), getattr(comp, 'port_right', None))
                if p is not None
            ]
            for port in ports:
                node = ("port", id(port))
                uf.add(node)
                p = port.scenePos()
                port_points[node] = self._net_point_key(p)
                conn = NetConnection(
                    component_refdes=(comp.display_refdes() if hasattr(comp, "display_refdes") else comp.refdes),
                    component_kind=comp.kind,
                    port_name=port.name,
                )
                port_connections.setdefault(node, []).append(conn)
                for w in endpoint_to_wires.get(port_points[node], []):
                    wire_node = wire_nodes.get(w)
                    if wire_node is not None:
                        uf.union(node, wire_node)

        # Wire-to-wire joins only happen at explicit junction nodes.
        active_junctions: set[Tuple[float, float]] = set()
        join_keys = set(self._wire_junctions)
        join_keys.update(self._implicit_wire_join_keys(wire_endpoint_keys))
        for key in list(join_keys):
            owners = list(self._junction_owners.get(key, set()))
            if not owners:
                owners = self._wires_at_key(key)
            if len(owners) < 2:
                continue
            active_junctions.add(key)
            base = wire_nodes.get(owners[0])
            if base is None:
                continue
            for w in owners[1:]:
                node = wire_nodes.get(w)
                if node is not None:
                    uf.union(base, node)
        self._wire_junctions.update(active_junctions)

        nets: List[Dict] = []
        live_keys: set[Tuple[float, float]] = set()
        sequence = 0
        for _, members in uf.groups().items():
            connections: List[NetConnection] = []
            wires_for_net: set = set()
            id_points: list[Tuple[float, float]] = []

            for m in members:
                if isinstance(m, tuple) and len(m) == 2 and m[0] == "port":
                    connections.extend(port_connections.get(m, []))
                    pkey = port_points.get(m)
                    if pkey is not None:
                        id_points.append(pkey)
                elif isinstance(m, tuple) and len(m) == 2 and m[0] == "wire":
                    wire_obj = node_to_wire.get(m)
                    if wire_obj is not None:
                        wires_for_net.add(wire_obj)
                        id_points.extend(wire_points.get(wire_obj, []))

            if not connections and not wires_for_net:
                continue
            sequence += 1
            default_name = NetlistBuilder._default_net_namer(connections, sequence)
            net_id = min(id_points) if id_points else (float(sequence), 0.0)
            label_name = self._net_label_overrides.get(net_id, "")
            name = label_name or self._net_name_overrides.get(net_id, default_name)
            nets.append({
                "id": net_id,
                "name": name,
                "default_name": default_name,
                "label_name": label_name,
                "connections": connections,
                "wires": list(wires_for_net),
            })
            live_keys.add(net_id)

        # Merge nets that share a label name (power/flags). These are global nets.
        merged: List[Dict] = []
        by_label: Dict[str, Dict] = {}
        for net in nets:
            label = (net.get("label_name") or "").strip()
            if label:
                bucket = by_label.get(label)
                if bucket is None:
                    label_id = self._label_key(label)
                    bucket = {
                        "id": label_id,
                        "name": label,
                        "default_name": label,
                        "label_name": label,
                        "connections": [],
                        "wires": [],
                    }
                    by_label[label] = bucket
                    merged.append(bucket)
                bucket["connections"].extend(net.get("connections", []))
                bucket["wires"].extend(net.get("wires", []))
            else:
                merged.append(net)

        # De-dup wires per merged net
        for net in merged:
            if net.get("wires"):
                net["wires"] = list(dict.fromkeys(net["wires"]))

        # Only keep manual overrides for non-labeled nets.
        live_non_label = {n["id"] for n in merged if not n.get("label_name")}
        stale_keys = set(self._net_name_overrides.keys()) - live_non_label
        for key in stale_keys:
            self._net_name_overrides.pop(key, None)

        return merged

    def set_net_name(self, net_id: Tuple[float, float], name: str, default_name: str):
        cleaned = name.strip()
        if not cleaned or cleaned == default_name:
            self._net_name_overrides.pop(net_id, None)
        else:
            self._net_name_overrides[net_id] = cleaned
        self._schedule_nets_changed()

    def highlight_net(self, net_id: Tuple[float, float]):
        target = next((net for net in self.net_data() if net["id"] == net_id), None)
        self.clearSelection()
        if not target:
            return
        for wire in target["wires"]:
            try:
                wire.setSelected(True)
            except Exception:
                pass

    # persistence
    
    def export_netlist_text(self) -> str:
        """Return a textual netlist for the current schematic."""
        from .netlist_exporter import NetlistBuilder
        builder = NetlistBuilder()
        return builder.export(self)
    
    def serialize(self) -> Dict:
        """Serialize the schematic to a JSON-friendly dict."""
        comps = [it for it in self.items() if isinstance(it, ComponentItem)]
        port_ref: Dict[PortItem, Tuple[int, str]] = {}
        for idx, c in enumerate(comps):
            for p in [pp for pp in getattr(c, 'ports', []) if pp is not None]:
                port_ref[p] = (idx, p.name)
            # Backward-compatible fallback
            if not any(k[0] == idx for k in port_ref.values()):
                pl = getattr(c, 'port_left', None)
                pr = getattr(c, 'port_right', None)
                if pl is not None: port_ref[pl] = (idx, getattr(pl, 'name', 'A'))
                if pr is not None: port_ref[pr] = (idx, getattr(pr, 'name', 'B'))
        # ✅ use runtime-safe wire check
        wires = [it for it in self.items() if (_WireItem is not None and isinstance(it, _WireItem))]
        wire_data = []
        for w in wires:
            a = port_ref.get(w.port_a)
            b = port_ref.get(w.port_b)
            #Include waypoints (if any) and free endpoints
            pts = getattr(w, "_pts", [])
            entry = {
                'points': [{'x': float(p.x()), 'y': float(p.y())} for p in pts]
            }
            entry["mode"] = getattr(w, "route_mode", "orth")
            color_hex = w.wire_color_hex() if hasattr(w, "wire_color_hex") else ""
            if color_hex:
                entry["color"] = color_hex
            if a:
                entry['a'] = [a[0], a[1]]
            elif getattr(w, "_start_point", None) is not None:
                entry['a_point'] = {'x': float(w._start_point.x()), 'y': float(w._start_point.y())}

            if b:
                entry['b'] = [b[0], b[1]]
            elif getattr(w, "_end_point", None) is not None:
                entry['b_point'] = {'x': float(w._end_point.x()), 'y': float(w._end_point.y())}

            wire_data.append(entry)
        return {
        'components': [{
            'kind': c.kind,
            'pos': [c.scenePos().x(), c.scenePos().y()],
            'rotation': c.rotation(),
            'mirror': c.mirror_state() if hasattr(c, "mirror_state") else {"mx": 1.0, "my": 1.0},
            'refdes': c.refdes,
            'value': c.value,
            'pl_source_id': int(getattr(c, "_pl_source_id", -1)),
            'labels': c.labels_state() if hasattr(c, "labels_state") else {},
            'chip': c.chip_data() if hasattr(c, "chip_data") else {},
            'chip_io': {
                'pins': c.chip_pin_count(),
            } if hasattr(c, "chip_io_counts") and getattr(c, "is_chip", lambda: False)() else {},
        } for c in comps],
        'texts': [
            {
                'text': t.toPlainText(),
                'pos': [float(t.scenePos().x()), float(t.scenePos().y())],
                'style': t.text_state() if hasattr(t, "text_state") else {},
            }
            for t in self.items()
            if isinstance(t, CommentTextItem)
        ],
        'wires': wire_data,
        'settings': {
            'grid_on': self.grid_on, 'grid_size': self.grid_size,
            'grid_style': self.grid_style, 'snap_on': self.snap_on,
            'wire_junctions': [{'x': float(k[0]), 'y': float(k[1])} for k in sorted(self._wire_junctions)],
            'net_names': [
                {'key': [float(k[0]), float(k[1])], 'name': v}
                for k, v in self._net_name_overrides.items()
            ],
        },
        '_format': 2,  # optional version tag
        }

    def load(self, data: Dict):
        """Load schematic data from a dict (inverse of serialize)."""
        for it in list(self.items()): self.removeItem(it)
        s = data.get('settings', {})
        self.grid_on = s.get('grid_on', self.grid_on); self.grid_size = s.get('grid_size', self.grid_size)
        self.grid_style = s.get('grid_style', self.grid_style); self.snap_on = s.get('snap_on', self.snap_on)
        self._junction_owners = {}
        for dot in list(getattr(self, "_junction_markers", [])):
            try:
                self.removeItem(dot)
            except Exception:
                pass
        self._junction_markers = []
        self._wire_junctions = set()
        for entry in s.get('wire_junctions', []):
            try:
                self._wire_junctions.add((round(float(entry.get('x', 0.0)), 4), round(float(entry.get('y', 0.0)), 4)))
            except Exception:
                pass
        self._net_name_overrides = {}
        for entry in s.get('net_names', []):
            key = entry.get('key')
            name = entry.get('name', "")
            if isinstance(key, list) and len(key) == 2 and name:
                self._net_name_overrides[(round(float(key[0]), 4), round(float(key[1]), 4))] = str(name)
        comps: List[ComponentItem] = []
        for cdata in data.get('components', []):
            kind = cdata['kind']; x, y = cdata['pos']; rot = cdata.get('rotation', 0)
            c = ComponentItem(kind, QPointF(x, y)); c.setRotation(rot)
            if kind == "Chip" and hasattr(c, "configure_chip_pins"):
                io = cdata.get("chip_io", {})
                if "pins" in io:
                    c.configure_chip_pins(int(io.get("pins", 2) or 2))
                else:
                    # Backward compatibility with older files.
                    ins = int(io.get("inputs", 2) or 2)
                    outs = int(io.get("outputs", 2) or 2)
                    c.configure_chip_pins(max(2, ins + outs))
            mirror = cdata.get("mirror", {})
            if hasattr(c, "set_mirror"):
                c.set_mirror(float(mirror.get("mx", 1.0)), float(mirror.get("my", 1.0)))
            c.set_refdes(cdata.get('refdes', "")); c.set_value(cdata.get('value', ""))
            setattr(c, "_pl_source_id", int(cdata.get("pl_source_id", -1)))
            if hasattr(c, "set_chip_data"):
                c.set_chip_data(cdata.get("chip", {}))
            if self.theme and hasattr(c, "apply_theme"):
                c.apply_theme(self.theme)
            if hasattr(c, "apply_labels_state"):
                c.apply_labels_state(cdata.get('labels', {}))
            if not c.value:
                cdef = load_component_library().get(kind)
                if cdef:
                    if getattr(cdef, "comp_type", "component") == "net" and cdef.net_name:
                        c.set_value(cdef.net_name)
                    elif cdef.default_value:
                        c.set_value(cdef.default_value)
                    else:
                        label = cdef.value_label.lower()
                        if "part" in label or cdef.spice_type.upper() in {"D", "Q", "U", "X"}:
                            if cdef.display_name:
                                c.set_value(cdef.display_name)
            self.addItem(c); comps.append(c)
        for tdata in data.get('texts', []):
            try:
                text = str(tdata.get('text', 'Comment'))
                pos = tdata.get('pos', [0.0, 0.0])
                t = CommentTextItem(text)
                t.setPos(QPointF(float(pos[0]), float(pos[1])))
                if hasattr(t, "apply_text_state"):
                    t.apply_text_state(tdata.get("style", {}))
                if self.theme and hasattr(t, "apply_theme"):
                    t.apply_theme(self.theme)
                self.addItem(t)
            except Exception:
                pass
        for wdata in data.get('wires', []):
            (ai, aside) = wdata.get('a', [None, None]); (bi, bside) = wdata.get('b', [None, None])
            a_point = wdata.get('a_point'); b_point = wdata.get('b_point')
            try:
                if _WireItem is None:
                    continue
                pa = pb = None
                start_point = end_point = None
                if ai is not None and aside is not None:
                    ca = comps[ai]
                    pa = next((p for p in getattr(ca, 'ports', []) if getattr(p, 'name', None) == aside), None) or (getattr(ca, 'port_left', None) if aside == 'A' else getattr(ca, 'port_right', None))
                elif a_point:
                    start_point = QPointF(a_point['x'], a_point['y'])

                if bi is not None and bside is not None:
                    cb = comps[bi]
                    pb = next((p for p in getattr(cb, 'ports', []) if getattr(p, 'name', None) == bside), None) or (getattr(cb, 'port_left', None) if bside == 'A' else getattr(cb, 'port_right', None))
                elif b_point:
                    end_point = QPointF(b_point['x'], b_point['y'])

                route_mode = wdata.get("mode", "orth")
                wire = _WireItem(
                    pa, pb,
                    start_point=start_point,
                    end_point=end_point,
                    theme=getattr(self, "theme", None),
                    route_mode=route_mode,
                )
                pts = [QPointF(d['x'], d['y']) for d in wdata.get('points', [])]
                if pts:
                    wire.set_points(pts)
                color_hex = wdata.get("color", "")
                if color_hex and hasattr(wire, "set_wire_color"):
                    wire.set_wire_color(color_hex)
                self.addItem(wire)
                if self.theme and hasattr(wire, "apply_theme"):
                    wire.apply_theme(self.theme)
            except Exception: pass
        self._reseed_refseq()
        self._rebuild_junction_markers()
        self.update()

    def _is_insert_modifier(self, e) -> bool:
        mods = e.modifiers()
        # Accept Option/Alt, Shift, or Command (Meta on macOS)
        return bool(mods & (Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier))
    
    def _finish_routed_wire(self, *, end_port: "PortItem" | None = None, end_point: QPointF | None = None, join_key: Tuple[float, float] | None = None, end_wire: "_WireItem" | None = None):
        """Create a WireItem from the current routing state, with simple obstacle-avoiding orthogonal routing."""
        from .graphics_items import WireItem
        from .commands import AddWireCommand, SetWirePointsCommand

        start_port = self._route_start_port
        start_point = self._route_start_point
        routed = self._compose_routed_spine(
            start_port,
            start_point,
            end_port,
            end_point,
            list(self._route_pts),
            route_mode=self.wire_route_mode,
            exclude_wires={end_wire} if end_wire is not None else None,
        )
        waypoints = routed[1:-1] if len(routed) >= 2 else []

        w = WireItem(
            start_port,
            end_port,
            waypoints,
            theme=getattr(self, "theme", None),
            start_point=start_point,
            end_point=end_point,
            cap_len=0.0,
            attach_end_waypoints=bool(start_port is not None or end_port is not None),
            route_mode=self.wire_route_mode,
        )

        self.undo_stack.beginMacro("Add Routed Wire")
        self.undo_stack.push(AddWireCommand(self, w))
        if waypoints:
            self.undo_stack.push(SetWirePointsCommand(w, waypoints))
        self.undo_stack.endMacro()

        if join_key is not None:
            self._register_wire_junction(QPointF(join_key[0], join_key[1]))
            self._add_junction_owner(join_key, w)
        if getattr(self, "_junction_start_key", None) is not None:
            key = self._junction_start_key
            self._register_wire_junction(QPointF(key[0], key[1]))
            self._add_junction_owner(key, w)

        # reset temp state
        self._clear_temp_wire()
        self._routing = False
        self._route_pts = []
        self._route_start_port = None
        self._route_start_point = None
        self._junction_start_key = None
    def mouseDoubleClickEvent(self, e):
        # Finish routed wire on double-click
        if self.mode == SchematicScene.Mode.WIRE and getattr(self, "_routing", False):
            item = self.itemAt(e.scenePos(), QTransform())
            end_port = item if isinstance(item, PortItem) else None
            wire = item if (_WireItem is not None and isinstance(item, _WireItem)) else None

            if end_port and end_port is not self._route_start_port:
                self._finish_routed_wire(end_port=end_port)
            elif wire is not None:
                self._finish_routed_wire_to_wire(wire, e.scenePos())
            else:
                #Finish with a free endpoint at the double-click position
                last = self._route_start_port.scenePos() if self._route_start_port else (self._route_start_point or QPointF())
                if self._route_pts:
                    last = self._route_pts[-1]
                endp = self._snap_for_mode(last, e.scenePos())
                self._finish_routed_wire(end_point=endp)

            e.accept()
            return

        super().mouseDoubleClickEvent(e)
