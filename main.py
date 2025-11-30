"""
PySide6 Schematic Capture – MVP + Properties Panel + Extra Symbols + Undo/Redo

Features:
- Main window with tabs (Schematic, Instruments placeholder) and a right-side Properties panel
- Grid (lines/dots), snap, adjustable grid size, zoom, pan
- Place components (Resistor, Capacitor, VSource, Inductor, Diode, ISource, Ground)
  • Ghost preview & rotation during placement ([ and ])
  • Auto RefDes on placement (R1, C1, V1, L1, D1, I1; Ground → GND)
- Wire mode (click two ports), cancel with Esc or right-click
- Wires update when components move/rotate
- Inline label editing (double-click component: "R5 10k") and Properties panel
- Delete selected, Fit, Zoom, Rotate selected
- Save/Load JSON + persist editor settings and component metadata (refdes, value)
- Undo/Redo for: add component, add wire, move component, rotate component, delete selection

Run:  python main.py
Deps: PySide6
"""
from __future__ import annotations

import json
import math
from typing import Optional, List, Dict, Tuple
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QAction, QPainter, QPen, QBrush, QTransform, QKeySequence,
    QPainterPath, QUndoStack, QUndoCommand
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QToolBar,
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsRectItem,
    QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsTextItem,
    QFileDialog, QMessageBox, QStatusBar, QLabel, QSpinBox, QDockWidget,
    QFormLayout, QLineEdit, QPushButton
)
from PySide6.QtSvgWidgets import QGraphicsSvgItem

# --------------------------
# Graphics Items
# --------------------------

PORT_RADIUS = 5.0
COMP_WIDTH = 100.0
COMP_HEIGHT = 40.0


class InlineLabel(QGraphicsTextItem):
    """Editable label that keeps text upright and commits to the parent ComponentItem."""
    def __init__(self, parent_item: 'ComponentItem'):
        super().__init__("", parent_item)
        self._parent = parent_item
        self.setDefaultTextColor(QApplication.instance().palette().text().color())
        self.setZValue(3)
        self.setTextInteractionFlags(Qt.NoTextInteraction)

    def start_edit(self):
        from PySide6.QtGui import QTextCursor
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)

    def _finish_edit(self):
        # Parse text into "REFDES VALUE" (first token refdes, rest value)
        raw = self.toPlainText().strip()
        if raw:
            parts = raw.split()
            refdes = parts[0]
            value = " ".join(parts[1:]) if len(parts) > 1 else self._parent.value
            self._parent.set_refdes(refdes)
            self._parent.set_value(value)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self._parent._update_label()

    def focusOutEvent(self, event):
        self._finish_edit()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
            self._finish_edit()
            event.accept(); return
        super().keyPressEvent(event)


class PortItem(QGraphicsEllipseItem):
    def __init__(self, parent: 'ComponentItem', name: str, rel_pos: QPointF):
        super().__init__(-PORT_RADIUS, -PORT_RADIUS, 2 * PORT_RADIUS, 2 * PORT_RADIUS, parent)
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(Qt.black, 1.25))
        self.setZValue(2)
        self.name = name
        self.rel_pos = rel_pos
        self.setPos(rel_pos)
        self.wires: List['WireItem'] = []
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

    def add_wire(self, wire: 'WireItem'):
        if wire not in self.wires:
            self.wires.append(wire)

    def remove_wire(self, wire: 'WireItem'):
        if wire in self.wires:
            self.wires.remove(wire)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for w in self.wires:
                w.update_path()
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self.setBrush(QBrush(Qt.yellow))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setBrush(QBrush(Qt.white))
        super().hoverLeaveEvent(event)


