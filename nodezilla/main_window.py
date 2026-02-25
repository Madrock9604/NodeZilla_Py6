# ========================================
# File: nodezilla/main_window.py
# ========================================
from __future__ import annotations
from typing import List
from pathlib import Path
import os
import tempfile
import time
from PySide6.QtCore import Qt, QEvent, QTimer, QPointF
from PySide6.QtGui import QAction, QKeySequence, QUndoStack, QIcon, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QToolBar, QLabel, QSpinBox,
    QDockWidget, QStatusBar, QFileDialog, QMessageBox, QDialog, QInputDialog, QTextEdit,
    QComboBox, QPushButton, QToolButton
)
import json
from .schematic_scene import SchematicScene
from .schematic_view import SchematicView
from .properties_panel import PropertiesPanel
from .net_panel import NetPanel
from .graphics_items import ComponentItem, WireItem, CommentTextItem
from .commands import DeleteItemsCommand, RotateComponentCommand
from .theme import ThemeWatcher
from .component_library import load_component_library
from .component_panel import ComponentPanel
from .custom_component_dialog import CustomComponentDialog
from .instruments_tab import InstrumentTool, ScopePanel, SuppliesPanel, WavegenPanel
from .discovery_backend import make_backend
from .pl_panel import PlPanel
from .project_explorer_panel import ProjectExplorerPanel
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
    """Top-docked schematic control island."""

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
            for i, w in enumerate(self._widgets):
                self._layout.addWidget(w, 0, i)
                w.show()
        self._layout_orientation = orientation
        self.setMinimumSize(0, 0)
        self.resize(self.sizeHint())
        self.adjustSize()
        self._clamp_to_parent()

    def reset_default_geometry(self):
        """Place island to a sane readable default after viewport is ready."""
        p = self.parentWidget()
        if p is None:
            return
        if p.width() < 220 or p.height() < 140:
            return
        # Always stay horizontal and top-centered.
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
                    self.reset_default_geometry()
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
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
        self._clipboard_payload: dict | None = None
        self._paste_serial: int = 0
        self._pending_pl_component_id: int | None = None

        self.tabs = QTabWidget()
        self.status_label = QLabel("Ready")
        self.undo_stack = QUndoStack(self)

        self._watcher = ThemeWatcher(QApplication.instance(), self._apply_theme)
        self.backend = make_backend()

        self.schematic_tab = SchematicTab(self.status_label, self.undo_stack)
        self.component_library = load_component_library()
        theme = self._watcher.current_theme()
        self._apply_theme(theme)
        self.instruments_tab = QMainWindow()
        self.instruments_tab.setDockNestingEnabled(True)
        self.instruments_tab.setDockOptions(
            QMainWindow.AnimatedDocks
            | QMainWindow.AllowNestedDocks
            | QMainWindow.AllowTabbedDocks
        )
        # Keep an empty central host so Qt shows full edge docking guides
        # (left/right/top/bottom), without a visible placeholder panel.
        inst_center = QWidget()
        inst_center.setObjectName("InstrumentsCenterHost")
        inst_center.setStyleSheet("#InstrumentsCenterHost { background: transparent; }")
        self.instruments_tab.setCentralWidget(inst_center)
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

        self.pl_panel = PlPanel()
        self.pl_panel.place_requested.connect(self._place_component_from_pl)
        self.pl_panel.verify_requested.connect(self._verify_pl_availability)
        pl_dock = QDockWidget("PL Components", self)
        pl_dock.setWidget(self.pl_panel)
        pl_dock.setObjectName("PlComponentsDock")
        pl_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea | Qt.BottomDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, pl_dock)
        self.pl_dock = pl_dock

        self.component_panel = ComponentPanel(self.component_library)
        self.component_panel.place_requested.connect(self.schematic_tab.scene.set_mode_place)
        component_dock = QDockWidget("Components", self)
        component_dock.setWidget(self.component_panel)
        component_dock.setObjectName("ComponentsDock")
        component_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, component_dock)
        self.component_dock = component_dock

        self.project_explorer_panel = ProjectExplorerPanel(Path.cwd())
        self.project_explorer_panel.open_requested.connect(self._open_schematic_path)
        project_dock = QDockWidget("Project Explorer", self)
        project_dock.setWidget(self.project_explorer_panel)
        project_dock.setObjectName("ProjectExplorerDock")
        project_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, project_dock)
        self.project_explorer_dock = project_dock
        self.tabifyDockWidget(self.component_dock, self.project_explorer_dock)
        self.component_dock.raise_()

        self.schematic_tab.scene.request_properties = self._show_properties_for
        self.schematic_tab.scene.selectionChanged.connect(self._on_selection_changed)
        self.schematic_tab.scene.component_placed.connect(self._on_component_placed)

        self._build_toolbar()
        self._build_schematic_island()
        self._build_instrument_docks()
        self._build_menu()
        self._build_schematic_shortcuts()

        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.props_dock.toggleViewAction())
        view_menu.addAction(self.nets_dock.toggleViewAction())
        view_menu.addAction(self.component_dock.toggleViewAction())
        view_menu.addAction(self.project_explorer_dock.toggleViewAction())
        view_menu.addAction(self.live_netlist_dock.toggleViewAction())
        view_menu.addAction(self.pl_dock.toggleViewAction())
        view_menu.addAction(self.scope_dock.toggleViewAction())
        view_menu.addAction(self.wavegen_dock.toggleViewAction())
        view_menu.addAction(self.supplies_dock.toggleViewAction())

        # Keep schematic-only docks hidden when Instruments tab is active.
        self._schematic_dock_visibility = {
            "props": self.props_dock.isVisible(),
            "nets": self.nets_dock.isVisible(),
            "components": self.component_dock.isVisible(),
            "project_explorer": self.project_explorer_dock.isVisible(),
            "live_netlist": self.live_netlist_dock.isVisible(),
            "pl": self.pl_dock.isVisible(),
        }
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(self.tabs.currentIndex())
        self.backend.connection_changed.connect(self._on_backend_connection_changed)
        self._refresh_hardware_devices()
        self.schematic_tab.scene.nets_changed.connect(self._refresh_live_spice_panel)
        self.schematic_tab.scene.nets_changed.connect(self._refresh_pl_used_flags)
        self._refresh_live_spice_panel()
        self._refresh_pl_used_flags()

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
            if self._schematic_dock_visibility.get("project_explorer", True):
                self.project_explorer_dock.show()
            else:
                self.project_explorer_dock.hide()
            if self._schematic_dock_visibility.get("live_netlist", True):
                self.live_netlist_dock.show()
            else:
                self.live_netlist_dock.hide()
            if self._schematic_dock_visibility.get("pl", True):
                self.pl_dock.show()
            else:
                self.pl_dock.hide()
            self.scope_dock.hide()
            self.wavegen_dock.hide()
            self.supplies_dock.hide()
            return

        # Snapshot current schematic dock visibility, then hide all of them.
        self._schematic_dock_visibility["props"] = self.props_dock.isVisible()
        self._schematic_dock_visibility["nets"] = self.nets_dock.isVisible()
        self._schematic_dock_visibility["components"] = self.component_dock.isVisible()
        self._schematic_dock_visibility["project_explorer"] = self.project_explorer_dock.isVisible()
        self._schematic_dock_visibility["live_netlist"] = self.live_netlist_dock.isVisible()
        self._schematic_dock_visibility["pl"] = self.pl_dock.isVisible()
        self.props_dock.hide()
        self.nets_dock.hide()
        self.component_dock.hide()
        self.project_explorer_dock.hide()
        self.live_netlist_dock.hide()
        self.pl_dock.hide()
        self._apply_default_instrument_layout()

    # selection → props
    def _on_selection_changed(self):
        """Push active selection into the properties panel."""
        selected = self.schematic_tab.scene.selectedItems()
        texts = [it for it in selected if isinstance(it, CommentTextItem)]
        if texts:
            self.props_panel.show_text(texts[0])
            return
        wires = [it for it in selected if isinstance(it, WireItem)]
        if wires:
            self.props_panel.show_wire(wires[0])
            return
        comps = [it for it in self.schematic_tab.scene.selectedItems() if isinstance(it, ComponentItem)]
        self.props_panel.show_component(comps[0] if comps else None)

    def _resolve_library_kind_for_pl(self, pl_type: str, pl_name: str, value_or_part: str) -> str | None:
        lib = load_component_library(force_reload=True)
        by_kind = {c.kind.lower(): c.kind for c in lib.all()}
        by_display = {c.display_name.lower(): c.kind for c in lib.all()}

        def pick(*candidates: str) -> str | None:
            for c in candidates:
                key = str(c or "").strip().lower()
                if not key:
                    continue
                if key in by_kind:
                    return by_kind[key]
                if key in by_display:
                    return by_display[key]
            return None

        t = str(pl_type or "").strip().lower()
        # Prefer explicit part/model name when provided.
        part_kind = pick(value_or_part)
        if part_kind is not None:
            return part_kind
        if t == "resistor":
            return pick("Resistor")
        if t == "capacitor":
            return pick("Capacitor")
        if t == "inductor":
            return pick("Inductor")
        if t == "diode":
            return pick("Diode")
        if t == "instrument":
            return pick(value_or_part, pl_name, "WaveGen", "Oscope")
        return pick(pl_type, pl_name, value_or_part)

    def _place_component_from_pl(self, payload: dict):
        pl_type = str(payload.get("type", "")).strip()
        pl_name = str(payload.get("name", "")).strip()
        value_or_part = str(payload.get("value_or_part", "")).strip()
        comp_id = int(payload.get("id", -1))
        kind = self._resolve_library_kind_for_pl(pl_type, pl_name, value_or_part)
        if not kind:
            QMessageBox.warning(
                self,
                "Component not found",
                (
                    f'PL row ID {comp_id} ({pl_type} {pl_name}) is not in the component library.\n'
                    "Add this component to the library before placing it."
                ),
            )
            return
        self.tabs.setCurrentWidget(self.schematic_tab)
        self.schematic_tab.scene.set_mode_place(kind)
        self.schematic_tab.scene.set_place_overrides(refdes=pl_name, value=value_or_part)
        self._pending_pl_component_id = comp_id
        self.statusBar().showMessage(
            f"PL row {comp_id} selected: place {kind} as {pl_name}",
            4000,
        )

    def _on_component_placed(self, _comp: ComponentItem):
        self._refresh_pl_used_flags()
        self._pending_pl_component_id = None

    def _refresh_pl_used_flags(self):
        comps = [it for it in self.schematic_tab.scene.items() if isinstance(it, ComponentItem)]
        if hasattr(self, "pl_panel") and self.pl_panel is not None:
            self.pl_panel.sync_used_from_components(comps)

    def _verify_pl_availability(self):
        if not hasattr(self, "pl_panel") or self.pl_panel is None:
            return
        comps = [it for it in self.schematic_tab.scene.items() if isinstance(it, ComponentItem)]
        placed_counts = {}
        placed_refs = {}
        for c in comps:
            if not self.pl_panel.is_physical_component(c):
                continue
            sig = self.pl_panel.component_signature(c)
            placed_counts[sig] = int(placed_counts.get(sig, 0)) + 1
            placed_refs.setdefault(sig, []).append(str(getattr(c, "refdes", "")).strip() or "?")

        shortages = []
        for sig, placed in placed_counts.items():
            requested = int(self.pl_panel.requested_count_for_signature(sig))
            missing = placed - requested
            if missing > 0:
                shortages.append((sig, requested, placed, missing, placed_refs.get(sig, [])))

        if not shortages:
            QMessageBox.information(
                self,
                "PL availability",
                "All currently placed components can be fulfilled by the PL configuration.",
            )
            return

        lines = ["Current PL configuration cannot fulfill these placed components:", ""]
        for sig, requested, placed, missing, refs in sorted(
            shortages, key=lambda x: (str(x[0][0]), str(x[0][1]))
        ):
            t_name = str(sig[0] or "component")
            val = sig[1][1] if isinstance(sig[1], tuple) and len(sig[1]) > 1 else sig[1]
            shown_refs = ", ".join(refs[:8]) + ("..." if len(refs) > 8 else "")
            lines.append(
                f"- {t_name} | value/part={val} | requested={requested}, placed={placed}, missing={missing} "
                f"| refs: {shown_refs}"
            )
        QMessageBox.warning(self, "PL availability warning", "\n".join(lines))

    def _apply_properties(
        self,
        kind: str | None,
        refdes: str | None,
        value: str | None,
        wire_color: str,
        text_value: str = "",
        text_size: int = 12,
        text_bold: bool = False,
        text_italic: bool = False,
        text_color: str = "",
        text_family: str = "",
    ):
        """Apply edits from PropertiesPanel back to selected scene items."""
        if kind == "text":
            texts = [it for it in self.schematic_tab.scene.selectedItems() if isinstance(it, CommentTextItem)]
            for t in texts:
                if text_value is not None:
                    t.setPlainText(text_value)
                f = t.font()
                if text_family:
                    f.setFamily(text_family)
                f.setPointSize(max(1, int(text_size)))
                f.setBold(bool(text_bold))
                f.setItalic(bool(text_italic))
                t.setFont(f)
                if hasattr(t, "set_text_color"):
                    t.set_text_color(QColor(text_color) if text_color else None)
            return
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
        self.hw_backend = QLabel(f"Backend: {self.backend.backend_name()}")

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
        backend = self.backend
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
        ok, msg = self.backend.connect_device(dev)
        self.hw_status.setText(f"Hardware: {msg}")
        if ok:
            self._refresh_hardware_devices()

    def _disconnect_hardware(self):
        ok, msg = self.backend.disconnect_device()
        self.hw_status.setText(f"Hardware: {msg}")
        if ok:
            self._refresh_hardware_devices()

    def _on_backend_connection_changed(self, _connected: bool, message: str):
        self.hw_status.setText(f"Hardware: {message}")
        for p in (self.scope_panel, self.wavegen_panel, self.supplies_panel):
            if hasattr(p, "on_connection_changed"):
                p.on_connection_changed()
        if _connected:
            backend = self.backend
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
            self._sync_supplies_docks()
        self._refresh_hardware_devices()

    def _sync_supplies_docks(self):
        if hasattr(self, "supplies_panel") and self.supplies_panel is not None:
            if hasattr(self.supplies_panel, "sync_from_backend"):
                self.supplies_panel.sync_from_backend()

    def _shutdown_instrument_panels(self):
        for p in (getattr(self, "scope_panel", None), getattr(self, "wavegen_panel", None), getattr(self, "supplies_panel", None)):
            if p is None:
                continue
            if hasattr(p, "shutdown"):
                try:
                    p.shutdown()
                except Exception:
                    pass
        try:
            if self.backend.connected_device() is not None:
                self.backend.disconnect_device()
        except Exception:
            pass

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
            act.triggered.connect(
                lambda _=False, k=comp.kind, s=str(comp.shortcut): self._handle_component_shortcut(k, s)
            )
            self.addAction(act)
            self._component_shortcut_actions.append(act)

    def _build_instrument_docks(self):
        """Create dockable instrument panels (Wavegen/Scope/Supplies)."""
        scope_tool = InstrumentTool("scope", "Scope", "Capture and inspect analog waveforms.")
        wavegen_tool = InstrumentTool("wavegen", "Wavegen", "Generate analog stimulus signals.")
        supplies_tool = InstrumentTool("supplies", "Supplies", "Control programmable power rails.")

        self.scope_panel = ScopePanel(scope_tool, self.backend)
        self.wavegen_panel = WavegenPanel(wavegen_tool, self.backend)
        self.supplies_panel = SuppliesPanel(supplies_tool, self.backend)

        self.scope_dock = QDockWidget("Scope", self)
        self.scope_dock.setObjectName("ScopeDock")
        self.scope_dock.setWidget(self.scope_panel)
        self.scope_dock.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea
        )
        self.scope_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
        )
        self.scope_dock.setMinimumSize(280, 180)

        self.wavegen_dock = QDockWidget("Wavegen", self)
        self.wavegen_dock.setObjectName("WavegenDock")
        self.wavegen_dock.setWidget(self.wavegen_panel)
        self.wavegen_dock.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea
        )
        self.wavegen_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
        )
        self.wavegen_dock.setMinimumSize(260, 160)

        self.supplies_dock = QDockWidget("Supplies", self)
        self.supplies_dock.setObjectName("SuppliesDock")
        self.supplies_dock.setWidget(self.supplies_panel)
        self.supplies_dock.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea | Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea
        )
        self.supplies_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
        )
        self.supplies_dock.setMinimumSize(260, 160)
        self._apply_default_instrument_layout()

    def _apply_default_instrument_layout(self):
        """Force default instruments layout:
        scope top, wavegen+supplies bottom split.
        """
        for d in (self.scope_dock, self.wavegen_dock, self.supplies_dock):
            if d.isFloating():
                d.setFloating(False)
        self.instruments_tab.addDockWidget(Qt.TopDockWidgetArea, self.scope_dock)
        self.instruments_tab.addDockWidget(Qt.BottomDockWidgetArea, self.wavegen_dock)
        self.instruments_tab.addDockWidget(Qt.BottomDockWidgetArea, self.supplies_dock)
        self.instruments_tab.splitDockWidget(self.wavegen_dock, self.supplies_dock, Qt.Horizontal)
        self.scope_dock.show()
        self.wavegen_dock.show()
        self.supplies_dock.show()
        preset = self._instrument_layout_preset()
        # Proportional sizing: scope gets more vertical space.
        self.instruments_tab.resizeDocks(
            [self.scope_dock, self.wavegen_dock],
            [int(preset["v_top"]), int(preset["v_bottom"])],
            Qt.Vertical,
        )
        # Bottom split: wavegen and supplies proportional by preset.
        self.instruments_tab.resizeDocks(
            [self.wavegen_dock, self.supplies_dock],
            [int(preset["h_left"]), int(preset["h_right"])],
            Qt.Horizontal,
        )
        self.scope_dock.raise_()

    def _instrument_layout_preset(self) -> dict:
        """Return dock-size preset tuned for current window size."""
        w = max(1, int(self.instruments_tab.width()))
        h = max(1, int(self.instruments_tab.height()))
        area = w * h
        # Small screens / compact windows.
        if w < 1300 or h < 760 or area < 900_000:
            return {
                "v_top": max(240, int(h * 0.58)),
                "v_bottom": max(180, int(h * 0.42)),
                "h_left": max(260, int(w * 0.55)),
                "h_right": max(220, int(w * 0.45)),
            }
        # Large monitors.
        if w > 2200 or h > 1300 or area > 2_600_000:
            return {
                "v_top": max(340, int(h * 0.66)),
                "v_bottom": max(230, int(h * 0.34)),
                "h_left": max(420, int(w * 0.60)),
                "h_right": max(320, int(w * 0.40)),
            }
        # Medium default.
        return {
            "v_top": max(300, int(h * 0.63)),
            "v_bottom": max(210, int(h * 0.37)),
            "h_left": max(340, int(w * 0.58)),
            "h_right": max(280, int(w * 0.42)),
        }

    def _handle_component_shortcut(self, kind: str, shortcut: str):
        """Resolve conflicts between placement hotkeys and component shortcuts."""
        sc = self.schematic_tab.scene
        key = shortcut.strip().upper()
        if self._is_schematic_active() and key == "T":
            sc.set_mode_text()
            return
        # While placing a component, let R rotate the ghost instead of
        # switching tool to the resistor shortcut.
        if (
            self._is_schematic_active()
            and getattr(sc, "mode", None) == getattr(sc, "Mode", object()).PLACE
            and getattr(sc, "_ghost_item", None) is not None
            and key == "R"
        ):
            g = sc._ghost_item
            g.setRotation((g.rotation() + 90) % 360)
            return
        sc.set_mode_place(kind)

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
        act_components.setShortcut("A")
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
        act_text = QAction("Text", self)
        act_text.setShortcut("T")
        act_text.triggered.connect(self.schematic_tab.scene.set_mode_text)
        tb.addAction(act_text)
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
        act_grid.setShortcut(QKeySequence("Ctrl+G"))
        act_grid.triggered.connect(self._toggle_grid)
        tb.addAction(act_grid)

        act_snap = QAction("Snap Ctrl+S", self)
        act_snap.setShortcut(QKeySequence("Ctrl+Shift+S"))
        act_snap.triggered.connect(self._toggle_snap)
        tb.addAction(act_snap)

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

        def btn(text: str, cb, *, icon_name: str = ""):
            b = QToolButton(island)
            b.setAutoRaise(False)
            b.setToolTip(text)
            b.setMinimumHeight(28)
            b.setMinimumWidth(36)
            icon = QIcon.fromTheme(icon_name) if icon_name else QIcon()
            if not icon.isNull():
                b.setIcon(icon)
                b.setText("")
            else:
                b.setText(text)
            b.clicked.connect(cb)
            return b

        select_btn = btn("Select", self.schematic_tab.scene.set_mode_select, icon_name="cursor-arrow")
        components_btn = btn("Components", lambda: (self.component_dock.show(), self.component_dock.raise_()), icon_name="folder")
        wire_btn = btn("Wire", self.schematic_tab.scene.set_mode_wire, icon_name="draw-line")
        text_btn = btn("Text", self.schematic_tab.scene.set_mode_text, icon_name="insert-text")
        net_label_btn = btn("Net Label", lambda: self.schematic_tab.scene.set_mode_place("NetLabel"), icon_name="tag")
        delete_btn = btn("Delete", self._delete_selected, icon_name="edit-delete")
        rot_cw_btn = btn("Rotate CW", lambda: self._rotate_selected(90), icon_name="object-rotate-right")
        rot_ccw_btn = btn("Rotate CCW", lambda: self._rotate_selected(-90), icon_name="object-rotate-left")
        mirror_x_btn = btn("Mirror X", self._on_shortcut_place_mirror_x, icon_name="object-flip-horizontal")
        mirror_y_btn = btn("Mirror Y", self._on_shortcut_place_mirror_y, icon_name="object-flip-vertical")
        grid_btn = btn("Grid", self._toggle_grid, icon_name="view-grid")
        snap_btn = btn("Snap", self._toggle_snap, icon_name="snap-to-grid")
        fit_btn = btn("Fit", self.schematic_tab.view.fit_all, icon_name="zoom-fit-best")
        zoom_in_btn = btn("Zoom In", lambda: self.schematic_tab.view.scale(1.15, 1.15), icon_name="zoom-in")
        zoom_out_btn = btn("Zoom Out", lambda: self.schematic_tab.view.scale(1 / 1.15, 1 / 1.15), icon_name="zoom-out")
        export_btn = btn("Export Netlist", self._export_netlist, icon_name="document-save")
        runtime_btn = btn("Build Runtime Netlist", self._build_runtime_netlist, icon_name="media-playback-start")

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
            select_btn, components_btn, wire_btn, text_btn, net_label_btn, delete_btn,
            rot_cw_btn, rot_ccw_btn, mirror_x_btn, mirror_y_btn, grid_btn, snap_btn,
            grid_label, self._island_grid_spin, grid_minus_btn, grid_plus_btn,
            fit_btn, zoom_in_btn, zoom_out_btn, export_btn, runtime_btn,
        ])
        island.move(20, 20)
        island.show()
        QTimer.singleShot(0, island.reset_default_geometry)
        # Keep island pinned to top when the canvas is panned/scrolled.
        self.schematic_tab.view.horizontalScrollBar().valueChanged.connect(
            lambda _v: island.reset_default_geometry()
        )
        self.schematic_tab.view.verticalScrollBar().valueChanged.connect(
            lambda _v: island.reset_default_geometry()
        )

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

    def _build_schematic_shortcuts(self):
        """Register schematic-only command shortcuts (Cmd/Ctrl aware)."""
        def add_shortcut(seq, cb):
            act = QAction(self)
            act.setShortcut(seq)
            act.triggered.connect(cb)
            self.addAction(act)
            return act

        self._act_toggle_grid = add_shortcut(QKeySequence("Ctrl+G"), self._on_shortcut_toggle_grid)
        self._act_toggle_snap = add_shortcut(QKeySequence("Ctrl+Shift+S"), self._on_shortcut_toggle_snap)
        self._act_wire = add_shortcut(QKeySequence("W"), self._on_shortcut_set_wire)
        self._act_text = add_shortcut(QKeySequence("T"), self._on_shortcut_set_text)
        self._act_fit = add_shortcut(QKeySequence("F"), self._on_shortcut_fit)
        self._act_components = add_shortcut(QKeySequence("A"), self._on_shortcut_open_components)
        self._act_escape = add_shortcut(QKeySequence(Qt.Key_Escape), self._on_shortcut_escape_to_select)
        self._act_place_rot_ccw = add_shortcut(QKeySequence("Shift+R"), self._on_shortcut_place_rotate_ccw)
        self._act_place_mirror_x = add_shortcut(QKeySequence("X"), self._on_shortcut_place_mirror_x)
        self._act_place_mirror_y = add_shortcut(QKeySequence("Y"), self._on_shortcut_place_mirror_y)
        self._act_cut = add_shortcut(QKeySequence.Cut, self._on_shortcut_cut)
        self._act_copy = add_shortcut(QKeySequence.Copy, self._on_shortcut_copy)
        self._act_paste = add_shortcut(QKeySequence.Paste, self._on_shortcut_paste)

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

    def _is_schematic_active(self) -> bool:
        return self.tabs.currentWidget() is self.schematic_tab

    def _on_shortcut_toggle_grid(self):
        if self._is_schematic_active():
            self._toggle_grid()

    def _on_shortcut_toggle_snap(self):
        if self._is_schematic_active():
            self._toggle_snap()

    def _on_shortcut_set_wire(self):
        if self._is_schematic_active():
            self.schematic_tab.scene.set_mode_wire()

    def _on_shortcut_set_text(self):
        if self._is_schematic_active():
            self.schematic_tab.scene.set_mode_text()

    def _on_shortcut_fit(self):
        if self._is_schematic_active():
            self.schematic_tab.view.fit_all()

    def _on_shortcut_open_components(self):
        if not self._is_schematic_active():
            return
        self.component_dock.show()
        self.component_dock.raise_()
        if hasattr(self.component_panel, "search"):
            self.component_panel.search.setFocus()

    def _on_shortcut_escape_to_select(self):
        if not self._is_schematic_active():
            return
        sc = self.schematic_tab.scene
        sc.clearSelection()
        sc.set_mode_select()

    def _in_place_mode_with_ghost(self) -> bool:
        if not self._is_schematic_active():
            return False
        sc = self.schematic_tab.scene
        return (
            getattr(sc, "mode", None) == getattr(sc, "Mode", object()).PLACE
            and getattr(sc, "_ghost_item", None) is not None
        )

    def _on_shortcut_place_rotate_cw(self):
        if not self._in_place_mode_with_ghost():
            return
        g = self.schematic_tab.scene._ghost_item
        g.setRotation((g.rotation() + 90) % 360)

    def _on_shortcut_place_rotate_ccw(self):
        if not self._in_place_mode_with_ghost():
            return
        g = self.schematic_tab.scene._ghost_item
        g.setRotation((g.rotation() - 90) % 360)

    def _on_shortcut_place_mirror_x(self):
        if not self._in_place_mode_with_ghost():
            return
        g = self.schematic_tab.scene._ghost_item
        if hasattr(g, "toggle_mirror_x"):
            g.toggle_mirror_x()

    def _on_shortcut_place_mirror_y(self):
        if not self._in_place_mode_with_ghost():
            return
        g = self.schematic_tab.scene._ghost_item
        if hasattr(g, "toggle_mirror_y"):
            g.toggle_mirror_y()

    def _on_shortcut_copy(self):
        if not self._is_schematic_active():
            return
        sc = self.schematic_tab.scene
        selected = list(sc.selectedItems())
        comps = [it for it in selected if isinstance(it, ComponentItem)]
        wires_selected = [it for it in selected if isinstance(it, WireItem)]
        if not comps and not wires_selected:
            return

        comp_map = {c: i for i, c in enumerate(comps)}
        payload = {"components": [], "wires": []}
        for c in comps:
            payload["components"].append({
                "kind": c.kind,
                "pos": [float(c.scenePos().x()), float(c.scenePos().y())],
                "rotation": float(c.rotation()),
                "mirror": c.mirror_state() if hasattr(c, "mirror_state") else {"mx": 1.0, "my": 1.0},
                "value": c.value,
                "labels": c.labels_state() if hasattr(c, "labels_state") else {},
            })

        all_wires = [it for it in sc.items() if isinstance(it, WireItem)]
        for w in all_wires:
            include = (w in wires_selected)
            if not include:
                pa = getattr(w, "port_a", None)
                pb = getattr(w, "port_b", None)
                include = bool(
                    pa is not None and pb is not None and
                    pa.parentItem() in comp_map and pb.parentItem() in comp_map
                )
            if not include:
                continue
            entry = {
                "points": [{"x": float(p.x()), "y": float(p.y())} for p in getattr(w, "_pts", [])],
                "mode": getattr(w, "route_mode", "orth"),
            }
            if hasattr(w, "wire_color_hex"):
                c = w.wire_color_hex()
                if c:
                    entry["color"] = c
            pa = getattr(w, "port_a", None)
            pb = getattr(w, "port_b", None)
            if pa is not None and pa.parentItem() in comp_map:
                entry["a"] = [comp_map[pa.parentItem()], getattr(pa, "name", "A")]
            elif getattr(w, "_start_point", None) is not None:
                entry["a_point"] = {"x": float(w._start_point.x()), "y": float(w._start_point.y())}
            if pb is not None and pb.parentItem() in comp_map:
                entry["b"] = [comp_map[pb.parentItem()], getattr(pb, "name", "B")]
            elif getattr(w, "_end_point", None) is not None:
                entry["b_point"] = {"x": float(w._end_point.x()), "y": float(w._end_point.y())}
            if "a" in entry or "a_point" in entry or "b" in entry or "b_point" in entry:
                payload["wires"].append(entry)

        self._clipboard_payload = payload
        self._paste_serial = 0

    def _on_shortcut_cut(self):
        if not self._is_schematic_active():
            return
        self._on_shortcut_copy()
        self._delete_selected()

    def _on_shortcut_paste(self):
        if not self._is_schematic_active():
            return
        payload = self._clipboard_payload or {}
        comps_data = payload.get("components", [])
        wires_data = payload.get("wires", [])
        if not comps_data and not wires_data:
            return
        sc = self.schematic_tab.scene
        self._paste_serial += 1
        d = float(getattr(sc, "grid_size", 20) * self._paste_serial)
        delta = QPointF(d, d)

        new_comps: list[ComponentItem] = []
        for cdata in comps_data:
            kind = cdata.get("kind", "")
            pos = cdata.get("pos", [0.0, 0.0])
            c = ComponentItem(kind, QPointF(float(pos[0]), float(pos[1])) + delta)
            c.setRotation(float(cdata.get("rotation", 0.0)))
            m = cdata.get("mirror", {})
            if hasattr(c, "set_mirror"):
                c.set_mirror(float(m.get("mx", 1.0)), float(m.get("my", 1.0)))
            c.set_refdes(sc._next_refdes(kind))
            sc._bump_refseq(kind)
            c.set_value(str(cdata.get("value", "")))
            if hasattr(c, "apply_labels_state"):
                c.apply_labels_state(cdata.get("labels", {}))
            sc.addItem(c)
            if sc.theme and hasattr(c, "apply_theme"):
                c.apply_theme(sc.theme)
            new_comps.append(c)

        for wdata in wires_data:
            ai, aside = wdata.get("a", [None, None])
            bi, bside = wdata.get("b", [None, None])
            pa = pb = None
            a_point = b_point = None
            if ai is not None and isinstance(ai, int) and 0 <= ai < len(new_comps):
                ca = new_comps[ai]
                pa = next((p for p in getattr(ca, "ports", []) if getattr(p, "name", None) == aside), None)
            elif isinstance(wdata.get("a_point"), dict):
                ap = wdata["a_point"]
                a_point = QPointF(float(ap.get("x", 0.0)), float(ap.get("y", 0.0))) + delta
            if bi is not None and isinstance(bi, int) and 0 <= bi < len(new_comps):
                cb = new_comps[bi]
                pb = next((p for p in getattr(cb, "ports", []) if getattr(p, "name", None) == bside), None)
            elif isinstance(wdata.get("b_point"), dict):
                bp = wdata["b_point"]
                b_point = QPointF(float(bp.get("x", 0.0)), float(bp.get("y", 0.0))) + delta

            w = WireItem(
                pa, pb,
                start_point=a_point,
                end_point=b_point,
                theme=getattr(sc, "theme", None),
                route_mode=wdata.get("mode", "orth"),
            )
            pts = [QPointF(float(p.get("x", 0.0)), float(p.get("y", 0.0))) + delta for p in wdata.get("points", [])]
            if pts:
                w.set_points(pts)
            color_hex = wdata.get("color", "")
            if color_hex and hasattr(w, "set_wire_color"):
                w.set_wire_color(color_hex)
            sc.addItem(w)
            if sc.theme and hasattr(w, "apply_theme"):
                w.apply_theme(sc.theme)

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
        """Generate SPICE netlist text and write a temp file in a writable user temp dir.

        Stores outputs on:
        - self.runtime_spice_netlist_text
        - self.runtime_spice_netlist_path
        """
        backend = self.backend
        sc = self.schematic_tab.scene
        if backend.connected_device() is None:
            QMessageBox.warning(self, "Device required", "Connect a device first.")
            return

        try:
            netlist_text = sc.export_netlist_text()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to build netlist: {e}")
            return

        # App bundles may run with a read-only cwd; use a user-writable runtime temp dir.
        root_dir = Path(tempfile.gettempdir()) / "nodezilla_runtime"
        try:
            root_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            root_dir = Path(tempfile.gettempdir())
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

        try:
            ComponentDataSet = P.CreateComponentDataSet.MakeDataSet()
            P.ComponentSerach(ComponentDataSet, tmp_path)
            Used_Components = P.ComponentSerach.GetComponentsUsed(ComponentDataSet)
            P.CirToScript(Used_Components, backend)
        except Exception as e:
            QMessageBox.critical(self, "Runtime script error", f"Failed while processing runtime script: {e}")
            return
        self.statusBar().showMessage(
            f"Runtime SPICE netlist ready: {self.runtime_spice_netlist_path}",
            5000,
        )

    def _enforce_runtime_supplies(self) -> bool:
        """Silently enforce runtime rail targets and wait for lock (+5V/-5V)."""
        backend = self.backend
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

        self._sync_supplies_docks()
        return locked



    def _open(self):
        """Load schematic JSON from disk into scene."""
        path, _ = QFileDialog.getOpenFileName(self, "Open schematic", filter="Schematic (*.json)")
        if path:
            self._open_schematic_path(path)

    def _open_schematic_path(self, path: str):
        """Load schematic JSON from an explicit path."""
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            self.tabs.setCurrentWidget(self.schematic_tab)
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
        """Graceful app shutdown: stop instruments, disconnect hardware, clean temp files."""
        try:
            self._shutdown_instrument_panels()
        except Exception:
            # Best-effort shutdown; never block close.
            pass

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
