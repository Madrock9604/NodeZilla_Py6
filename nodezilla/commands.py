# ========================================
# File: nodezilla/commands.py
# ========================================
from __future__ import annotations
from typing import List, TYPE_CHECKING
from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand
from .graphics_items import ComponentItem, PortItem

# For type hints only (never executed at runtime)
if TYPE_CHECKING:
    from .graphics_items import WireItem as WireType

# For runtime isinstance checks (may be None during import)
try:
    from .graphics_items import WireItem as _WireItem
except Exception:
    _WireItem = None  # type: ignore

if TYPE_CHECKING:
    from .schematic_scene import SchematicScene




class AddComponentCommand(QUndoCommand):
    def __init__(self, scene: 'SchematicScene', comp: ComponentItem):
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
    def __init__(self, scene: 'SchematicScene', wire: 'WireType'):
        super().__init__("Add wire")
        self.scene = scene
        self.wire = wire


    def redo(self):
        self.wire.attach(self.scene)


    def undo(self):
        self.wire.detach(self.scene)


class MoveComponentCommand(QUndoCommand):
    def __init__(self, comp: ComponentItem, old_pos: QPointF, new_pos: QPointF):
        super().__init__(f"Move {comp.refdes or comp.kind}")
        self.comp = comp
        self.old = QPointF(old_pos); self.new = QPointF(new_pos)


    def redo(self):
        self.comp.setPos(self.new)


    def undo(self):
        self.comp.setPos(self.old)


class RotateComponentCommand(QUndoCommand):
    def __init__(self, comp: ComponentItem, old_angle: float, new_angle: float):
        super().__init__(f"Rotate {comp.refdes or comp.kind}")
        self.comp = comp
        self.old = old_angle; self.new = new_angle


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
    def __init__(self, scene: 'SchematicScene', items: List):
        super().__init__("Delete selection")
        self.scene = scene
        comps = [i for i in items if isinstance(i, ComponentItem)]
        # ✅ Only call isinstance if WireItem is a real type
        wires = [i for i in items if (_WireItem and isinstance(i, _WireItem))]
        for c in comps:
            for port in (c.port_left, c.port_right):
                for w in port.wires:
                    if w not in wires:
                        wires.append(w)
        self.comps = comps
        self.wires = wires
        self._comp_state = [
            (c, QPointF(c.pos()), c.rotation(), c.refdes, c.value)
            for c in self.comps
        ]

    def redo(self):
        for w in self.wires:
            if w.scene():
                w.detach(self.scene)
        for c, *_ in self._comp_state:
            if c.scene():
                self.scene.removeItem(c)


    def undo(self):
        for c, pos, rot, refdes, value in self._comp_state:
            if not c.scene():
                self.scene.addItem(c)
            c.setPos(pos); c.setRotation(rot); c.set_refdes(refdes); c.set_value(value)
        for w in self.wires:
            if not w.scene():
                w.attach(self.scene)