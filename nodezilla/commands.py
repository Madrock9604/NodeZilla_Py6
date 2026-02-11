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
    """Undo/redo wrapper for adding a component item to the scene."""
    def __init__(self, scene: 'SchematicScene', comp: ComponentItem):
        super().__init__(f"Add {comp.kind} {comp.refdes or ''}")
        self.scene = scene
        self.comp = comp


    def redo(self):
        self.scene.addItem(self.comp)
        theme = getattr(self.scene, "theme", None)
        if theme and hasattr(self.comp, "apply_theme"):
            self.comp.apply_theme(theme)


    def undo(self):
        # remove connected wires first
        for port in ([p for p in getattr(self.comp, 'ports', []) if p is not None] or [p for p in (getattr(self.comp, 'port_left', None), getattr(self.comp, 'port_right', None)) if p is not None]):
            for w in list(port.wires):
                w.detach(self.scene)
                self.scene.removeItem(w)
        self.scene.removeItem(self.comp)


class AddWireCommand(QUndoCommand):
    """Undo/redo wrapper for adding a wire item and reattaching endpoints."""
    def __init__(self, scene: 'SchematicScene', wire: 'WireType'):
        super().__init__("Add wire")
        self.scene = scene
        self.wire = wire


    def redo(self):
        self.scene.addItem(self.wire)
        if hasattr(self.wire, "attach"):
            try:
                self.wire.attach()
            except Exception:
                pass
        theme = getattr(self.scene, "theme", None)
        if theme and hasattr(self.wire, "apply_theme"):
            self.wire.apply_theme(theme)
        self.wire.setSelected(True)
        if hasattr(self.scene, "_rebuild_junction_markers"):
            self.scene._rebuild_junction_markers()


    def undo(self):
        if hasattr(self.wire, "detach"):
            try:
                self.wire.detach(self.scene)
            except Exception:
                pass
        self.scene.removeItem(self.wire)
        if hasattr(self.scene, "_rebuild_junction_markers"):
            self.scene._rebuild_junction_markers()


class MoveComponentCommand(QUndoCommand):
    """Undoable translation of one component item."""
    def __init__(self, comp: ComponentItem, old_pos: QPointF, new_pos: QPointF):
        super().__init__(f"Move {comp.refdes or comp.kind}")
        self.comp = comp
        self.old = QPointF(old_pos); self.new = QPointF(new_pos)


    def redo(self):
        self.comp.setPos(self.new)


    def undo(self):
        self.comp.setPos(self.old)


class RotateComponentCommand(QUndoCommand):
    """Undoable component rotation + dependent wire path refresh."""
    def __init__(self, comp: ComponentItem, old_angle: float, new_angle: float):
        super().__init__(f"Rotate {comp.refdes or comp.kind}")
        self.comp = comp
        self.old = old_angle; self.new = new_angle


    def _refresh(self):
        for port in ([p for p in getattr(self.comp, 'ports', []) if p is not None] or [p for p in (getattr(self.comp, 'port_left', None), getattr(self.comp, 'port_right', None)) if p is not None]):
            for w in list(port.wires):
                w.update_path()
        self.comp._update_label()


    def redo(self):
        self.comp.setRotation(self.new); self._refresh()


    def undo(self):
        self.comp.setRotation(self.old); self._refresh()


class DeleteItemsCommand(QUndoCommand):
    """Delete selected components/wires with proper dependency ordering."""
    def __init__(self, scene, selected_items):
        super().__init__("Delete")
        self.scene = scene

        # Normalize selection and expand to include wires attached to components.
        # This avoids leaving dangling wires when only components are selected.
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
    """Undoable edit for wire waypoint lists (segment dragging/reroutes)."""
    def __init__(self, wire, new_pts):
        super().__init__("Edit Wire")
        self.wire = wire
        self.new = list(new_pts) if new_pts else []
        self.old = list(getattr(wire, "_pts", []))

    def redo(self):
        self.wire.set_points(self.new)

    def undo(self):
        self.wire.set_points(self.old)
