# ========================================
# File: nodezilla/main_window.py
# ========================================
from __future__ import annotations
from typing import List
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QToolBar, QLabel, QSpinBox,
    QDockWidget, QStatusBar, QFileDialog, QMessageBox, QDialog, QInputDialog
)
import json
from .schematic_scene import SchematicScene
from .schematic_view import SchematicView
from .properties_panel import PropertiesPanel
from .net_panel import NetPanel
from .graphics_items import ComponentItem, WireItem
from .commands import DeleteItemsCommand, RotateComponentCommand
from .theme import ThemeWatcher
from .component_library import load_component_library
from .component_panel import ComponentPanel
from .custom_component_dialog import CustomComponentDialog


class InstrumentsPlaceholder(QWidget):
    """Stub tab kept visible while instrument module is under development."""
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Instruments (AD2/AD3) placeholder – coming soon"))
        v.addWidget(QLabel("Plan: Wavegen, Scope, Voltmeter, Logic Analyzer, Patterns, Static IO, Supplies"))
        self.setLayout(v)


class SchematicTab(QWidget):
    """Container for one schematic scene+view pair."""
    def __init__(self, status_label: QLabel, undo_stack: QUndoStack):
        super().__init__()
        self.scene = SchematicScene(status_label, undo_stack)
        self.view = SchematicView(self.scene)
        self.scene.attach_view(self.view)
        v = QVBoxLayout(self)
        v.addWidget(self.view)
        self.setLayout(v)


