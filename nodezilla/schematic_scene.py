# ========================================
# File: nodezilla/schematic_scene.py
# ========================================
from __future__ import annotations
from typing import Optional, List, Dict, Tuple
import json
import zlib
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QTimer
from PySide6.QtGui import QPen, QPainterPath, QTransform, QPainter, QBrush, QColor
from PySide6.QtWidgets import QGraphicsScene, QLabel, QGraphicsView, QGraphicsItem, QGraphicsEllipseItem
from .graphics_items import ComponentItem, PortItem
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


    def apply_theme(self, theme: "Theme"):
        """Remember theme, recolor bg/grid, and propagate to items."""
        self.theme = theme
        self.setBackgroundBrush(theme.bg)

        # (grid pens, if you have them — keep as-is or derive from theme)
        # self._grid_pen_lines = QPen(theme.component_stroke, 0); self._grid_pen_lines.setCosmetic(True)
        # self._grid_pen_dots  = QPen(theme.component_stroke, 2); self._grid_pen_dots.setCosmetic(True)

        for it in self.items():
            if hasattr(it, "apply_theme"):
                it.apply_theme(theme)
        self.update()
        self._rebuild_junction_markers()

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
        self.undo_stack.push(MoveComponentCommand(comp, old_pos, new_pos))

    # modes
    def set_mode_select(self):
        self.mode = SchematicScene.Mode.SELECT
        self.component_to_place = None
        self._remove_ghost()
        if self._view: self._view.setDragMode(QGraphicsView.RubberBandDrag)
        self.status_label.setText("Arrow: select/move")

    def set_mode_place(self, kind: str):
        self.mode = SchematicScene.Mode.PLACE
        self.component_to_place = kind
        self._remove_ghost(); self._ghost_kind = kind; self._ensure_ghost()
        if self._view: self._view.setDragMode(QGraphicsView.NoDrag)
        comp_def = load_component_library().get(kind)
        if comp_def and getattr(comp_def, "comp_type", "component") == "net":
            self.status_label.setText(
                f"Place: {kind} – click a wire to attach (Esc to cancel, [ / ] to rotate)"
            )
        else:
            self.status_label.setText(
                f"Place: {kind} (next {self._next_refdes(kind)}) – click to place, ESC to cancel, [ / ] to rotate"
            )

    def set_mode_wire(self):
        self.mode = SchematicScene.Mode.WIRE
        self.component_to_place = None
        self._remove_ghost(); self._clear_temp_wire()
        if self._view: self._view.setDragMode(QGraphicsView.RubberBandDrag)
        self.status_label.setText(f"Wire ({self._wire_mode_label()}): click ports or wires, double click to finish (Esc/right-click to cancel)")

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
            gs = float(getattr(self, "grid_size", 20) or 20)
            tol = max(1.0, gs * 0.1)
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

        live_junctions: set[Tuple[float, float]] = set()
        for (x, y) in sorted(self._wire_junctions):
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
        self._wire_junctions = live_junctions

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


    def _obstacle_rects(self, *, pad: float = 6.0, exclude: set[ComponentItem] | None = None) -> list[QRectF]:
        """Rects to avoid when routing wires.

        Important: ComponentItem.boundingRect() in this project may *not* include child items (SVG symbol, ports, labels),
        and QGraphicsItem.sceneBoundingRect() is based only on the item's own boundingRect().
        So for routing we build obstacles from (boundingRect ∪ childrenBoundingRect) mapped into scene coords.

        `exclude` lets us ignore the start/end components so their own body doesn't block the first/last segment.
        """
        rects: list[QRectF] = []
        ex = exclude or set()
        for it in self.items():
            if isinstance(it, ComponentItem) and it not in ex:
                try:
                    if hasattr(it, "routing_scene_rect"):
                        r = it.routing_scene_rect(pad=0.0)
                    else:
                        r = it.mapRectToScene(it.boundingRect()).normalized()
                except Exception:
                    r = it.sceneBoundingRect().normalized()
                rects.append(r.adjusted(-pad, -pad, pad, pad))
        return rects


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

    def _route_direct_manhattan(self, a: QPointF, b: QPointF) -> list[QPointF]:
        """Return a simple Manhattan path from a to b (no obstacle checks)."""
        if getattr(self, "snap_on", True):
            a = self._snap_point(a)
            b = self._snap_point(b)
        if abs(a.x() - b.x()) < 1e-6 or abs(a.y() - b.y()) < 1e-6:
            return [a, b]
        mid = QPointF(b.x(), a.y())
        return self._simplify_points([a, mid, b])
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

        spine = wire.render_points() if hasattr(wire, "render_points") else wire._manhattan_points()
        if len(spine) < 2:
            snapped = self._snap_point(raw_pos)
            wire.set_points([snapped])
            return snapped

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
        # Strip endpoints before storing back on the wire
        wire.set_points(dedup[1:-1])
        self._register_wire_junction(snapped)
        self._add_junction_owner(self._net_point_key(snapped), wire)
        return snapped

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
        start = self._route_start_port.scenePos() if self._route_start_port else (self._route_start_point or QPointF())
        last = start if not self._route_pts else self._route_pts[-1]
        cur = self._snap_for_mode(last, scene_pos)
        if self.wire_route_mode == "orth":
            seg = self._route_direct_manhattan(last, cur)
        else:
            seg = [last, cur]

        path = QPainterPath(start)
        for p in self._route_pts:
            path.lineTo(p)
        for p in seg[1:]:
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
                theme = getattr(self, "theme", None)
                if theme and hasattr(comp, "apply_theme"):
                    comp.apply_theme(theme)
                comp.set_refdes(self._next_refdes(self.component_to_place))
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
                self.undo_stack.push(AddComponentCommand(self, comp))
                self._bump_refseq(self.component_to_place)
                if self._ghost_item: comp.setRotation(self._ghost_item.rotation())
                self.status_label.setText(f"Placed {comp.refdes} ({self.component_to_place}) at {pos.x():.0f},{pos.y():.0f}")
                e.accept(); return
            elif e.button() == Qt.RightButton:
                self.set_mode_select(); e.accept(); return

        if self.mode == SchematicScene.Mode.WIRE:
            # Right-click cancels temp wire (keep your existing code)
            if e.button() == Qt.RightButton:
                if self._pending_port or getattr(self, '_temp_dash', None):
                    self._clear_temp_wire()
                    self._routing = False
                    self._route_pts.clear()
                    self._route_start_port = None
                    self._route_start_point = None
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
                        anchor = self._prepare_wire_anchor(wire, scene_pos)
                        self._finish_routed_wire(end_port=None, end_point=anchor, join_key=self._net_point_key(anchor))
                        e.accept(); return

                    # otherwise, drop a corner based on the route mode
                    last = self._route_start_port.scenePos() if self._route_start_port else (self._route_start_point or QPointF())
                    if self._route_pts:
                        last = self._route_pts[-1]
                    pt = self._snap_for_mode(last, scene_pos)
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
        if e.key() == Qt.Key_D:
            self.grid_style = 'dots' if self.grid_style == 'lines' else 'lines'
            self.status_label.setText(f"Grid style: {self.grid_style}"); self.update(); e.accept(); return
        if e.key() == Qt.Key_S and (e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)) == (Qt.ControlModifier | Qt.ShiftModifier):
            self.snap_on = not self.snap_on; self.status_label.setText(f"Snap: {'ON' if self.snap_on else 'OFF'} (grid {self.grid_size})"); e.accept(); return
        if e.key() == Qt.Key_W and (e.modifiers() & Qt.ShiftModifier):
            order = ["orth", "45", "free"]
            idx = order.index(self.wire_route_mode) if self.wire_route_mode in order else 0
            self.wire_route_mode = order[(idx + 1) % len(order)]
            self.status_label.setText(f"Wire mode: {self._wire_mode_label()} (Shift+W to cycle)")
            e.accept(); return
        if self.mode == SchematicScene.Mode.PLACE and self._ghost_item is not None:
            if e.key() == Qt.Key_BracketRight:
                self._ghost_item.setRotation((self._ghost_item.rotation()+90)%360); e.accept(); return
            if e.key() == Qt.Key_BracketLeft:
                self._ghost_item.setRotation((self._ghost_item.rotation()-90)%360); e.accept(); return
            if e.key() == Qt.Key_Escape:
                self.set_mode_select(); e.accept(); return
        if self.mode == SchematicScene.Mode.WIRE and e.key() == Qt.Key_Escape:
            if self._pending_port or getattr(self, '_temp_dash', None):
                self._clear_temp_wire()
                self._routing = False
                self._route_pts.clear()
                self._route_start_port = None
                self._route_start_point = None
            else: self.set_mode_select()
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

    def _next_refdes(self, kind: str) -> str:
        p = self._prefix_for_kind(kind)
        if p == 'GND': return 'GND'
        n = self._refseq.get(kind, 1); return f"{p}{n}"

    def _bump_refseq(self, kind: str):
        if self._prefix_for_kind(kind) == 'GND': return
        self._refseq[kind] = self._refseq.get(kind, 1) + 1

    def _reseed_refseq(self):
        counters: Dict[str, int] = {}
        for it in self.items():
            if isinstance(it, ComponentItem) and it.refdes:
                key = it.kind
                digits = ''.join(ch for ch in it.refdes if ch.isdigit())
                num = int(digits) if digits else 0
                counters[key] = max(counters.get(key, 0), num)
        for k, v in counters.items(): self._refseq[k] = v + 1

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
        for w in wires:
            key = ("wire", id(w))
            wire_nodes[w] = key
            node_to_wire[key] = w
            uf.add(key)
            pts = w.render_points() if hasattr(w, "render_points") else w._manhattan_points()
            wire_points[w] = [self._net_point_key(p) for p in pts] if pts else []

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
                    component_refdes=comp.refdes,
                    component_kind=comp.kind,
                    port_name=port.name,
                )
                port_connections.setdefault(node, []).append(conn)
                for w in wires:
                    if self._wire_contains_point(w, p):
                        uf.union(node, wire_nodes[w])

        # Wire-to-wire joins only happen at explicit junction nodes.
        active_junctions: set[Tuple[float, float]] = set()
        for key in list(self._wire_junctions):
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
        self._wire_junctions = active_junctions

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
            'refdes': c.refdes,
            'value': c.value,
            'labels': c.labels_state() if hasattr(c, "labels_state") else {},
        } for c in comps],
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
            c.set_refdes(cdata.get('refdes', "")); c.set_value(cdata.get('value', ""))
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
        for wdata in data.get('wires', []):
            (ai, aside) = wdata.get('a', [None, None]); (bi, bside) = wdata.get('b', [None, None])
            a_point = wdata.get('a_point'); b_point = wdata.get('b_point')
            try:
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
                wire = WireItem(pa, pb, start_point=start_point, end_point=end_point, theme=getattr(self, "theme", None), route_mode=route_mode)
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
    
    def _finish_routed_wire(self, *, end_port: "PortItem" | None = None, end_point: QPointF | None = None, join_key: Tuple[float, float] | None = None):
        """Create a WireItem from the current routing state, with simple obstacle-avoiding orthogonal routing."""
        from .graphics_items import WireItem
        from .commands import AddWireCommand, SetWirePointsCommand

        # Determine the start/end anchors (ports or explicit points)
        start_port = self._route_start_port
        start_point = self._route_start_point
        a_end = start_port.scenePos() if start_port is not None else (start_point or QPointF(0, 0))

        if end_port is not None:
            b_end = end_port.scenePos()
        else:
            b_end = end_point or QPointF(0, 0)

        # Build anchor list: start -> (user corners) -> end
        anchors: list[QPointF] = [self._snap_point(a_end)]
        for p in self._route_pts:
            anchors.append(self._snap_point(p))
        anchors.append(self._snap_point(b_end))

        # Route between consecutive anchors (mode-aware)
        routed: list[QPointF] = []
        for i in range(len(anchors) - 1):
            a = anchors[i]
            b = anchors[i + 1]
            if self.wire_route_mode == "orth":
                seg = self._route_direct_manhattan(a, b)
            elif self.wire_route_mode == "free":
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
        routed = self._simplify_points(routed)

        waypoints = routed[:] if routed else []
        if start_port is None and waypoints:
            waypoints = waypoints[1:]
        if end_port is None and waypoints:
            waypoints = waypoints[:-1]

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
                anchor = self._prepare_wire_anchor(wire, e.scenePos())
                self._finish_routed_wire(end_port=None, end_point=anchor)
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
