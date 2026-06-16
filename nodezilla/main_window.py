# ========================================
# File: nodezilla/main_window.py
# ========================================
from __future__ import annotations
from typing import List
from pathlib import Path
import os
import tempfile
import time
from PySide6.QtCore import Qt, QEvent, QTimer, QPointF, QSize
from PySide6.QtGui import QAction, QKeySequence, QUndoStack, QIcon, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QToolBar, QLabel, QSpinBox, QDoubleSpinBox, QSlider, QFrame, QSizePolicy,
    QDockWidget, QStatusBar, QFileDialog, QMessageBox, QDialog, QInputDialog, QTextEdit,
    QComboBox, QPushButton, QToolButton, QScrollArea
)
import json
from .schematic_scene import SchematicScene
from .schematic_view import SchematicView
from .properties_panel import PropertiesPanel
from .net_panel import NetPanel
from .graphics_items import ComponentItem, WireItem, CommentTextItem
from .commands import DeleteItemsCommand, RotateComponentCommand
from .commands import MirrorComponentCommand
from .theme import ThemeWatcher
from .component_library import load_component_library
from .component_panel import ComponentPanel
from .custom_component_dialog import CustomComponentDialog
from .instruments_tab import ScopeWindow, WavegenWindow
from .discovery_backend import make_backend
from .pl_panel import PlPanel
from .project_explorer_panel import ProjectExplorerPanel
from .hardware_builder_panel import HardwareBuilderPanel
from .chip_editor_dialog import ChipEditorDialog
from .paths import user_examples_dir, user_projects_dir, user_assets_root, user_root
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


class RuntimeBuildIndicator(QWidget):
    """Compact status-bar LED for runtime netlist programming state."""

    _COLORS = {
        "idle": ("#7b7f87", "Build idle"),
        "running": ("#f2c94c", "Programming"),
        "ok": ("#32d74b", "Programmed"),
        "error": ("#ff453a", "Program error"),
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RuntimeBuildIndicator")
        self._led = QFrame(self)
        self._led.setObjectName("RuntimeBuildIndicatorLed")
        self._led.setFixedSize(11, 11)
        self._label = QLabel("Build idle", self)
        self._label.setObjectName("RuntimeBuildIndicatorLabel")
        self._label.setMinimumWidth(76)
        self._label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)
        layout.addWidget(self._led, 0, Qt.AlignVCenter)
        layout.addWidget(self._label, 0, Qt.AlignVCenter)
        self.set_state("idle")

    def set_state(self, state: str):
        color, text = self._COLORS.get(state, self._COLORS["idle"])
        self._label.setText(text)
        self.setToolTip(text)
        self._led.setStyleSheet(
            "#RuntimeBuildIndicatorLed {"
            f"background: {color};"
            f"border: 1px solid {color};"
            "border-radius: 5px;"
            "}"
        )