class MainWindow(QMainWindow):
    """Application shell wiring scene, docks, menus, and file operations."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NodeZilla – Schematic & Instruments (Modular)")
        self.resize(1400, 850)

        self.tabs = QTabWidget()
        self.status_label = QLabel("Ready")
        self.undo_stack = QUndoStack(self)

        self._watcher = ThemeWatcher(QApplication.instance(), self._apply_theme)

        self.schematic_tab = SchematicTab(self.status_label, self.undo_stack)
        self.component_library = load_component_library()
        theme = self._watcher.current_theme()
        self._apply_theme(theme)
        self.instruments_tab = InstrumentsPlaceholder()
        self.tabs.addTab(self.schematic_tab, "Schematic")
        self.tabs.addTab(self.instruments_tab, "Instruments")
        self.setCentralWidget(self.tabs)

        self.props_panel = PropertiesPanel()
        self.props_panel.set_callbacks(self._apply_properties)
        dock = QDockWidget("Properties", self)
        dock.setWidget(self.props_panel)
        dock.setObjectName("PropertiesDock")
        dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.props_dock = dock

        self.net_panel = NetPanel()
        self.net_panel.set_scene(self.schematic_tab.scene)
        nets_dock = QDockWidget("Nets", self)
        nets_dock.setWidget(self.net_panel)
        nets_dock.setObjectName("NetsDock")
        nets_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, nets_dock)
        self.nets_dock = nets_dock

        self.component_panel = ComponentPanel(self.component_library)
        self.component_panel.place_requested.connect(self.schematic_tab.scene.set_mode_place)
        component_dock = QDockWidget("Components", self)
        component_dock.setWidget(self.component_panel)
        component_dock.setObjectName("ComponentsDock")
        component_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, component_dock)
        self.component_dock = component_dock

        self.schematic_tab.scene.request_properties = self._show_properties_for
        self.schematic_tab.scene.selectionChanged.connect(self._on_selection_changed)

        self._build_toolbar()
        self._build_menu()

        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.props_dock.toggleViewAction())
        view_menu.addAction(self.nets_dock.toggleViewAction())
        view_menu.addAction(self.component_dock.toggleViewAction())

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
        self._install_component_shortcuts()


    def _apply_theme(self, theme):
        """Apply theme to scene items (deferred safely until UI exists)."""
        # If UI not ready yet, try again on the next event loop tick
        if not hasattr(self, "schematic_tab") or self.schematic_tab is None:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._apply_theme(theme))
            return
        self.schematic_tab.scene.apply_theme(theme)

    # selection → props
    def _on_selection_changed(self):
        """Push active selection into the properties panel."""
        selected = self.schematic_tab.scene.selectedItems()
        wires = [it for it in selected if isinstance(it, WireItem)]
        if wires:
            self.props_panel.show_wire(wires[0])
            return
        comps = [it for it in self.schematic_tab.scene.selectedItems() if isinstance(it, ComponentItem)]
        self.props_panel.show_component(comps[0] if comps else None)

    def _apply_properties(self, kind: str | None, refdes: str | None, value: str | None, wire_color: str):
        """Apply edits from PropertiesPanel back to selected scene items."""
        if kind == "wire":
            wires = [it for it in self.schematic_tab.scene.selectedItems() if isinstance(it, WireItem)]
            for w in wires:
                if hasattr(w, "set_wire_color"):
                    w.set_wire_color(wire_color or None)
            return
        comps = [it for it in self.schematic_tab.scene.selectedItems() if isinstance(it, ComponentItem)]
        if not comps:
            return
        for c in comps:
            if refdes is not None:
                c.set_refdes(refdes)
            if value is not None:
                c.set_value(value)

    # toolbar/menu builders
    def _install_component_shortcuts(self):
        """Build dynamic placement shortcuts from component library metadata."""
        for act in getattr(self, "_component_shortcut_actions", []):
            self.removeAction(act)
        self._component_shortcut_actions = []
        for comp in self.component_library.sorted_components():
            if not comp.shortcut:
                continue
            act = QAction(f"Place {comp.display_name}", self)
            act.setShortcut(comp.shortcut)
            act.triggered.connect(lambda _=False, k=comp.kind: self.schematic_tab.scene.set_mode_place(k))
            self.addAction(act)
            self._component_shortcut_actions.append(act)

    def _build_toolbar(self):
        """Create top-level CAD actions for editing/navigation/export."""
        tb = QToolBar("Tools")
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)

        act_select = QAction("Select", self)
        act_select.setShortcut("V")
        act_select.triggered.connect(self.schematic_tab.scene.set_mode_select)
        tb.addAction(act_select)

        act_components = QAction("Components", self)
        def _open_components():
            self.component_dock.show()
            self.component_dock.raise_()
            if hasattr(self.component_panel, "search"):
                self.component_panel.search.setFocus()
        act_components.triggered.connect(_open_components)
        tb.addAction(act_components)

        tb.addSeparator()
        act_wire = QAction("Wire", self)
        act_wire.setShortcut("W")
        act_wire.triggered.connect(self.schematic_tab.scene.set_mode_wire)
        tb.addAction(act_wire)
        act_net_label = QAction("Net Label", self)
        act_net_label.triggered.connect(lambda: self.schematic_tab.scene.set_mode_place("NetLabel"))
        tb.addAction(act_net_label)

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

        tb.addSeparator()
        act_export_netlist = QAction("Export Netlist", self)
        act_export_netlist.setShortcut("Ctrl+E")
        act_export_netlist.triggered.connect(self._export_netlist)
        tb.addAction(act_export_netlist)

    def _build_menu(self):
        """Create file menu actions (new/open/save/export/custom parts)."""
        fm = self.menuBar().addMenu("File")

        act_new = QAction("New", self)
        act_new.setShortcut(QKeySequence.New)
        act_new.triggered.connect(self._new_schematic)
        fm.addAction(act_new)

        act_open = QAction("Open…", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self._open)
        fm.addAction(act_open)

        act_save = QAction("Save…", self)
        act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self._save)
        fm.addAction(act_save)

        act_export_netlist = QAction("Export Netlist…", self)
        act_export_netlist.setShortcut("Ctrl+E")
        act_export_netlist.triggered.connect(self._export_netlist)
        fm.addAction(act_export_netlist)

        act_custom_component = QAction("Create Custom Component…", self)
        act_custom_component.triggered.connect(self._create_custom_component)
        fm.addAction(act_custom_component)

        act_edit_custom = QAction("Edit Custom Component…", self)
        act_edit_custom.triggered.connect(self._edit_custom_component)
        fm.addAction(act_edit_custom)

        fm.addSeparator()
        act_quit = QAction("Quit", self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        fm.addAction(act_quit)

    # grid/snap helpers
    def _toggle_grid(self):
        sc = self.schematic_tab.scene
        sc.grid_on = not sc.grid_on
        sc.update()

    def _toggle_snap(self):
        sc = self.schematic_tab.scene
        sc.snap_on = not sc.snap_on
        self.statusBar().showMessage(f"Snap: {'ON' if sc.snap_on else 'OFF'}", 3000)

    def _toggle_grid_style(self):
        sc = self.schematic_tab.scene
        sc.grid_style = 'dots' if sc.grid_style == 'lines' else 'lines'
        self.statusBar().showMessage(f"Grid style: {sc.grid_style}", 2000)
        sc.update()

    def _change_grid_size(self, v: int):
        sc = self.schematic_tab.scene
        sc.grid_size = max(1, int(v))
        sc.update()
        self.statusBar().showMessage(f"Grid size: {sc.grid_size}px", 1500)

    def _nudge_grid(self, d: int):
        self._grid_spin.setValue(max(1, int(self._grid_spin.value() + d)))

    def _show_properties_for(self, comp: ComponentItem):
        """Focus properties dock and select the target component."""
        if self.props_dock.isHidden():
            self.props_dock.show()
        self.props_dock.raise_()
        self.schematic_tab.scene.clearSelection()
        comp.setSelected(True)
        self.props_panel.show_component(comp)
        self.props_panel.refdes_edit.setFocus()

    def _delete_selected(self):
        """Delete selected scene items using undo stack command."""
        sc = self.schematic_tab.scene
        sel = list(sc.selectedItems())
        if sel:
            sc.undo_stack.push(DeleteItemsCommand(sc, sel))

    def _create_custom_component(self):
        """Open component creator dialog and reload library on success."""
        dlg = CustomComponentDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.component_library = load_component_library(force_reload=True)
            self.component_panel.reload_library()
            self._install_component_shortcuts()
            self.statusBar().showMessage("Custom component saved.", 3000)

    def _edit_custom_component(self):
        """Open existing custom component in editor dialog."""
        lib = load_component_library(force_reload=True)
        customs = [c for c in lib.sorted_components() if str(c.symbol).startswith("custom/")]
        if not customs:
            QMessageBox.information(self, "Edit Custom Component", "No custom components found.")
            return
        kinds = [c.kind for c in customs if c.kind]
        kind, ok = QInputDialog.getItem(self, "Edit Custom Component", "Component:", kinds, 0, False)
        if not ok or not kind:
            return
        dlg = CustomComponentDialog(self)
        if not dlg.load_from_library(kind):
            QMessageBox.warning(self, "Edit Custom Component", "Failed to load component.")
            return
        if dlg.exec() == QDialog.Accepted:
            self.component_library = load_component_library(force_reload=True)
            self.component_panel.reload_library()
            self._install_component_shortcuts()
            self.statusBar().showMessage("Custom component updated.", 3000)

    def _rotate_selected(self, angle: int):
        """Rotate selected components and record undo command."""
        sc = self.schematic_tab.scene
        comps = [it for it in sc.selectedItems() if isinstance(it, ComponentItem)]
        if not comps:
            self.statusBar().showMessage("Select a component to rotate", 2000)
            return
        for c in comps:
            old = c.rotation()
            if angle > 0:
                c.rotate_cw()
            else:
                c.rotate_ccw()
            new = c.rotation()
            self.undo_stack.push(RotateComponentCommand(c, old, new))

    # file ops
    def _new_schematic(self):
        """Clear scene after user confirmation."""
        if QMessageBox.question(
            self, "New schematic", "Clear current schematic?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.schematic_tab.scene.load({'components': [], 'wires': [], 'settings': {}})

    def _export_netlist(self):
        """Generate a netlist from the current schematic and save it to a file."""
        sc = self.schematic_tab.scene
        # Uses SchematicScene.export_netlist_text() that we added earlier
        try:
            netlist_text = sc.export_netlist_text()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to build netlist: {e}")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export netlist",
            filter="Netlist (*.net *.cir);;All Files (*)"
        )
        if not path:
            return

        try:
            with open(path, "w") as f:
                f.write(netlist_text)
            self.statusBar().showMessage(f"Netlist exported to {path}", 4000)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save netlist: {e}")

    def _open(self):
        """Load schematic JSON from disk into scene."""
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
        """Serialize scene JSON and save to disk."""
        path, _ = QFileDialog.getSaveFileName(self, "Save schematic", filter="Schematic (*.json)")
        if path:
            try:
                data = self.schematic_tab.scene.serialize()
                with open(path, 'w') as f:
                    json.dump(data, f, indent=2)
                self.statusBar().showMessage(f"Saved {path}", 4000)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save: {e}")
