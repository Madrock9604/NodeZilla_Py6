# ========================================
# File: nodezilla/graphics_items.py
# ========================================
from __future__ import annotations
from pathlib import Path
import json
from typing import Optional, List
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QBrush, QPen, QPainterPath, QTransform, QFont
from PySide6.QtWidgets import (
QGraphicsItem, QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsTextItem,
QGraphicsPathItem
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .schematic_scene import SchematicScene
from .theme import Theme
from .component_library import load_component_library

PORT_RADIUS = 5.0
COMP_WIDTH = 100.0
COMP_HEIGHT = 40.0
DEBUG_COMPONENT_OVERLAY = False


def _auto_contrast_color(bg: QColor) -> QColor:
    r, g, b = bg.red(), bg.green(), bg.blue()
    luma = (299 * r + 587 * g + 114 * b) / 1000.0
    return QColor(20, 20, 20) if luma > 128 else QColor(235, 235, 235)


class InlineLabel(QGraphicsTextItem):
    """Draggable label bound to a ComponentItem (refdes or value)."""
    def __init__(self, parent_item: 'ComponentItem', kind: str):
        super().__init__("", parent_item)
        self._parent = parent_item
        self.kind = kind  # "refdes" | "value"
        self._manual_pos = False
        self._setting_default = False
        self.setDefaultTextColor(self._text_color())
        self.setZValue(4)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def _text_color(self):
        from PySide6.QtWidgets import QApplication
        return QApplication.instance().palette().text().color()

    def set_default_pos(self, p: QPointF):
        self._setting_default = True
        self.setPos(p)
        self._setting_default = False

    def reset_to_default(self):
        self._manual_pos = False
        self._parent._update_label()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged and not self._setting_default:
            self._manual_pos = True
        return super().itemChange(change, value)

    def mousePressEvent(self, e):
        e.accept()
        super().mousePressEvent(e)


class PortItem(QGraphicsEllipseItem):
    """Terminal pin on a ComponentItem; drives wire updates."""
    def __init__(self, parent: 'ComponentItem', name: str, rel_pos: QPointF):
        super().__init__(-PORT_RADIUS, -PORT_RADIUS, 2*PORT_RADIUS, 2*PORT_RADIUS, parent)
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(Qt.black, 1.25))
        self.setZValue(2)
        self.name = name
        self.rel_pos = rel_pos
        self.setPos(rel_pos)
        self.wires: List['WireItem'] = []
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

    def apply_theme(self, theme: Theme):
        from PySide6.QtGui import QBrush, QPen
        # Keep port fill visible against bg; white works in both themes
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(theme.component_stroke, 1.25))

    def ensure_visible(self):
        try:
            sc = self.scene()
            bg = sc.backgroundBrush().color() if sc else None
        except Exception:
            bg = None
        if bg is None or not isinstance(bg, QColor):
            bg = QColor(245, 246, 248)
        pen = QPen(_auto_contrast_color(bg), 1.25)
        self.setPen(pen)

    def add_wire(self, wire: 'WireItem'):
        if wire not in self.wires:
            self.wires.append(wire)


    def remove_wire(self, wire: 'WireItem'):
        if wire in self.wires:
            self.wires.remove(wire)


    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for w in self.wires:
                w.update_path()
        return super().itemChange(change, value)


    def hoverEnterEvent(self, e):
        self.setBrush(QBrush(Qt.yellow)); super().hoverEnterEvent(e)


    def hoverLeaveEvent(self, e):
        self.setBrush(QBrush(Qt.white)); super().hoverLeaveEvent(e)


class JsonSymbolItem(QGraphicsPathItem):
    def __init__(self, path: QPainterPath):
        super().__init__(path)
        pen = QPen(QColor(20, 20, 20), 2)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(Qt.NoBrush)
        self.setZValue(2)