class FloatingToolIsland(QWidget):
    """Top-docked schematic control island."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("FloatingToolIsland")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._apply_theme_colors(None)
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

    def _apply_theme_colors(self, theme):
        if theme is None:
            bg = QColor(38, 38, 42, 215)
            border = QColor(120, 120, 130, 180)
        else:
            bg = QColor(theme.bg)
            bg.setAlpha(220)
            border = QColor(theme.text)
            border.setAlpha(130)
        self.setStyleSheet(
            "#FloatingToolIsland {"
            f"background: rgba({bg.red()}, {bg.green()}, {bg.blue()}, {bg.alpha()});"
            f"border: 1px solid rgba({border.red()}, {border.green()}, {border.blue()}, {border.alpha()});"
            "border-radius: 12px;"
            "}"
        )

    def apply_theme(self, theme):
        self._apply_theme_colors(theme)

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
            self._layout_horizontal_wrapped()
        self._layout_orientation = orientation
        self.setMinimumSize(0, 0)
        self.resize(self.sizeHint())
        self.adjustSize()
        self._clamp_to_parent()

    def _layout_horizontal_wrapped(self):
        """Place controls in 1..N rows based on available parent width."""
        p = self.parentWidget()
        available = max(320, (p.width() - 12) if p is not None else 1200)
        hspace = int(self._layout.horizontalSpacing() if self._layout.horizontalSpacing() >= 0 else 6)

        def place_for_budget(budget: int):
            while self._layout.count():
                item = self._layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    self._layout.removeWidget(w)
            row = 0
            col = 0
            used = 0
            for w in self._widgets:
                hint_w = max(24, int(w.minimumSizeHint().width()))
                if col > 0 and (used + hspace + hint_w) > budget:
                    row += 1
                    col = 0
                    used = 0
                self._layout.addWidget(w, row, col)
                w.show()
                used += (hspace if col > 0 else 0) + hint_w
                col += 1

        # Retry with tighter budgets until full island width fits viewport.
        budget = max(220, available - 20)
        for _ in range(5):
            place_for_budget(budget)
            self.adjustSize()
            if self.sizeHint().width() <= available:
                break
            budget = max(180, budget - 40)

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
                if self._layout_orientation == "h":
                    self._rebuild_layout("h")
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


class SchematicPowerOverlay(QWidget):
    """Compact always-visible supply controls and monitor for the schematic view."""

    def __init__(self, backend, parent: QWidget):
        super().__init__(parent)
        self.backend = backend
        self.setObjectName("SchematicPowerOverlay")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._theme_is_dark = True
        self._managed_by_layout = False
        self._compact = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.controls = QFrame(self)
        self.controls.setObjectName("PowerControlsOverlay")
        ctrl = QGridLayout(self.controls)
        ctrl.setContentsMargins(6, 3, 6, 3)
        ctrl.setHorizontalSpacing(4)
        ctrl.setVerticalSpacing(4)

        self.master = QToolButton()
        self.master.setText("Master")
        self.master.setCheckable(True)
        self.master.setFixedWidth(68)

        self.v_pos_label = QLabel("V+")
        self.v_neg_label = QLabel("V-")

        self.v_pos = QDoubleSpinBox()
        self.v_pos.setRange(0.0, 5.0)
        self.v_pos.setDecimals(3)
        self.v_pos.setSingleStep(0.1)
        self.v_pos.setValue(1.0)
        self.v_pos.setSuffix(" V")
        self.v_pos.setFixedWidth(82)
        self.v_pos_slider = QSlider(Qt.Horizontal)
        self.v_pos_slider.setRange(0, 5000)
        self.v_pos_slider.setValue(1000)
        self.v_pos_slider.setFixedWidth(86)

        self.v_neg = QDoubleSpinBox()
        self.v_neg.setRange(-5.0, 0.0)
        self.v_neg.setDecimals(3)
        self.v_neg.setSingleStep(0.1)
        self.v_neg.setValue(-1.0)
        self.v_neg.setSuffix(" V")
        self.v_neg.setFixedWidth(82)
        self.v_neg_slider = QSlider(Qt.Horizontal)
        self.v_neg_slider.setRange(0, 5000)
        self.v_neg_slider.setValue(1000)
        self.v_neg_slider.setFixedWidth(86)

        ctrl.addWidget(self.master, 0, 0)
        ctrl.addWidget(self.v_pos_label, 0, 1)
        ctrl.addWidget(self.v_pos, 0, 2)
        ctrl.addWidget(self.v_pos_slider, 0, 3)
        ctrl.addWidget(self.v_neg_label, 0, 4)
        ctrl.addWidget(self.v_neg, 0, 5)
        ctrl.addWidget(self.v_neg_slider, 0, 6)
        self.controls.setMinimumSize(self.controls.sizeHint())
        root.addWidget(self.controls)

        self.monitor = QFrame(self)
        self.monitor.setObjectName("PowerMonitorOverlay")
        mon = QGridLayout(self.monitor)
        mon.setContentsMargins(8, 5, 8, 5)
        mon.setHorizontalSpacing(8)
        mon.setVerticalSpacing(3)
        self.m_vp_name = QLabel("V+")
        self.m_vn_name = QLabel("V-")
        self.m_usb_v_name = QLabel("USB V")
        self.m_usb_i_name = QLabel("USB I")
        self.m_aux_v_name = QLabel("AUX V")
        self.m_aux_i_name = QLabel("AUX I")
        self.m_vp = self._metric("--")
        self.m_vn = self._metric("--")
        self.m_usb_v = self._metric("--")
        self.m_usb_i = self._metric("--")
        self.m_aux_v = self._metric("--")
        self.m_aux_i = self._metric("--")
        self._monitor_items = [
            (self.m_vp_name, self.m_vp),
            (self.m_vn_name, self.m_vn),
            (self.m_usb_v_name, self.m_usb_v),
            (self.m_usb_i_name, self.m_usb_i),
            (self.m_aux_v_name, self.m_aux_v),
            (self.m_aux_i_name, self.m_aux_i),
        ]
        for col, (name, lbl) in enumerate(
            self._monitor_items
        ):
            mon.addWidget(name, 0, col * 2)
            mon.addWidget(lbl, 0, col * 2 + 1)
        self.monitor.setMinimumSize(self.monitor.sizeHint())
        root.addWidget(self.monitor)
        self.controls.setParent(parent)
        self.monitor.setParent(parent)

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(200)
        self._apply_timer.timeout.connect(lambda: self._apply_config(quiet=True))
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self.refresh_status)

        self.master.toggled.connect(self._on_master_toggled)
        self.v_pos_slider.valueChanged.connect(lambda v: self.v_pos.setValue(float(v) / 1000.0))
        self.v_neg_slider.valueChanged.connect(lambda v: self.v_neg.setValue(-float(v) / 1000.0))
        self.v_pos.valueChanged.connect(lambda v: self._sync_slider(self.v_pos_slider, v))
        self.v_neg.valueChanged.connect(lambda v: self._sync_slider(self.v_neg_slider, -v))
        self.v_pos.valueChanged.connect(self._schedule_apply)
        self.v_neg.valueChanged.connect(self._schedule_apply)

        self.apply_theme(None)
        self.sync_from_backend()
        self.refresh_status()
        self._poll_timer.start()
        self.setGeometry(0, 0, 0, 0)
        parent.installEventFilter(self)
        QTimer.singleShot(0, self.reposition)

    def _metric(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setMinimumWidth(58)
        return lbl

    def set_compact(self, compact: bool):
        compact = bool(compact)
        if self._compact == compact:
            return
        self._compact = compact
        self.v_pos_slider.setVisible(not compact)
        self.v_neg_slider.setVisible(not compact)
        self.v_pos_slider.setFixedWidth(52 if compact else 86)
        self.v_neg_slider.setFixedWidth(52 if compact else 86)
        for widget in (self.m_aux_v_name, self.m_aux_v, self.m_aux_i_name, self.m_aux_i):
            widget.setVisible(not compact)
        for lbl in (self.m_vp, self.m_vn, self.m_usb_v, self.m_usb_i, self.m_aux_v, self.m_aux_i):
            lbl.setMinimumWidth(44 if compact else 58)
        self.controls.setMinimumSize(0, 0)
        self.monitor.setMinimumSize(0, 0)
        self.controls.adjustSize()
        self.monitor.adjustSize()
        self.controls.setMinimumSize(self.controls.sizeHint())
        self.monitor.setMinimumSize(self.monitor.sizeHint())

    def _sync_slider(self, slider: QSlider, value_v: float):
        value = int(round(float(value_v) * 1000.0))
        if slider.value() == value:
            return
        slider.blockSignals(True)
        slider.setValue(value)
        slider.blockSignals(False)

    def _params(self) -> dict:
        return {
            "master_enabled": bool(self.master.isChecked()),
            "v_pos_v": float(self.v_pos.value()),
            "v_neg_v": float(self.v_neg.value()),
            "tracking": False,
            "power_limit_w": 2.5,
        }

    def _schedule_apply(self, *_args):
        self._apply_timer.start()

    def _on_master_toggled(self, checked: bool):
        self._apply_timer.stop()
        self._apply_config(quiet=True)
        self._style_master(bool(checked))

    def _apply_config(self, quiet: bool = False):
        ok, _msg = self.backend.configure_supplies(**self._params())
        self._style_master(bool(self.master.isChecked()))
        self.refresh_status()
        return ok

    def refresh_status(self):
        ok, _msg, st = self.backend.read_supplies_status()
        if not ok:
            return
        self.m_vp.setText(f"{float(st.get('v_pos_meas_v', 0.0)):+.3f} V")
        self.m_vn.setText(f"{float(st.get('v_neg_meas_v', 0.0)):+.3f} V")
        self.m_usb_v.setText(f"{float(st.get('usb_voltage_v', 0.0)):.3f} V")
        self.m_usb_i.setText(f"{float(st.get('usb_current_a', 0.0)) * 1e3:.1f} mA")
        self.m_aux_v.setText(f"{float(st.get('aux_voltage_v', 0.0)):.3f} V")
        self.m_aux_i.setText(f"{float(st.get('aux_current_a', 0.0)) * 1e3:.1f} mA")
        self._style_master(bool(self.master.isChecked()))
        self._style_rail_slider(
            self.v_pos_slider,
            target_v=float(self.v_pos.value()),
            measured_v=float(st.get("v_pos_meas_v", 0.0)),
            master_on=bool(self.master.isChecked()),
        )
        self._style_rail_slider(
            self.v_neg_slider,
            target_v=float(self.v_neg.value()),
            measured_v=float(st.get("v_neg_meas_v", 0.0)),
            master_on=bool(self.master.isChecked()),
        )

    def sync_from_backend(self):
        ok, _msg, st = self.backend.read_supplies_status()
        if not ok:
            return
        for widget in (self.master, self.v_pos, self.v_neg):
            widget.blockSignals(True)
        try:
            self.master.setChecked(bool(st.get("master_enabled", False)))
            self.v_pos.setValue(float(st.get("v_pos_v", self.v_pos.value())))
            self.v_neg.setValue(float(st.get("v_neg_v", self.v_neg.value())))
            self._sync_slider(self.v_pos_slider, float(self.v_pos.value()))
            self._sync_slider(self.v_neg_slider, -float(self.v_neg.value()))
        finally:
            for widget in (self.master, self.v_pos, self.v_neg):
                widget.blockSignals(False)
        self.refresh_status()

    def on_connection_changed(self):
        self.sync_from_backend()
        self.refresh_status()

    def shutdown(self):
        self._poll_timer.stop()
        self._apply_timer.stop()

    def reposition(self):
        if self._managed_by_layout:
            return
        p = self.parentWidget()
        if p is None:
            return
        margin = 8
        self.controls.adjustSize()
        self.monitor.adjustSize()
        self.adjustSize()
        ctrl_w = self.controls.sizeHint().width()
        ctrl_h = self.controls.sizeHint().height()
        mon_w = self.monitor.sizeHint().width()
        mon_h = self.monitor.sizeHint().height()
        toolbar = getattr(p, "_hardware_toolbar", None)
        if toolbar is not None and toolbar.isVisible():
            tgeo = toolbar.geometry()
            top_y = tgeo.y() + max(0, int((tgeo.height() - ctrl_h) / 2))
        else:
            top_y = margin
        status = p.statusBar() if hasattr(p, "statusBar") else None
        if status is not None and status.isVisible():
            sgeo = status.geometry()
            bottom_y = min(
                sgeo.y() + max(0, int((sgeo.height() - mon_h) / 2)),
                max(margin, p.height() - mon_h - 2),
            )
        else:
            bottom_y = max(margin, p.height() - mon_h - margin)
        self.controls.setGeometry(max(margin, p.width() - ctrl_w - margin), top_y, ctrl_w, ctrl_h)
        self.monitor.setGeometry(max(margin, p.width() - mon_w - margin), bottom_y, mon_w, mon_h)
        self.controls.raise_()
        self.monitor.raise_()

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() in (QEvent.Resize, QEvent.Show):
            QTimer.singleShot(0, self.reposition)
        return super().eventFilter(obj, event)

    def _style_master(self, enabled: bool):
        if enabled:
            bg = "#1f6f3f" if self._theme_is_dark else "#ccefd7"
            fg = "#e9fff0" if self._theme_is_dark else "#143e22"
            border = "#46a765"
            padding = "5px 7px 3px 9px"
            inset = "border-top-color: #123d22; border-left-color: #123d22;"
        else:
            bg = "#4a3030" if self._theme_is_dark else "#f1d0d0"
            fg = "#ffe8e8" if self._theme_is_dark else "#5e2222"
            border = "#875353"
            padding = "4px 8px"
            inset = ""
        self.master.setStyleSheet(
            f"QToolButton {{ background: {bg}; color: {fg}; border: 1px solid {border}; "
            f"border-radius: 3px; padding: {padding}; font-weight: 700; {inset} }}"
            "QToolButton:pressed { padding: 5px 7px 3px 9px; "
            "border-top-color: #123d22; border-left-color: #123d22; }"
        )

    def _style_rail_slider(self, slider: QSlider, target_v: float, measured_v: float, master_on: bool):
        target_abs = abs(float(target_v))
        error = abs(float(target_v) - float(measured_v))
        if not master_on or target_abs <= 0.01:
            rail = "#5b5d66" if self._theme_is_dark else "#b8bec8"
            handle = "#8b8e98" if self._theme_is_dark else "#747b86"
        elif error <= max(0.05, target_abs * 0.04):
            rail = "#1fa64a" if self._theme_is_dark else "#48b96a"
            handle = "#8df0a4" if self._theme_is_dark else "#256f3b"
        else:
            rail = "#b88622" if self._theme_is_dark else "#d39a26"
            handle = "#ffd26a" if self._theme_is_dark else "#805a11"
        slider.setStyleSheet(
            f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: {rail};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {rail};
                border-radius: 2px;
            }}
            QSlider::add-page:horizontal {{
                background: rgba(90, 92, 100, 0.45);
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {handle};
                border: 1px solid {rail};
                width: 16px;
                margin: -6px 0;
                border-radius: 8px;
            }}
            """
        )

    def apply_theme(self, theme):
        self._theme_is_dark = bool(getattr(theme, "name", "dark") == "dark")
        if self._theme_is_dark:
            panel_bg = "rgba(40,40,44,215)"
            border = "#696a72"
            fg = "#eeeeee"
            metric_bg = "#17181c"
            metric_border = "#30323a"
        else:
            panel_bg = "rgba(246,247,249,232)"
            border = "#aeb4bf"
            fg = "#252a33"
            metric_bg = "#ffffff"
            metric_border = "#c8ced8"
        self.setStyleSheet(
            f"""
            QFrame#PowerControlsOverlay,
            QFrame#PowerMonitorOverlay {{
                background: {panel_bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QLabel {{
                color: {fg};
                font-weight: 600;
            }}
            QLabel#PowerMetric {{
                background: {metric_bg};
                border: 1px solid {metric_border};
                border-radius: 3px;
                padding: 2px 6px;
                font-weight: 500;
            }}
            QDoubleSpinBox {{
                background: {metric_bg};
                color: {fg};
                border: 1px solid {metric_border};
                min-height: 22px;
            }}
            """
        )
        for lbl in (self.m_vp, self.m_vn, self.m_usb_v, self.m_usb_i, self.m_aux_v, self.m_aux_i):
            lbl.setObjectName("PowerMetric")
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)
        self._style_master(bool(self.master.isChecked()))
        self.refresh_status()


