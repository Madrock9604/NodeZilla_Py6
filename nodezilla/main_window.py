# ========================================
# File: nodezilla/main_window.py
# ========================================
from __future__ import annotations
from typing import List
from pathlib import Path
import os
import tempfile
import time
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QToolBar, QLabel, QSpinBox,
    QDockWidget, QStatusBar, QFileDialog, QMessageBox, QDialog, QInputDialog, QTextEdit,
    QComboBox, QPushButton
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
from .instruments_tab import InstrumentsTab
from nodezilla import Program as P


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


class FloatingToolIsland(QWidget):
    """Draggable floating controls that reflow by nearest viewport edge."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("FloatingToolIsland")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "#FloatingToolIsland {"
            "background: rgba(38, 38, 42, 215);"
            "border: 1px solid rgba(120, 120, 130, 180);"
            "border-radius: 12px;"
            "}"
        )
        self._widgets: List[QWidget] = []
        self._layout_orientation = "h"
        self._dragging = False
        self._drag_offset = None
        from PySide6.QtWidgets import QGridLayout
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setHorizontalSpacing(6)
        self._layout.setVerticalSpacing(6)
        self._initialized = False
        self._rebuild_layout("h")
        self.parent().installEventFilter(self)

    def add_controls(self, widgets: List[QWidget]):
        self._widgets = list(widgets)
        self._rebuild_layout(self._layout_orientation)

    def _rebuild_layout(self, orientation: str):
        # Reuse a single layout instance; recreating layouts on the same widget
        # causes Qt warnings and inconsistent geometry.
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self._layout.removeWidget(w)
        if orientation == "v":
            for row, w in enumerate(self._widgets):
                self._layout.addWidget(w, row, 0)
                w.show()
        else:
            # Horizontal mode: bounded packing by max column count computed
            # from viewport width and control widths.
            p = self.parentWidget()
            if p is not None:
                avail_w = max(280, p.width() - max(8, self.x()) - 12)
            else:
                avail_w = 1200
            spacing = self._layout.horizontalSpacing()
            margins = self._layout.contentsMargins()
            inner_w = max(200, avail_w - margins.left() - margins.right())
            max_cell_w = 56
            for w in self._widgets:
                max_cell_w = max(max_cell_w, w.minimumSizeHint().width(), w.sizeHint().width())
            cols = max(1, int((inner_w + spacing) / max(1, (max_cell_w + spacing))))
            for i, w in enumerate(self._widgets):
                row = i // cols
                col = i % cols
                self._layout.addWidget(w, row, col)
                w.show()
        self._layout_orientation = orientation
        self.setMinimumSize(0, 0)
        self.resize(self.sizeHint())
        self.adjustSize()
        if orientation == "h":
            p = self.parentWidget()
            if p is not None:
                nx = max(8, min(self.x(), max(8, p.width() - self.width() - 8)))
                self.move(nx, self.y())
        self._clamp_to_parent()

    def reset_default_geometry(self):
        """Place island to a sane readable default after viewport is ready."""
        p = self.parentWidget()
        if p is None:
            return
        if p.width() < 220 or p.height() < 140:
            return
        # Always start horizontal at top-center.
        self._rebuild_layout("h")
        x = max(8, int((p.width() - self.width()) / 2))
        x = min(x, max(8, p.width() - self.width() - 8))
        y = 8
        self.move(x, y)
        self._clamp_to_parent()
        self._initialized = True

    def _clamp_to_parent(self):
        p = self.parentWidget()
        if p is None:
            return
        x = min(max(0, self.x()), max(0, p.width() - self.width()))
        y = min(max(0, self.y()), max(0, p.height() - self.height()))
        self.move(x, y)

    def _nearest_edge_orientation(self) -> str:
        p = self.parentWidget()
        if p is None:
            return "h"
        d_left = self.x()
        d_right = p.width() - (self.x() + self.width())
        d_top = self.y()
        d_bottom = p.height() - (self.y() + self.height())

        # Explicit dock zones: side zones force vertical, top/bottom force horizontal.
        edge_zone = 64
        if d_left <= edge_zone or d_right <= edge_zone:
            return "v"
        if d_top <= edge_zone or d_bottom <= edge_zone:
            return "h"

        # Fallback to nearest axis when in the middle area.
        return "v" if min(d_left, d_right) < min(d_top, d_bottom) else "h"

    def eventFilter(self, obj, event):
        if obj is self.parent():
            if event.type() in (QEvent.Resize, QEvent.Show):
                if not self._initialized:
                    # Defer until viewport has a stable size.
                    QTimer.singleShot(0, self.reset_default_geometry)
                else:
                    # Reflow to current viewport size before clamping.
                    self._rebuild_layout(self._layout_orientation)
                self._clamp_to_parent()
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_offset is not None:
            target = self.mapToParent(event.position().toPoint() - self._drag_offset)
            self.move(target)
            self._clamp_to_parent()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.LeftButton:
            self._dragging = False
            desired = self._nearest_edge_orientation()
            if desired != self._layout_orientation:
                self._rebuild_layout(desired)
                # Keep horizontal dock readable after mode switch.
                if desired == "h":
                    p = self.parentWidget()
                    if p is not None:
                        nx = max(8, int((p.width() - self.width()) / 2))
                        self.move(nx, self.y())
                        self._clamp_to_parent()
            else:
                # Even with same orientation, reflow to ensure bounds fit.
                self._rebuild_layout(self._layout_orientation)
                self._clamp_to_parent()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    """Application shell wiring scene, docks, menus, and file operations."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NodeZilla – Schematic & Instruments (Modular)")
        self.resize(1400, 850)
        # Runtime netlist outputs for external automation/gizmo flows.
        self.runtime_spice_netlist_text: str = ""
        self.runtime_spice_netlist_path: str = ""

        self.tabs = QTabWidget()
        self.status_label = QLabel("Ready")
        self.undo_stack = QUndoStack(self)

        self._watcher = ThemeWatcher(QApplication.instance(), self._apply_theme)

        self.schematic_tab = SchematicTab(self.status_label, self.undo_stack)
        self.component_library = load_component_library()
        theme = self._watcher.current_theme()
        self._apply_theme(theme)
        self.instruments_tab = InstrumentsTab(show_connection_strip=False)
        self.tabs.addTab(self.schematic_tab, "Schematic")
        self.tabs.addTab(self.instruments_tab, "Instruments")
        self.setCentralWidget(self.tabs)
        self._build_hardware_toolbar()

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

        self.live_netlist_view = QTextEdit()
        self.live_netlist_view.setReadOnly(True)
        self.live_netlist_view.setPlaceholderText("Live SPICE netlist will appear here.")
        live_netlist_dock = QDockWidget("Live SPICE Netlist", self)
        live_netlist_dock.setWidget(self.live_netlist_view)
        live_netlist_dock.setObjectName("LiveSpiceNetlistDock")
        live_netlist_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea | Qt.BottomDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, live_netlist_dock)
        self.live_netlist_dock = live_netlist_dock

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
        self._build_schematic_island()
        self._build_menu()

        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.props_dock.toggleViewAction())
        view_menu.addAction(self.nets_dock.toggleViewAction())
        view_menu.addAction(self.component_dock.toggleViewAction())
        view_menu.addAction(self.live_netlist_dock.toggleViewAction())

        # Keep schematic-only docks hidden when Instruments tab is active.
        self._schematic_dock_visibility = {
            "props": self.props_dock.isVisible(),
            "nets": self.nets_dock.isVisible(),
            "components": self.component_dock.isVisible(),
            "live_netlist": self.live_netlist_dock.isVisible(),
        }
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(self.tabs.currentIndex())
        self.instruments_tab.backend.connection_changed.connect(self._on_backend_connection_changed)
        self._refresh_hardware_devices()
        self.schematic_tab.scene.nets_changed.connect(self._refresh_live_spice_panel)
        self._refresh_live_spice_panel()

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

    def _on_tab_changed(self, index: int):
        """Show schematic docks only on the Schematic tab.

        Preserve user visibility choices when returning from Instruments.
        """
        on_schematic = self.tabs.widget(index) is self.schematic_tab
        if hasattr(self, "_schematic_toolbar") and self._schematic_toolbar is not None:
            self._schematic_toolbar.setVisible(False)
        if hasattr(self, "_schematic_island") and self._schematic_island is not None:
            self._schematic_island.setVisible(on_schematic)
        if on_schematic:
            if self._schematic_dock_visibility.get("props", True):
                self.props_dock.show()
            else:
                self.props_dock.hide()
            if self._schematic_dock_visibility.get("nets", True):
                self.nets_dock.show()
            else:
                self.nets_dock.hide()
            if self._schematic_dock_visibility.get("components", True):
                self.component_dock.show()
            else:
                self.component_dock.hide()
            if self._schematic_dock_visibility.get("live_netlist", True):
                self.live_netlist_dock.show()
            else:
                self.live_netlist_dock.hide()
            return

        # Snapshot current schematic dock visibility, then hide all of them.
        self._schematic_dock_visibility["props"] = self.props_dock.isVisible()
        self._schematic_dock_visibility["nets"] = self.nets_dock.isVisible()
        self._schematic_dock_visibility["components"] = self.component_dock.isVisible()
        self._schematic_dock_visibility["live_netlist"] = self.live_netlist_dock.isVisible()
        self.props_dock.hide()
        self.nets_dock.hide()
        self.component_dock.hide()
        self.live_netlist_dock.hide()

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
    def _build_hardware_toolbar(self):
        """Create a global hardware connection strip shared across tabs."""
        tb = QToolBar("Hardware")
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)
        self._hardware_toolbar = tb

        self.hw_status = QLabel("Hardware: Disconnected")
        self.hw_devices = QComboBox()
        self.hw_refresh_btn = QPushButton("Refresh")
        self.hw_connect_btn = QPushButton("Connect")
        self.hw_disconnect_btn = QPushButton("Disconnect")
        self.hw_disconnect_btn.setEnabled(False)
        self.hw_backend = QLabel(f"Backend: {self.instruments_tab.backend.backend_name()}")

        tb.addWidget(self.hw_status)
        tb.addSeparator()
        tb.addWidget(QLabel("Device"))
        tb.addWidget(self.hw_devices)
        tb.addWidget(self.hw_refresh_btn)
        tb.addWidget(self.hw_connect_btn)
        tb.addWidget(self.hw_disconnect_btn)
        tb.addSeparator()
        tb.addWidget(self.hw_backend)

        self.hw_refresh_btn.clicked.connect(self._refresh_hardware_devices)
        self.hw_connect_btn.clicked.connect(self._connect_hardware_selected)
        self.hw_disconnect_btn.clicked.connect(self._disconnect_hardware)

    def _refresh_hardware_devices(self):
        backend = self.instruments_tab.backend
        connected = backend.connected_device()
        self.hw_devices.clear()
        devices = backend.list_devices()
        for dev in devices:
            self.hw_devices.addItem(dev)
        if not devices:
            self.hw_devices.addItem("No devices found")
            self.hw_devices.setEnabled(False)
            self.hw_connect_btn.setEnabled(False)
            self.hw_disconnect_btn.setEnabled(bool(connected))
            if connected:
                self.hw_status.setText(f"Hardware: Connected to {connected}.")
            else:
                self.hw_status.setText("Hardware: Disconnected")
            return
        if connected:
            idx = self.hw_devices.findText(connected)
            if idx >= 0:
                self.hw_devices.setCurrentIndex(idx)
            self.hw_devices.setEnabled(False)
            self.hw_connect_btn.setEnabled(False)
            self.hw_disconnect_btn.setEnabled(True)
            self.hw_status.setText(f"Hardware: Connected to {connected}.")
        else:
            self.hw_devices.setEnabled(True)
            self.hw_connect_btn.setEnabled(True)
            self.hw_disconnect_btn.setEnabled(False)
            self.hw_status.setText("Hardware: Disconnected")

    def _connect_hardware_selected(self):
        if self.hw_devices.count() == 0 or not self.hw_devices.isEnabled():
            return
        dev = self.hw_devices.currentText()
        ok, msg = self.instruments_tab.backend.connect_device(dev)
        self.hw_status.setText(f"Hardware: {msg}")
        if ok:
            self._refresh_hardware_devices()

    def _disconnect_hardware(self):
        ok, msg = self.instruments_tab.backend.disconnect_device()
        self.hw_status.setText(f"Hardware: {msg}")
        if ok:
            self._refresh_hardware_devices()

    def _on_backend_connection_changed(self, _connected: bool, message: str):
        self.hw_status.setText(f"Hardware: {message}")
        if _connected:
            backend = self.instruments_tab.backend
            ok, _msg, st = backend.read_supplies_status()
            tracking = bool(st.get("tracking", False)) if ok else False
            power_limit_w = float(st.get("power_limit_w", 2.5)) if ok else 2.5
            backend.configure_supplies(
                master_enabled=True,
                v_pos_v=5.0,
                v_neg_v=-5.0,
                tracking=tracking,
                power_limit_w=power_limit_w,
            )
            if hasattr(self.instruments_tab, "sync_supplies_panels"):
                self.instruments_tab.sync_supplies_panels()
        self._refresh_hardware_devices()

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
        self._schematic_toolbar = tb

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

        act_build_runtime_netlist = QAction("Build Runtime Netlist", self)
        act_build_runtime_netlist.setShortcut("Ctrl+Shift+E")
        act_build_runtime_netlist.triggered.connect(self._build_runtime_netlist)
        tb.addAction(act_build_runtime_netlist)

    def _build_schematic_island(self):
        """Create floating schematic controls on top of the canvas."""
        vp = self.schematic_tab.view.viewport()
        island = FloatingToolIsland(vp)
        self._schematic_island = island

        def btn(text: str, cb):
            b = QPushButton(text, island)
            b.setMinimumHeight(24)
            b.setMinimumWidth(92)
            b.clicked.connect(cb)
            return b

        select_btn = btn("Select", self.schematic_tab.scene.set_mode_select)
        components_btn = btn("Components", lambda: (self.component_dock.show(), self.component_dock.raise_()))
        wire_btn = btn("Wire", self.schematic_tab.scene.set_mode_wire)
        net_label_btn = btn("Net Label", lambda: self.schematic_tab.scene.set_mode_place("NetLabel"))
        delete_btn = btn("Delete", self._delete_selected)
        rot_cw_btn = btn("Rotate ⟳", lambda: self._rotate_selected(90))
        rot_ccw_btn = btn("Rotate ⟲", lambda: self._rotate_selected(-90))
        grid_btn = btn("Grid G", self._toggle_grid)
        snap_btn = btn("Snap Ctrl+S", self._toggle_snap)
        grid_style_btn = btn("Grid Style (D)", self._toggle_grid_style)
        fit_btn = btn("Fit", self.schematic_tab.view.fit_all)
        zoom_in_btn = btn("Zoom +", lambda: self.schematic_tab.view.scale(1.15, 1.15))
        zoom_out_btn = btn("Zoom -", lambda: self.schematic_tab.view.scale(1 / 1.15, 1 / 1.15))
        export_btn = btn("Export Netlist", self._export_netlist)
        runtime_btn = btn("Build Runtime Netlist", self._build_runtime_netlist)

        grid_label = QLabel("Grid:", island)
        self._island_grid_spin = QSpinBox(island)
        self._island_grid_spin.setRange(5, 200)
        self._island_grid_spin.setSingleStep(5)
        self._island_grid_spin.setSuffix(" px")
        self._island_grid_spin.setValue(self.schematic_tab.scene.grid_size)
        self._island_grid_spin.valueChanged.connect(self._change_grid_size)
        grid_minus_btn = btn("Grid −", lambda: self._nudge_grid(-5))
        grid_plus_btn = btn("Grid +", lambda: self._nudge_grid(5))

        island.add_controls([
            select_btn, components_btn, wire_btn, net_label_btn, delete_btn,
            rot_cw_btn, rot_ccw_btn, grid_btn, snap_btn, grid_style_btn,
            grid_label, self._island_grid_spin, grid_minus_btn, grid_plus_btn,
            fit_btn, zoom_in_btn, zoom_out_btn, export_btn, runtime_btn,
        ])
        island.move(20, 20)
        island.show()
        QTimer.singleShot(0, island.reset_default_geometry)

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

        act_build_runtime_netlist = QAction("Build Runtime Netlist", self)
        act_build_runtime_netlist.setShortcut("Ctrl+Shift+E")
        act_build_runtime_netlist.triggered.connect(self._build_runtime_netlist)
        fm.addAction(act_build_runtime_netlist)

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
        if hasattr(self, "_grid_spin") and self._grid_spin.value() != sc.grid_size:
            self._grid_spin.blockSignals(True)
            self._grid_spin.setValue(sc.grid_size)
            self._grid_spin.blockSignals(False)
        if hasattr(self, "_island_grid_spin") and self._island_grid_spin.value() != sc.grid_size:
            self._island_grid_spin.blockSignals(True)
            self._island_grid_spin.setValue(sc.grid_size)
            self._island_grid_spin.blockSignals(False)
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

    def _refresh_live_spice_panel(self):
        """Refresh live SPICE text dock from current schematic scene."""
        sc = self.schematic_tab.scene
        try:
            netlist_text = sc.export_netlist_text()
        except Exception as e:
            self.live_netlist_view.setPlainText(f"* Netlist build error\n* {e}")
            return
        self.live_netlist_view.setPlainText(netlist_text)
        # Keep runtime copy in sync for integrations that consume in-memory text.
        self.runtime_spice_netlist_text = netlist_text

    def _build_runtime_netlist(self):
        """Generate SPICE netlist text and write a temp file in project root.

        Stores outputs on:
        - self.runtime_spice_netlist_text
        - self.runtime_spice_netlist_path
        """
        backend = self.instruments_tab.backend
        sc = self.schematic_tab.scene
        if backend.connected_device() is None:
            QMessageBox.warning(self, "Device required", "Connect a device first.")
            return

        try:
            netlist_text = sc.export_netlist_text()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to build netlist: {e}")
            return

        root_dir = Path.cwd()
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix="nodezilla_spice_",
                suffix=".cir",
                dir=str(root_dir),
            )
            with os.fdopen(fd, "w") as f:
                f.write(netlist_text)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to write runtime netlist: {e}")
            return

        self.runtime_spice_netlist_text = netlist_text
        self.runtime_spice_netlist_path = str(tmp_path)
        
        Feedback_message = ""
        ComponentDataSet = P.CreateComponentDataSet.MakeDataSet()
        P.ComponentSerach(ComponentDataSet, tmp_path)
        Used_Components = P.ComponentSerach.GetComponentsUsed(ComponentDataSet)
        P.CirToScript(Used_Components, backend)
        self.statusBar().showMessage(
            f"Runtime SPICE netlist ready: {self.runtime_spice_netlist_path}",
            5000,
        )

    def _enforce_runtime_supplies(self) -> bool:
        """Silently enforce runtime rail targets and wait for lock (+5V/-5V)."""
        backend = self.instruments_tab.backend
        target_vp = 5.0
        target_vn = -5.0
        tol = 0.05
        timeout_s = 8.0
        poll_s = 0.15
        deadline = time.monotonic() + timeout_s

        ok, _msg, st = backend.read_supplies_status()
        tracking = bool(st.get("tracking", False)) if ok else False
        power_limit_w = float(st.get("power_limit_w", 2.5)) if ok else 2.5

        locked = False
        while time.monotonic() < deadline:
            backend.configure_supplies(
                master_enabled=True,
                v_pos_v=target_vp,
                v_neg_v=target_vn,
                tracking=tracking,
                power_limit_w=power_limit_w,
            )
            ok2, _msg2, st2 = backend.read_supplies_status()
            if ok2:
                vp = float(st2.get("v_pos_meas_v", st2.get("v_pos_v", 0.0)))
                vn = float(st2.get("v_neg_meas_v", st2.get("v_neg_v", 0.0)))
                if abs(vp - target_vp) <= tol and abs(vn - target_vn) <= tol:
                    locked = True
                    break
            time.sleep(poll_s)

        if hasattr(self.instruments_tab, "sync_supplies_panels"):
            self.instruments_tab.sync_supplies_panels()
        return locked



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

    def closeEvent(self, event):
        """Delete runtime netlist temp file on app close, then continue shutdown."""
        tmp_path = (self.runtime_spice_netlist_path or "").strip()
        if tmp_path:
            try:
                p = Path(tmp_path)
                if p.exists() and p.is_file():
                    p.unlink()
            except Exception:
                # Best-effort cleanup only; do not block close on file issues.
                pass
        super().closeEvent(event)