class CommentTextItem(QGraphicsTextItem):
    """Free-form schematic comment text."""
    def __init__(self, text: str = "Comment"):
        super().__init__(text)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(6)
        self._custom_color: QColor | None = None
        self._apply_default_style()

    def _apply_default_style(self):
        self.setDefaultTextColor(QColor(220, 220, 220))

    def apply_theme(self, theme: Theme):
        self.setDefaultTextColor(self._custom_color or theme.text)

    def set_text_color(self, color: QColor | None):
        self._custom_color = QColor(color) if color is not None else None
        sc = self.scene()
        theme = getattr(sc, "theme", None) if sc else None
        if theme is not None:
            self.apply_theme(theme)
        else:
            self.setDefaultTextColor(self._custom_color or QColor(220, 220, 220))

    def text_state(self) -> dict:
        f = self.font()
        c = self.defaultTextColor()
        return {
            "family": f.family(),
            "point_size": int(f.pointSize()) if f.pointSize() > 0 else 12,
            "bold": bool(f.bold()),
            "italic": bool(f.italic()),
            "color": c.name(QColor.HexRgb),
            "custom_color": self._custom_color.name(QColor.HexRgb) if self._custom_color else "",
        }

    def apply_text_state(self, state: dict):
        try:
            f = QFont(self.font())
            family = str(state.get("family", "")).strip()
            if family:
                f.setFamily(family)
            f.setPointSize(max(1, int(state.get("point_size", 12))))
            f.setBold(bool(state.get("bold", False)))
            f.setItalic(bool(state.get("italic", False)))
            self.setFont(f)
            custom = str(state.get("custom_color", "")).strip()
            if custom:
                self.set_text_color(QColor(custom))
            else:
                col = str(state.get("color", "")).strip()
                if col:
                    self.set_text_color(QColor(col))
        except Exception:
            pass

    def mouseDoubleClickEvent(self, e):
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus(Qt.MouseFocusReason)
        super().mouseDoubleClickEvent(e)

    def focusOutEvent(self, e):
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        super().focusOutEvent(e)