class MainWindow(QMainWindow):
    """Application shell wiring scene, docks, menus, and file operations."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NodeZilla (Beta) V1.0.4")
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        if avail is not None and avail.isValid():
            self.resize(min(1400, int(avail.width() * 0.94)), min(850, int(avail.height() * 0.90)))
        else:
            self.resize(1100, 720)
        self._did_initial_screen_fit = False
        self._screen_fit_hooked = False
        # Runtime netlist outputs for external automation/gizmo flows.
        self.runtime_spice_netlist_text: str = ""
        self.runtime_spice_netlist_path: str = ""
        self._clipboard_payload: dict | None = None
        self._paste_serial: int = 0
        self._pending_pl_component_id: str | None = None
        self._pending_pl_source_id: int = -1
        self._is_closing = False

        self.tabs = QTabWidget()
        self.status_label = QLabel("Ready")
        self.runtime_build_indicator = RuntimeBuildIndicator(self)
        self.undo_stack = QUndoStack(self)

        self._watcher = ThemeWatcher(QApplication.instance(), self._apply_theme)
        self.backend = make_backend()

        self.schematic_tab = SchematicTab(self.status_label, self.undo_stack)
        self._active_scene = self.schematic_tab.scene
        self._active_view = self.schematic_tab.view
        self._chip_editors: list[ChipEditorDialog] = []
        self._scope_window: ScopeWindow | None = None
        self._wavegen_windows: dict[int, WavegenWindow] = {}
        self._wavegen_state = {
            1: {
                "enabled": False,
                "waveform": "sine",
                "frequency_hz": 1000.0,
                "amplitude_v": 1.0,
                "offset_v": 0.0,
                "symmetry_pct": 50.0,
                "phase_deg": 0.0,
            },
            2: {
                "enabled": False,
                "waveform": "sine",
                "frequency_hz": 1000.0,
                "amplitude_v": 1.0,
                "offset_v": 0.0,
                "symmetry_pct": 50.0,
                "phase_deg": 0.0,
            },
        }
        self.component_library = load_component_library()
        theme = self._watcher.current_theme()
        self._apply_theme(theme)
        self.hardware_builder_tab = QWidget()
        self.hardware_builder_tab_layout = QVBoxLayout(self.hardware_builder_tab)
        self.hardware_builder_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs.addTab(self.schematic_tab, "Schematic")
        self.tabs.addTab(self.hardware_builder_tab, "Hardware Configuration")
        self.setCentralWidget(self.tabs)
        self._build_hardware_toolbar()
        sb = QStatusBar()
        sb.addWidget(self.hw_status)
        sb.addWidget(self.status_label)
        self.setStatusBar(sb)

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

        self.hardware_builder_panel = HardwareBuilderPanel()
        self.hardware_builder_panel.pl_generated.connect(self._on_pl_generated)
        self.hardware_builder_scroll = QScrollArea()
        self.hardware_builder_scroll.setWidgetResizable(True)
        self.hardware_builder_scroll.setFrameShape(QScrollArea.NoFrame)
        self.hardware_builder_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.hardware_builder_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.hardware_builder_scroll.setWidget(self.hardware_builder_panel)
        self.hardware_builder_tab_layout.addWidget(self.hardware_builder_scroll)

        self.component_panel = ComponentPanel(self.component_library)
        self.component_panel.place_requested.connect(self._set_mode_place_active)
        component_dock = QDockWidget("Components", self)
        component_dock.setWidget(self.component_panel)
        component_dock.setObjectName("ComponentsDock")
        component_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, component_dock)
        self.component_dock = component_dock

        self.project_explorer_panel = ProjectExplorerPanel(user_projects_dir(), user_examples_dir())
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
        self.schematic_tab.scene.request_open_chip = self._open_chip_editor_for_component
        self.schematic_tab.scene.request_open_scope = self._open_scope_window_for_component
        self.schematic_tab.scene.request_open_wavegen = self._open_wavegen_window_for_component
        self.schematic_tab.scene.selectionChanged.connect(self._on_selection_changed)
        self.schematic_tab.scene.component_placed.connect(self._on_component_placed)
        self.schematic_tab.view.viewport().installEventFilter(self)

        self._build_toolbar()
        self._build_schematic_island()
        self._build_schematic_power_overlay()
        self._build_menu()
        self._build_schematic_shortcuts()

        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.props_dock.toggleViewAction())
        view_menu.addAction(self.nets_dock.toggleViewAction())
        view_menu.addAction(self.component_dock.toggleViewAction())
        view_menu.addAction(self.project_explorer_dock.toggleViewAction())
        view_menu.addAction(self.live_netlist_dock.toggleViewAction())
        view_menu.addAction(self.pl_dock.toggleViewAction())

        # Keep schematic-only docks hidden outside the Schematic tab.
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
        self._fit_window_to_screen()

        edit_menu = self.menuBar().addMenu("Edit")
        undo_act = self.undo_stack.createUndoAction(self, "Undo")
        undo_act.setShortcut(QKeySequence.Undo)
        redo_act = self.undo_stack.createRedoAction(self, "Redo")
        redo_act.setShortcut(QKeySequence.Redo)
        edit_menu.addAction(undo_act)
        edit_menu.addAction(redo_act)

        self._install_component_shortcuts()


    def _on_pl_generated(self, pl_path: str):
        self.status_label.setText(f"PL generated: {pl_path}")
        try:
            self.pl_panel.refresh()
            self._refresh_pl_used_flags()
        except Exception:
            pass

    def _apply_theme(self, theme):
        """Apply theme to scene items (deferred safely until UI exists)."""
        if getattr(self, "_theme_apply_in_progress", False):
            return
        # If UI not ready yet, try again on the next event loop tick
        if not hasattr(self, "schematic_tab") or self.schematic_tab is None:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._apply_theme(theme))
            return
        self._theme_apply_in_progress = True
        try:
            self.schematic_tab.scene.apply_theme(theme)
            if hasattr(self, "_schematic_island") and self._schematic_island is not None:
                self._schematic_island.apply_theme(theme)
            if hasattr(self, "_schematic_power_overlay") and self._schematic_power_overlay is not None:
                self._schematic_power_overlay.apply_theme(theme)
            for panel_attr in ("scope_panel", "wavegen_panel", "supplies_panel"):
                panel = getattr(self, panel_attr, None)
                if panel is not None and hasattr(panel, "apply_theme"):
                    try:
                        panel.apply_theme(theme)
                    except Exception:
                        pass
            scope_window = getattr(self, "_scope_window", None)
            if scope_window is not None and scope_window.isVisible():
                try:
                    scope_window.apply_theme(theme)
                except Exception:
                    pass
            for wavegen_window in list(getattr(self, "_wavegen_windows", {}).values()):
                try:
                    if wavegen_window is not None and wavegen_window.isVisible():
                        wavegen_window.apply_theme(theme)
                except Exception:
                    pass
            for dlg in list(getattr(self, "_chip_editors", [])):
                try:
                    if dlg is not None and dlg.isVisible():
                        dlg.apply_theme(theme)
                except Exception:
                    continue
        finally:
            self._theme_apply_in_progress = False

    def eventFilter(self, obj, event):
        if obj is self.schematic_tab.view.viewport():
            if event.type() in (QEvent.MouseButtonPress, QEvent.FocusIn):
                self._set_active_schematic_context(self.schematic_tab.scene, self.schematic_tab.view)
        return super().eventFilter(obj, event)

    def _set_active_schematic_context(self, scene, view):
        self._active_scene = scene
        self._active_view = view
        try:
            self._sync_grid_controls_from_scene(scene)
        except Exception:
            pass

    def _scene_for_controls(self):
        return self._active_scene if self._is_schematic_active() else self.schematic_tab.scene

    def _view_for_controls(self):
        return self._active_view if self._is_schematic_active() else self.schematic_tab.view

    def _sync_grid_controls_from_scene(self, sc):
        if sc is None:
            return
        gv = int(getattr(sc, "grid_size", 20) or 20)
        if hasattr(self, "_grid_spin") and self._grid_spin.value() != gv:
            self._grid_spin.blockSignals(True)
            self._grid_spin.setValue(gv)
            self._grid_spin.blockSignals(False)
        if hasattr(self, "_island_grid_spin") and self._island_grid_spin.value() != gv:
            self._island_grid_spin.blockSignals(True)
            self._island_grid_spin.setValue(gv)
            self._island_grid_spin.blockSignals(False)

    def _on_tab_changed(self, index: int):
        """Show schematic docks only on the Schematic tab."""
        current = self.tabs.widget(index)
        on_schematic = current is self.schematic_tab
        if hasattr(self, "_schematic_toolbar") and self._schematic_toolbar is not None:
            self._schematic_toolbar.setVisible(False)
        if hasattr(self, "_schematic_island") and self._schematic_island is not None:
            self._schematic_island.setVisible(on_schematic)
        if hasattr(self, "_schematic_power_overlay") and self._schematic_power_overlay is not None:
            self._schematic_power_overlay.controls.setVisible(True)
            self._schematic_power_overlay.monitor.setVisible(True)
            self._schematic_power_overlay.reposition()
        if on_schematic:
            self._sync_grid_controls_from_scene(self._scene_for_controls())
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
            QTimer.singleShot(0, self._fit_window_to_screen)
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
        QTimer.singleShot(0, self._fit_window_to_screen)
        QTimer.singleShot(120, self._fit_window_to_screen)

    # selection → props
    def _on_selection_changed(self):
        """Push active selection into the properties panel."""
        if getattr(self, "_is_closing", False):
            return
        try:
            selected = self._scene_for_controls().selectedItems()
        except RuntimeError:
            return
        texts = [it for it in selected if isinstance(it, CommentTextItem)]
        if texts:
            self.props_panel.show_text(texts[0])
            return
        wires = [it for it in selected if isinstance(it, WireItem)]
        if wires:
            self.props_panel.show_wire(wires[0])
            return
        comps = [it for it in selected if isinstance(it, ComponentItem)]
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
        comp_id = str(payload.get("id", "-1"))
        kind = str(payload.get("kind", "") or "").strip()
        if not kind:
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
        refdes_override = str(payload.get("base_refdes", "") or pl_name).strip()
        self.tabs.setCurrentWidget(self.schematic_tab)
        self.schematic_tab.scene.set_mode_place(kind)
        self.schematic_tab.scene.set_place_overrides(refdes=refdes_override, value=value_or_part)
        self._pending_pl_component_id = comp_id
        self._pending_pl_source_id = int(payload.get("source_id", -1))
        self.statusBar().showMessage(
            f"PL row {comp_id} selected: place {kind} as {pl_name}",
            4000,
        )

    def _on_component_placed(self, _comp: ComponentItem):
        if self._pending_pl_source_id >= 0:
            setattr(_comp, "_pl_source_id", int(self._pending_pl_source_id))
        self._refresh_pl_used_flags()
        self._pending_pl_component_id = None
        self._pending_pl_source_id = -1

    def _open_chip_editor_for_component(self, comp: ComponentItem):
        dlg = ChipEditorDialog(comp, self)
        dlg.scene.request_properties = self._show_properties_for
        dlg.scene.request_open_chip = self._open_chip_editor_for_component
        dlg.scene.request_open_scope = self._open_scope_window_for_component
        dlg.scene.request_open_wavegen = self._open_wavegen_window_for_component
        dlg.scene.selectionChanged.connect(self._on_selection_changed)
        dlg.activated.connect(self._set_active_schematic_context)
        dlg.closed.connect(self._on_chip_editor_closed)
        self._chip_editors.append(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _open_scope_window_for_component(self, comp: ComponentItem):
        ref = (getattr(comp, "refdes", "") or getattr(comp, "value", "") or "Scope").strip()
        if self._scope_window is None:
            self._scope_window = ScopeWindow(self.backend, self, "Oscilloscope")
            self._scope_window.destroyed.connect(lambda *_: setattr(self, "_scope_window", None))
            try:
                self._scope_window.apply_theme(self._watcher.current_theme())
            except Exception:
                pass
        self._scope_window.setWindowTitle(f"Oscilloscope - {ref}")
        self._scope_window.show()
        self._scope_window.raise_()
        self._scope_window.activateWindow()

    def _wavegen_channel_for_component(self, comp: ComponentItem) -> int:
        ref = (getattr(comp, "refdes", "") or "").strip().upper()
        digits = "".join(ch for ch in ref if ch.isdigit())
        if digits:
            try:
                return 2 if int(digits) == 2 else 1
            except Exception:
                pass
        return 1

    def _open_wavegen_window_for_component(self, comp: ComponentItem):
        ref = (getattr(comp, "refdes", "") or getattr(comp, "value", "") or "Wavegen").strip()
        channel = self._wavegen_channel_for_component(comp)
        window = self._wavegen_windows.get(channel)
        if window is None:
            window = WavegenWindow(
                self.backend,
                channel=channel,
                shared_state=self._wavegen_state,
                parent=self,
                title=f"Wave Generator - W{channel}",
            )
            self._wavegen_windows[channel] = window
            window.destroyed.connect(lambda *_args, ch=channel: self._wavegen_windows.pop(ch, None))
            try:
                window.apply_theme(self._watcher.current_theme())
            except Exception:
                pass
        window.setWindowTitle(f"Wave Generator - {ref}")
        window.show()
        window.raise_()
        window.activateWindow()

    def _on_chip_editor_closed(self, dlg):
        self._chip_editors = [d for d in self._chip_editors if d is not dlg]
        if self._active_scene is getattr(dlg, "scene", None):
            self._set_active_schematic_context(self.schematic_tab.scene, self.schematic_tab.view)
            self._on_selection_changed()
        # Chip internals are saved on close; refresh panels that depend on netlist.
        self._refresh_live_spice_panel()
        self._refresh_pl_used_flags()

    def _set_mode_place_active(self, kind: str):
        sc = self._scene_for_controls()
        if sc is not None:
            sc.set_mode_place(kind)

    def _start_place_chip(self):
        sc = self._scene_for_controls()
        if sc is None:
            return
        pin_count, ok = QInputDialog.getInt(
            self,
            "Create Chip",
            "Number of pins:",
            4,
            2,
            128,
            1,
        )
        if not ok:
            return
        sc.set_mode_place_chip(pin_count)

    @staticmethod
    def _chip_port_defs(pin_count: int):
        n = max(2, int(pin_count))
        left_n = (n + 1) // 2
        right_n = n - left_n
        span_left = max(40.0, (left_n - 1) * 16.0) if left_n > 1 else 0.0
        span_right = max(40.0, (right_n - 1) * 16.0) if right_n > 1 else 0.0
        left_top = -span_left / 2.0
        right_top = -span_right / 2.0
        left_step = (span_left / float(left_n - 1)) if left_n > 1 else 0.0
        right_step = (span_right / float(right_n - 1)) if right_n > 1 else 0.0
        ports = []
        idx = 1
        for i in range(left_n):
            ports.append({"name": str(idx), "x": -50.0, "y": float(left_top + i * left_step)})
            idx += 1
        for i in range(right_n):
            ports.append({"name": str(idx), "x": 50.0, "y": float(right_top + i * right_step)})
            idx += 1
        return ports

    def _save_selected_chip_as_reusable(self):
        sc = self._scene_for_controls()
        if sc is None:
            return
        chips = [
            it for it in sc.selectedItems()
            if isinstance(it, ComponentItem) and hasattr(it, "is_chip") and it.is_chip()
        ]
        if not chips:
            QMessageBox.information(self, "Save Chip", "Select one chip to save as reusable component.")
            return
        chip = chips[0]
        default_name = (chip.value or chip.refdes or "MyChip").strip()
        display_name, ok = QInputDialog.getText(
            self,
            "Save Chip as Reusable",
            "Display name:",
            text=default_name,
        )
        if not ok:
            return
        display_name = display_name.strip()
        if not display_name:
            QMessageBox.warning(self, "Save Chip", "Display name cannot be empty.")
            return

        slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in display_name).strip("_")
        if not slug:
            slug = "chip"
        kind = f"Chip_{slug}"

        assets_root = user_assets_root()
        chips_root = assets_root / "chips" / "library"
        comps_root = assets_root / "components" / "library" / "Hierarchy" / "Chips"
        chips_root.mkdir(parents=True, exist_ok=True)
        comps_root.mkdir(parents=True, exist_ok=True)

        template_rel = f"library/{slug}.json"
        template_path = chips_root / f"{slug}.json"
        comp_path = comps_root / f"{slug}.json"

        chip_internal = chip.chip_data() if hasattr(chip, "chip_data") else {}
        if not isinstance(chip_internal, dict):
            chip_internal = {}
        if "components" not in chip_internal:
            chip_internal = sc.serialize()

        pin_count = chip.chip_pin_count() if hasattr(chip, "chip_pin_count") else max(2, len(getattr(chip, "ports", [])))
        if isinstance(chip_internal, dict):
            chip_internal = dict(chip_internal)
            chip_internal["io"] = {"pins": int(pin_count)}

        component_def = {
            "kind": kind,
            "display_name": display_name,
            "category": "Hierarchy / Chips",
            "prefix": "U",
            "spice_type": "X",
            "value_label": "Part Number",
            "show_value": True,
            "default_value": display_name,
            "type": "component",
            "is_chip": True,
            "chip_template": template_rel,
            "visible": True,
            "symbol": "defaults/chip.json",
            "auto_align_terminals": False,
            "auto_scale_symbol": False,
            "ports": self._chip_port_defs(int(pin_count)),
        }

        try:
            template_path.write_text(json.dumps(chip_internal, indent=2))
            comp_path.write_text(json.dumps(component_def, indent=2))
        except Exception as e:
            QMessageBox.critical(self, "Save Chip", f"Failed to save reusable chip: {e}")
            return

        self.component_library = load_component_library(force_reload=True)
        self.component_panel.reload_library()
        self._install_component_shortcuts()
        self.statusBar().showMessage(f"Reusable chip saved: {display_name}", 4000)

    def _refresh_pl_used_flags(self):
        # Use flattened netlist components so chip internals count as used parts.
        from .netlist_exporter import NetlistBuilder
        try:
            comps = NetlistBuilder().build(self.schematic_tab.scene).components
        except Exception:
            comps = [it for it in self.schematic_tab.scene.items() if isinstance(it, ComponentItem)]
        if hasattr(self, "pl_panel") and self.pl_panel is not None:
            self.pl_panel.sync_used_from_components(comps)

    def _verify_pl_availability(self):
        if not hasattr(self, "pl_panel") or self.pl_panel is None:
            return
        # Verify from generated netlist (flattened), not from raw scene items.
        from .netlist_exporter import NetlistBuilder
        netlist = NetlistBuilder().build(self.schematic_tab.scene)
        placed_counts = {}
        placed_refs = {}
        placed_multipart_groups = {}
        for c in netlist.components:
            family = str(getattr(c, "multipart_family", "") or "").strip()
            package_ref = str(getattr(c, "package_refdes", "") or getattr(c, "refdes", "") or "").strip()
            if family and package_ref:
                placed_multipart_groups.setdefault((package_ref, family), c)
                continue
            sig = self.pl_panel.signature_from_kind_value(getattr(c, "kind", ""), getattr(c, "value", ""))
            placed_counts[sig] = int(placed_counts.get(sig, 0)) + 1
            placed_refs.setdefault(sig, []).append(str(getattr(c, "refdes", "")).strip() or "?")

        for (_package_ref, _family), comp in placed_multipart_groups.items():
            sig = self.pl_panel.physical_component_signature(comp)
            placed_counts[sig] = int(placed_counts.get(sig, 0)) + 1
            placed_refs.setdefault(sig, []).append(str(getattr(comp, "package_refdes", "") or getattr(comp, "refdes", "")).strip() or "?")

        shortages = []
        for sig, placed in placed_counts.items():
            requested = int(self.pl_panel.requested_physical_count_for_signature(sig))
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
            t_name = "multipart" if str(sig[0]) == "multipart" else str(sig[0] or "component")
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
        sc = self._scene_for_controls()
        if kind == "text":
            texts = [it for it in sc.selectedItems() if isinstance(it, CommentTextItem)]
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
            wires = [it for it in sc.selectedItems() if isinstance(it, WireItem)]
            for w in wires:
                if hasattr(w, "set_wire_color"):
                    w.set_wire_color(wire_color or None)
            return
        comps = [it for it in sc.selectedItems() if isinstance(it, ComponentItem)]
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
        self.hw_devices.setMinimumWidth(130)
        self.hw_devices.setMaximumWidth(270)

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
        for p in (
            getattr(self, "scope_panel", None),
            getattr(self, "wavegen_panel", None),
            getattr(self, "supplies_panel", None),
        ):
            if p is None:
                continue
            if hasattr(p, "on_connection_changed"):
                p.on_connection_changed()
        if hasattr(self, "_schematic_power_overlay") and self._schematic_power_overlay is not None:
            self._schematic_power_overlay.on_connection_changed()
        if _connected:
            # Do not auto-enable supplies on connect; user controls master power.
            self._sync_supplies_docks()
        self._refresh_hardware_devices()

    def _sync_supplies_docks(self):
        if hasattr(self, "supplies_panel") and self.supplies_panel is not None:
            if hasattr(self.supplies_panel, "sync_from_backend"):
                self.supplies_panel.sync_from_backend()
        if hasattr(self, "_schematic_power_overlay") and self._schematic_power_overlay is not None:
            self._schematic_power_overlay.sync_from_backend()

    def _shutdown_instrument_panels(self):
        for p in (
            getattr(self, "scope_panel", None),
            getattr(self, "wavegen_panel", None),
            getattr(self, "supplies_panel", None),
            getattr(self, "_schematic_power_overlay", None),
        ):
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

    def _available_screen_rect(self):
        screen = self.windowHandle().screen() if self.windowHandle() is not None else None
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else self.geometry()

    def _fit_window_to_screen(self):
        """Clamp window size/position to current display so UI never opens off-screen."""
        # In maximized/fullscreen we should not cap the window size.
        if self.isMaximized() or self.isFullScreen():
            self.setMaximumSize(QSize(16777215, 16777215))
            return
        avail = self._available_screen_rect()
        if not avail.isValid():
            return
        max_w = min(int(avail.width()), int(avail.width() * 0.97))
        max_h = min(int(avail.height()), int(avail.height() * 0.96))
        max_w = max(480, max_w)
        max_h = max(380, max_h)
        # Hard cap against size-hint growth from dock contents.
        self.setMaximumSize(max_w, max_h)
        target_w = min(self.width(), max_w)
        target_h = min(self.height(), max_h)
        self.resize(target_w, target_h)
        # Keep top-left within visible area.
        x = min(max(self.x(), avail.left()), max(avail.left(), avail.right() - self.width() + 1))
        y = min(max(self.y(), avail.top()), max(avail.top(), avail.bottom() - self.height() + 1))
        self.move(x, y)
        self._update_responsive_chrome()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            if self.isMaximized() or self.isFullScreen():
                self.setMaximumSize(QSize(16777215, 16777215))
            else:
                QTimer.singleShot(0, self._fit_window_to_screen)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_schematic_power_overlay"):
            self._update_responsive_chrome()

    def _ensure_screen_fit_hooks(self):
        if self._screen_fit_hooked:
            return
        wh = self.windowHandle()
        if wh is None:
            return
        wh.screenChanged.connect(lambda _screen: QTimer.singleShot(0, self._fit_window_to_screen))
        self._screen_fit_hooked = True

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_screen_fit_hooks()
        if not self._did_initial_screen_fit:
            self._fit_window_to_screen()
            self._did_initial_screen_fit = True

    def _handle_component_shortcut(self, kind: str, shortcut: str):
        """Resolve conflicts between placement hotkeys and component shortcuts."""
        sc = self._scene_for_controls()
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
        act_select.triggered.connect(lambda: self._scene_for_controls().set_mode_select())
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

        act_chip = QAction("Chip", self)
        act_chip.setShortcut("H")
        act_chip.triggered.connect(self._start_place_chip)
        tb.addAction(act_chip)

        tb.addSeparator()
        act_wire = QAction("Wire", self)
        act_wire.setShortcut("W")
        act_wire.triggered.connect(lambda: self._scene_for_controls().set_mode_wire())
        tb.addAction(act_wire)
        act_text = QAction("Text", self)
        act_text.setShortcut("T")
        act_text.triggered.connect(lambda: self._scene_for_controls().set_mode_text())
        tb.addAction(act_text)
        act_net_label = QAction("Net Label", self)
        act_net_label.triggered.connect(lambda: self._scene_for_controls().set_mode_place("NetLabel"))
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
        act_fit.triggered.connect(lambda: self._view_for_controls().fit_all())
        tb.addAction(act_fit)

        act_zoom_in = QAction("Zoom +", self)
        act_zoom_in.setShortcut("+")
        act_zoom_in.triggered.connect(lambda: self._view_for_controls().scale(1.15, 1.15))
        tb.addAction(act_zoom_in)

        act_zoom_out = QAction("Zoom -", self)
        act_zoom_out.setShortcut("-")
        act_zoom_out.triggered.connect(lambda: self._view_for_controls().scale(1/1.15, 1/1.15))
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

        select_btn = btn("Select", lambda: self._scene_for_controls().set_mode_select(), icon_name="cursor-arrow")
        components_btn = btn("Components", lambda: (self.component_dock.show(), self.component_dock.raise_()), icon_name="folder")
        chip_btn = btn("Chip", self._start_place_chip, icon_name="applications-engineering")
        wire_btn = btn("Wire", lambda: self._scene_for_controls().set_mode_wire(), icon_name="draw-line")
        text_btn = btn("Text", lambda: self._scene_for_controls().set_mode_text(), icon_name="insert-text")
        net_label_btn = btn("Net Label", lambda: self._scene_for_controls().set_mode_place("NetLabel"), icon_name="tag")
        delete_btn = btn("Delete", self._delete_selected, icon_name="edit-delete")
        rot_cw_btn = btn("Rotate CW", lambda: self._rotate_selected(90), icon_name="object-rotate-right")
        rot_ccw_btn = btn("Rotate CCW", lambda: self._rotate_selected(-90), icon_name="object-rotate-left")
        mirror_x_btn = btn("Mirror X", self._on_shortcut_place_mirror_x, icon_name="object-flip-horizontal")
        mirror_y_btn = btn("Mirror Y", self._on_shortcut_place_mirror_y, icon_name="object-flip-vertical")
        grid_btn = btn("Grid", self._toggle_grid, icon_name="view-grid")
        snap_btn = btn("Snap", self._toggle_snap, icon_name="snap-to-grid")
        fit_btn = btn("Fit", lambda: self._view_for_controls().fit_all(), icon_name="zoom-fit-best")
        zoom_in_btn = btn("Zoom In", lambda: self._view_for_controls().scale(1.15, 1.15), icon_name="zoom-in")
        zoom_out_btn = btn("Zoom Out", lambda: self._view_for_controls().scale(1 / 1.15, 1 / 1.15), icon_name="zoom-out")
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
            select_btn, components_btn, chip_btn, wire_btn, text_btn, net_label_btn, delete_btn,
            rot_cw_btn, rot_ccw_btn, mirror_x_btn, mirror_y_btn, grid_btn, snap_btn,
            grid_label, self._island_grid_spin, grid_minus_btn, grid_plus_btn,
            fit_btn, zoom_in_btn, zoom_out_btn, export_btn, runtime_btn,
        ])
        try:
            island.apply_theme(self._watcher.current_theme())
        except Exception:
            pass
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

    def _build_schematic_power_overlay(self):
        """Create compact power controls in the window chrome."""
        self._schematic_power_overlay = SchematicPowerOverlay(self.backend, self)
        self._schematic_power_overlay._managed_by_layout = True
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._hardware_toolbar.addWidget(spacer)
        self._hardware_toolbar.addWidget(self._schematic_power_overlay.controls)
        self.statusBar().addPermanentWidget(self.runtime_build_indicator)
        self.statusBar().addPermanentWidget(self._schematic_power_overlay.monitor)
        try:
            self._schematic_power_overlay.apply_theme(self._watcher.current_theme())
        except Exception:
            pass
        self._schematic_power_overlay.controls.show()
        self._schematic_power_overlay.monitor.show()
        self._update_responsive_chrome()

    def _set_runtime_build_indicator(self, state: str):
        indicator = getattr(self, "runtime_build_indicator", None)
        if indicator is None:
            return
        indicator.set_state(state)
        QApplication.processEvents()

    def _update_responsive_chrome(self):
        width = max(1, int(self.width()))
        screen = self.windowHandle().screen() if self.windowHandle() is not None else QApplication.primaryScreen()
        avail_w = screen.availableGeometry().width() if screen is not None else width
        compact = width < 1180 or avail_w < 1280
        if hasattr(self, "_schematic_power_overlay") and self._schematic_power_overlay is not None:
            self._schematic_power_overlay.set_compact(compact)
        if hasattr(self, "hw_refresh_btn"):
            self.hw_refresh_btn.setText("Ref" if compact else "Refresh")
            self.hw_connect_btn.setText("Conn" if compact else "Connect")
            self.hw_disconnect_btn.setText("Disc" if compact else "Disconnect")
            self.hw_backend.setVisible(not compact)
            self.hw_devices.setMaximumWidth(170 if compact else 270)
        if hasattr(self, "status_label"):
            self.status_label.setVisible(not compact)

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

        act_save_chip = QAction("Save Selected Chip as Reusable…", self)
        act_save_chip.triggered.connect(self._save_selected_chip_as_reusable)
        fm.addAction(act_save_chip)

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
        self._act_chip = add_shortcut(QKeySequence("H"), self._on_shortcut_set_chip)
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
        sc = self._scene_for_controls()
        sc.grid_on = not sc.grid_on
        sc.update()

    def _toggle_snap(self):
        sc = self._scene_for_controls()
        sc.snap_on = not sc.snap_on
        self.statusBar().showMessage(f"Snap: {'ON' if sc.snap_on else 'OFF'}", 3000)

    def _toggle_grid_style(self):
        sc = self._scene_for_controls()
        sc.grid_style = 'dots' if sc.grid_style == 'lines' else 'lines'
        self.statusBar().showMessage(f"Grid style: {sc.grid_style}", 2000)
        sc.update()

    def _change_grid_size(self, v: int):
        sc = self._scene_for_controls()
        sc.grid_size = max(1, int(v))
        sc.update()
        self._sync_grid_controls_from_scene(sc)
        self.statusBar().showMessage(f"Grid size: {sc.grid_size}px", 1500)

    def _nudge_grid(self, d: int):
        self._grid_spin.setValue(max(1, int(self._grid_spin.value() + d)))

    def _show_properties_for(self, comp: ComponentItem):
        """Focus properties dock and select the target component."""
        if self.props_dock.isHidden():
            self.props_dock.show()
        self.props_dock.raise_()
        sc = comp.scene()
        if sc is not None:
            sc.clearSelection()
        comp.setSelected(True)
        self.props_panel.show_component(comp)
        self.props_panel.refdes_edit.setFocus()

    def _delete_selected(self):
        """Delete selected scene items using undo stack command."""
        sc = self._scene_for_controls()
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
            self._scene_for_controls().set_mode_wire()

    def _on_shortcut_set_text(self):
        if self._is_schematic_active():
            self._scene_for_controls().set_mode_text()

    def _on_shortcut_set_chip(self):
        if self._is_schematic_active():
            self._start_place_chip()

    def _on_shortcut_fit(self):
        if self._is_schematic_active():
            self._view_for_controls().fit_all()

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
        sc = self._scene_for_controls()
        sc.clearSelection()
        sc.set_mode_select()

    def _in_place_mode_with_ghost(self) -> bool:
        if not self._is_schematic_active():
            return False
        sc = self._scene_for_controls()
        return (
            getattr(sc, "mode", None) == getattr(sc, "Mode", object()).PLACE
            and getattr(sc, "_ghost_item", None) is not None
        )

    def _on_shortcut_place_rotate_cw(self):
        if not self._in_place_mode_with_ghost():
            return
        g = self._scene_for_controls()._ghost_item
        g.setRotation((g.rotation() + 90) % 360)

    def _on_shortcut_place_rotate_ccw(self):
        if not self._in_place_mode_with_ghost():
            return
        g = self._scene_for_controls()._ghost_item
        g.setRotation((g.rotation() - 90) % 360)

    def _on_shortcut_place_mirror_x(self):
        if self._in_place_mode_with_ghost():
            g = self._scene_for_controls()._ghost_item
            if hasattr(g, "toggle_mirror_x"):
                g.toggle_mirror_x()
            return
        self._mirror_selected("x")

    def _on_shortcut_place_mirror_y(self):
        if self._in_place_mode_with_ghost():
            g = self._scene_for_controls()._ghost_item
            if hasattr(g, "toggle_mirror_y"):
                g.toggle_mirror_y()
            return
        self._mirror_selected("y")

    def _mirror_selected(self, axis: str):
        sc = self._scene_for_controls()
        comps = [it for it in sc.selectedItems() if isinstance(it, ComponentItem)]
        if not comps:
            self.statusBar().showMessage("Select a component to mirror", 2000)
            return
        for c in comps:
            ms = c.mirror_state() if hasattr(c, "mirror_state") else {"mx": 1.0, "my": 1.0}
            old = (float(ms.get("mx", 1.0)), float(ms.get("my", 1.0)))
            if axis == "x":
                new = (-old[0], old[1])
            else:
                new = (old[0], -old[1])
            self.undo_stack.push(MirrorComponentCommand(c, old, new))

    def _on_shortcut_copy(self):
        if not self._is_schematic_active():
            return
        sc = self._scene_for_controls()
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
                "chip": c.chip_data() if hasattr(c, "chip_data") else {},
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
        sc = self._scene_for_controls()
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
            if hasattr(c, "set_chip_data"):
                c.set_chip_data(cdata.get("chip", {}))
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
        sc = self._scene_for_controls()
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
        self._set_runtime_build_indicator("running")
        if backend.connected_device() is None:
            self._set_runtime_build_indicator("error")
            QMessageBox.warning(self, "Device required", "Connect a device first.")
            return

        # Start from a known-safe matrix state so a previous runtime build
        # cannot contaminate this one.
        try:
            if hasattr(backend, "RESET"):
                backend.RESET(getattr(P, "programming_delay", 10))
                time.sleep(0.02)
            if hasattr(backend, "digitalio_write_mask"):
                backend.digitalio_write_mask(0)
        except Exception:
            pass

        # Safety interlock: if Wavegen is running, pause it during runtime build
        # to avoid driving an intermediate/shorted physical state.
        wavegen_was_running = bool(getattr(getattr(self, "wavegen_panel", None), "_running", False))
        wavegen_cfg = None
        try:
            if getattr(self, "wavegen_panel", None) is not None and hasattr(self.wavegen_panel, "_params"):
                wavegen_cfg = dict(self.wavegen_panel._params())
        except Exception:
            wavegen_cfg = None
        if wavegen_was_running:
            try:
                backend.stop_tool("wavegen")
            except Exception:
                pass
            try:
                if getattr(self, "wavegen_panel", None) is not None and hasattr(self.wavegen_panel, "_set_running"):
                    self.wavegen_panel._set_running(False)
                    self.wavegen_panel.state_label.setText("State: Paused for runtime build")
            except Exception:
                pass

        # Preserve current supplies state so runtime build cannot silently
        # change rails configuration (especially when master is OFF).
        pre_supplies_ok = False
        pre_supplies = {}
        try:
            pre_supplies_ok, _pre_msg, pre_supplies = backend.read_supplies_status()
        except Exception:
            pre_supplies_ok = False
            pre_supplies = {}

        try:
            netlist_text = sc.export_netlist_text()
        except Exception as e:
            self._set_runtime_build_indicator("error")
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
            self._set_runtime_build_indicator("error")
            QMessageBox.critical(self, "Error", f"Failed to write runtime netlist: {e}")
            return

        self.runtime_spice_netlist_text = netlist_text
        self.runtime_spice_netlist_path = str(tmp_path)
        runtime_debug_enabled = os.environ.get("NODEZILLA_RUNTIME_DEBUG", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        debug_log_path = str(root_dir / "runtime_build_debug.log")
        debug_log_fallback_path = str(user_root() / "runtime_build_debug.log")

        def _runtime_debug_log(message: str):
            if not runtime_debug_enabled:
                return
            try:
                with open(debug_log_path, "a", encoding="utf-8") as dbg:
                    dbg.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
            except Exception:
                pass
            try:
                with open(debug_log_fallback_path, "a", encoding="utf-8") as dbg:
                    dbg.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
            except Exception:
                pass

        if runtime_debug_enabled:
            try:
                with open(debug_log_path, "w", encoding="utf-8") as dbg:
                    dbg.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} runtime-build-start\n")
                    dbg.write(f"netlist_path={tmp_path}\n")
                with open(debug_log_fallback_path, "w", encoding="utf-8") as dbg:
                    dbg.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} runtime-build-start\n")
                    dbg.write(f"netlist_path={tmp_path}\n")
                    dbg.write(f"temp_log_path={debug_log_path}\n")
            except Exception:
                pass

        try:
            ComponentDataSet = P.CreateComponentDataSet.MakeDataSet()
            _runtime_debug_log(f"component-dataset-size={len(ComponentDataSet)}")
            P.ComponentSerach(ComponentDataSet, tmp_path)
            Used_Components = P.ComponentSerach.GetComponentsUsed(ComponentDataSet)
            _runtime_debug_log(f"used-components={len(Used_Components)}")
            P.CirToScript(
                Used_Components,
                backend,
                logger=_runtime_debug_log if runtime_debug_enabled else None,
            )
        except Exception as e:
            extra = f"\n\nDebug log:\n{debug_log_fallback_path}" if runtime_debug_enabled else ""
            self._set_runtime_build_indicator("error")
            QMessageBox.critical(
                self,
                "Runtime script error",
                f"Failed while processing runtime script: {e}{extra}",
            )
            return
        finally:
            # Leave the programmed matrix intact after a successful build, but
            # return GPIO control lines to an idle state.
            try:
                if hasattr(backend, "digitalio_write_mask"):
                    backend.digitalio_write_mask(0)
            except Exception:
                pass

            # Restore supplies to their prior state.
            was_on = bool(pre_supplies.get("master_enabled", False)) if pre_supplies_ok else False
            tracking = bool(pre_supplies.get("tracking", False))
            power_limit_w = float(pre_supplies.get("power_limit_w", 2.5))
            try:
                if was_on:
                    backend.configure_supplies(
                        master_enabled=True,
                        v_pos_v=float(pre_supplies.get("v_pos_v", 1.0)),
                        v_neg_v=float(pre_supplies.get("v_neg_v", -1.0)),
                        tracking=tracking,
                        power_limit_w=power_limit_w,
                    )
                else:
                    # Hard-off path: guarantees rails and DIO are forced low.
                    ok_stop, _msg_stop = backend.stop_tool("supplies")
                    if not ok_stop:
                        backend.configure_supplies(
                            master_enabled=False,
                            v_pos_v=0.0,
                            v_neg_v=0.0,
                            tracking=tracking,
                            power_limit_w=power_limit_w,
                        )
            except Exception:
                pass
            self._sync_supplies_docks()
            # Resume Wavegen if it was active before runtime build.
            if wavegen_was_running:
                try:
                    if isinstance(wavegen_cfg, dict):
                        backend.configure_wavegen(**wavegen_cfg)
                    backend.start_tool("wavegen")
                    if getattr(self, "wavegen_panel", None) is not None and hasattr(self.wavegen_panel, "_set_running"):
                        self.wavegen_panel._set_running(True)
                        self.wavegen_panel.state_label.setText("State: Running")
                except Exception:
                    try:
                        if getattr(self, "wavegen_panel", None) is not None:
                            self.wavegen_panel.state_label.setText("State: Error resuming wavegen")
                    except Exception:
                        pass
        if runtime_debug_enabled:
            self.statusBar().showMessage(
                f"Runtime build debug log: {debug_log_fallback_path}",
                8000,
            )
        self._set_runtime_build_indicator("ok")
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
        self._is_closing = True
        try:
            if getattr(self, "net_panel", None) is not None:
                self.net_panel.set_scene(None)
        except Exception:
            pass
        try:
            self._shutdown_instrument_panels()
        except Exception:
            # Best-effort shutdown; never block close.
            pass
        try:
            if self._scope_window is not None:
                self._scope_window.close()
        except Exception:
            pass
        try:
            for window in list(getattr(self, "_wavegen_windows", {}).values()):
                if window is not None:
                    window.close()
            self._wavegen_windows.clear()
        except Exception:
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