class ComponentItem(QGraphicsRectItem):
    def __init__(self, kind: str, pos: QPointF):
        super().__init__(-COMP_WIDTH/2, -COMP_HEIGHT/2, COMP_WIDTH, COMP_HEIGHT)
        self.kind = kind
        self.setBrush(QBrush(Qt.white))
        shead = 1.5
        self.setPen(QPen(Qt.black, shead))
        self.setFlags(
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setZValue(1)
        self.setRotation(0)
        # Metadata
        self.refdes: str = ""
        self.value: str = ""
        # Label overlay
        self.label = InlineLabel(self)
        self.label.setDefaultTextColor(QApplication.instance().palette().text().color())
        self.label.setZValue(3)
        # SVG symbol
        self.symbol_item: QGraphicsSvgItem | None = None
        # Ports BEFORE position
        self.port_left = PortItem(self, 'A', QPointF(-COMP_WIDTH/2, 0))
        self.port_right = PortItem(self, 'B', QPointF(COMP_WIDTH/2, 0))
        # Adjust ports for special symbols (e.g., ground)
        lk = self.kind.lower()
        if lk.startswith('ground') or lk.startswith('gnd'):
            p = QPointF(0, -COMP_HEIGHT/2)
            self.port_left.setPos(p)
            self.port_right.setPos(p)
        self.setPos(pos)
        self._update_label()
        # Symbol artwork
        self._load_symbol_graphic()
        # For move undo
        self._press_pos: Optional[QPointF] = None

    def _update_label(self):
        text = (self.refdes + (" " if self.refdes and self.value else "") + self.value).strip()
        # Keep text color synced to theme
        self.label.setDefaultTextColor(QApplication.instance().palette().text().color())
        self.label.setPlainText(text)
        br = self.rect()
        self.label.setPos(-self.label.boundingRect().width()/2, br.top() - 18)
        self.label.setRotation(-self.rotation())

    def set_refdes(self, refdes: str):
        self.refdes = refdes
        self._update_label()

    def set_value(self, value: str):
        self.value = value
        self._update_label()

    def boundingRect(self) -> QRectF:
        return super().boundingRect().adjusted(-14, -24, 14, 14)

    def _symbol_path_for_kind(self) -> Path | None:
        base = Path(__file__).resolve().parent / "assets" / "svg"
        k = self.kind.lower()
        mapping = (
            ("res", "resistor"),
            ("cap", "capacitor"),
            ("ind", "inductor"),
            (("vsource", "vs"), "voltage_source"),
            (("isource", "current"), "current_source"),
            ("dio", "diode"),
            (("ground", "gnd"), "ground"),
        )

        for prefixes, filename in mapping:
            if isinstance(prefixes, str):
                prefixes = (prefixes,)
            if any(k.startswith(pfx) for pfx in prefixes):
                candidate = base / f"{filename}.svg"
                return candidate if candidate.exists() else None
        return None

    def _load_symbol_graphic(self):
        path = self._symbol_path_for_kind()
        if path is None:
            self.symbol_item = None
            return

        self.symbol_item = QGraphicsSvgItem(str(path), self)
        self.symbol_item.setZValue(2)
        self._fit_symbol_to_body()

    def _fit_symbol_to_body(self):
        if not self.symbol_item:
            return
        br = self.symbol_item.boundingRect()
        if br.isNull():
            return

        scale_x = (COMP_WIDTH - 12) / br.width()
        scale_y = (COMP_HEIGHT - 12) / br.height()
        scale = min(scale_x, scale_y)
        self.symbol_item.setScale(scale)

        center = br.center()
        self.symbol_item.setPos(-center.x() * scale, -center.y() * scale)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            scene: Optional['SchematicScene'] = self.scene()
            if scene and getattr(scene, 'snap_on', False):
                g = getattr(scene, 'grid_size', 20)
                p: QPointF = value
                return QPointF(round(p.x()/g)*g, round(p.y()/g)*g)
        if change in (QGraphicsItem.ItemPositionHasChanged, QGraphicsItem.ItemTransformHasChanged):
            if hasattr(self, 'port_left') and hasattr(self, 'port_right'):
                for port in (self.port_left, self.port_right):
                    for w in list(port.wires):
                        w.update_path()
            self._update_label()
        return super().itemChange(change, value)

    def rotate_cw(self):
        self.setRotation((self.rotation() + 90) % 360)
        for port in (self.port_left, self.port_right):
            for w in list(port.wires):
                w.update_path()
        self._update_label()

    def rotate_ccw(self):
        self.setRotation((self.rotation() - 90) % 360)
        for port in (self.port_left, self.port_right):
            for w in list(port.wires):
                w.update_path()
        self._update_label()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._press_pos is not None:
            new_pos = self.pos()
            if new_pos != self._press_pos:
                sc: Optional['SchematicScene'] = self.scene()
                if sc and getattr(sc, 'undo_stack', None):
                    sc.undo_stack.push(MoveComponentCommand(self, self._press_pos, new_pos))
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Start inline edit and open properties
        self.label.start_edit()
        sc = self.scene()
        if sc is not None and hasattr(sc, 'request_properties') and sc.request_properties:
            sc.request_properties(self)
        super().mouseDoubleClickEvent(event)


class WireItem(QGraphicsPathItem):
    def __init__(self, a: PortItem, b: PortItem):
        super().__init__()
        self.setZValue(0)
        self.setPen(QPen(Qt.black, 2))
        self.port_a = a
        self.port_b = b
        self.port_a.add_wire(self)
        self.port_b.add_wire(self)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.update_path()

    def update_path(self):
        p1 = self.port_a.scenePos()
        p2 = self.port_b.scenePos()
        mid_x = (p1.x() + p2.x()) / 2
        path = QPainterPath(p1)
        path.lineTo(mid_x, p1.y())
        path.lineTo(mid_x, p2.y())
        path.lineTo(p2)
        self.setPath(path)

    # helpers for undo/redo
    def attach(self, scene: 'SchematicScene'):
        self.port_a.add_wire(self)
        self.port_b.add_wire(self)
        scene.addItem(self)
        self.update_path()

    def detach(self, scene: 'SchematicScene'):
        self.port_a.remove_wire(self)
        self.port_b.remove_wire(self)
        scene.removeItem(self)


# --------------------------
# Undo/Redo Commands
# --------------------------

class AddComponentCommand(QUndoCommand):
    def __init__(self, scene: 'SchematicScene', comp: 'ComponentItem'):
        super().__init__(f"Add {comp.kind} {comp.refdes or ''}")
        self.scene = scene
        self.comp = comp

    def redo(self):
        self.scene.addItem(self.comp)

    def undo(self):
        # remove connected wires first
        for port in (self.comp.port_left, self.comp.port_right):
            for w in list(port.wires):
                port.remove_wire(w)
                other = w.port_a if w.port_b is port else w.port_b
                other.remove_wire(w)
                self.scene.removeItem(w)
        self.scene.removeItem(self.comp)


class AddWireCommand(QUndoCommand):
    def __init__(self, scene: 'SchematicScene', wire: WireItem):
        super().__init__("Add wire")
        self.scene = scene
        self.wire = wire

    def redo(self):
        self.wire.attach(self.scene)

    def undo(self):
        self.wire.detach(self.scene)


class MoveComponentCommand(QUndoCommand):
    def __init__(self, comp: 'ComponentItem', old_pos: QPointF, new_pos: QPointF):
        super().__init__(f"Move {comp.refdes or comp.kind}")
        self.comp = comp
        self.old = QPointF(old_pos)
        self.new = QPointF(new_pos)

    def redo(self):
        self.comp.setPos(self.new)

    def undo(self):
        self.comp.setPos(self.old)


class RotateComponentCommand(QUndoCommand):
    def __init__(self, comp: 'ComponentItem', old_angle: float, new_angle: float):
        super().__init__(f"Rotate {comp.refdes or comp.kind}")
        self.comp = comp
        self.old = old_angle
        self.new = new_angle

    def _refresh(self):
        for port in (self.comp.port_left, self.comp.port_right):
            for w in list(port.wires):
                w.update_path()
        self.comp._update_label()

    def redo(self):
        self.comp.setRotation(self.new); self._refresh()

    def undo(self):
        self.comp.setRotation(self.old); self._refresh()


class DeleteItemsCommand(QUndoCommand):
    """
    Deletes selected components and wires as a single action; restores on undo.
    """
    def __init__(self, scene: 'SchematicScene', items: list[QGraphicsItem]):
        super().__init__("Delete selection")
        self.scene = scene
        comps = [i for i in items if isinstance(i, ComponentItem)]
        wires = [i for i in items if isinstance(i, WireItem)]
        for c in comps:
            for port in (c.port_left, c.port_right):
                for w in port.wires:
                    if w not in wires:
                        wires.append(w)
        self.comps = comps
        self.wires = wires
        self._comp_state = [(c, QPointF(c.pos()), c.rotation(), c.refdes, c.value) for c in self.comps]

    def redo(self):
        # Remove wires first
        for w in self.wires:
            if w.scene():
                w.detach(self.scene)
        # Remove components
        for c, *_ in self._comp_state:
            if c.scene():
                self.scene.removeItem(c)

    def undo(self):
        # Re-add components first (restore pose + metadata)
        for c, pos, rot, refdes, value in self._comp_state:
            if not c.scene():
                self.scene.addItem(c)
            c.setPos(pos); c.setRotation(rot); c.set_refdes(refdes); c.set_value(value)
        # Re-attach wires
        for w in self.wires:
            if not w.scene():
                w.attach(self.scene)


# --------------------------
# Schematic Scene/View
# --------------------------

class SchematicScene(QGraphicsScene):
    class Mode:
        SELECT = 0
        PLACE = 1
        WIRE = 2

    def __init__(self, status_label: QLabel, undo_stack: QUndoStack):
        super().__init__()
        self.mode = SchematicScene.Mode.SELECT
        self.component_to_place: Optional[str] = None
        self.status_label = status_label
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self._pending_port: Optional[PortItem] = None
        self._temp_line: Optional[QGraphicsPathItem] = None
        # Grid & snap
        self.grid_on = True
        self.snap_on = True
        self.grid_size = 20
        self.grid_style = 'lines'  # 'lines' or 'dots'
        # Placement ghost
        self._ghost_kind: Optional[str] = None
        self._ghost_item: Optional[ComponentItem] = None
        # View reference
        self._view: Optional['SchematicView'] = None
        # Callback to open/show properties from scene/items
        self.request_properties = None  # type: Optional[callable]
        self.undo_stack = undo_stack
        # Auto-RefDes counters per kind
        self._refseq: Dict[str, int] = {"Resistor": 1, "Capacitor": 1, "VSource": 1, "Inductor": 1, "Diode": 1, "ISource": 1}

    def attach_view(self, view: 'SchematicView'):
        self._view = view

    # ---- Modes ----
    def set_mode_select(self):
        self.mode = SchematicScene.Mode.SELECT
        self.component_to_place = None
        self._remove_ghost()
        if self._view:
            self._view.setDragMode(QGraphicsView.RubberBandDrag)
        self.status_label.setText("Arrow: select/move")

    def set_mode_place(self, kind: str):
        self.mode = SchematicScene.Mode.PLACE
        self.component_to_place = kind
        self._remove_ghost()
        self._ghost_kind = kind
        self._ensure_ghost()
        if self._view:
            self._view.setDragMode(QGraphicsView.NoDrag)
        next_ref = self._next_refdes(kind)
        self.status_label.setText(f"Place: {kind} (next {next_ref}) – click to place, ESC to cancel, [ / ] to rotate")

    def set_mode_wire(self):
        self.mode = SchematicScene.Mode.WIRE
        self.component_to_place = None
        self._remove_ghost()
        self._clear_temp_wire()
        if self._view:
            self._view.setDragMode(QGraphicsView.RubberBandDrag)
        self.status_label.setText("Wire: click two ports to connect (Esc/right-click to cancel)")

    # ---- Helpers ----
    def _ensure_ghost(self):
        if self._ghost_item is None and self._ghost_kind:
            self._ghost_item = ComponentItem(self._ghost_kind, QPointF(0, 0))
            self._ghost_item.setOpacity(0.35)
            self._ghost_item.setAcceptedMouseButtons(Qt.NoButton)
            self._ghost_item.setAcceptHoverEvents(False)
            self._ghost_item.setFlag(QGraphicsItem.ItemIsMovable, False)
            self._ghost_item.setFlag(QGraphicsItem.ItemIsSelectable, False)
            self._ghost_item.setZValue(3)
            self.addItem(self._ghost_item)

    def _remove_ghost(self):
        if self._ghost_item is not None:
            self.removeItem(self._ghost_item)
            self._ghost_item = None
            self._ghost_kind = None

    def _snap_point(self, p: QPointF) -> QPointF:
        if not self.snap_on:
            return p
        g = self.grid_size
        return QPointF(round(p.x()/g)*g, round(p.y()/g)*g)

    def _nearest_port(self, comp: ComponentItem, to_scene: QPointF) -> PortItem:
        ports = [comp.port_left, comp.port_right]
        dists = [(p, (p.scenePos() - to_scene).manhattanLength()) for p in ports]
        return min(dists, key=lambda t: t[1])[0]

    def _start_temp_wire(self):
        self._temp_line = QGraphicsPathItem()
        self._temp_line.setPen(QPen(Qt.darkGray, 1, Qt.DashLine))
        self.addItem(self._temp_line)

    def _clear_temp_wire(self):
        self._pending_port = None
        if self._temp_line is not None:
            self.removeItem(self._temp_line)
            self._temp_line = None

    # ---- Events ----
    def mousePressEvent(self, event):
        scene_pos = event.scenePos()

        # Place mode
        if self.mode == SchematicScene.Mode.PLACE and self.component_to_place:
            if event.button() == Qt.LeftButton:
                pos = self._snap_point(scene_pos)
                comp = ComponentItem(self.component_to_place, pos)
                comp.set_refdes(self._next_refdes(self.component_to_place))
                # undoable add
                self.undo_stack.push(AddComponentCommand(self, comp))
                self._bump_refseq(self.component_to_place)
                if self._ghost_item:
                    comp.setRotation(self._ghost_item.rotation())
                self.status_label.setText(f"Placed {comp.refdes} ({self.component_to_place}) at {pos.x():.0f},{pos.y():.0f}")
                event.accept(); return
            elif event.button() == Qt.RightButton:
                self.set_mode_select(); event.accept(); return

        # Wire mode
        if self.mode == SchematicScene.Mode.WIRE:
            if event.button() == Qt.RightButton:
                if self._pending_port or self._temp_line:
                    self._clear_temp_wire()
                event.accept(); return

            item = self.itemAt(scene_pos, QTransform())
            port = item if isinstance(item, PortItem) else None
            if port is None and isinstance(item, ComponentItem):
                port = self._nearest_port(item, scene_pos)

            if port is not None:
                if self._pending_port is None:
                    self._pending_port = port
                    self._start_temp_wire()
                else:
                    if port is not self._pending_port:
                        wire = WireItem(self._pending_port, port)
                        self.undo_stack.push(AddWireCommand(self, wire))
                    self._clear_temp_wire()
                event.accept(); return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.mode == SchematicScene.Mode.PLACE and self._ghost_item is not None:
            self._ghost_item.setPos(self._snap_point(event.scenePos()))
        if self.mode == SchematicScene.Mode.WIRE and self._temp_line is not None and self._pending_port is not None:
            start = self._pending_port.scenePos()
            end = event.scenePos()
            mid_x = (start.x() + end.x()) / 2
            path = QPainterPath(start)
            path.lineTo(mid_x, start.y())
            path.lineTo(mid_x, end.y())
            path.lineTo(end)
            self._temp_line.setPath(path)
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        # If an inline label is being edited, let it handle keys (so Backspace/Delete edit text)
        fi = self.focusItem()
        from PySide6.QtWidgets import QGraphicsTextItem as _QGT
        if isinstance(fi, _QGT) and fi.textInteractionFlags() != Qt.NoTextInteraction:
            return super().keyPressEvent(event)

        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            selected = list(self.selectedItems())
            if selected:
                self.undo_stack.push(DeleteItemsCommand(self, selected))
            event.accept(); return
        if event.key() == Qt.Key_G:
            self.grid_on = not self.grid_on
            self.update(); event.accept(); return
        if event.key() == Qt.Key_D:
            self.grid_style = 'dots' if self.grid_style == 'lines' else 'lines'
            self.status_label.setText(f"Grid style: {self.grid_style}")
            self.update(); event.accept(); return
        if event.key() == Qt.Key_S and (event.modifiers() & Qt.ControlModifier):
            self.snap_on = not self.snap_on
            self.status_label.setText(f"Snap: {'ON' if self.snap_on else 'OFF'} (grid {self.grid_size})")
            event.accept(); return
        if self.mode == SchematicScene.Mode.PLACE and self._ghost_item is not None:
            if event.key() == Qt.Key_BracketRight:
                self._ghost_item.setRotation((self._ghost_item.rotation() + 90) % 360)
                event.accept(); return
            if event.key() == Qt.Key_BracketLeft:
                self._ghost_item.setRotation((self._ghost_item.rotation() - 90) % 360)
                event.accept(); return
            if event.key() == Qt.Key_Escape:
                self.set_mode_select(); event.accept(); return
        if self.mode == SchematicScene.Mode.WIRE and event.key() == Qt.Key_Escape:
            if self._pending_port or self._temp_line:
                self._clear_temp_wire()
            else:
                self.set_mode_select()
            event.accept(); return
        super().keyPressEvent(event)

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
        if p == 'GND':
            return 'GND'
        n = self._refseq.get(kind, 1)
        return f"{p}{n}"

    def _bump_refseq(self, kind: str):
        if self._prefix_for_kind(kind) == 'GND':
            return
        self._refseq[kind] = self._refseq.get(kind, 1) + 1

    def _reseed_refseq(self):
        # Scan items to find max index per kind
        counters: Dict[str, int] = {}
        for it in self.items():
            if isinstance(it, ComponentItem) and it.refdes:
                key = it.kind
                digits = ''.join(ch for ch in it.refdes if ch.isdigit())
                num = int(digits) if digits else 0
                counters[key] = max(counters.get(key, 0), num)
        for k, v in counters.items():
            self._refseq[k] = v + 1

    def drawBackground(self, painter: QPainter, rect: QRectF):
        if not self.grid_on:
            return
        g = self.grid_size
        left = int(math.floor(rect.left() / g) * g)
        top = int(math.floor(rect.top() / g) * g)
        painter.save()
        if self.grid_style == 'lines':
            painter.setPen(QPen(Qt.lightGray, 0))
            x = left
            while x < rect.right():
                painter.drawLine(x, rect.top(), x, rect.bottom())
                x += g
            y = top
            while y < rect.bottom():
                painter.drawLine(rect.left(), y, rect.right(), y)
                y += g
        else:
            painter.setPen(QPen(Qt.lightGray, 2))
            x = left
            while x < rect.right():
                y = top
                while y < rect.bottom():
                    painter.drawPoint(int(x), int(y))
                    y += g
                x += g
        painter.restore()

    # ---- Persistence ----
    def serialize(self) -> Dict:
        comps: List[ComponentItem] = [it for it in self.items() if isinstance(it, ComponentItem)]
        port_ref: Dict[PortItem, Tuple[int, str]] = {}
        for idx, c in enumerate(comps):
            port_ref[c.port_left] = (idx, 'A')
            port_ref[c.port_right] = (idx, 'B')
        wires: List[WireItem] = [it for it in self.items() if isinstance(it, WireItem)]
        wire_data = []
        for w in wires:
            a = port_ref.get(w.port_a)
            b = port_ref.get(w.port_b)
            if a and b:
                wire_data.append({'a': [a[0], a[1]], 'b': [b[0], b[1]]})
        return {
            'components': [
                {
                    'kind': c.kind,
                    'pos': [c.scenePos().x(), c.scenePos().y()],
                    'rotation': c.rotation(),
                    'refdes': c.refdes,
                    'value': c.value,
                } for c in comps
            ],
            'wires': wire_data,
            'settings': {
                'grid_on': self.grid_on,
                'grid_size': self.grid_size,
                'grid_style': self.grid_style,
                'snap_on': self.snap_on,
            }
        }

    def load(self, data: Dict):
        for it in list(self.items()):
            self.removeItem(it)
        settings = data.get('settings', {})
        self.grid_on = settings.get('grid_on', self.grid_on)
        self.grid_size = settings.get('grid_size', self.grid_size)
        self.grid_style = settings.get('grid_style', self.grid_style)
        self.snap_on = settings.get('snap_on', self.snap_on)
        comps: List[ComponentItem] = []
        for cdata in data.get('components', []):
            kind = cdata['kind']
            x, y = cdata['pos']
            rot = cdata.get('rotation', 0)
            c = ComponentItem(kind, QPointF(x, y))
            c.setRotation(rot)
            c.set_refdes(cdata.get('refdes', ""))
            c.set_value(cdata.get('value', ""))
            self.addItem(c)
            comps.append(c)
        for wdata in data.get('wires', []):
            (ai, aside) = wdata.get('a', [None, None])
            (bi, bside) = wdata.get('b', [None, None])
            try:
                ca = comps[ai]
                cb = comps[bi]
                pa = ca.port_left if aside == 'A' else ca.port_right
                pb = cb.port_left if bside == 'A' else cb.port_right
                self.addItem(WireItem(pa, pb))
            except Exception:
                pass
        self._reseed_refseq()
        self.update()


class SchematicView(QGraphicsView):
    def __init__(self, scene: SchematicScene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self._panning = False
        self._pan_start = QPointF()

    def wheelEvent(self, event):
        zoom_in_factor = 1.15
        zoom_out_factor = 1 / zoom_in_factor
        old_pos = self.mapToScene(event.position().toPoint())
        factor = zoom_in_factor if event.angleDelta().y() > 0 else zoom_out_factor
        self.scale(factor, factor)
        new_pos = self.mapToScene(event.position().toPoint())
        delta = new_pos - old_pos
        self.translate(delta.x(), delta.y())

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.translate(delta.x(), delta.y())
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept(); return
        super().mouseReleaseEvent(event)

    def fit_all(self):
        items = self.scene().items()
        if not items:
            self.fitInView(QRectF(-500, -500, 1000, 1000), Qt.KeepAspectRatio)
            return
        rect = None
        for it in items:
            r = it.sceneBoundingRect()
            rect = r if rect is None else rect.united(r)
        self.fitInView(rect.adjusted(-50, -50, 50, 50), Qt.KeepAspectRatio)


# --------------------------
# Properties Panel
# --------------------------

class PropertiesPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.form = QFormLayout(self)
        self.refdes_edit = QLineEdit()
        self.value_edit = QLineEdit()
        self.apply_btn = QPushButton("Apply to Selection")
        self.form.addRow("RefDes", self.refdes_edit)
        self.form.addRow("Value", self.value_edit)
        self.form.addRow(self.apply_btn)
        self.setDisabled(True)
        self._on_apply = None

    def set_callbacks(self, on_apply):
        self._on_apply = on_apply
        self.apply_btn.clicked.connect(self._apply_clicked)
        # Pressing Enter in either field applies to selection
        self.refdes_edit.returnPressed.connect(self._apply_clicked)
        self.value_edit.returnPressed.connect(self._apply_clicked)

    def _apply_clicked(self):
        if self._on_apply:
            self._on_apply(self.refdes_edit.text().strip(), self.value_edit.text().strip())

    def show_component(self, comp: Optional[ComponentItem]):
        if comp is None:
            self.setDisabled(True)
            self.refdes_edit.setText("")
            self.value_edit.setText("")
        else:
            self.setDisabled(False)
            self.refdes_edit.setText(comp.refdes)
            self.value_edit.setText(comp.value)


# --------------------------
# Main Window / Tabs
# --------------------------

class InstrumentsPlaceholder(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Instruments (AD2/AD3) placeholder – coming soon"))
        v.addWidget(QLabel("Plan: Wavegen, Scope, Voltmeter, Logic Analyzer, Patterns, Static IO, Supplies"))
        self.setLayout(v)


class SchematicTab(QWidget):
    def __init__(self, status_label: QLabel, undo_stack: QUndoStack):
        super().__init__()
        self.scene = SchematicScene(status_label, undo_stack)
        self.view = SchematicView(self.scene)
        self.scene.attach_view(self.view)

        v = QVBoxLayout(self)
        v.addWidget(self.view)
        self.setLayout(v)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NodeZilla – Schematic & Instruments (MVP)")
        self.resize(1400, 850)

        self.tabs = QTabWidget()
        self.status_label = QLabel("Ready")
        self.undo_stack = QUndoStack(self)
        self.schematic_tab = SchematicTab(self.status_label, self.undo_stack)
        self.instruments_tab = InstrumentsPlaceholder()
        self.tabs.addTab(self.schematic_tab, "Schematic")
        self.tabs.addTab(self.instruments_tab, "Instruments")
        self.setCentralWidget(self.tabs)

        # Properties dock
        self.props_panel = PropertiesPanel()
        self.props_panel.set_callbacks(self._apply_properties)
        dock = QDockWidget("Properties", self)
        dock.setWidget(self.props_panel)
        dock.setObjectName("PropertiesDock")
        dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.props_dock = dock
        # Allow scene/components to request showing the properties
        self.schematic_tab.scene.request_properties = self._show_properties_for

        # React to selection changes
        self.schematic_tab.scene.selectionChanged.connect(self._on_selection_changed)

        self._build_toolbar()
        self._build_menu()
        # Menus
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.props_dock.toggleViewAction())
        edit_menu = self.menuBar().addMenu("Edit")
        undo_act = self.undo_stack.createUndoAction(self, "Undo")
        undo_act.setShortcut(QKeySequence.Undo)
        redo_act = self.undo_stack.createRedoAction(self, "Redo")
        redo_act.setShortcut(QKeySequence.Redo)
        edit_menu.addAction(undo_act)
        edit_menu.addAction(redo_act)

        sb = QStatusBar()
        sb.addWidget(self.status_label)
        self.setStatusBar(sb)

    # ---- Selection → Properties ----
    def _on_selection_changed(self):
        comps = [it for it in self.schematic_tab.scene.selectedItems() if isinstance(it, ComponentItem)]
        self.props_panel.show_component(comps[0] if comps else None)

    def _apply_properties(self, refdes: str, value: str):
        comps = [it for it in self.schematic_tab.scene.selectedItems() if isinstance(it, ComponentItem)]
        if not comps:
            return
        for c in comps:
            c.set_refdes(refdes)
            c.set_value(value)

    # ---- UI builders ----
    def _build_toolbar(self):
        tb = QToolBar("Tools")
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)

        act_select = QAction("Select", self)
        act_select.setShortcut("V")
        act_select.triggered.connect(self.schematic_tab.scene.set_mode_select)
        tb.addAction(act_select)

        tb.addSeparator()
        for kind, key in [("Resistor", "R"), ("Capacitor", "C"), ("VSource", "V"),
                          ("Inductor", "L"), ("Diode", "Shift+D"), ("ISource", "I"),
                          ("Ground", "Shift+G")]:
            act = QAction(kind, self)
            act.setShortcut(key)
            act.triggered.connect(lambda checked=False, k=kind: self.schematic_tab.scene.set_mode_place(k))
            tb.addAction(act)

        tb.addSeparator()
        act_wire = QAction("Wire", self)
        act_wire.setShortcut("W")
        act_wire.triggered.connect(self.schematic_tab.scene.set_mode_wire)
        tb.addAction(act_wire)

        act_delete = QAction("Delete", self)
        act_delete.setShortcut(QKeySequence.Delete)
        act_delete.triggered.connect(self._delete_selected)
        tb.addAction(act_delete)

        tb.addSeparator()
        act_rot_cw = QAction("Rotate ⟳", self)
        act_rot_cw.setShortcut("]")
        act_rot_cw.triggered.connect(lambda: self._rotate_selected(90))
        tb.addAction(act_rot_cw)

        act_rot_ccw = QAction("Rotate ⟲", self)
        act_rot_ccw.setShortcut("[")
        act_rot_ccw.triggered.connect(lambda: self._rotate_selected(-90))
        tb.addAction(act_rot_ccw)

        tb.addSeparator()
        act_grid = QAction("Grid G", self)
        act_grid.triggered.connect(self._toggle_grid)
        tb.addAction(act_grid)

        act_snap = QAction("Snap Ctrl+S", self)
        act_snap.triggered.connect(self._toggle_snap)
        tb.addAction(act_snap)

        act_grid_style = QAction("Grid Style (D)", self)
        act_grid_style.triggered.connect(self._toggle_grid_style)
        tb.addAction(act_grid_style)

        tb.addSeparator()
        tb.addWidget(QLabel("Grid:"))
        self._grid_spin = QSpinBox()
        self._grid_spin.setRange(5, 200)
        self._grid_spin.setSingleStep(5)
        self._grid_spin.setSuffix(" px")
        self._grid_spin.setValue(self.schematic_tab.scene.grid_size)
        self._grid_spin.valueChanged.connect(self._change_grid_size)
        tb.addWidget(self._grid_spin)

        act_grid_minus = QAction("Grid −", self)
        act_grid_minus.setShortcut("Ctrl+-")
        act_grid_minus.triggered.connect(lambda: self._nudge_grid(-5))
        tb.addAction(act_grid_minus)

        act_grid_plus = QAction("Grid +", self)
        act_grid_plus.setShortcut("Ctrl+=")
        act_grid_plus.triggered.connect(lambda: self._nudge_grid(5))
        tb.addAction(act_grid_plus)

        tb.addSeparator()
        act_fit = QAction("Fit", self)
        act_fit.setShortcut("F")
        act_fit.triggered.connect(self.schematic_tab.view.fit_all)
        tb.addAction(act_fit)

        act_zoom_in = QAction("Zoom +", self)
        act_zoom_in.setShortcut("+")
        act_zoom_in.triggered.connect(lambda: self.schematic_tab.view.scale(1.15, 1.15))
        tb.addAction(act_zoom_in)

        act_zoom_out = QAction("Zoom -", self)
        act_zoom_out.setShortcut("-")
        act_zoom_out.triggered.connect(lambda: self.schematic_tab.view.scale(1/1.15, 1/1.15))
        tb.addAction(act_zoom_out)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("File")

        act_new = QAction("New", self)
        act_new.setShortcut(QKeySequence.New)
        act_new.triggered.connect(self._new_schematic)
        file_menu.addAction(act_new)

        act_open = QAction("Open…", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self._open)
        file_menu.addAction(act_open)

        act_save = QAction("Save…", self)
        act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self._save)
        file_menu.addAction(act_save)

        file_menu.addSeparator()
        act_quit = QAction("Quit", self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

    def _toggle_grid(self):
        scene = self.schematic_tab.scene
        scene.grid_on = not scene.grid_on
        scene.update()

    def _toggle_snap(self):
        scene = self.schematic_tab.scene
        scene.snap_on = not scene.snap_on
        self.statusBar().showMessage(f"Snap: {'ON' if scene.snap_on else 'OFF'}", 3000)

    def _toggle_grid_style(self):
        scene = self.schematic_tab.scene
        scene.grid_style = 'dots' if scene.grid_style == 'lines' else 'lines'
        self.statusBar().showMessage(f"Grid style: {scene.grid_style}", 2000)
        scene.update()

    def _change_grid_size(self, value: int):
        scene = self.schematic_tab.scene
        scene.grid_size = max(1, int(value))
        scene.update()
        self.statusBar().showMessage(f"Grid size: {scene.grid_size}px", 1500)

    def _nudge_grid(self, delta: int):
        self._grid_spin.setValue(max(1, int(self._grid_spin.value() + delta)))

    def _show_properties_for(self, comp: ComponentItem):
        # Ensure dock is visible and focused, select the component
        if self.props_dock.isHidden():
            self.props_dock.show()
        self.props_dock.raise_()
        self.schematic_tab.scene.clearSelection()
        comp.setSelected(True)
        self.props_panel.show_component(comp)
        self.props_panel.refdes_edit.setFocus()

    def _delete_selected(self):
        scene = self.schematic_tab.scene
        selected = list(scene.selectedItems())
        if selected:
            scene.undo_stack.push(DeleteItemsCommand(scene, selected))

    def _rotate_selected(self, angle: int):
        scene = self.schematic_tab.scene
        comps = [it for it in scene.selectedItems() if isinstance(it, ComponentItem)]
        if not comps:
            self.statusBar().showMessage("Select a component to rotate", 2000)
            return
        for c in comps:
            old = c.rotation()
            if angle > 0: c.rotate_cw()
            else: c.rotate_ccw()
            new = c.rotation()
            self.undo_stack.push(RotateComponentCommand(c, old, new))

    def _new_schematic(self):
        reply = QMessageBox.question(self, "New schematic", "Clear current schematic?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.schematic_tab.scene.load({'components': [], 'wires': [], 'settings': {}})

    def _open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open schematic", filter="Schematic (*.json)")
        if path:
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                self.schematic_tab.scene.load(data)
                self._grid_spin.setValue(self.schematic_tab.scene.grid_size)
                self.statusBar().showMessage(f"Loaded {path}", 4000)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load: {e}")

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save schematic", filter="Schematic (*.json)")
        if path:
            try:
                data = self.schematic_tab.scene.serialize()
                with open(path, 'w') as f:
                    json.dump(data, f, indent=2)
                self.statusBar().showMessage(f"Saved {path}", 4000)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save: {e}")


def main():
    app = QApplication([])
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
