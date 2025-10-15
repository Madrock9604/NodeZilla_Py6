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
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .graphics_items import WireItem  # for type hints only
from .commands import (
    AddComponentCommand, AddWireCommand, MoveComponentCommand,
    RotateComponentCommand, DeleteItemsCommand
)

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
        self._temp_line: Optional[WireItem] = None
        self.grid_on = True; self.snap_on = True; self.grid_size = 20; self.grid_style = 'lines'
        self._ghost_kind: Optional[str] = None; self._ghost_item: Optional[ComponentItem] = None
        self._view = None
        self.request_properties = None
        self.undo_stack = undo_stack
        self._refseq: Dict[str, int] = {"Resistor":1, "Capacitor":1, "VSource":1, "Inductor":1, "Diode":1, "ISource":1}

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

            # 🔧 use enum from QGraphicsItem, not the instance
            from PySide6.QtWidgets import QGraphicsItem
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
        self._temp_dash.setPen(QPen(Qt.darkGray, 1, Qt.DashLine))
        self.addItem(self._temp_dash)

    def _clear_temp_wire(self):
        self._pending_port = None
        if hasattr(self, '_temp_dash') and self._temp_dash is not None:
            self.removeItem(self._temp_dash); self._temp_dash = None

    # events
    def mousePressEvent(self, e):
        scene_pos = e.scenePos()
        if self.mode == SchematicScene.Mode.PLACE and self.component_to_place:
            if e.button() == Qt.LeftButton:
                pos = self._snap_point(scene_pos)
                comp = ComponentItem(self.component_to_place, pos)
                comp.set_refdes(self._next_refdes(self.component_to_place))
                self.undo_stack.push(AddComponentCommand(self, comp))
                self._bump_refseq(self.component_to_place)
                if self._ghost_item: comp.setRotation(self._ghost_item.rotation())
                self.status_label.setText(f"Placed {comp.refdes} ({self.component_to_place}) at {pos.x():.0f},{pos.y():.0f}")
                e.accept(); return
            elif e.button() == Qt.RightButton:
                self.set_mode_select(); e.accept(); return

        if self.mode == SchematicScene.Mode.WIRE:
            if e.button() == Qt.RightButton:
                if self._pending_port or getattr(self, '_temp_dash', None): self._clear_temp_wire()
                e.accept(); return
            item = self.itemAt(scene_pos, QTransform())
            port = item if isinstance(item, PortItem) else None
            if port is None and isinstance(item, ComponentItem):
                port = self._nearest_port(item, scene_pos)
            if port is not None:
                if self._pending_port is None:
                    self._pending_port = port; self._start_temp_wire()
                else:
                    if port is not self._pending_port:
                        from .graphics_items import WireItem  # lazy import to avoid init-order issues
                        wire = WireItem(self._pending_port, port)
                        self.undo_stack.push(AddWireCommand(self, wire))
                    self._clear_temp_wire()
                e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.mode == SchematicScene.Mode.PLACE and self._ghost_item is not None:
            self._ghost_item.setPos(self._snap_point(e.scenePos()))
        if self.mode == SchematicScene.Mode.WIRE and getattr(self, '_temp_dash', None) is not None and self._pending_port is not None:
            start = self._pending_port.scenePos(); end = e.scenePos(); mid_x = (start.x()+end.x())/2
            path = QPainterPath(start); path.lineTo(mid_x, start.y()); path.lineTo(mid_x, end.y()); path.lineTo(end)
            self._temp_dash.setPath(path)
        super().mouseMoveEvent(e)

    def keyPressEvent(self, e):
        fi = self.focusItem()
        from PySide6.QtWidgets import QGraphicsTextItem as _QGT
        if isinstance(fi, _QGT) and fi.textInteractionFlags() != Qt.NoTextInteraction:
            return super().keyPressEvent(e)
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            sel = list(self.selectedItems())
            if sel: self.undo_stack.push(DeleteItemsCommand(self, sel))
            e.accept(); return
        if e.key() == Qt.Key_G:
            self.grid_on = not self.grid_on; self.update(); e.accept(); return
        if e.key() == Qt.Key_D:
            self.grid_style = 'dots' if self.grid_style == 'lines' else 'lines'
            self.status_label.setText(f"Grid style: {self.grid_style}"); self.update(); e.accept(); return
        if e.key() == Qt.Key_S and (e.modifiers() & Qt.ControlModifier):
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

    # background grid
    def drawBackground(self, p: 'QPainter', rect: QRectF):
        if not self.grid_on: return
        from PySide6.QtGui import QPen
        g = self.grid_size; left = int((rect.left()//g)*g); top = int((rect.top()//g)*g)
        p.save()
        if self.grid_style == 'lines':
            p.setPen(QPen(Qt.lightGray, 0)); x = left
            while x < rect.right(): p.drawLine(x, rect.top(), x, rect.bottom()); x += g
            y = top
            while y < rect.bottom(): p.drawLine(rect.left(), y, rect.right(), y); y += g
        else:
            p.setPen(QPen(Qt.lightGray, 2)); x = left
            while x < rect.right():
                y = top
                while y < rect.bottom(): p.drawPoint(int(x), int(y)); y += g
                x += g
        p.restore()

    # persistence
    def serialize(self) -> Dict:
        comps = [it for it in self.items() if isinstance(it, ComponentItem)]
        port_ref: Dict[PortItem, Tuple[int, str]] = {}
        for idx, c in enumerate(comps):
            port_ref[c.port_left] = (idx, 'A'); port_ref[c.port_right] = (idx, 'B')
        wires = [it for it in self.items() if isinstance(it, WireItem)]
        wire_data = []
        for w in wires:
            a = port_ref.get(w.port_a); b = port_ref.get(w.port_b)
            if a and b: wire_data.append({'a':[a[0],a[1]], 'b':[b[0],b[1]]})
        return {
            'components': [{
                'kind': c.kind,
                'pos': [c.scenePos().x(), c.scenePos().y()],
                'rotation': c.rotation(), 'refdes': c.refdes, 'value': c.value,
            } for c in comps],
            'wires': wire_data,
            'settings': {'grid_on': self.grid_on, 'grid_size': self.grid_size, 'grid_style': self.grid_style, 'snap_on': self.snap_on}
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
                self.addItem(WireItem(pa, pb))
            except Exception: pass
        self._reseed_refseq(); self.update()