class ComponentItem(QGraphicsRectItem):
    """Component graphics + ports + labels.

    Uses component_library to load symbol + port definitions.
    """
    def __init__(self, kind: str, pos: QPointF):
        super().__init__(-COMP_WIDTH/2, -COMP_HEIGHT/2, COMP_WIDTH, COMP_HEIGHT)
        init_pos = QPointF(pos)
        self.kind = kind
        self.setBrush(QBrush(Qt.NoBrush))
        self.setPen(QPen(Qt.NoPen))
        self._theme: Theme | None = None
        self._mirror_x = 1.0
        self._mirror_y = 1.0
        self.setFlags(
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setZValue(1)
        self.setRotation(0)

        # metadata
        self.refdes: str = ""
        self.value: str = ""

        # separate draggable labels
        self.refdes_label = InlineLabel(self, "refdes")
        self.value_label = InlineLabel(self, "value")
        self.pin_labels: List[QGraphicsTextItem] = []

        # symbol
        self.symbol_item: QGraphicsPathItem | None = None

        # ports
        self.ports: List[PortItem] = []
        self._comp_def = load_component_library().get(self.kind)
        self._is_chip = bool(self._comp_def and getattr(self._comp_def, "is_chip", False))
        self._chip_data: dict = {}
        self._auto_align_terminals = bool(getattr(self._comp_def, "auto_align_terminals", True))
        self._auto_scale_symbol = bool(getattr(self._comp_def, "auto_scale_symbol", True))
        if self._comp_def and self._comp_def.ports:
            for pd in self._comp_def.ports:
                port_pos = QPointF(pd.x, pd.y)
                self.ports.append(PortItem(self, pd.name, port_pos))
        else:
            # Fallback for unknown parts: default two terminals.
            self.ports = [
                PortItem(self, 'A', QPointF(-COMP_WIDTH/2, 0)),
                PortItem(self, 'B', QPointF(COMP_WIDTH/2, 0)),
            ]

        self.port_left = self.ports[0] if self.ports else None
        self.port_right = self.ports[1] if len(self.ports) > 1 else None
        if self._is_chip and self._comp_def and getattr(self._comp_def, "chip_template", ""):
            self._chip_data = self._load_chip_template(getattr(self._comp_def, "chip_template", ""))
        if self._is_chip and "io" not in self._chip_data:
            self._chip_data["io"] = {"pins": max(2, len(self.ports))}

        # initial position + labels
        self.setPos(init_pos)
        self._update_label()

        # symbol artwork
        self._load_symbol_graphic()
        # ensure ports are visible even before theme applies
        for port in getattr(self, "ports", []):
            if hasattr(port, "ensure_visible"):
                port.ensure_visible()
        if DEBUG_COMPONENT_OVERLAY:
            self._add_debug_overlay()

        # for undoable moves
        self._press_pos: Optional[QPointF] = None

    def _load_chip_template(self, rel_path: str) -> dict:
        try:
            root = Path(__file__).resolve().parent.parent
            p = (root / "assets" / "chips" / str(rel_path).replace("\\", "/")).resolve()
            data = json.loads(p.read_text()) if p.exists() else {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _replace_ports(self, port_defs: list[tuple[str, QPointF]]):
        # Ports are child items; replacing them is safe before wiring.
        old_ports = list(getattr(self, "ports", []))
        for p in old_ports:
            try:
                if p.scene() is not None:
                    p.scene().removeItem(p)
            except Exception:
                pass
        self.ports = [PortItem(self, name, pos) for (name, pos) in port_defs]
        self.port_left = self.ports[0] if self.ports else None
        self.port_right = self.ports[1] if len(self.ports) > 1 else None
        self.prepareGeometryChange()
        self.update()
        self._update_label()
        self._update_pin_labels()

    def set_mirror(self, mirror_x: float, mirror_y: float):
        self._mirror_x = -1.0 if mirror_x < 0 else 1.0
        self._mirror_y = -1.0 if mirror_y < 0 else 1.0
        self.setTransform(QTransform().scale(self._mirror_x, self._mirror_y))
        for port in getattr(self, 'ports', []):
            for w in list(port.wires):
                w.update_path()
        self._update_label()

    def toggle_mirror_x(self):
        self.set_mirror(-self._mirror_x, self._mirror_y)

    def toggle_mirror_y(self):
        self.set_mirror(self._mirror_x, -self._mirror_y)

    def mirror_state(self):
        return {"mx": float(self._mirror_x), "my": float(self._mirror_y)}

    def apply_theme(self, theme: Theme):
        self._theme = theme
        #self.setBrush(QBrush(theme.component_fill))
        #self.setPen(QPen(theme.component_stroke, 1.5))
        if hasattr(self, "refdes_label") and self.refdes_label is not None:
            self.refdes_label.setDefaultTextColor(theme.text)
        if hasattr(self, "value_label") and self.value_label is not None:
            self.value_label.setDefaultTextColor(theme.text)
        for port in getattr(self, "ports", []):
            if hasattr(port, "apply_theme"):
                port.apply_theme(theme)
        self._apply_symbol_theme()

    def _add_debug_overlay(self):
        # Bright magenta crosshair and label to confirm placement/visibility.
        cross = QPainterPath()
        cross.moveTo(-8, 0); cross.lineTo(8, 0)
        cross.moveTo(0, -8); cross.lineTo(0, 8)
        self._debug_cross = QGraphicsPathItem(cross, self)
        pen = QPen(QColor(255, 0, 255), 1.5)
        pen.setCosmetic(True)
        self._debug_cross.setPen(pen)
        self._debug_cross.setZValue(5)
        self._debug_label = QGraphicsTextItem(self.kind, self)
        self._debug_label.setDefaultTextColor(QColor(255, 0, 255))
        self._debug_label.setPos(10, 10)
        self._debug_label.setZValue(5)

    def _update_label(self):
        """Update label text/visibility and place defaults if not manually moved."""
        from PySide6.QtWidgets import QApplication
        sc = self.scene()
        theme = getattr(sc, "theme", None) if sc else None
        ref_text = self.refdes.strip()
        val_text = self.value.strip()
        is_net = bool(self._comp_def and getattr(self._comp_def, "comp_type", "component") == "net")
        if theme:
            self.refdes_label.setDefaultTextColor(theme.text)
            self.value_label.setDefaultTextColor(theme.text)
        else:
            tc = QApplication.instance().palette().text().color()
            self.refdes_label.setDefaultTextColor(tc)
            self.value_label.setDefaultTextColor(tc)

        self.refdes_label.setPlainText(ref_text)
        self.value_label.setPlainText(val_text)

        br = self.routing_local_rect()
        self.refdes_label.setVisible(not is_net)
        if not self.refdes_label._manual_pos:
            self.refdes_label.set_default_pos(QPointF(-self.refdes_label.boundingRect().width() / 2, br.top() - 18))
        if not self.value_label._manual_pos:
            if is_net:
                self.value_label.set_default_pos(
                    QPointF(br.right() + 6, -self.value_label.boundingRect().height() / 2)
                )
            else:
                self.value_label.set_default_pos(
                    QPointF(-self.value_label.boundingRect().width() / 2, br.bottom() + 4)
                )

        # Keep text upright/readable for both rotation and mirroring.
        label_tf = QTransform()
        label_tf.rotate(-self.rotation())
        label_tf.scale(self._mirror_x, self._mirror_y)
        self.refdes_label.setRotation(0.0)
        self.value_label.setRotation(0.0)
        self.refdes_label.setTransform(label_tf)
        self.value_label.setTransform(label_tf)
        self._update_pin_labels()

    def _update_pin_labels(self):
        # Only show per-pin numbering labels for chip boundary ports.
        for lbl in getattr(self, "pin_labels", []):
            try:
                if lbl.scene() is not None:
                    lbl.scene().removeItem(lbl)
            except Exception:
                pass
        self.pin_labels = []
        if not self.is_chip():
            return

        from PySide6.QtWidgets import QApplication
        sc = self.scene()
        text_color = (getattr(sc, "theme", None).text if getattr(sc, "theme", None) is not None
                      else QApplication.instance().palette().text().color())
        label_tf = QTransform()
        label_tf.rotate(-self.rotation())
        label_tf.scale(self._mirror_x, self._mirror_y)
        for p in getattr(self, "ports", []):
            if p is None:
                continue
            t = QGraphicsTextItem(str(getattr(p, "name", "")), self)
            t.setDefaultTextColor(text_color)
            t.setZValue(4)
            t.setFlag(QGraphicsItem.ItemIsSelectable, False)
            t.setFlag(QGraphicsItem.ItemIsMovable, False)
            t.setRotation(0.0)
            t.setTransform(label_tf)
            br = t.boundingRect()
            # Place pin number outward from pin side.
            if p.pos().x() <= 0:
                x = p.pos().x() - br.width() - 8
            else:
                x = p.pos().x() + 8
            y = p.pos().y() - br.height() / 2.0
            t.setPos(QPointF(x, y))
            self.pin_labels.append(t)

    def set_refdes(self, refdes: str):
        self.refdes = refdes
        self._update_label()

    def set_value(self, value: str):
        self.value = value
        self._update_label()
        if self._comp_def and getattr(self._comp_def, "comp_type", "component") == "net":
            sc = self.scene()
            if sc and hasattr(sc, "_schedule_nets_changed"):
                sc._schedule_nets_changed()

    def is_chip(self) -> bool:
        return bool(self._is_chip)

    def chip_data(self) -> dict:
        try:
            return json.loads(json.dumps(self._chip_data or {}))
        except Exception:
            return {}

    def set_chip_data(self, data: dict):
        prev_io = (self._chip_data or {}).get("io", {})
        if isinstance(data, dict):
            try:
                self._chip_data = json.loads(json.dumps(data))
            except Exception:
                self._chip_data = {}
        else:
            self._chip_data = {}
        # Keep chip boundary pin configuration stable across editor open/close.
        if isinstance(prev_io, dict) and prev_io:
            self._chip_data = dict(self._chip_data or {})
            self._chip_data["io"] = dict(prev_io)

    def configure_chip_ports(self, inputs: int, outputs: int):
        """Backward-compatible helper (old API)."""
        self.configure_chip_pins(int(inputs) + int(outputs))

    def configure_chip_pins(self, pins: int):
        """Define chip boundary pins as numbered connectors: 1..N."""
        if not self.is_chip():
            return
        n = max(2, int(pins or 2))
        self._chip_data = dict(self._chip_data or {})
        self._chip_data["io"] = {"pins": n}

        def spread_y(count: int) -> list[float]:
            if count <= 1:
                return [0.0]
            span = max(40.0, (count - 1) * 16.0)
            top = -span / 2.0
            step = span / float(count - 1)
            return [top + i * step for i in range(count)]

        left_n = (n + 1) // 2
        right_n = n - left_n
        port_defs: list[tuple[str, QPointF]] = []
        idx = 1
        for y in spread_y(left_n):
            port_defs.append((str(idx), QPointF(-COMP_WIDTH / 2, y)))
            idx += 1
        for y in spread_y(right_n):
            port_defs.append((str(idx), QPointF(COMP_WIDTH / 2, y)))
            idx += 1
        self._replace_ports(port_defs)

    def chip_io_counts(self) -> tuple[int, int]:
        io = (self._chip_data or {}).get("io", {})
        try:
            if "pins" in io:
                n = max(2, int(io.get("pins", 2)))
                left_n = (n + 1) // 2
                right_n = n - left_n
                return left_n, right_n
            i = max(1, int(io.get("inputs", 2)))
            o = max(1, int(io.get("outputs", 2)))
            return i, o
        except Exception:
            return 2, 2

    def chip_pin_count(self) -> int:
        io = (self._chip_data or {}).get("io", {})
        try:
            if "pins" in io:
                return max(2, int(io.get("pins", 2)))
        except Exception:
            pass
        # Fallback: trust current boundary port count.
        try:
            n_ports = len([p for p in getattr(self, "ports", []) if p is not None])
            if n_ports >= 2:
                return n_ports
        except Exception:
            pass
        i, o = self.chip_io_counts()
        return max(2, i + o)

    def labels_state(self) -> dict:
        return {
            "refdes_pos": [float(self.refdes_label.pos().x()), float(self.refdes_label.pos().y())],
            "value_pos": [float(self.value_label.pos().x()), float(self.value_label.pos().y())],
            "refdes_manual": bool(getattr(self.refdes_label, "_manual_pos", False)),
            "value_manual": bool(getattr(self.value_label, "_manual_pos", False)),
        }

    def apply_labels_state(self, state: dict):
        try:
            rp = state.get("refdes_pos")
            if isinstance(rp, (list, tuple)) and len(rp) == 2:
                self.refdes_label.set_default_pos(QPointF(float(rp[0]), float(rp[1])))
                self.refdes_label._manual_pos = bool(state.get("refdes_manual", True))
            vp = state.get("value_pos")
            if isinstance(vp, (list, tuple)) and len(vp) == 2:
                self.value_label.set_default_pos(QPointF(float(vp[0]), float(vp[1])))
                self.value_label._manual_pos = bool(state.get("value_manual", True))
            self._update_label()
        except Exception:
            pass

    def boundingRect(self) -> QRectF:
        # Include labels for proper repaint while moving; routing uses routing_local_rect() instead.
        r = self.routing_local_rect()
        for lbl_name in ("refdes_label", "value_label"):
            lbl = getattr(self, lbl_name, None)
            if lbl is not None:
                try:
                    r = r.united(lbl.mapRectToParent(lbl.boundingRect()))
                except Exception:
                    pass
        return r.adjusted(-2, -2, 2, 2)

    def routing_local_rect(self) -> QRectF:
        """Tight component rect used by the router (ignores labels)."""
        r = QRectF(-COMP_WIDTH / 2, -COMP_HEIGHT / 2, COMP_WIDTH, COMP_HEIGHT)
        if self.symbol_item is not None:
            r = r.united(self.symbol_item.mapRectToParent(self.symbol_item.boundingRect()))
        for p in getattr(self, "ports", []):
            if p is not None:
                r = r.united(p.mapRectToParent(p.boundingRect()))
        return r

    def routing_scene_rect(self, pad: float = 0.0) -> QRectF:
        rr = self.mapRectToScene(self.routing_local_rect()).normalized()
        if pad:
            rr = rr.adjusted(-pad, -pad, pad, pad)
        return rr


    def _symbol_path_for_kind(self) -> Optional[Path]:
        comp_def = load_component_library().get(self.kind)
        if comp_def and comp_def.symbol:
            root = Path(__file__).resolve().parent.parent
            assets = root / "assets"
            symbol = str(comp_def.symbol).replace("\\", "/")
            base_sym = assets / "symbols"
            candidate = base_sym / symbol
            if candidate.exists():
                return candidate
        return None
    
    def _load_symbol_graphic(self):
        """Load a JSON symbol and convert it to a QPainterPath."""
        path = self._symbol_path_for_kind()
        if path is None:
            self.symbol_item = None
            # Fallback: simple outline box so the component is visible.
            p = QPainterPath()
            p.addRect(-COMP_WIDTH / 2, -COMP_HEIGHT / 2, COMP_WIDTH, COMP_HEIGHT)
            self.symbol_item = JsonSymbolItem(p)
            self.symbol_item.setParentItem(self)
            self._apply_symbol_theme()
            return

        try:
            data = json.loads(path.read_text())
            p = QPainterPath()
            for shape in data.get("shapes", []):
                t = shape.get("type")
                if t == "line":
                    p.moveTo(shape["x1"], shape["y1"])
                    p.lineTo(shape["x2"], shape["y2"])
                elif t == "rect":
                    p.addRect(shape["x"], shape["y"], shape["w"], shape["h"])
                elif t == "ellipse":
                    p.addEllipse(shape["x"], shape["y"], shape["w"], shape["h"])
                elif t == "polyline":
                    pts = shape.get("points", [])
                    if pts:
                        p.moveTo(pts[0][0], pts[0][1])
                        for x, y in pts[1:]:
                            p.lineTo(x, y)
            if p.isEmpty():
                p = QPainterPath()
                p.addRect(-COMP_WIDTH / 2, -COMP_HEIGHT / 2, COMP_WIDTH, COMP_HEIGHT)
            self.symbol_item = JsonSymbolItem(p)
            self.symbol_item.setParentItem(self)
            self.symbol_item.setPos(0, 0)
            self.symbol_item.setOpacity(1.0)
            self.symbol_item.setVisible(True)
            self._apply_symbol_theme()
            return
        except Exception:
            self.symbol_item = None
            # Fallback: simple outline box so the component is visible.
            p = QPainterPath()
            p.addRect(-COMP_WIDTH / 2, -COMP_HEIGHT / 2, COMP_WIDTH, COMP_HEIGHT)
            self.symbol_item = JsonSymbolItem(p)
            self.symbol_item.setParentItem(self)
            self.symbol_item.setPos(0, 0)
            self.symbol_item.setOpacity(1.0)
            self.symbol_item.setVisible(True)
            self._apply_symbol_theme()
            return

    def _fit_symbol_to_body(self):
        """Scale/align symbol to component body (optional)."""
        if not self.symbol_item:
            return
        if not getattr(self, "_auto_scale_symbol", True):
            # Keep user-drawn symbols at native size; just center them.
            br = self.symbol_item.boundingRect()
            if br.isNull():
                return
            center = br.center()
            self.symbol_item.setScale(1.0)
            self.symbol_item.setPos(-center.x(), -center.y())
            return
        br = self.symbol_item.boundingRect()
        if br.isNull():
            return

        scale_x = (COMP_WIDTH - 12) / br.width()
        scale_y = (COMP_HEIGHT - 12) / br.height()
        scale = min(scale_x, scale_y)
        self.symbol_item.setScale(scale)

        center = br.center()
        self.symbol_item.setPos(-center.x() * scale, -center.y() * scale)

        # Optionally align 1/2-terminal parts to symbol ends.
        if self._auto_align_terminals and getattr(self, "ports", None) and len(self.ports) in (1, 2):
            br_local = self.symbol_item.boundingRect()
            br_mapped = self.symbol_item.mapRectToParent(br_local)
            if len(self.ports) == 1:
                top_center = QPointF(br_mapped.center().x(), br_mapped.top())
                self.ports[0].setPos(top_center)
            else:
                left_center = QPointF(br_mapped.left(), br_mapped.center().y())
                right_center = QPointF(br_mapped.right(), br_mapped.center().y())
                self.ports[0].setPos(left_center)
                self.ports[1].setPos(right_center)

    def _apply_symbol_theme(self):
        if not self.symbol_item:
            return
        color = None
        if self._theme is not None:
            color = self._theme.component_stroke
        else:
            try:
                sc = self.scene()
                bg = sc.backgroundBrush().color() if sc else None
            except Exception:
                bg = None
            if bg is None or not isinstance(bg, QColor):
                try:
                    from PySide6.QtWidgets import QApplication
                    app = QApplication.instance()
                    bg = app.palette().color(app.palette().Window) if app else QColor(245, 246, 248)
                except Exception:
                    bg = QColor(245, 246, 248)
            color = _auto_contrast_color(bg)

        if isinstance(self.symbol_item, JsonSymbolItem):
            pen = self.symbol_item.pen()
            pen.setColor(color)
            pen.setWidthF(2)
            self.symbol_item.setPen(pen)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            scene = self.scene()
            if scene and getattr(scene, 'snap_on', False):
                g = getattr(scene, 'grid_size', 20)
                p = value
                return QPointF(round(p.x()/g)*g, round(p.y()/g)*g)

        if change in (QGraphicsItem.ItemPositionHasChanged, QGraphicsItem.ItemTransformHasChanged):
            if getattr(self, 'ports', None):
                for port in self.ports:
                    for w in list(port.wires):
                        w.update_path()
            self._update_label()
        return super().itemChange(change, value)

    def rotate_cw(self):
        self.setRotation((self.rotation() + 90) % 360)
        for port in getattr(self, 'ports', []):
            for w in list(port.wires):
                w.update_path()
        self._update_label()

    def rotate_ccw(self):
        self.setRotation((self.rotation() - 90) % 360)
        for port in getattr(self, 'ports', []):
            for w in list(port.wires):
                w.update_path()
        self._update_label()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = self.pos()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._press_pos is not None:
            new_pos = self.pos()
            if new_pos != self._press_pos:
                sc = self.scene()
                if sc and getattr(sc, 'on_component_moved', None):
                    sc.on_component_moved(self, self._press_pos, new_pos)
        self._press_pos = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):  # open Properties on double-click
        sc = self.scene()
        if self.is_chip() and sc and getattr(sc, "request_open_chip", None):
            sc.request_open_chip(self)
            e.accept()
            return
        if sc and getattr(sc, "request_properties", None):
            sc.request_properties(self)  # MainWindow._show_properties_for
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


class WireItem(QGraphicsPathItem):
    """Wire with optional waypoints, route mode, and selection handles."""
    def __init__(
            self,
            start_port: Optional[PortItem],
            end_port: Optional[PortItem],
            points: List[QPointF] = None,
            *,
            theme: Theme | None = None,
            start_point: QPointF | None = None,
            end_point: QPointF | None = None,
            attach_end_waypoints: bool = False,
            cap_len: float | None = None,
            route_mode: str = "orth",
    ):
        super().__init__()
        self.setZValue(0)
        pen = QPen(Qt.black, 2)
        pen.setCosmetic(True)
        self.setPen(pen)

        self.port_a = start_port
        self.port_b = end_port
        self._start_point = QPointF(start_point) if start_point is not None else None
        self._end_point = QPointF(end_point) if end_point is not None else None
        self._pts: List[QPointF] = list(points) if points else []
        self._custom_color: QColor | None = None
        self.route_mode = route_mode
        self._handles: list[_Handle] = []
        self._segment_handles: list[_SegmentHandle] = []
        self._updating_handles = False

        if self.port_a is not None:
            self.port_a.add_wire(self)
        if self.port_b is not None:
            self.port_b.add_wire(self)

        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        if theme is None:
            sc = (self.port_a.scene() if self.port_a else None) or (self.port_b.scene() if self.port_b else None)
            theme = getattr(sc, "theme", None)
        if theme:
            self.apply_theme(theme)
        self.update_path()

    def apply_theme(self, theme: Theme, selected: bool | None = None):
        if selected is None:
            selected = self.isSelected()
        pen = self.pen()
        pen.setCosmetic(True)
        pen.setColor(theme.wire_selected if selected else (self._custom_color or theme.wire))
        self.setPen(pen)

    def set_wire_color(self, color: QColor | str | None):
        if isinstance(color, str):
            c = QColor(color)
            self._custom_color = c if c.isValid() else None
        elif isinstance(color, QColor):
            self._custom_color = QColor(color) if color.isValid() else None
        else:
            self._custom_color = None
        sc = self.scene()
        theme = getattr(sc, "theme", None)
        if theme:
            self.apply_theme(theme)
        else:
            pen = self.pen()
            pen.setColor(self._custom_color or Qt.black)
            self.setPen(pen)

    def wire_color_hex(self) -> str:
        return self._custom_color.name() if self._custom_color is not None else ""

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
            sc = self.scene(); theme = getattr(sc, "theme", None)
            if theme:
                self.apply_theme(theme, selected=bool(value))
            if self.route_mode == "orth":
                self._rebuild_handles()
                for h in self._handles:
                    h.setVisible(bool(value))
                for h in self._segment_handles:
                    h.setVisible(bool(value))
        elif change == QGraphicsItem.ItemSceneHasChanged:
            sc = self.scene(); theme = getattr(sc, "theme", None)
            if theme:
                self.apply_theme(theme)
        return super().itemChange(change, value)

    def _endpoint_pos(self, port: Optional[PortItem], fallback: QPointF | None) -> QPointF:
        if port is not None:
            return port.scenePos()
        if fallback is not None:
            return QPointF(fallback)
        return QPointF()

    def points(self) -> list[QPointF]:
        out: list[QPointF] = []
        out.append(self._endpoint_pos(self.port_a, self._start_point))
        out.extend(self._pts)
        out.append(self._endpoint_pos(self.port_b, self._end_point))
        cleaned = [out[0]]
        for p in out[1:]:
            if (p - cleaned[-1]).manhattanLength() > 1e-6:
                cleaned.append(p)
        return cleaned

    def set_points(self, pts: list[QPointF]):
        self._pts = pts[:] if pts else []
        self.update_path()

    def _manhattan_points(self) -> list[QPointF]:
        pts = self.points()
        if not pts:
            return []
        out = [pts[0]]
        for q in pts[1:]:
            p = out[-1]
            if abs(p.x() - q.x()) < 1e-6 or abs(p.y() - q.y()) < 1e-6:
                out.append(q)
            else:
                out.append(QPointF(q.x(), p.y()))
                out.append(q)
        return out

    def update_path(self):
        pts = self.render_points()
        if not pts:
            self.setPath(QPainterPath())
            return
        p = QPainterPath(pts[0])
        for q in pts[1:]:
            p.lineTo(q)
        self.setPath(p)
        sc = self.scene()
        if sc and hasattr(sc, "_rebuild_junction_markers"):
            sc._rebuild_junction_markers()
        self._sync_handles()

    def detach(self, scene):
        if hasattr(self.port_a, "remove_wire"):
            try: self.port_a.remove_wire(self)
            except Exception: pass
        if hasattr(self.port_b, "remove_wire"):
            try: self.port_b.remove_wire(self)
            except Exception: pass
        self._clear_handles(scene)

    def attach(self):
        if hasattr(self.port_a, "add_wire"):
            self.port_a.add_wire(self)
        if hasattr(self.port_b, "add_wire"):
            self.port_b.add_wire(self)

    def _rebuild_handles(self):
        if self.route_mode != "orth":
            return
        sc = self.scene()
        self._clear_handles(sc)
        self._updating_handles = True
        for i, pt in enumerate(self._pts):
            h = _Handle(self, i)
            h.setParentItem(self)
            h.setPos(pt)
            h.setVisible(self.isSelected())
            self._handles.append(h)
        spine = self._manhattan_points()
        for i in range(1, len(spine) - 2):
            h = _SegmentHandle(self, i)
            h.setParentItem(self)
            h.setVisible(self.isSelected())
            self._segment_handles.append(h)
        self._updating_handles = False
        self._sync_handles()

    def _sync_handles(self):
        if self._updating_handles:
            return
        if self.route_mode != "orth":
            return
        self._updating_handles = True
        for i, h in enumerate(self._handles):
            if i < len(self._pts):
                h.setPos(self._pts[i])
            h.setVisible(self.isSelected())
        spine = self._manhattan_points()
        for h in self._segment_handles:
            i = h.seg_idx
            if i <= 0 or i + 1 >= len(spine) - 1:
                h.setVisible(False)
                continue
            a = spine[i]
            b = spine[i + 1]
            if (b - a).manhattanLength() < 1e-6:
                h.setVisible(False)
                continue
            h.setPos(QPointF((a.x() + b.x()) * 0.5, (a.y() + b.y()) * 0.5))
            h.setVisible(self.isSelected())
        self._updating_handles = False

    def _clear_handles(self, sc):
        for h in self._handles:
            try:
                if sc:
                    sc.removeItem(h)
            except Exception:
                pass
        for h in self._segment_handles:
            try:
                if sc:
                    sc.removeItem(h)
            except Exception:
                pass
        self._handles = []
        self._segment_handles = []

    def _set_pts_from_spine(self, spine: list[QPointF]):
        if len(spine) <= 2:
            self._pts = []
        else:
            self._pts = [QPointF(p) for p in spine[1:-1]]

    def render_points(self) -> list[QPointF]:
        if self.route_mode == "orth":
            return self._manhattan_points()
        return self.points()


class _Handle(QGraphicsEllipseItem):
    def __init__(self, wire: WireItem, idx: int):
        super().__init__(-4.0, -4.0, 8.0, 8.0)
        self.wire = wire
        self.idx = idx
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(Qt.darkGray, 1))
        self.setZValue(3)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            if getattr(self.wire, "_updating_handles", False):
                return value
            scene = self.scene()
            mouse = value
            if hasattr(scene, "_snap_point"):
                mouse = scene._snap_point(mouse)

            w = self.wire
            i = self.idx
            prev_pt = w._endpoint_pos(w.port_a, w._start_point) if i == 0 else w._pts[i - 1]
            next_pt = w._endpoint_pos(w.port_b, w._end_point) if i == len(w._pts) - 1 else w._pts[i + 1]

            cand1 = QPointF(prev_pt.x(), mouse.y())
            cand2 = QPointF(mouse.x(), next_pt.y())
            d1 = (cand1.x() - mouse.x()) ** 2 + (cand1.y() - mouse.y()) ** 2
            d2 = (cand2.x() - mouse.x()) ** 2 + (cand2.y() - mouse.y()) ** 2
            newpos = cand1 if d1 <= d2 else cand2

            w._pts[i] = newpos
            w.update_path()
            return newpos
        return super().itemChange(change, value)


