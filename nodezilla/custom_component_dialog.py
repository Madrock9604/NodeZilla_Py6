from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import List, Tuple, Optional

from PySide6.QtCore import Qt, QPointF, QRectF, QSize, QLineF, QEvent
from PySide6.QtGui import QPen, QPainterPath, QPainter, QAction, QColor, QFont, QBrush
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QGraphicsView, QGraphicsScene, QLabel, QWidget,
    QMessageBox, QSpinBox, QCheckBox, QGraphicsLineItem, QGraphicsRectItem,
    QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsTextItem, QGraphicsItem,
    QComboBox, QFileDialog, QApplication, QToolButton, QFrame, QGroupBox
)

from .component_library import load_component_library, find_component_file
from .graphics_items import COMP_WIDTH, COMP_HEIGHT
from .paths import user_assets_root, user_library_root
from .theme import ThemeWatcher


# Custom symbol normalization target.
GUIDE_WIDTH = 100.0
GUIDE_HEIGHT = 100.0
GUIDE_PADDING = 2.0
# Keep the editor workspace larger than the guide box for easier drawing.
EDITOR_SCENE_WIDTH = 520.0
EDITOR_SCENE_HEIGHT = 360.0


@dataclass
class _PinData:
    name: str
    x: float
    y: float
    pin_number: int | None = None


