from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import List, Tuple, Optional

from PySide6.QtCore import Qt, QPointF, QRectF, QSize, QLineF, QEvent
from PySide6.QtGui import QPen, QPainterPath, QPainter, QAction
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QGraphicsView, QGraphicsScene, QLabel,
    QMessageBox, QSpinBox, QCheckBox, QGraphicsLineItem, QGraphicsRectItem,
    QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsTextItem, QGraphicsItem
)

from .component_library import load_component_library
from .graphics_items import COMP_WIDTH, COMP_HEIGHT


@dataclass
class _PinData:
    name: str
    x: float
    y: float


class _GridView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.grid_on = True
        self.snap_on = True
        self.grid_size = 10

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
        p.setPen(QPen(Qt.darkGray, 1, Qt.DotLine))
        x = left
        while x < rect.right():
            y = top
            while y < rect.bottom():
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


class _PinItem(QGraphicsEllipseItem):
    def __init__(self, name: str, pos: QPointF, snap_fn, on_moved):
        super().__init__(-3, -3, 6, 6)
        self.name = name
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Custom Component")
        self.resize(900, 600)

        self._tool = "select"  # select | line | rect | ellipse | poly | pin
        self._start: Optional[QPointF] = None
        self._active_item = None
        self._poly_points: List[QPointF] = []
        self._poly_item = None
        self._pin_items: List[_PinItem] = []

        root = QHBoxLayout(self)

        left = QVBoxLayout()
        self.view = _GridView()
        self.scene = QGraphicsScene()
        self.view.setScene(self.scene)
        self.scene.setSceneRect(-COMP_WIDTH / 2, -COMP_HEIGHT / 2, COMP_WIDTH, COMP_HEIGHT)
        self.view.setRenderHints(self.view.renderHints())

        self._guide_rect = self.scene.addRect(
            -COMP_WIDTH / 2, -COMP_HEIGHT / 2, COMP_WIDTH, COMP_HEIGHT,
            QPen(Qt.darkGray, 1, Qt.DotLine)
        )
        self._guide_rect.setData(0, "guide")

        tools = QHBoxLayout()
        self.select_btn = QPushButton("Select")
        self.line_btn = QPushButton("Line")
        self.rect_btn = QPushButton("Rect")
        self.ellipse_btn = QPushButton("Ellipse")
        self.poly_btn = QPushButton("Polyline")
        self.pin_btn = QPushButton("Pin")
        tools.addWidget(self.select_btn)
        tools.addWidget(self.line_btn)
        tools.addWidget(self.rect_btn)
        tools.addWidget(self.ellipse_btn)
        tools.addWidget(self.poly_btn)
        tools.addWidget(self.pin_btn)

        modifiers = QHBoxLayout()
        self.delete_btn = QPushButton("Delete")
        self.dup_btn = QPushButton("Duplicate")
        self.rotate_l_btn = QPushButton("Rotate ⟲")
        self.rotate_r_btn = QPushButton("Rotate ⟳")
        self.clear_btn = QPushButton("Clear")
        modifiers.addWidget(self.delete_btn)
        modifiers.addWidget(self.dup_btn)
        modifiers.addWidget(self.rotate_l_btn)
        modifiers.addWidget(self.rotate_r_btn)
        modifiers.addWidget(self.clear_btn)
        modifiers.addStretch(1)

        snap_row = QHBoxLayout()
        self.snap_chk = QCheckBox("Snap")
        self.snap_chk.setChecked(True)
        self.grid_chk = QCheckBox("Grid")
        self.grid_chk.setChecked(True)
        self.grid_spin = QSpinBox()
        self.grid_spin.setRange(2, 50)
        self.grid_spin.setValue(10)
        snap_row.addWidget(self.snap_chk)
        snap_row.addWidget(self.grid_chk)
        snap_row.addWidget(QLabel("Grid:"))
        snap_row.addWidget(self.grid_spin)
        snap_row.addStretch(1)

        left.addLayout(tools)
        left.addLayout(modifiers)
        left.addLayout(snap_row)
        left.addWidget(self.view, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self.kind_edit = QLineEdit()
        self.display_edit = QLineEdit()
        self.prefix_edit = QLineEdit()
        self.category_edit = QLineEdit("Custom")
        self.shortcut_edit = QLineEdit()
        form.addRow("Kind", self.kind_edit)
        form.addRow("Display", self.display_edit)
        form.addRow("Prefix", self.prefix_edit)
        form.addRow("Category", self.category_edit)
        form.addRow("Shortcut", self.shortcut_edit)

        right.addLayout(form)
        right.addWidget(QLabel("Pins"))
        self.pin_table = QTableWidget(0, 3)
        self.pin_table.setHorizontalHeaderLabels(["Name", "X", "Y"])
        self.pin_table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.pin_table, 1)

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
        self.poly_btn.clicked.connect(lambda: self._set_tool("poly"))
        self.pin_btn.clicked.connect(lambda: self._set_tool("pin"))

        self.delete_btn.clicked.connect(self._delete_selected)
        self.dup_btn.clicked.connect(self._duplicate_selected)
        self.rotate_l_btn.clicked.connect(lambda: self._rotate_selected(-90))
        self.rotate_r_btn.clicked.connect(lambda: self._rotate_selected(90))
        self.clear_btn.clicked.connect(self._clear_canvas)

        self.snap_chk.toggled.connect(self._sync_snap)
        self.grid_chk.toggled.connect(self._sync_snap)
        self.grid_spin.valueChanged.connect(self._sync_snap)

        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self._save_component)
        self.pin_table.itemChanged.connect(self._pin_table_changed)

        self._set_tool("select")
        self._sync_snap()
        self.view.viewport().installEventFilter(self)
        self._editing_kind: str | None = None

    def _sync_snap(self):
        self.view.snap_on = self.snap_chk.isChecked()
        self.view.grid_on = self.grid_chk.isChecked()
        self.view.grid_size = int(self.grid_spin.value())
        self.view.viewport().update()

    def _set_tool(self, tool: str):
        self._tool = tool
        for btn, name in [
            (self.select_btn, "select"),
            (self.line_btn, "line"),
            (self.rect_btn, "rect"),
            (self.ellipse_btn, "ellipse"),
            (self.poly_btn, "poly"),
            (self.pin_btn, "pin"),
        ]:
            btn.setEnabled(name != tool)
        if tool == "select":
            self.view.setDragMode(QGraphicsView.RubberBandDrag)
        else:
            self.view.setDragMode(QGraphicsView.NoDrag)
        self._start = None
        self._active_item = None
        self._poly_points = []
        self._poly_item = None

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
            self.pin_table.setItem(i, 1, QTableWidgetItem(f"{p.scenePos().x():.1f}"))
            self.pin_table.setItem(i, 2, QTableWidgetItem(f"{p.scenePos().y():.1f}"))
        self.pin_table.blockSignals(False)

    def _pin_table_changed(self, item: QTableWidgetItem):
        row = item.row()
        if row < 0 or row >= len(self._pin_items):
            return
        pin = self._pin_items[row]
        name_item = self.pin_table.item(row, 0)
        x_item = self.pin_table.item(row, 1)
        y_item = self.pin_table.item(row, 2)
        if name_item:
            pin.set_name(name_item.text().strip() or pin.name)
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
            return p
        if item is self._guide_rect:
            return None
        if isinstance(item, QGraphicsLineItem):
            it = _SnapLine(item.line(), snap)
            it.setRotation(item.rotation())
            return it
        if isinstance(item, QGraphicsRectItem):
            it = _SnapRect(item.rect(), snap)
            it.setRotation(item.rotation())
            return it
        if isinstance(item, QGraphicsEllipseItem) and item.data(0) == "symbol":
            it = _SnapEllipse(item.rect(), snap)
            it.setRotation(item.rotation())
            return it
        if isinstance(item, QGraphicsPathItem):
            it = _SnapPath(item.path(), snap)
            it.setRotation(item.rotation())
            return it
        return None

    def _export_symbol_json(self, json_path: Path):
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
            bounds = QRectF(-COMP_WIDTH / 2, -COMP_HEIGHT / 2, COMP_WIDTH, COMP_HEIGHT)
        bounds = bounds.adjusted(-6, -6, 6, 6)

        shapes = []
        for it in symbol_items:
            tr = it.sceneTransform()
            if isinstance(it, QGraphicsLineItem):
                ln = it.line()
                a = tr.map(QPointF(ln.x1(), ln.y1()))
                b = tr.map(QPointF(ln.x2(), ln.y2()))
                shapes.append({"type": "line", "x1": a.x(), "y1": a.y(), "x2": b.x(), "y2": b.y()})
            elif isinstance(it, QGraphicsRectItem):
                r = it.rect()
                tl = tr.map(QPointF(r.left(), r.top()))
                br = tr.map(QPointF(r.right(), r.bottom()))
                shapes.append({"type": "rect", "x": tl.x(), "y": tl.y(), "w": br.x() - tl.x(), "h": br.y() - tl.y()})
            elif isinstance(it, QGraphicsEllipseItem):
                r = it.rect()
                tl = tr.map(QPointF(r.left(), r.top()))
                br = tr.map(QPointF(r.right(), r.bottom()))
                shapes.append({"type": "ellipse", "x": tl.x(), "y": tl.y(), "w": br.x() - tl.x(), "h": br.y() - tl.y()})
            elif isinstance(it, QGraphicsPathItem):
                pts = []
                path = it.path()
                for i in range(path.elementCount()):
                    el = path.elementAt(i)
                    pts.append(tr.map(QPointF(el.x, el.y)))
                shapes.append({"type": "polyline", "points": [[p.x(), p.y()] for p in pts]})

        data = {
            "bounds": [bounds.left(), bounds.top(), bounds.width(), bounds.height()],
            "shapes": shapes,
        }
        json_path.write_text(json.dumps(data, indent=2))

    def _save_component(self):
        kind = self.kind_edit.text().strip()
        display = self.display_edit.text().strip() or kind
        prefix = self.prefix_edit.text().strip() or (kind[:1].upper() if kind else "X")
        category = self.category_edit.text().strip() or "Custom"
        shortcut = self.shortcut_edit.text().strip()

        if not kind:
            QMessageBox.warning(self, "Missing Kind", "Please enter a component kind.")
            return

        if not self._pin_items:
            QMessageBox.warning(self, "Missing Pins", "Please place at least one pin.")
            return

        sym_dir = Path(__file__).resolve().parent.parent / "assets" / "symbols" / "custom"
        sym_dir.mkdir(parents=True, exist_ok=True)
        sym_path = sym_dir / f"{kind}.json"
        self._export_symbol_json(sym_path)

        lib_path = Path(__file__).resolve().parent.parent / "assets" / "components" / "custom.json"
        try:
            data = json.loads(lib_path.read_text())
        except Exception:
            data = {"components": []}

        comps = data.get("components", [])
        comps = [c for c in comps if c.get("kind") != kind]
        comps.append({
            "kind": kind,
            "display_name": display,
            "category": category,
            "prefix": prefix,
            "shortcut": shortcut,
            "symbol": f"custom/{kind}.json",
            "auto_align_terminals": False,
            "auto_scale_symbol": False,
            "ports": [
                {"name": p.name, "x": p.scenePos().x(), "y": p.scenePos().y()} for p in self._pin_items
            ],
        })
        data["components"] = comps
        lib_path.write_text(json.dumps(data, indent=2))

        load_component_library(force_reload=True)
        self.accept()

    def load_from_library(self, kind: str) -> bool:
        root = Path(__file__).resolve().parent.parent
        custom_path = root / "assets" / "components" / "custom.json"
        default_path = root / "assets" / "components" / "defaults.json"
        entry = None
        try:
            data = json.loads(custom_path.read_text())
            entry = next((c for c in data.get("components", []) if c.get("kind") == kind), None)
        except Exception:
            entry = None
        if entry is None:
            try:
                data = json.loads(default_path.read_text())
                entry = next((c for c in data.get("components", []) if c.get("kind") == kind), None)
            except Exception:
                entry = None
        if not entry:
            return False

        self._clear_canvas()
        self.kind_edit.setText(entry.get("kind", ""))
        self.display_edit.setText(entry.get("display_name", ""))
        self.prefix_edit.setText(entry.get("prefix", ""))
        self.category_edit.setText(entry.get("category", "Custom"))
        self.shortcut_edit.setText(entry.get("shortcut", ""))
        self._editing_kind = kind

        symbol = entry.get("symbol", "")
        if symbol.endswith(".json"):
            sym_path = Path(__file__).resolve().parent.parent / "assets" / "symbols" / symbol
            if sym_path.exists():
                try:
                    sym = json.loads(sym_path.read_text())
                    for shape in sym.get("shapes", []):
                        t = shape.get("type")
                        if t == "line":
                            it = _SnapLine(QLineF(shape["x1"], shape["y1"], shape["x2"], shape["y2"]), self.view.snap_point)
                            self.scene.addItem(it)
                        elif t == "rect":
                            it = _SnapRect(QRectF(shape["x"], shape["y"], shape["w"], shape["h"]), self.view.snap_point)
                            self.scene.addItem(it)
                        elif t == "ellipse":
                            it = _SnapEllipse(QRectF(shape["x"], shape["y"], shape["w"], shape["h"]), self.view.snap_point)
                            self.scene.addItem(it)
                        elif t == "polyline":
                            pts = shape.get("points", [])
                            if pts:
                                path = QPainterPath(QPointF(pts[0][0], pts[0][1]))
                                for x, y in pts[1:]:
                                    path.lineTo(x, y)
                                it = _SnapPath(path, self.view.snap_point)
                                self.scene.addItem(it)
                except Exception:
                    pass

        for p in entry.get("ports", []):
            try:
                pos = QPointF(float(p.get("x", 0.0)), float(p.get("y", 0.0)))
                pin = _PinItem(str(p.get("name", "P")), pos, self.view.snap_point, self._pin_moved)
                self.scene.addItem(pin)
                self._pin_items.append(pin)
            except Exception:
                pass
        self._refresh_pin_table()
        return True

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
                        pin = _PinItem(f"P{len(self._pin_items) + 1}", pos, self.view.snap_point, self._pin_moved)
                        self.scene.addItem(pin)
                        self._pin_items.append(pin)
                        self._refresh_pin_table()
                        return True
                    if self._tool == "line":
                        self._start = pos
                        self._active_item = _SnapLine(QLineF(pos, pos), self.view.snap_point)
                        self.scene.addItem(self._active_item)
                        return True
                    if self._tool == "rect":
                        self._start = pos
                        self._active_item = _SnapRect(QRectF(pos, pos), self.view.snap_point)
                        self.scene.addItem(self._active_item)
                        return True
                    if self._tool == "ellipse":
                        self._start = pos
                        self._active_item = _SnapEllipse(QRectF(pos, pos), self.view.snap_point)
                        self.scene.addItem(self._active_item)
                        return True
                    if self._tool == "poly":
                        if not self._poly_points:
                            self._poly_points = [pos]
                            path = QPainterPath(pos)
                            self._poly_item = _SnapPath(path, self.view.snap_point)
                            self.scene.addItem(self._poly_item)
                        else:
                            self._poly_points.append(pos)
                        return True
            elif et == QEvent.MouseMove:
                pos = self.view.mapToScene(event.pos())
                pos = self.view.snap_point(pos)
                if self._tool in ("line", "rect", "ellipse") and self._start and self._active_item:
                    if self._tool == "line":
                        self._active_item.setLine(QLineF(self._start, pos))
                    elif self._tool == "rect":
                        self._active_item.setRect(QRectF(self._start, pos).normalized())
                    elif self._tool == "ellipse":
                        self._active_item.setRect(QRectF(self._start, pos).normalized())
                    return True
                if self._tool == "poly" and self._poly_item and self._poly_points:
                    path = QPainterPath(self._poly_points[0])
                    for p in self._poly_points[1:]:
                        path.lineTo(p)
                    path.lineTo(pos)
                    self._poly_item.setPath(path)
                    return True
            elif et == QEvent.MouseButtonRelease:
                if self._tool in ("line", "rect", "ellipse"):
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
