# ========================================
# File: nodezilla/schematic_scene.py
# ========================================
from __future__ import annotations
from typing import Optional, List, Dict, Tuple
import json, math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPen, QPainterPath, QPainterPathStroker, QTransform, QPainter, QBrush
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
try:
    from .graphics_items import WireItem as _WireItem  # runtime check
except Exception:
    _WireItem = None

class SchematicScene(QGraphicsScene):
    class Mode:
        SELECT = 0; PLACE = 1; WIRE = 2

    def __init__(self, status_label: QLabel, undo_stack):
        super().__init__()
        self.mode = SchematicScene.Mode.SELECT
        self.component_to_place: Optional[str] = None
        self.status_label = status_label
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self._pending_port: Optional[PortItem] = None
        self.grid_on = True; self.snap_on = True; self.grid_size = 20; self.grid_style = 'lines'
        self._ghost_kind: Optional[str] = None; self._ghost_item: Optional[ComponentItem] = None
        self._view = None
        self.request_properties = None
        self.undo_stack = undo_stack
        self._refseq: Dict[str, int] = {"Resistor":1, "Capacitor":1, "VSource":1, "Inductor":1, "Diode":1, "ISource":1}
        self.theme: Theme | None = None
        # default grid pen; will be set in apply_theme
        self._grid_pen_lines = QPen(Qt.lightGray, 0)
        self._grid_pen_dots  = QPen(Qt.lightGray, 2)
        self._routing = False
        self._route_pts = []          # waypoints between endpoints (QPointF)
        self._route_start_port = None # PortItem or None if starting from free/junction point
        self._route_start_point: Optional[QPointF] = None
        self._temp_dash = None        # QGraphicsPathItem (you already use one)
        self._junction_markers: list[QGraphicsItem] = []


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

    def drawBackground(self, p: 'QPainter', rect: QRectF):
        if not self.grid_on: return
        g = self.grid_size
        left = int((rect.left()//g)*g); top = int((rect.top()//g)*g)
        p.save()
        if self.grid_style == 'lines':
            p.setPen(self._grid_pen_lines)
            x = left
            while x < rect.right(): p.drawLine(x, rect.top(), x, rect.bottom()); x += g
            y = top
            while y < rect.bottom(): p.drawLine(rect.left(), y, rect.right(), y); y += g
        else:
            p.setPen(self._grid_pen_dots)
            x = left
            while x < rect.right():
                y = top
                while y < rect.bottom(): p.drawPoint(int(x), int(y)); y += g
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
        self.status_label.setText(f"Place: {kind} (next {self._next_refdes(kind)}) – click to place, ESC to cancel, [ / ] to rotate")

    def set_mode_wire(self):
        self.mode = SchematicScene.Mode.WIRE
        self.component_to_place = None
        self._remove_ghost(); self._clear_temp_wire()
        if self._view: self._view.setDragMode(QGraphicsView.RubberBandDrag)
        self.status_label.setText("Wire: click ports or wires, double click to finish (Esc/right-click to cancel)")

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


    def _remove_ghost(self):
        if self._ghost_item is not None:
            self.removeItem(self._ghost_item); self._ghost_item = None; self._ghost_kind = None

    def _rebuild_junction_markers(self):
        """Show a dot where two or more wires share the exact same point."""

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
        if len(wires) < 2:
            return

        point_wires: Dict[Tuple[float, float], set[int]] = {}
        for w in wires:
            seen = set()
            for p in w._manhattan_points():
                key = (round(p.x(), 4), round(p.y(), 4))
                if key in seen:
                    continue
                seen.add(key)
                point_wires.setdefault(key, set()).add(id(w))

        for (x, y), owners in point_wires.items():
            if len(owners) < 2:
                continue
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

    def _snap_point(self, p: QPointF) -> QPointF:
        if not self.snap_on: return p
        g = self.grid_size; return QPointF(round(p.x()/g)*g, round(p.y()/g)*g)

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
                    local = it.boundingRect().united(it.childrenBoundingRect())
                    r = it.mapRectToScene(local).normalized()
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

        # Build a stroked path so "touching" an obstacle counts as a collision.
        # Width is tied to grid size but clamped to stay reasonable.
        gs = float(getattr(self, "grid_size", 20) or 20)
        width = max(1.5, min(6.0, gs * 0.20))

        path = QPainterPath()
        path.moveTo(a)
        path.lineTo(b)

        stroker = QPainterPathStroker()
        stroker.setWidth(width)
        stroked = stroker.createStroke(path)

        for r in rects:
            rr = r.normalized()
            # Inflate a little so we don't slip through on edges
            rr = rr.adjusted(-0.5, -0.5, 0.5, 0.5)
            if stroked.intersects(rr):
                return False
        return True
    def _simplify_points(self, pts: list[QPointF]) -> list[QPointF]:
        """Drop duplicates and collinear midpoints."""
        if not pts:
            return []
        # drop consecutive duplicates
        out = [pts[0]]
        for p in pts[1:]:
            if (p - out[-1]).manhattanLength() > 1e-6:
                out.append(p)
        # drop collinear
        i = 1
        while i < len(out) - 1:
            a, b, c = out[i - 1], out[i], out[i + 1]
            if (abs(a.x() - b.x()) < 1e-6 and abs(b.x() - c.x()) < 1e-6) or (abs(a.y() - b.y()) < 1e-6 and abs(b.y() - c.y()) < 1e-6):
                out.pop(i)
            else:
                i += 1
        return out

    def _route_orthogonal(self, a: QPointF, b: QPointF, rects: list[QRectF]) -> list[QPointF]:
        """Find a simple orthogonal path a->b avoiding obstacle rects.
        Returns [a, ..., b] including endpoints.
        """
        gs = float(getattr(self, "grid_size", 20) or 20)

        def snap(p: QPointF) -> QPointF:
            if getattr(self, "snap_on", True):
                return self._snap_point(p)
            return p

        a = snap(a); b = snap(b)

        # Candidate 1: straight if aligned
        if abs(a.x() - b.x()) < 1e-6 or abs(a.y() - b.y()) < 1e-6:
            if self._segment_clear(a, b, rects):
                return [a, b]

        # Candidate 2: L-shapes (HV and VH)
        mid_hv = QPointF(b.x(), a.y())
        if self._segment_clear(a, mid_hv, rects) and self._segment_clear(mid_hv, b, rects):
            return self._simplify_points([a, mid_hv, b])

        mid_vh = QPointF(a.x(), b.y())
        if self._segment_clear(a, mid_vh, rects) and self._segment_clear(mid_vh, b, rects):
            return self._simplify_points([a, mid_vh, b])

        # Candidate 3: try a few offset "channels"
        # We generate candidates and pick the shortest; prefer routing "up" first (Qt scene Y grows downward).
        best: list[QPointF] | None = None
        best_len: float = 1e18
        best_bends: int = 1_000_000
        best_pref: int = 1_000_000  # smaller is better (up is 0, down is 1)

        def manhattan_len(pts: list[QPointF]) -> float:
            s = 0.0
            for i in range(len(pts) - 1):
                s += abs(pts[i+1].x() - pts[i].x()) + abs(pts[i+1].y() - pts[i].y())
            return s

        def bends(pts: list[QPointF]) -> int:
            # count direction changes (after simplifying)
            pts2 = self._simplify_points(pts)
            bcount = 0
            for i in range(1, len(pts2) - 1):
                dx1 = pts2[i].x() - pts2[i-1].x()
                dy1 = pts2[i].y() - pts2[i-1].y()
                dx2 = pts2[i+1].x() - pts2[i].x()
                dy2 = pts2[i+1].y() - pts2[i].y()
                if (abs(dx1) > 1e-6 and abs(dy2) > 1e-6) or (abs(dy1) > 1e-6 and abs(dx2) > 1e-6):
                    bcount += 1
            return bcount

        def consider(candidate: list[QPointF], pref_rank: int):
            nonlocal best, best_len, best_bends, best_pref
            cand = self._simplify_points(candidate)
            L = manhattan_len(cand)
            B = bends(cand)
            if (L < best_len - 1e-6) or (abs(L - best_len) < 1e-6 and (B < best_bends or (B == best_bends and pref_rank < best_pref))):
                best = cand
                best_len = L
                best_bends = B
                best_pref = pref_rank

        # Try horizontal channel (vary y): up first, then down
        for k in range(1, 16):
            for pref_rank, sign in ((0, -1), (1, 1)):
                y = a.y() + sign * k * gs
                p1 = snap(QPointF(a.x(), y))
                p2 = snap(QPointF(b.x(), y))
                if self._segment_clear(a, p1, rects) and self._segment_clear(p1, p2, rects) and self._segment_clear(p2, b, rects):
                    consider([a, p1, p2, b], pref_rank)
                    # small k already likely near-optimal, but keep searching for a shorter route if any

        # Try vertical channel (vary x): left first, then right
        for k in range(1, 16):
            for pref_rank, sign in ((0, -1), (1, 1)):
                x = a.x() + sign * k * gs
                p1 = snap(QPointF(x, a.y()))
                p2 = snap(QPointF(x, b.y()))
                if self._segment_clear(a, p1, rects) and self._segment_clear(p1, p2, rects) and self._segment_clear(p2, b, rects):
                    consider([a, p1, p2, b], 10 + pref_rank)

        if best is not None:
            return best

        # Give up: return a simple L (still orthogonal)
        return self._simplify_points([a, mid_hv, b])

    def _prepare_wire_anchor(self, wire: "_WireItem", raw_pos: QPointF) -> QPointF:
        """Snap to the nearest point on the drawn wire and insert a junction waypoint."""

        spine = wire._manhattan_points()
        if len(spine) < 2:
            snapped = self._snap_point(raw_pos)
            wire.set_points([snapped])
            return snapped

        # Find the closest point on any rendered segment (already orthogonal)
        best_d2 = float("inf"); insert_idx = 0; best_q = spine[0]
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
                best_d2, best_q, insert_idx = d2, q, i

        snapped = self._snap_point(best_q)

        # Insert the anchor into the spine (between insert_idx and insert_idx+1)
        new_spine = list(spine)
        if snapped != new_spine[insert_idx] and snapped != new_spine[insert_idx + 1]:
            new_spine.insert(insert_idx + 1, snapped)

        # Collapse redundant collinear points so the junction is clean
        def _collapse(pts: List[QPointF]) -> List[QPointF]:
            if len(pts) <= 2:
                return pts
            clean = [pts[0]]
            for i in range(1, len(pts) - 1):
                prev, cur, nxt = pts[i - 1], pts[i], pts[i + 1]
                same_x = prev.x() == cur.x() == nxt.x()
                same_y = prev.y() == cur.y() == nxt.y()
                if not (same_x or same_y):
                    clean.append(cur)
            clean.append(pts[-1])
            return clean

        new_spine = _collapse(new_spine)
        # Strip endpoints before storing back on the wire
        wire.set_points(new_spine[1:-1])
        return snapped

    def _start_temp_wire(self):
        from PySide6.QtWidgets import QGraphicsPathItem
        self._temp_dash = QGraphicsPathItem()
        pen = QPen(Qt.black, 1)
        pen.setCosmetic(True)
        pen.setStyle(Qt.DashLine)
        # theme-aware color
        if getattr(self, "theme", None):
            pen.setColor(self.theme.wire_selected if self.theme else pen.color())
        self._temp_dash.setPen(pen)
        self._temp_dash.setZValue(-1) # keep below wires so clicks hit targets
        self._temp_dash.setAcceptedMouseButtons(Qt.NoButton)
        self.addItem(self._temp_dash)

    def _clear_temp_wire(self):
        if getattr(self, "_temp_dash", None) is not None:
            self.removeItem(self._temp_dash)
            self._temp_dash = None

    # events
    def mousePressEvent(self, e):
        scene_pos = e.scenePos()
        if self.mode == SchematicScene.Mode.PLACE and self.component_to_place:
            if e.button() == Qt.LeftButton:
                pos = self._snap_point(scene_pos)
                comp = ComponentItem(self.component_to_place, pos)
                theme = getattr(self, "theme", None)
                if theme and hasattr(comp, "apply_theme"):
                    comp.apply_theme(theme)
                comp.set_refdes(self._next_refdes(self.component_to_place))
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
                        e.accept(); return
                    self._routing = True
                    self._route_pts = []                 # clear waypoints
                    if wire is not None:
                        anchor = self._prepare_wire_anchor(wire, scene_pos)
                        self._route_start_port = None
                        self._route_start_point = anchor
                    else:
                        self._route_start_port = port
                        self._route_start_point = None
                    self._start_temp_wire()              # create dashed preview path
                    e.accept(); return
                else:
                    # we are routing
                    # finish if we clicked a different port
                    if port is not None and port is not self._route_start_port:
                        self._finish_routed_wire(end_port=port)
                        e.accept(); return
                    if wire is not None:
                        anchor = self._prepare_wire_anchor(wire, scene_pos)
                        self._finish_routed_wire(end_port=None, end_point=anchor)
                        e.accept(); return

                    # otherwise, drop a snapped corner
                    self._route_pts.append(self._snap_point(scene_pos))
                    e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.mode == SchematicScene.Mode.PLACE and self._ghost_item is not None:
            self._ghost_item.setPos(self._snap_point(e.scenePos()))
        if self.mode == SchematicScene.Mode.WIRE and self._routing and self._temp_dash is not None:
            # build a Manhattan L-segment from last anchor to cursor
            start = self._route_start_port.scenePos() if self._route_start_port else (self._route_start_point or QPointF())
            last  = start if not self._route_pts else self._route_pts[-1]
            cur   = e.scenePos()
            mid   = QPointF(cur.x(), last.y())       # orthogonal dog-leg

            path = QPainterPath(start)
            for p in self._route_pts:
                path.lineTo(p)
            path.lineTo(mid)
            path.lineTo(cur)
            self._temp_dash.setPath(path)
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

    # persistence
    
    def export_netlist_text(self) -> str:
        """Return a textual netlist for the current schematic."""
        from .netlist_exporter import NetlistBuilder
        builder = NetlistBuilder()
        return builder.export(self)
    
    def serialize(self) -> Dict:
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
        } for c in comps],
        'wires': wire_data,
        'settings': {
            'grid_on': self.grid_on, 'grid_size': self.grid_size,
            'grid_style': self.grid_style, 'snap_on': self.snap_on,
        },
        '_format': 2,  # optional version tag
        }

    def load(self, data: Dict):
        for it in list(self.items()): self.removeItem(it)
        s = data.get('settings', {})
        self.grid_on = s.get('grid_on', self.grid_on); self.grid_size = s.get('grid_size', self.grid_size)
        self.grid_style = s.get('grid_style', self.grid_style); self.snap_on = s.get('snap_on', self.snap_on)
        comps: List[ComponentItem] = []
        for cdata in data.get('components', []):
            kind = cdata['kind']; x, y = cdata['pos']; rot = cdata.get('rotation', 0)
            c = ComponentItem(kind, QPointF(x, y)); c.setRotation(rot)
            c.set_refdes(cdata.get('refdes', "")); c.set_value(cdata.get('value', ""))
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

                wire = WireItem(pa, pb, start_point=start_point, end_point=end_point, theme=getattr(self, "theme", None))
                pts = [QPointF(d['x'], d['y']) for d in wdata.get('points', [])]
                if pts:
                    wire.set_points(pts)
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
    
    def _finish_routed_wire(self, *, end_port: "PortItem" | None = None, end_point: QPointF | None = None):
        """Create a WireItem from the current routing state, with simple obstacle-avoiding orthogonal routing."""
        from .graphics_items import WireItem
        from .commands import AddWireCommand, SetWirePointsCommand

        # Build obstacle map once (include all components; endcaps are pushed outside)
        rects = self._obstacle_rects(pad=max(6.0, float(getattr(self, "grid_size", 20)) * 0.35), exclude=set())


        # Determine the start/end anchors (ports or explicit points)
        start_port = self._route_start_port
        start_point = self._route_start_point
        a_end = start_port.scenePos() if start_port is not None else (start_point or QPointF(0, 0))

        if end_port is not None:
            b_end = end_port.scenePos()
        else:
            b_end = end_point or QPointF(0, 0)

        cap_len = float(getattr(self, "grid_size", 20)) * 1.5

        def cap_point(port: PortItem, length: float) -> QPointF:
            """Return a cap point guaranteed to be outside the component obstacle.

            Direction is derived from the port LOCAL position (stable even when labels change bounds).
            The local direction is mapped into scene (handles rotation) and snapped to the dominant
            scene axis so routing stays orthogonal.
            """
            parent = port.parentItem()
            if parent is None:
                return port.scenePos()

            sp = port.scenePos()

            # 1) Choose a direction in parent-local coords based on port side
            lp = port.pos()
            if abs(lp.x()) >= abs(lp.y()):
                d_local = QPointF(-1.0 if lp.x() < 0 else 1.0, 0.0)
            else:
                d_local = QPointF(0.0, -1.0 if lp.y() < 0 else 1.0)

            # 2) Map to scene and snap to dominant axis
            p0 = parent.mapToScene(QPointF(0.0, 0.0))
            p1 = parent.mapToScene(d_local)
            v = p1 - p0
            if abs(v.x()) >= abs(v.y()):
                dx, dy = (-1.0, 0.0) if v.x() < 0 else (1.0, 0.0)
            else:
                dx, dy = (0.0, -1.0) if v.y() < 0 else (0.0, 1.0)

            # Ensure we step far enough to clear the component padded bounding box
            gs_local = float(getattr(self, "grid_size", 20) or 20)
            pad_local = max(6.0, gs_local * 0.35)
            try:
                local = parent.boundingRect().united(parent.childrenBoundingRect())
                obst = parent.mapRectToScene(local).normalized().adjusted(-pad_local, -pad_local, pad_local, pad_local)
            except Exception:
                obst = parent.sceneBoundingRect().normalized().adjusted(-pad_local, -pad_local, pad_local, pad_local)

            L = float(length)
            p = QPointF(sp.x() + dx * L, sp.y() + dy * L)
            it = 0
            while obst.contains(p) and it < 128:
                L += gs_local
                p = QPointF(sp.x() + dx * L, sp.y() + dy * L)
                it += 1
            return p

        a_anchor = cap_point(start_port, cap_len) if start_port is not None else a_end
        b_anchor = cap_point(end_port, cap_len) if end_port is not None else b_end

        # Build anchor list: start anchor -> (user corners) -> end anchor
        anchors: list[QPointF] = [self._snap_point(a_anchor)]
        for p in self._route_pts:
            anchors.append(self._snap_point(p))
        anchors.append(self._snap_point(b_anchor))

        # Route between each consecutive anchor with obstacle avoidance
        routed: list[QPointF] = []
        for i in range(len(anchors) - 1):
            seg = self._route_orthogonal(anchors[i], anchors[i + 1], rects)
            if not routed:
                routed.extend(seg)
            else:
                routed.extend(seg[1:])  # avoid dup
        routed = self._simplify_points(routed)

        # Store endcaps as explicit waypoints so WireItem.update_path() cannot "rebuild" a path that goes through symbols.
        waypoints = routed[:] if routed else []
        # Trim anchors that are actual free endpoints (no port) to avoid duplicating the true endpoints.
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
        )


        self.undo_stack.beginMacro("Add Routed Wire")
        self.undo_stack.push(AddWireCommand(self, w))
        if waypoints:
            self.undo_stack.push(SetWirePointsCommand(w, waypoints))
        self.undo_stack.endMacro()

        # reset temp state
        self._clear_temp_wire()
        self._routing = False
        self._route_pts = []
        self._route_start_port = None
        self._route_start_point = None
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
                self._finish_routed_wire(end_point=self._snap_point(e.scenePos()))

            e.accept()
            return

        super().mouseDoubleClickEvent(e)
