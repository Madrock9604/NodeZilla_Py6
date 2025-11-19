# ========================================
# File: nodezilla/schematic_scene.py
# ========================================
from __future__ import annotations
from typing import Optional, List, Dict, Tuple
import json, math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPen, QPainterPath, QTransform, QPainter
from PySide6.QtWidgets import QGraphicsScene, QLabel, QGraphicsView, QGraphicsItem
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
        self._route_start_port = None # PortItem
        self._temp_dash = None        # QGraphicsPathItem (you already use one)


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
        self.status_label.setText("Wire: click two ports to connect (Esc/right-click to cancel)")

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

    def _snap_point(self, p: QPointF) -> QPointF:
        if not self.snap_on: return p
        g = self.grid_size; return QPointF(round(p.x()/g)*g, round(p.y()/g)*g)

    def _nearest_port(self, comp: ComponentItem, to_scene: QPointF) -> PortItem:
        ports = [comp.port_left, comp.port_right]
        dists = [(p, (p.scenePos()-to_scene).manhattanLength()) for p in ports]
        return min(dists, key=lambda t: t[1])[0]

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
                if self._pending_port or getattr(self, '_temp_dash', None): self._clear_temp_wire()
                e.accept(); return

            if e.button() == Qt.LeftButton:
                item = self.itemAt(scene_pos, QTransform())
                port = item if isinstance(item, PortItem) else (self._nearest_port(item, scene_pos) if isinstance(item, ComponentItem) else None)

                if not self._routing:
                    # must start from a port
                    if port is None:
                        # (optional) beep or status hint
                        e.accept(); return
                    self._routing = True
                    self._route_pts = []                 # clear waypoints
                    self._route_start_port = port        # lock start
                    self._start_temp_wire()              # create dashed preview path
                    e.accept(); return
                else:
                    # we are routing
                    # finish if we clicked a different port
                    if port is not None and port is not self._route_start_port:
                        self._finish_routed_wire(port)   # implement below
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
            start = self._route_start_port.scenePos()
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
            if self._pending_port or getattr(self, '_temp_dash', None): self._clear_temp_wire()
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
    def serialize(self) -> Dict:
        comps = [it for it in self.items() if isinstance(it, ComponentItem)]
        port_ref: Dict[PortItem, Tuple[int, str]] = {}
        for idx, c in enumerate(comps):
            port_ref[c.port_left] = (idx, 'A'); port_ref[c.port_right] = (idx, 'B')
        # ✅ use runtime-safe wire check
        wires = [it for it in self.items() if (_WireItem is not None and isinstance(it, _WireItem))]
        wire_data = []
        for w in wires:
            a = port_ref.get(w.port_a)
            b = port_ref.get(w.port_b)
            if a and b:
                # NEW: include waypoints (if any)
                pts = getattr(w, "_pts", [])
                wire_data.append({
                    'a': [a[0], a[1]],
                    'b': [b[0], b[1]],
                    'points': [{'x': float(p.x()), 'y': float(p.y())} for p in pts],
                })
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
            try:
                ca = comps[ai]; cb = comps[bi]
                pa = ca.port_left if aside == 'A' else ca.port_right
                pb = cb.port_left if bside == 'A' else cb.port_right
                wire = WireItem(pa, pb)
                pts = [QPointF(d['x'], d['y']) for d in wdata.get('points', [])]
                if pts:
                    wire.set_points(pts)
                self.addItem(wire)
                if self.theme and hasattr(wire, "apply_theme"):
                    wire.apply_theme(self.theme)
            except Exception: pass
        self._reseed_refseq(); self.update()

    def _is_insert_modifier(self, e) -> bool:
        mods = e.modifiers()
        # Accept Option/Alt, Shift, or Command (Meta on macOS)
        return bool(mods & (Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier))
    
    def _finish_routed_wire(self, end_port: "PortItem"):
        from .graphics_items import WireItem
        from .commands import AddWireCommand, SetWirePointsCommand

        w = WireItem(self._route_start_port, end_port, theme=getattr(self, "theme", None))
        # push both: add wire + set waypoints (as a macro for clean undo)
        self.undo_stack.beginMacro("Add Routed Wire")
        self.undo_stack.push(AddWireCommand(self, w))
        if self._route_pts:
            self.undo_stack.push(SetWirePointsCommand(w, self._route_pts))
        self.undo_stack.endMacro()

        # reset temp state
        self._clear_temp_wire()
        self._routing = False
        self._route_pts = []
        self._route_start_port = None

    def mouseDoubleClickEvent(self, e):
        # Finish routed wire on double-click
        if self.mode == SchematicScene.Mode.WIRE and getattr(self, "_routing", False):
            item = self.itemAt(e.scenePos(), QTransform())
            end_port = item if isinstance(item, PortItem) else None

            if end_port and end_port is not self._route_start_port:
                # finish to a port
                self._finish_routed_wire(end_port)
            else:
                # no valid port under cursor → just cancel the preview for now
                # (we’ll add “open end / junction” soon)
                self._clear_temp_wire()
                self._routing = False
                self._route_pts.clear()
                self._route_start_port = None

            e.accept()
            return

        super().mouseDoubleClickEvent(e)