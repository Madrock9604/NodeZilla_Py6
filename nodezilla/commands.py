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
    from .graphics_items import ComponentItem as _ComponentItem
    from .graphics_items import WireItem as _WireItem
except Exception:
    _WireItem = None  # type: ignore
    _ComponentItem = _WireItem = None

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
                w.detach(self.scene)
                self.scene.removeItem(w)
        self.scene.removeItem(self.comp)


class AddWireCommand(QUndoCommand):
    def __init__(self, scene: 'SchematicScene', wire: 'WireType'):
        super().__init__("Add wire")
        self.scene = scene
        self.wire = wire


    def redo(self):
        self.scene.addItem(self.wire)
        theme = getattr(self.scene, "theme", None)
        if theme and hasattr(self.wire, "apply_theme"):
            self.wire.apply_theme(theme)
        self.wire.setSelected(True)
        if hasattr(self.scene, "_rebuild_junction_markers"):
            self.scene._rebuild_junction_markers()


    def undo(self):
        self.scene.removeItem(self.wire)
        if hasattr(self.scene, "_rebuild_junction_markers"):
            self.scene._rebuild_junction_markers()


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
    def __init__(self, scene, selected_items):
        super().__init__("Delete")
        self.scene = scene

        # Normalize selection and expand to include wires attached to components
        comps = []
        wires = []

        for it in selected_items:
            if _ComponentItem is not None and isinstance(it, _ComponentItem):
                comps.append(it)
            elif _WireItem is not None and isinstance(it, _WireItem):
                wires.append(it)

        # Add wires attached to selected components
        for c in comps:
            for port_attr in ("port_left", "port_right"):
                p = getattr(c, port_attr, None)
                if p and hasattr(p, "wires"):
                    for w in list(p.wires):
                        if w not in wires:
                            wires.append(w)

        # Dedup, and store ordering for redo/undo
        self._comps = list(dict.fromkeys(comps))
        self._wires = list(dict.fromkeys(wires))

    def redo(self):
        # Remove wires first, then components
        for w in self._wires:
            if hasattr(w, "detach"):  # detach from ports
                w.detach(self.scene)
            self.scene.removeItem(w)  # ✅ actually remove from scene
        if hasattr(self.scene, "_rebuild_junction_markers"):
            self.scene._rebuild_junction_markers()

        for c in self._comps:
            self.scene.removeItem(c)  # components go after wires

    def undo(self):
        # Restore components first, so ports exist
        for c in self._comps:
            self.scene.addItem(c)

        # Then restore wires and reattach to ports
        for w in self._wires:
            self.scene.addItem(w)
            if hasattr(w, "attach"):
                w.attach()
            # ensure the path reflects current port positions
            if hasattr(w, "update_path"):
                w.update_path()
        if hasattr(self.scene, "_rebuild_junction_markers"):
            self.scene._rebuild_junction_markers()

class SetWirePointsCommand(QUndoCommand):
    def __init__(self, wire, new_pts):
        super().__init__("Edit Wire")
        self.wire = wire
        self.new = list(new_pts) if new_pts else []
        self.old = list(getattr(wire, "_pts", []))

    def redo(self):
        self.wire.set_points(self.new)

    def undo(self):
        self.wire.set_points(self.old)