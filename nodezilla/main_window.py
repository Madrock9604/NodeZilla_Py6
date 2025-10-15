# ========================================
# File: nodezilla/main_window.py
# ========================================
from __future__ import annotations
from typing import List
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout, QToolBar, QLabel, QSpinBox,
    QDockWidget, QStatusBar, QFileDialog, QMessageBox
)
import json
from .schematic_scene import SchematicScene
from .schematic_view import SchematicView
from .properties_panel import PropertiesPanel
from .graphics_items import ComponentItem
from .commands import DeleteItemsCommand, RotateComponentCommand


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
        self.setWindowTitle("NodeZilla – Schematic & Instruments (Modular)")
        self.resize(1400, 850)

        self.tabs = QTabWidget()
        self.status_label = QLabel("Ready")
        self.undo_stack = QUndoStack(self)

        self.schematic_tab = SchematicTab(self.status_label, self.undo_stack)
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

        self.schematic_tab.scene.request_properties = self._show_properties_for
        self.schematic_tab.scene.selectionChanged.connect(self._on_selection_changed)

        self._build_toolbar()
        self._build_menu()

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

    # selection → props
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

    # toolbar/menu builders
    def _build_toolbar(self):
        tb = QToolBar("Tools")
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)

        act_select = QAction("Select", self)
        act_select.setShortcut("V")
        act_select.triggered.connect(self.schematic_tab.scene.set_mode_select)
        tb.addAction(act_select)

        tb.addSeparator()
        for kind, key in [
            ("Resistor", "R"),
            ("Capacitor", "C"),
            ("VSource", "V"),
            ("Inductor", "L"),
            ("Diode", "Shift+D"),
            ("ISource", "I"),
            ("Ground", "Shift+G"),
        ]:
            act = QAction(kind, self)
            act.setShortcut(key)
            act.triggered.connect(lambda _=False, k=kind: self.schematic_tab.scene.set_mode_place(k))
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
        if self.props_dock.isHidden():
            self.props_dock.show()
        self.props_dock.raise_()
        self.schematic_tab.scene.clearSelection()
        comp.setSelected(True)
        self.props_panel.show_component(comp)
        self.props_panel.refdes_edit.setFocus()

    def _delete_selected(self):
        sc = self.schematic_tab.scene
        sel = list(sc.selectedItems())
        if sel:
            sc.undo_stack.push(DeleteItemsCommand(sc, sel))

    def _rotate_selected(self, angle: int):
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
        if QMessageBox.question(
            self, "New schematic", "Clear current schematic?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
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