class _SegmentHandle(QGraphicsRectItem):
    def __init__(self, wire: WireItem, seg_idx: int):
        super().__init__(-5.0, -5.0, 10.0, 10.0)
        self.wire = wire
        self.seg_idx = seg_idx
        self.setBrush(QBrush(Qt.lightGray))
        self.setPen(QPen(Qt.darkGray, 1))
        self.setZValue(3)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            if getattr(self.wire, "_updating_handles", False):
                return value
            scene = self.scene()
            mouse = value
            if hasattr(scene, "_snap_point"):
                mouse = scene._snap_point(mouse)

            w = self.wire
            spine = w._manhattan_points()
            i = self.seg_idx
            if i <= 0 or i + 1 >= len(spine) - 1:
                return self.pos()

            a = QPointF(spine[i])
            b = QPointF(spine[i + 1])
            if abs(a.x() - b.x()) < 1e-6:
                nx = mouse.x()
                spine[i].setX(nx)
                spine[i + 1].setX(nx)
                newpos = QPointF(nx, (a.y() + b.y()) * 0.5)
            elif abs(a.y() - b.y()) < 1e-6:
                ny = mouse.y()
                spine[i].setY(ny)
                spine[i + 1].setY(ny)
                newpos = QPointF((a.x() + b.x()) * 0.5, ny)
            else:
                return self.pos()

            w._set_pts_from_spine(spine)
            w.update_path()
            return newpos
        return super().itemChange(change, value)