class _GridView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.grid_on = True
        self.snap_on = True
        self.grid_size = 10
        self.grid_minor_color = Qt.darkGray
        self.grid_major_color = Qt.darkGray

    def snap_point(self, p: QPointF) -> QPointF:
        if not self.snap_on:
            return p
        g = float(self.grid_size)
        return QPointF(round(p.x() / g) * g, round(p.y() / g) * g)

    def drawBackground(self, p: QPainter, rect: QRectF):
        super().drawBackground(p, rect)
        if not self.grid_on or self.grid_size <= 1:
            return
        g = self.grid_size
        left = int((rect.left() // g) * g)
        top = int((rect.top() // g) * g)
        p.save()
        # Dotted grid with minor/major emphasis (major every 5 cells)
        major_step = g * 5
        minor_pen = QPen(self.grid_minor_color, 1)
        minor_pen.setCosmetic(True)
        minor_color = minor_pen.color()
        minor_color.setAlphaF(0.55)
        minor_pen.setColor(minor_color)
        major_pen = QPen(self.grid_major_color, 2)
        major_pen.setCosmetic(True)
        major_color = major_pen.color()
        major_color.setAlphaF(0.85)
        major_pen.setColor(major_color)
        x = left
        while x < rect.right():
            y = top
            while y < rect.bottom():
                is_major = (int(round(x)) % int(major_step) == 0) and (int(round(y)) % int(major_step) == 0)
                p.setPen(major_pen if is_major else minor_pen)
                p.drawPoint(int(x), int(y))
                y += g
            x += g
        p.restore()


class _SnapLine(QGraphicsLineItem):
    def __init__(self, line, snap_fn):
        super().__init__(line)
        self._snap_fn = snap_fn
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setPen(QPen(Qt.white, 2))
        self.setData(0, "symbol")
        self.setTransformOriginPoint(self.boundingRect().center())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self._snap_fn:
            return self._snap_fn(value)
        return super().itemChange(change, value)


class _SnapRect(QGraphicsRectItem):
    def __init__(self, rect: QRectF, snap_fn):
        super().__init__(rect)
        self._snap_fn = snap_fn
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setPen(QPen(Qt.white, 2))
        self.setBrush(Qt.NoBrush)
        self.setData(0, "symbol")
        self.setTransformOriginPoint(self.boundingRect().center())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self._snap_fn:
            return self._snap_fn(value)
        return super().itemChange(change, value)


class _SnapEllipse(QGraphicsEllipseItem):
    def __init__(self, rect: QRectF, snap_fn):
        super().__init__(rect)
        self._snap_fn = snap_fn
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setPen(QPen(Qt.white, 2))
        self.setBrush(Qt.NoBrush)
        self.setData(0, "symbol")
        self.setTransformOriginPoint(self.boundingRect().center())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self._snap_fn:
            return self._snap_fn(value)
        return super().itemChange(change, value)


class _SnapPath(QGraphicsPathItem):
    def __init__(self, path: QPainterPath, snap_fn):
        super().__init__(path)
        self._snap_fn = snap_fn
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setPen(QPen(Qt.white, 2))
        self.setData(0, "symbol")
        self.setTransformOriginPoint(self.boundingRect().center())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self._snap_fn:
            return self._snap_fn(value)
        return super().itemChange(change, value)


class _SnapText(QGraphicsTextItem):
    def __init__(self, text: str, snap_fn):
        super().__init__(text)
        self._snap_fn = snap_fn
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setData(0, "symbol")

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self._snap_fn:
            return self._snap_fn(value)
        return super().itemChange(change, value)


class _PinItem(QGraphicsEllipseItem):
    def __init__(self, name: str, pos: QPointF, snap_fn, on_moved, pin_number: int | None = None):
        super().__init__(-3, -3, 6, 6)
        self.name = name
        self.pin_number = pin_number
        self._snap_fn = snap_fn
        self._on_moved = on_moved
        self.setBrush(Qt.yellow)
        self.setPen(QPen(Qt.black, 1))
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setData(0, "pin")
        self.setPos(pos)
        self.label = QGraphicsTextItem(name)
        self.label.setDefaultTextColor(Qt.yellow)
        self.label.setParentItem(self)
        self.label.setPos(6, -10)

    def set_name(self, name: str):
        self.name = name
        self.label.setPlainText(name)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self._snap_fn:
            return self._snap_fn(value)
        if change == QGraphicsItem.ItemPositionHasChanged and self._on_moved:
            self._on_moved(self)
        return super().itemChange(change, value)


class CustomComponentDialog(QDialog):
    """Interactive symbol/pin editor for creating custom components."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Custom Component")
        self.resize(900, 600)

        self._tool = "select"  # select | line | rect | ellipse | arc | semicircle | poly | pin | text
        self._start: Optional[QPointF] = None
        self._active_item = None
        self._poly_points: List[QPointF] = []
        self._poly_item = None
        self._pin_items: List[_PinItem] = []
        self._copy_buffer: List[dict] = []
        self._pin_prefix = "P"
        self._pin_index = 1
        self._pin_edge_snap = True
        self._pin_edge_tol = 8.0
        self._symbol_pen_color = QApplication.instance().palette().text().color()
        self._pin_label_color = self._symbol_pen_color
        self._stroke_width = 2
        self._fill_enabled = False
        self._font_size = 12
        self._default_text = "Label"
        self._arc_start_deg = 0
        self._arc_span_deg = 180

        root = QHBoxLayout(self)

        left = QVBoxLayout()
        self.view = _GridView()
        self.scene = QGraphicsScene()
        self.view.setScene(self.scene)
        self.scene.setSceneRect(
            -EDITOR_SCENE_WIDTH / 2,
            -EDITOR_SCENE_HEIGHT / 2,
            EDITOR_SCENE_WIDTH,
            EDITOR_SCENE_HEIGHT,
        )
        self.view.setRenderHints(self.view.renderHints())

        self._guide_rect = self.scene.addRect(
            -GUIDE_WIDTH / 2, -GUIDE_HEIGHT / 2, GUIDE_WIDTH, GUIDE_HEIGHT,
            QPen(Qt.darkGray, 1, Qt.DotLine)
        )
        self._guide_rect.setData(0, "guide")

        tools_box = QGroupBox("Tools")
        tools = QHBoxLayout(tools_box)
        tools.setSpacing(6)
        self.select_btn = self._make_tool_btn("Select")
        self.line_btn = self._make_tool_btn("Line")
        self.rect_btn = self._make_tool_btn("Rect")
        self.ellipse_btn = self._make_tool_btn("Ellipse")
        self.arc_tool_btn = self._make_tool_btn("Arc")
        self.arc_btn = self._make_tool_btn("Semicircle")
        self.poly_btn = self._make_tool_btn("Polyline")
        self.pin_btn = self._make_tool_btn("Pin")
        self.text_btn = self._make_tool_btn("Text")
        for btn in (
            self.select_btn,
            self.line_btn,
            self.rect_btn,
            self.ellipse_btn,
            self.arc_tool_btn,
            self.arc_btn,
            self.poly_btn,
            self.pin_btn,
            self.text_btn,
        ):
            tools.addWidget(btn)
        tools.addStretch(1)

        modifiers_box = QGroupBox("Edit")
        modifiers = QHBoxLayout(modifiers_box)
        self.delete_btn = QPushButton("Delete")
        self.dup_btn = QPushButton("Duplicate")
        self.rotate_l_btn = QPushButton("Rotate ⟲")
        self.rotate_r_btn = QPushButton("Rotate ⟳")
        self.mirror_h_btn = QPushButton("Mirror H")
        self.mirror_v_btn = QPushButton("Mirror V")
        self.front_btn = QPushButton("Bring Front")
        self.back_btn = QPushButton("Send Back")
        self.clear_btn = QPushButton("Clear")
        modifiers.addWidget(self.delete_btn)
        modifiers.addWidget(self.dup_btn)
        modifiers.addWidget(self.rotate_l_btn)
        modifiers.addWidget(self.rotate_r_btn)
        modifiers.addWidget(self.mirror_h_btn)
        modifiers.addWidget(self.mirror_v_btn)
        modifiers.addWidget(self.front_btn)
        modifiers.addWidget(self.back_btn)
        modifiers.addWidget(self.clear_btn)
        modifiers.addStretch(1)

        view_box = QGroupBox("Canvas")
        snap_row = QHBoxLayout(view_box)
        self.snap_chk = QCheckBox("Snap")
        self.snap_chk.setChecked(True)
        self.grid_chk = QCheckBox("Grid")
        self.grid_chk.setChecked(True)
        self.grid_spin = QSpinBox()
        self.grid_spin.setRange(2, 50)
        self.grid_spin.setValue(10)
        self.pin_edge_chk = QCheckBox("Pin Edge Snap")
        self.pin_edge_chk.setChecked(True)
        self.pin_edge_tol_spin = QSpinBox()
        self.pin_edge_tol_spin.setRange(2, 50)
        self.pin_edge_tol_spin.setValue(8)
        snap_row.addWidget(self.snap_chk)
        snap_row.addWidget(self.grid_chk)
        snap_row.addWidget(QLabel("Grid:"))
        snap_row.addWidget(self.grid_spin)
        snap_row.addWidget(self.pin_edge_chk)
        snap_row.addWidget(QLabel("Tol:"))
        snap_row.addWidget(self.pin_edge_tol_spin)
        self.zoom_out_btn = QPushButton("Zoom -")
        self.zoom_in_btn = QPushButton("Zoom +")
        self.fit_btn = QPushButton("Fit")
        snap_row.addWidget(self.zoom_out_btn)
        snap_row.addWidget(self.zoom_in_btn)
        snap_row.addWidget(self.fit_btn)
        snap_row.addStretch(1)

        self.status_label = QLabel("Select a tool, draw on the canvas, and edit pins on the right.")
        self.status_label.setObjectName("componentEditorStatus")
        self.status_label.setWordWrap(True)
        status_frame = QFrame()
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(8, 6, 8, 6)
        status_layout.addWidget(self.status_label)

        left.addWidget(tools_box)
        left.addWidget(modifiers_box)
        left.addWidget(view_box)
        left.addWidget(status_frame)
        left.addWidget(self.view, 1)

        right = QVBoxLayout()
        style_box = QGroupBox("Style")
        style_form = QFormLayout(style_box)
        self.stroke_spin = QSpinBox()
        self.stroke_spin.setRange(1, 8)
        self.stroke_spin.setValue(self._stroke_width)
        self.fill_chk = QCheckBox("Fill closed shapes")
        self.fill_chk.setChecked(self._fill_enabled)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 48)
        self.font_size_spin.setValue(self._font_size)
        self.arc_start_spin = QSpinBox()
        self.arc_start_spin.setRange(-360, 360)
        self.arc_start_spin.setValue(self._arc_start_deg)
        self.arc_span_spin = QSpinBox()
        self.arc_span_spin.setRange(-360, 360)
        self.arc_span_spin.setValue(self._arc_span_deg)
        self.default_text_edit = QLineEdit(self._default_text)
        self.apply_style_btn = QPushButton("Apply to Selection")
        style_form.addRow("Stroke", self.stroke_spin)
        style_form.addRow("", self.fill_chk)
        style_form.addRow("Text Size", self.font_size_spin)
        style_form.addRow("Arc Start", self.arc_start_spin)
        style_form.addRow("Arc Span", self.arc_span_spin)
        style_form.addRow("New Text", self.default_text_edit)
        style_form.addRow("", self.apply_style_btn)
        right.addWidget(style_box)

        form = QFormLayout()
        self.kind_edit = QLineEdit()
        self.display_edit = QLineEdit()
        self.prefix_edit = QLineEdit()
        self.multipart_family_edit = QLineEdit()
        self.unit_name_edit = QLineEdit()
        self.category_edit = QLineEdit("Custom")
        self.category_btn = QPushButton("Choose…")
        self.category_refresh_btn = QPushButton("↻")
        self.spice_type_edit = QLineEdit()
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Component", "Net Label"])
        self.net_name_edit = QLineEdit()
        form.addRow("Kind (Unique ID)", self.kind_edit)
        form.addRow("Display", self.display_edit)
        form.addRow("Prefix", self.prefix_edit)
        form.addRow("Multipart Family", self.multipart_family_edit)
        form.addRow("Unit", self.unit_name_edit)
        cat_row = QWidget()
        cat_row_layout = QHBoxLayout(cat_row)
        cat_row_layout.setContentsMargins(0, 0, 0, 0)
        cat_row_layout.addWidget(self.category_edit, 1)
        cat_row_layout.addWidget(self.category_btn, 0)
        cat_row_layout.addWidget(self.category_refresh_btn, 0)
        form.addRow("Category", cat_row)
        form.addRow("SPICE Type", self.spice_type_edit)
        form.addRow("Type", self.type_combo)
        form.addRow("Net Name", self.net_name_edit)

        meta_box = QGroupBox("Component")
        meta_box.setLayout(form)
        right.addWidget(meta_box)
        pin_row = QHBoxLayout()
        self.pin_prefix_edit = QLineEdit("P")
        self.pin_index_spin = QSpinBox()
        self.pin_index_spin.setRange(1, 999)
        self.pin_index_spin.setValue(1)
        pin_row.addWidget(QLabel("Prefix"))
        pin_row.addWidget(self.pin_prefix_edit)
        pin_row.addWidget(QLabel("Start"))
        pin_row.addWidget(self.pin_index_spin)
        pin_row.addStretch(1)
        pin_box = QGroupBox("Pins")
        pin_box_layout = QVBoxLayout(pin_box)
        pin_box_layout.addLayout(pin_row)
        self.pin_table = QTableWidget(0, 4)
        self.pin_table.setHorizontalHeaderLabels(["Name", "Pin #", "X", "Y"])
        self.pin_table.horizontalHeader().setStretchLastSection(True)
        pin_box_layout.addWidget(self.pin_table, 1)
        right.addWidget(pin_box, 1)

        help_box = QGroupBox("Workflow")
        help_layout = QVBoxLayout(help_box)
        help_layout.addWidget(QLabel(
            "• Use Shift while drawing to constrain line, polyline, and shape angles.\n"
            "• Double-click to finish a polyline.\n"
            "• Double-click placed text to edit it inline.\n"
            "• Esc returns to Select mode and clears the active drawing.\n"
            "• Arrow keys nudge selection; Shift+Arrow moves faster."
        ))
        right.addWidget(help_box)

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Save Component")
        self.cancel_btn = QPushButton("Cancel")
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.save_btn)
        right.addLayout(buttons)

        root.addLayout(left, 3)
        root.addLayout(right, 2)

        # wiring
        self.select_btn.clicked.connect(lambda: self._set_tool("select"))
        self.line_btn.clicked.connect(lambda: self._set_tool("line"))
        self.rect_btn.clicked.connect(lambda: self._set_tool("rect"))
        self.ellipse_btn.clicked.connect(lambda: self._set_tool("ellipse"))
        self.arc_tool_btn.clicked.connect(lambda: self._set_tool("arc"))
        self.arc_btn.clicked.connect(lambda: self._set_tool("semicircle"))
        self.poly_btn.clicked.connect(lambda: self._set_tool("poly"))
        self.pin_btn.clicked.connect(lambda: self._set_tool("pin"))
        self.text_btn.clicked.connect(lambda: self._set_tool("text"))

        self.delete_btn.clicked.connect(self._delete_selected)
        self.dup_btn.clicked.connect(self._duplicate_selected)
        self.rotate_l_btn.clicked.connect(lambda: self._rotate_selected(-90))
        self.rotate_r_btn.clicked.connect(lambda: self._rotate_selected(90))
        self.mirror_h_btn.clicked.connect(lambda: self._mirror_selected(horizontal=True))
        self.mirror_v_btn.clicked.connect(lambda: self._mirror_selected(horizontal=False))
        self.front_btn.clicked.connect(lambda: self._restack_selected(front=True))
        self.back_btn.clicked.connect(lambda: self._restack_selected(front=False))
        self.clear_btn.clicked.connect(self._clear_canvas)
        self.category_btn.clicked.connect(self._choose_category_folder)
        self.category_refresh_btn.clicked.connect(self._refresh_category_folder)

        self.snap_chk.toggled.connect(self._sync_snap)
        self.grid_chk.toggled.connect(self._sync_snap)
        self.grid_spin.valueChanged.connect(self._sync_snap)
        self.pin_edge_chk.toggled.connect(self._sync_pin_snap)
        self.pin_edge_tol_spin.valueChanged.connect(self._sync_pin_snap)
        self.stroke_spin.valueChanged.connect(self._sync_style_defaults)
        self.fill_chk.toggled.connect(self._sync_style_defaults)
        self.font_size_spin.valueChanged.connect(self._sync_style_defaults)
        self.arc_start_spin.valueChanged.connect(self._sync_style_defaults)
        self.arc_span_spin.valueChanged.connect(self._sync_style_defaults)
        self.default_text_edit.textChanged.connect(self._sync_style_defaults)
        self.apply_style_btn.clicked.connect(self._apply_style_to_selection)
        self.zoom_in_btn.clicked.connect(lambda: self.view.scale(1.2, 1.2))
        self.zoom_out_btn.clicked.connect(lambda: self.view.scale(1 / 1.2, 1 / 1.2))
        self.fit_btn.clicked.connect(self._fit_canvas)

        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self._save_component)
        self.pin_table.itemChanged.connect(self._pin_table_changed)
        self.type_combo.currentTextChanged.connect(self._sync_type_fields)
        self.pin_prefix_edit.textChanged.connect(self._sync_pin_defaults)
        self.pin_index_spin.valueChanged.connect(self._sync_pin_defaults)

        self._set_tool("select")
        self._sync_snap()
        self._sync_pin_defaults()
        self._sync_pin_snap()
        self._sync_style_defaults()
        self._sync_type_fields()
        self.view.viewport().installEventFilter(self)
        self._editing_kind: str | None = None
        self._editing_path: Path | None = None
        self._theme_watcher = ThemeWatcher(QApplication.instance(), self._apply_editor_theme)
        self._fit_canvas()

    def _make_tool_btn(self, text: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setCheckable(True)
        btn.setMinimumWidth(82)
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        return btn

    def _update_tool_buttons(self):
        mapping = {
            "select": self.select_btn,
            "line": self.line_btn,
            "rect": self.rect_btn,
            "ellipse": self.ellipse_btn,
            "arc": self.arc_tool_btn,
            "semicircle": self.arc_btn,
            "poly": self.poly_btn,
            "pin": self.pin_btn,
            "text": self.text_btn,
        }
        for name, btn in mapping.items():
            btn.blockSignals(True)
            btn.setChecked(name == self._tool)
            btn.blockSignals(False)

    def _sync_style_defaults(self):
        self._stroke_width = int(self.stroke_spin.value())
        self._fill_enabled = self.fill_chk.isChecked()
        self._font_size = int(self.font_size_spin.value())
        self._default_text = self.default_text_edit.text().strip() or "Label"
        self._arc_start_deg = int(self.arc_start_spin.value())
        self._arc_span_deg = int(self.arc_span_spin.value())

    def _current_symbol_pen(self) -> QPen:
        pen = QPen(self._symbol_pen_color, float(self._stroke_width))
        pen.setCosmetic(True)
        return pen

    def _current_symbol_brush(self) -> QBrush:
        if not self._fill_enabled:
            return QBrush(Qt.NoBrush)
        fill = QColor(self._symbol_pen_color)
        fill.setAlpha(36)
        return QBrush(fill)

    def _restack_selected(self, front: bool = True):
        for item in self.scene.selectedItems():
            if item is self._guide_rect:
                continue
            item.setZValue(10 if front else 0)

    def _fit_canvas(self):
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def _sync_snap(self):
        self.view.snap_on = self.snap_chk.isChecked()
        self.view.grid_on = self.grid_chk.isChecked()
        self.view.grid_size = int(self.grid_spin.value())
        self.view.viewport().update()

    def _sync_pin_defaults(self):
        """Update auto-generated pin naming pattern from UI controls."""
        self._pin_prefix = self.pin_prefix_edit.text().strip() or "P"
        self._pin_index = int(self.pin_index_spin.value())

    def _sync_pin_snap(self):
        """Update pin-to-edge snap behavior."""
        self._pin_edge_snap = self.pin_edge_chk.isChecked()
        self._pin_edge_tol = float(self.pin_edge_tol_spin.value())

    def _next_pin_name(self) -> str:
        """Allocate next pin name using Prefix + incrementing index."""
        name = f"{self._pin_prefix}{self._pin_index}"
        self._pin_index += 1
        self.pin_index_spin.setValue(self._pin_index)
        return name

    def _snap_to_guide_edge(self, pos: QPointF) -> QPointF:
        """Snap pin placement near the guide rectangle edge for cleaner symbols."""
        if not self._pin_edge_snap:
            return pos
        r = self._guide_rect.rect()
        tol = self._pin_edge_tol
        x = pos.x()
        y = pos.y()
        edges = [
            (abs(x - r.left()), "x", r.left()),
            (abs(x - r.right()), "x", r.right()),
            (abs(y - r.top()), "y", r.top()),
            (abs(y - r.bottom()), "y", r.bottom()),
        ]
        edges.sort(key=lambda t: t[0])
        if edges and edges[0][0] <= tol:
            axis = edges[0][1]
            val = edges[0][2]
            if axis == "x":
                x = val
            else:
                y = val
        return QPointF(x, y)

    def _constrain_point(self, start: QPointF, pos: QPointF, tool: str) -> QPointF:
        """Constrain drawing with Shift:
        - line/poly -> horizontal/vertical/45 deg
        - rect/ellipse -> square/circle
        """
        dx = pos.x() - start.x()
        dy = pos.y() - start.y()
        if tool in ("rect", "ellipse"):
            size = max(abs(dx), abs(dy))
            dx = size if dx >= 0 else -size
            dy = size if dy >= 0 else -size
            return QPointF(start.x() + dx, start.y() + dy)
        # line/poly constraint to 0/45/90
        adx = abs(dx)
        ady = abs(dy)
        if adx < 1e-6 and ady < 1e-6:
            return pos
        if adx > 2 * ady:
            return QPointF(pos.x(), start.y())
        if ady > 2 * adx:
            return QPointF(start.x(), pos.y())
        d = max(adx, ady)
        return QPointF(start.x() + (d if dx >= 0 else -d), start.y() + (d if dy >= 0 else -d))

    def _choose_category_folder(self):
        root = user_assets_root() / "components" / "library"
        root.mkdir(parents=True, exist_ok=True)
        folder = QFileDialog.getExistingDirectory(self, "Choose Category Folder", str(root))
        if not folder:
            return
        try:
            rel = Path(folder).resolve().relative_to(root.resolve())
            parts = [p.replace("_", " ") for p in rel.parts if p]
            self.category_edit.setText(" / ".join(parts) if parts else "Custom")
        except Exception:
            self.category_edit.setText("Custom")

    def _refresh_category_folder(self):
        """Validate current category path still exists on disk."""
        root = user_assets_root() / "components" / "library"
        root.mkdir(parents=True, exist_ok=True)
        current = self.category_edit.text().strip()
        if current:
            parts = [p.strip() for p in current.replace(" / ", "/").split("/") if p.strip()]
            folder = root.joinpath(*[p.replace(" ", "_") for p in parts])
        else:
            folder = root
        if folder.exists():
            self.category_edit.setText(current)
        else:
            self.category_edit.setText("Custom")

    def _sync_type_fields(self):
        is_net = self.type_combo.currentText().lower().startswith("net")
        self.net_name_edit.setEnabled(is_net)

    def _apply_editor_theme(self, theme):
        bg = theme.bg
        self.scene.setBackgroundBrush(bg)
        luma = 0.2126 * bg.redF() + 0.7152 * bg.greenF() + 0.0722 * bg.blueF()
        if luma > 0.5:
            grid_minor = QColor(110, 110, 110)
            grid_major = QColor(70, 70, 70)
            symbol = QColor(25, 25, 25)
            pin_label = QColor(30, 30, 30)
        else:
            grid_minor = QColor(170, 170, 170)
            grid_major = QColor(220, 220, 220)
            symbol = QColor(235, 235, 235)
            pin_label = QColor(235, 235, 235)
        self.view.grid_minor_color = grid_minor
        self.view.grid_major_color = grid_major
        self._symbol_pen_color = symbol
        self._pin_label_color = pin_label
        gp = self._guide_rect.pen()
        gp.setColor(grid_major)
        self._guide_rect.setPen(gp)

        for it in self.scene.items():
            self._apply_item_theme(it)
        self.view.viewport().update()

    def _apply_item_theme(self, item):
        if item is self._guide_rect:
            return
        role = item.data(0)
        if role == "symbol":
            if isinstance(item, QGraphicsTextItem):
                item.setDefaultTextColor(self._symbol_pen_color)
                return
            if hasattr(item, "pen"):
                pen = item.pen()
                if pen.style() != Qt.NoPen:
                    pen.setColor(self._symbol_pen_color)
                    item.setPen(pen)
            if hasattr(item, "brush"):
                brush = item.brush()
                if brush.style() != Qt.NoBrush:
                    fill = QColor(self._symbol_pen_color)
                    fill.setAlpha(36)
                    item.setBrush(QBrush(fill))
        elif role == "pin":
            if hasattr(item, "setPen"):
                item.setPen(QPen(self._symbol_pen_color, 1))
            if hasattr(item, "label"):
                item.label.setDefaultTextColor(self._pin_label_color)

    def _set_tool(self, tool: str):
        """Set the active drawing tool and reset transient state."""
        self._tool = tool
        self._update_tool_buttons()
        if tool == "select":
            self.view.setDragMode(QGraphicsView.RubberBandDrag)
        else:
            self.view.setDragMode(QGraphicsView.NoDrag)
        self._start = None
        self._active_item = None
        self._poly_points = []
        self._poly_item = None
        hints = {
            "select": "Select, move, rotate, mirror, duplicate, and style existing shapes.",
            "line": "Click-drag to draw a line. Hold Shift to constrain to horizontal, vertical, or 45 degrees.",
            "rect": "Click-drag to draw a rectangle. Hold Shift for a square.",
            "ellipse": "Click-drag to draw an ellipse. Hold Shift for a circle.",
            "arc": "Click-drag to draw an arc using the Arc Start and Arc Span settings. Hold Shift for a circular arc.",
            "semicircle": "Click-drag to draw a semicircle. Drag direction chooses whether it bulges up, down, left, or right.",
            "poly": "Click to place polyline corners. Double-click to finish.",
            "pin": "Click near the guide box edge to place a pin. Pin edge snap keeps terminals tidy.",
            "text": "Click once to place editable text using the current text template and size.",
        }
        self.status_label.setText(hints.get(tool, "Ready."))

    def _clear_canvas(self):
        for item in list(self.scene.items()):
            if item is self._guide_rect:
                continue
            self.scene.removeItem(item)
        self._pin_items.clear()
        self.pin_table.setRowCount(0)
        self._start = None
        self._active_item = None
        self._poly_points = []
        self._poly_item = None
        self._editing_kind = None
        self._editing_path = None
        self.type_combo.setCurrentText("Component")
        self.net_name_edit.setText("")
        self._sync_type_fields()
        self._copy_buffer.clear()
        self.pin_index_spin.setValue(1)

    def _delete_selected(self):
        for item in list(self.scene.selectedItems()):
            if item is self._guide_rect:
                continue
            self.scene.removeItem(item)
        self._pin_items = [p for p in self._pin_items if p.scene() is not None]
        self._refresh_pin_table()

    def _duplicate_selected(self):
        for item in self.scene.selectedItems():
            if item is self._guide_rect:
                continue
            clone = self._clone_item(item)
            if clone is not None:
                clone.setPos(item.pos() + QPointF(10, 10))
                self.scene.addItem(clone)
        self._pin_items = [p for p in self._pin_items if p.scene() is not None]
        self._refresh_pin_table()

    def _mirror_selected(self, horizontal: bool = True):
        """Mirror selected scene items around their own local centers."""
        for item in self.scene.selectedItems():
            if item is self._guide_rect:
                continue
            item.setTransformOriginPoint(item.boundingRect().center())
            sx = -1 if horizontal else 1
            sy = -1 if not horizontal else 1
            t = item.transform()
            item.setTransform(t.scale(sx, sy))
        self._pin_moved(None)

    def _rotate_selected(self, angle: float):
        for item in self.scene.selectedItems():
            if item is self._guide_rect:
                continue
            item.setTransformOriginPoint(item.boundingRect().center())
            item.setRotation((item.rotation() + angle) % 360)
        self._pin_moved(None)

    def _pin_moved(self, pin_item):
        self._refresh_pin_table()

    def _refresh_pin_table(self):
        self.pin_table.blockSignals(True)
        self.pin_table.setRowCount(len(self._pin_items))
        for i, p in enumerate(self._pin_items):
            self.pin_table.setItem(i, 0, QTableWidgetItem(p.name))
            self.pin_table.setItem(i, 1, QTableWidgetItem("" if getattr(p, "pin_number", None) in (None, "") else str(p.pin_number)))
            self.pin_table.setItem(i, 2, QTableWidgetItem(f"{p.scenePos().x():.1f}"))
            self.pin_table.setItem(i, 3, QTableWidgetItem(f"{p.scenePos().y():.1f}"))
        self.pin_table.blockSignals(False)

    def _pin_table_changed(self, item: QTableWidgetItem):
        row = item.row()
        if row < 0 or row >= len(self._pin_items):
            return
        pin = self._pin_items[row]
        name_item = self.pin_table.item(row, 0)
        num_item = self.pin_table.item(row, 1)
        x_item = self.pin_table.item(row, 2)
        y_item = self.pin_table.item(row, 3)
        if name_item:
            pin.set_name(name_item.text().strip() or pin.name)
        try:
            raw_num = num_item.text().strip() if num_item else ""
            pin.pin_number = int(raw_num) if raw_num else None
        except Exception:
            pin.pin_number = None
        try:
            x = float(x_item.text()) if x_item else pin.scenePos().x()
            y = float(y_item.text()) if y_item else pin.scenePos().y()
            pin.setPos(self.view.snap_point(QPointF(x, y)))
        except Exception:
            pass

    def _clone_item(self, item):
        snap = self.view.snap_point
        if item.data(0) == "pin":
            p = _PinItem(item.name, item.pos(), snap, self._pin_moved)
            self._pin_items.append(p)
            self._apply_item_theme(p)
            return p
        if item is self._guide_rect:
            return None
        if isinstance(item, QGraphicsLineItem):
            it = _SnapLine(item.line(), snap)
            it.setRotation(item.rotation())
            self._apply_item_theme(it)
            return it
        if isinstance(item, QGraphicsRectItem):
            it = _SnapRect(item.rect(), snap)
            it.setRotation(item.rotation())
            self._apply_item_theme(it)
            return it
        if isinstance(item, QGraphicsEllipseItem) and item.data(0) == "symbol":
            it = _SnapEllipse(item.rect(), snap)
            it.setRotation(item.rotation())
            self._apply_item_theme(it)
            return it
        if isinstance(item, QGraphicsPathItem):
            it = _SnapPath(item.path(), snap)
            it.setRotation(item.rotation())
            self._apply_item_theme(it)
            return it
        if isinstance(item, QGraphicsTextItem):
            it = _SnapText(item.toPlainText(), snap)
            it.setFont(item.font())
            it.setDefaultTextColor(item.defaultTextColor())
            self._apply_item_theme(it)
            return it
        return None

    def _serialize_item(self, item) -> Optional[dict]:
        """Convert one selected graphics item into clipboard-safe payload."""
        if item is self._guide_rect:
            return None
        if item.data(0) == "pin":
            return {"type": "pin", "name": item.name, "pin_number": getattr(item, "pin_number", None), "pos": [item.pos().x(), item.pos().y()]}
        if isinstance(item, QGraphicsLineItem):
            ln = item.line()
            return {"type": "line", "line": [ln.x1(), ln.y1(), ln.x2(), ln.y2()], "rot": item.rotation()}
        if isinstance(item, QGraphicsRectItem):
            r = item.rect()
            return {"type": "rect", "rect": [r.x(), r.y(), r.width(), r.height()], "rot": item.rotation()}
        if isinstance(item, QGraphicsEllipseItem) and item.data(0) == "symbol":
            r = item.rect()
            return {"type": "ellipse", "rect": [r.x(), r.y(), r.width(), r.height()], "rot": item.rotation()}
        if isinstance(item, QGraphicsPathItem):
            path = item.path()
            pts = []
            for i in range(path.elementCount()):
                el = path.elementAt(i)
                pts.append([el.x, el.y])
            return {"type": "path", "points": pts, "rot": item.rotation()}
        if isinstance(item, QGraphicsTextItem):
            return {
                "type": "text",
                "text": item.toPlainText(),
                "pos": [item.pos().x(), item.pos().y()],
                "font_size": item.font().pointSize() or self._font_size,
            }
        return None

    def _deserialize_item(self, payload: dict, offset: QPointF = QPointF(0, 0)):
        """Instantiate one item payload back into the scene."""
        snap = self.view.snap_point
        t = payload.get("type")
        if t == "pin":
            pos = QPointF(payload["pos"][0], payload["pos"][1]) + offset
            pin = _PinItem(payload.get("name", self._next_pin_name()), pos, snap, self._pin_moved, payload.get("pin_number", None))
            self.scene.addItem(pin)
            self._pin_items.append(pin)
            self._apply_item_theme(pin)
            return
        if t == "line":
            x1, y1, x2, y2 = payload["line"]
            it = _SnapLine(QLineF(QPointF(x1, y1) + offset, QPointF(x2, y2) + offset), snap)
            it.setRotation(payload.get("rot", 0))
            self.scene.addItem(it)
            self._apply_item_theme(it)
            return
        if t == "rect":
            x, y, w, h = payload["rect"]
            it = _SnapRect(QRectF(x + offset.x(), y + offset.y(), w, h), snap)
            it.setRotation(payload.get("rot", 0))
            self.scene.addItem(it)
            self._apply_item_theme(it)
            return
        if t == "ellipse":
            x, y, w, h = payload["rect"]
            it = _SnapEllipse(QRectF(x + offset.x(), y + offset.y(), w, h), snap)
            it.setRotation(payload.get("rot", 0))
            self.scene.addItem(it)
            self._apply_item_theme(it)
            return
        if t == "path":
            pts = payload.get("points", [])
            if pts:
                path = QPainterPath(QPointF(pts[0][0], pts[0][1]) + offset)
                for x, y in pts[1:]:
                    path.lineTo(x + offset.x(), y + offset.y())
                it = _SnapPath(path, snap)
                it.setRotation(payload.get("rot", 0))
                self.scene.addItem(it)
                self._apply_item_theme(it)
            return
        if t == "text":
            x, y = payload.get("pos", [0, 0])
            it = _SnapText(payload.get("text", self._default_text), snap)
            it.setPos(QPointF(x, y) + offset)
            f = it.font()
            f.setPointSize(int(payload.get("font_size", self._font_size)))
            it.setFont(f)
            self.scene.addItem(it)
            self._apply_item_theme(it)
            return

    def _build_semicircle_path(self, start: QPointF, end: QPointF) -> QPainterPath:
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        path = QPainterPath()
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            path.moveTo(start)
            path.lineTo(end)
            return path

        if abs(dx) >= abs(dy):
            left = min(start.x(), end.x())
            right = max(start.x(), end.x())
            width = max(right - left, 1.0)
            radius = width / 2.0
            center_x = (left + right) / 2.0
            base_y = start.y()
            top = base_y - 2.0 * radius if dy <= 0 else base_y
            rect = QRectF(center_x - radius, top, 2.0 * radius, 2.0 * radius)
            start_angle = 180 if dy <= 0 else 0
            sweep = -180
            path.arcMoveTo(rect, start_angle)
            path.arcTo(rect, start_angle, sweep)
        else:
            top = min(start.y(), end.y())
            bottom = max(start.y(), end.y())
            height = max(bottom - top, 1.0)
            radius = height / 2.0
            center_y = (top + bottom) / 2.0
            base_x = start.x()
            left = base_x - 2.0 * radius if dx <= 0 else base_x
            rect = QRectF(left, center_y - radius, 2.0 * radius, 2.0 * radius)
            start_angle = 90 if dx <= 0 else -90
            sweep = -180
            path.arcMoveTo(rect, start_angle)
            path.arcTo(rect, start_angle, sweep)
        return path

    def _build_arc_path(self, start: QPointF, end: QPointF) -> QPainterPath:
        rect = QRectF(start, end).normalized()
        path = QPainterPath()
        if rect.width() < 1e-6 or rect.height() < 1e-6 or self._arc_span_deg == 0:
            path.moveTo(start)
            path.lineTo(end)
            return path
        path.arcMoveTo(rect, float(self._arc_start_deg))
        path.arcTo(rect, float(self._arc_start_deg), float(self._arc_span_deg))
        return path

    def _apply_style_to_item(self, item):
        if item is self._guide_rect or item.data(0) == "pin":
            return
        if isinstance(item, (QGraphicsLineItem, QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsPathItem)):
            item.setPen(self._current_symbol_pen())
            if hasattr(item, "setBrush"):
                item.setBrush(self._current_symbol_brush())
        elif isinstance(item, QGraphicsTextItem):
            font = item.font()
            font.setPointSize(self._font_size)
            item.setFont(font)
            item.setDefaultTextColor(self._symbol_pen_color)

    def _apply_style_to_selection(self):
        for item in self.scene.selectedItems():
            self._apply_style_to_item(item)
        self.view.viewport().update()

    def _export_symbol_json(self, json_path: Path):
        """Convert scene items into normalized JSON symbol format.

        Returns transform used for normalization so pins can be exported in the
        same coordinate space.
        """
        symbol_items = []
        bounds = None
        for it in self.scene.items():
            if it is self._guide_rect:
                continue
            if it.data(0) == "symbol":
                symbol_items.append(it)
                r = it.mapToScene(it.boundingRect()).boundingRect()
                bounds = r if bounds is None else bounds.united(r)
        if bounds is None:
            bounds = QRectF(-GUIDE_WIDTH / 2, -GUIDE_HEIGHT / 2, GUIDE_WIDTH, GUIDE_HEIGHT)
        bounds = bounds.adjusted(-GUIDE_PADDING, -GUIDE_PADDING, GUIDE_PADDING, GUIDE_PADDING)

        # Normalize all saved geometry into the guide box so custom symbols
        # keep consistent size when instantiated.
        target = self._guide_rect.rect().adjusted(
            GUIDE_PADDING,
            GUIDE_PADDING,
            -GUIDE_PADDING,
            -GUIDE_PADDING,
        )
        if bounds.width() > 1e-9 and bounds.height() > 1e-9:
            sx = target.width() / bounds.width()
            sy = target.height() / bounds.height()
            scale = min(sx, sy)
        else:
            scale = 1.0
        tx = target.center().x() - bounds.center().x() * scale
        ty = target.center().y() - bounds.center().y() * scale

        def _norm_point(p: QPointF) -> QPointF:
            return QPointF(p.x() * scale + tx, p.y() * scale + ty)

        shapes = []
        for it in symbol_items:
            tr = it.sceneTransform()
            if isinstance(it, QGraphicsLineItem):
                ln = it.line()
                a = _norm_point(tr.map(QPointF(ln.x1(), ln.y1())))
                b = _norm_point(tr.map(QPointF(ln.x2(), ln.y2())))
                shapes.append({"type": "line", "x1": a.x(), "y1": a.y(), "x2": b.x(), "y2": b.y(), "stroke": float(it.pen().widthF() or 2.0)})
            elif isinstance(it, QGraphicsRectItem):
                r = it.rect()
                tl = _norm_point(tr.map(QPointF(r.left(), r.top())))
                br = _norm_point(tr.map(QPointF(r.right(), r.bottom())))
                shapes.append({"type": "rect", "x": tl.x(), "y": tl.y(), "w": br.x() - tl.x(), "h": br.y() - tl.y(), "stroke": float(it.pen().widthF() or 2.0), "fill": it.brush().style() != Qt.NoBrush})
            elif isinstance(it, QGraphicsEllipseItem):
                r = it.rect()
                tl = _norm_point(tr.map(QPointF(r.left(), r.top())))
                br = _norm_point(tr.map(QPointF(r.right(), r.bottom())))
                shapes.append({"type": "ellipse", "x": tl.x(), "y": tl.y(), "w": br.x() - tl.x(), "h": br.y() - tl.y(), "stroke": float(it.pen().widthF() or 2.0), "fill": it.brush().style() != Qt.NoBrush})
            elif isinstance(it, QGraphicsPathItem):
                pts = []
                path = it.path()
                for i in range(path.elementCount()):
                    el = path.elementAt(i)
                    pts.append(_norm_point(tr.map(QPointF(el.x, el.y))))
                shapes.append({"type": "polyline", "points": [[p.x(), p.y()] for p in pts], "stroke": float(it.pen().widthF() or 2.0)})
            elif isinstance(it, QGraphicsTextItem):
                pos = _norm_point(tr.map(QPointF(0, 0)))
                shapes.append({
                    "type": "text",
                    "text": it.toPlainText(),
                    "x": pos.x(),
                    "y": pos.y(),
                    "font_size": it.font().pointSize() or self._font_size,
                })

        data = {
            "bounds": [target.left(), target.top(), target.width(), target.height()],
            "shapes": shapes,
        }
        json_path.write_text(json.dumps(data, indent=2))
        return {"scale": float(scale), "tx": float(tx), "ty": float(ty)}

    def _save_component(self):
        """Save symbol JSON + component JSON into the library tree."""
        try:
            kind = self.kind_edit.text().strip()
            display = self.display_edit.text().strip() or kind
            prefix = self.prefix_edit.text().strip() or (kind[:1].upper() if kind else "X")
            multipart_family = self.multipart_family_edit.text().strip()
            unit_name = self.unit_name_edit.text().strip()
            category = self.category_edit.text().strip() or "Custom"
            spice_type = self.spice_type_edit.text().strip()
            comp_type = "net" if self.type_combo.currentText().lower().startswith("net") else "component"
            net_name = self.net_name_edit.text().strip()
            st = spice_type.upper()
            is_part_number_component = comp_type == "component" and st not in {"R", "C", "L"}
            value_label = "Part Number" if is_part_number_component else "Value"
            default_value = ""
            if comp_type == "component":
                if st == "R":
                    default_value = "1k"
                elif st == "C":
                    default_value = "1uF"
                elif st == "L":
                    default_value = "1mH"
                elif is_part_number_component:
                    default_value = display

            if not kind:
                QMessageBox.warning(self, "Missing Kind", "Please enter a component kind.")
                return

            if not self._pin_items:
                QMessageBox.warning(self, "Missing Pins", "Please place at least one pin.")
                return

            sym_dir = user_assets_root() / "symbols" / "custom"
            sym_dir.mkdir(parents=True, exist_ok=True)
            safe_kind = "".join(c for c in kind if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_") or kind
            sym_path = sym_dir / f"{safe_kind}.json"
            xform = self._export_symbol_json(sym_path)
            scale = float((xform or {}).get("scale", 1.0))
            tx = float((xform or {}).get("tx", 0.0))
            ty = float((xform or {}).get("ty", 0.0))

            def _norm_xy(x: float, y: float) -> tuple[float, float]:
                return (x * scale + tx, y * scale + ty)

            root = user_library_root()
            # Allow nested categories using "A/B" or "A / B"
            raw_parts = [p.strip() for p in category.replace(" / ", "/").split("/") if p.strip()]
            if not raw_parts:
                raw_parts = ["Custom"]
            safe_parts = [
                "".join(c for c in part if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_") or "Custom"
                for part in raw_parts
            ]
            target_dir = root
            for part in safe_parts:
                target_dir = target_dir / part
            target_dir.mkdir(parents=True, exist_ok=True)

            def _is_under(path: Path, base: Path) -> bool:
                try:
                    path.resolve().relative_to(base.resolve())
                    return True
                except Exception:
                    return False

            # Never overwrite non-user/bundled read-only files.
            comp_path = target_dir / f"{safe_kind}.json"
            if self._editing_path and self._editing_path.exists() and _is_under(self._editing_path, root):
                comp_path = self._editing_path

            data = {
                "kind": kind,
                "display_name": display,
                "category": category,
                "prefix": prefix,
                "multipart_family": multipart_family,
                "unit_name": unit_name,
                "spice_type": spice_type,
                "value_label": value_label,
                "show_value": True,
                "default_value": default_value,
                "type": comp_type,
                "net_name": net_name,
                "symbol": f"custom/{safe_kind}.json",
                "auto_align_terminals": False,
                "auto_scale_symbol": False,
                "ports": [
                    {
                        "name": p.name,
                        "pin_number": getattr(p, "pin_number", None),
                        "x": _norm_xy(p.scenePos().x(), p.scenePos().y())[0],
                        "y": _norm_xy(p.scenePos().x(), p.scenePos().y())[1],
                    }
                    for p in self._pin_items
                ],
            }
            comp_path.write_text(json.dumps(data, indent=2))

            load_component_library(force_reload=True)
            self.accept()
        except Exception as e:
            QMessageBox.critical(
                self,
                "Save Component Error",
                "Failed to save custom component.\n"
                f"Details: {e}\n\n"
                f"Try saving under:\n{user_library_root()}",
            )

    def load_from_library(self, kind: str) -> bool:
        comp_path = find_component_file(kind)
        if comp_path is None or not comp_path.exists():
            return False
        try:
            entry = json.loads(comp_path.read_text())
        except Exception:
            return False

        self._clear_canvas()
        self.kind_edit.setText(entry.get("kind", ""))
        self.display_edit.setText(entry.get("display_name", ""))
        self.prefix_edit.setText(entry.get("prefix", ""))
        self.multipart_family_edit.setText(entry.get("multipart_family", ""))
        self.unit_name_edit.setText(entry.get("unit_name", ""))
        self.category_edit.setText(entry.get("category", "Custom"))
        self.spice_type_edit.setText(entry.get("spice_type", ""))
        ctype = str(entry.get("type", "component")).lower()
        self.type_combo.setCurrentText("Net Label" if ctype == "net" else "Component")
        self.net_name_edit.setText(entry.get("net_name", ""))
        self._sync_type_fields()
        self._editing_kind = kind
        self._editing_path = comp_path

        symbol = entry.get("symbol", "")
        if symbol.endswith(".json"):
            sym_path = user_assets_root() / "symbols" / symbol
            if sym_path.exists():
                try:
                    sym = json.loads(sym_path.read_text())
                    for shape in sym.get("shapes", []):
                        t = shape.get("type")
                        if t == "line":
                            it = _SnapLine(QLineF(shape["x1"], shape["y1"], shape["x2"], shape["y2"]), self.view.snap_point)
                            pen = it.pen(); pen.setWidthF(float(shape.get("stroke", 2.0))); it.setPen(pen)
                            self.scene.addItem(it)
                            self._apply_item_theme(it)
                        elif t == "rect":
                            it = _SnapRect(QRectF(shape["x"], shape["y"], shape["w"], shape["h"]), self.view.snap_point)
                            pen = it.pen(); pen.setWidthF(float(shape.get("stroke", 2.0))); it.setPen(pen)
                            if shape.get("fill"):
                                fill = QColor(self._symbol_pen_color); fill.setAlpha(36); it.setBrush(QBrush(fill))
                            self.scene.addItem(it)
                            self._apply_item_theme(it)
                        elif t == "ellipse":
                            it = _SnapEllipse(QRectF(shape["x"], shape["y"], shape["w"], shape["h"]), self.view.snap_point)
                            pen = it.pen(); pen.setWidthF(float(shape.get("stroke", 2.0))); it.setPen(pen)
                            if shape.get("fill"):
                                fill = QColor(self._symbol_pen_color); fill.setAlpha(36); it.setBrush(QBrush(fill))
                            self.scene.addItem(it)
                            self._apply_item_theme(it)
                        elif t == "polyline":
                            pts = shape.get("points", [])
                            if pts:
                                path = QPainterPath(QPointF(pts[0][0], pts[0][1]))
                                for x, y in pts[1:]:
                                    path.lineTo(x, y)
                                it = _SnapPath(path, self.view.snap_point)
                                pen = it.pen(); pen.setWidthF(float(shape.get("stroke", 2.0))); it.setPen(pen)
                                self.scene.addItem(it)
                                self._apply_item_theme(it)
                        elif t == "text":
                            it = _SnapText(shape.get("text", self._default_text), self.view.snap_point)
                            it.setPos(QPointF(float(shape.get("x", 0.0)), float(shape.get("y", 0.0))))
                            font = it.font()
                            font.setPointSize(int(shape.get("font_size", self._font_size)))
                            it.setFont(font)
                            self.scene.addItem(it)
                            self._apply_item_theme(it)
                except Exception:
                    pass

        for p in entry.get("ports", []):
            try:
                pos = QPointF(float(p.get("x", 0.0)), float(p.get("y", 0.0)))
                pin = _PinItem(str(p.get("name", "P")), pos, self.view.snap_point, self._pin_moved, p.get("pin_number", None))
                self.scene.addItem(pin)
                self._pin_items.append(pin)
                self._apply_item_theme(pin)
            except Exception:
                pass
        if self._pin_items:
            self._pin_index = len(self._pin_items) + 1
            self.pin_index_spin.setValue(self._pin_index)
        self._refresh_pin_table()
        return True

    def keyPressEvent(self, e):
        """Keyboard workflow for editing: copy/paste/rotate/nudge/delete."""
        if e.key() == Qt.Key_Escape:
            # Use Esc to return to select mode instead of closing dialog.
            self._set_tool("select")
            self._start = None
            self._active_item = None
            self._poly_points = []
            self._poly_item = None
            self.scene.clearSelection()
            e.accept()
            return
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self._delete_selected()
            e.accept()
            return
        if e.key() == Qt.Key_D and (e.modifiers() & Qt.ControlModifier):
            self._duplicate_selected()
            e.accept()
            return
        if e.key() == Qt.Key_C and (e.modifiers() & Qt.ControlModifier):
            self._copy_buffer = []
            for it in self.scene.selectedItems():
                payload = self._serialize_item(it)
                if payload:
                    self._copy_buffer.append(payload)
            e.accept()
            return
        if e.key() == Qt.Key_V and (e.modifiers() & Qt.ControlModifier):
            if self._copy_buffer:
                offset = QPointF(self.view.grid_size * 2, self.view.grid_size * 2)
                for payload in self._copy_buffer:
                    self._deserialize_item(payload, offset=offset)
                self._refresh_pin_table()
            e.accept()
            return
        if e.key() in (Qt.Key_R, Qt.Key_E):
            self._rotate_selected(90 if e.key() == Qt.Key_R else -90)
            e.accept()
            return
        if e.key() == Qt.Key_B and not (e.modifiers() & Qt.ControlModifier):
            self._restack_selected(front=True)
            e.accept()
            return
        if e.key() == Qt.Key_F and not (e.modifiers() & Qt.ControlModifier):
            self._fit_canvas()
            e.accept()
            return
        if e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            step = self.view.grid_size if self.view.snap_on else 1
            if e.modifiers() & Qt.ShiftModifier:
                step *= 5
            dx = dy = 0
            if e.key() == Qt.Key_Left:
                dx = -step
            elif e.key() == Qt.Key_Right:
                dx = step
            elif e.key() == Qt.Key_Up:
                dy = -step
            elif e.key() == Qt.Key_Down:
                dy = step
            for it in self.scene.selectedItems():
                if it is self._guide_rect:
                    continue
                it.setPos(it.pos() + QPointF(dx, dy))
            self._pin_moved(None)
            e.accept()
            return
        super().keyPressEvent(e)

    def eventFilter(self, obj, event):
        # Only handle view mouse events; ignore paint/hide etc.
        if obj is self.view.viewport():
            et = event.type()
            if et == QEvent.MouseButtonPress:
                pos = self.view.mapToScene(event.pos())
                pos = self.view.snap_point(pos)
                if event.button() == Qt.RightButton:
                    self._poly_points = []
                    self._poly_item = None
                    self._start = None
                    return True
                if event.button() == Qt.LeftButton:
                    if self._tool == "pin":
                        pos = self._snap_to_guide_edge(pos)
                        pin = _PinItem(self._next_pin_name(), pos, self.view.snap_point, self._pin_moved, None)
                        self.scene.addItem(pin)
                        self._pin_items.append(pin)
                        self._apply_item_theme(pin)
                        self._refresh_pin_table()
                        return True
                    if self._tool == "line":
                        self._start = pos
                        self._active_item = _SnapLine(QLineF(pos, pos), self.view.snap_point)
                        self.scene.addItem(self._active_item)
                        self._apply_style_to_item(self._active_item)
                        self._apply_item_theme(self._active_item)
                        return True
                    if self._tool == "rect":
                        self._start = pos
                        self._active_item = _SnapRect(QRectF(pos, pos), self.view.snap_point)
                        self.scene.addItem(self._active_item)
                        self._apply_style_to_item(self._active_item)
                        self._apply_item_theme(self._active_item)
                        return True
                    if self._tool == "ellipse":
                        self._start = pos
                        self._active_item = _SnapEllipse(QRectF(pos, pos), self.view.snap_point)
                        self.scene.addItem(self._active_item)
                        self._apply_style_to_item(self._active_item)
                        self._apply_item_theme(self._active_item)
                        return True
                    if self._tool == "arc":
                        self._start = pos
                        self._active_item = _SnapPath(self._build_arc_path(pos, pos), self.view.snap_point)
                        self.scene.addItem(self._active_item)
                        self._apply_style_to_item(self._active_item)
                        self._apply_item_theme(self._active_item)
                        return True
                    if self._tool == "semicircle":
                        self._start = pos
                        self._active_item = _SnapPath(self._build_semicircle_path(pos, pos), self.view.snap_point)
                        self.scene.addItem(self._active_item)
                        self._apply_style_to_item(self._active_item)
                        self._apply_item_theme(self._active_item)
                        return True
                    if self._tool == "poly":
                        if not self._poly_points:
                            self._poly_points = [pos]
                            path = QPainterPath(pos)
                            self._poly_item = _SnapPath(path, self.view.snap_point)
                            self.scene.addItem(self._poly_item)
                            self._apply_style_to_item(self._poly_item)
                            self._apply_item_theme(self._poly_item)
                        else:
                            self._poly_points.append(pos)
                        return True
                    if self._tool == "text":
                        text_item = _SnapText(self._default_text, self.view.snap_point)
                        text_item.setPos(pos)
                        self.scene.addItem(text_item)
                        self._apply_style_to_item(text_item)
                        self._apply_item_theme(text_item)
                        text_item.setFocus()
                        return True
            elif et == QEvent.MouseMove:
                pos = self.view.mapToScene(event.pos())
                pos = self.view.snap_point(pos)
                if self._tool in ("line", "rect", "ellipse", "arc", "semicircle") and self._start and self._active_item:
                    if event.modifiers() & Qt.ShiftModifier:
                        constraint_tool = "ellipse" if self._tool in ("arc", "semicircle") else self._tool
                        pos = self._constrain_point(self._start, pos, constraint_tool)
                    if self._tool == "line":
                        self._active_item.setLine(QLineF(self._start, pos))
                    elif self._tool == "rect":
                        self._active_item.setRect(QRectF(self._start, pos).normalized())
                    elif self._tool == "ellipse":
                        self._active_item.setRect(QRectF(self._start, pos).normalized())
                    elif self._tool == "arc":
                        self._active_item.setPath(self._build_arc_path(self._start, pos))
                    elif self._tool == "semicircle":
                        self._active_item.setPath(self._build_semicircle_path(self._start, pos))
                    return True
                if self._tool == "poly" and self._poly_item and self._poly_points:
                    if event.modifiers() & Qt.ShiftModifier:
                        last = self._poly_points[-1]
                        pos = self._constrain_point(last, pos, "line")
                    path = QPainterPath(self._poly_points[0])
                    for p in self._poly_points[1:]:
                        path.lineTo(p)
                    path.lineTo(pos)
                    self._poly_item.setPath(path)
                    return True
            elif et == QEvent.MouseButtonRelease:
                if self._tool in ("line", "rect", "ellipse", "arc", "semicircle"):
                    self._start = None
                    self._active_item = None
                    return True
            elif et == QEvent.MouseButtonDblClick:
                if self._tool == "poly" and self._poly_item and self._poly_points:
                    path = QPainterPath(self._poly_points[0])
                    for p in self._poly_points[1:]:
                        path.lineTo(p)
                    self._poly_item.setPath(path)
                    self._poly_points = []
                    self._poly_item = None
                    return True
        return super().eventFilter(obj, event)
