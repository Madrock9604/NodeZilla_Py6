# ========================================
# File: nodezilla/graphics_items.py
# ========================================
from __future__ import annotations
from typing import Optional, List
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QBrush, QPen, QPainter, QPainterPath
from PySide6.QtWidgets import (
QGraphicsItem, QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsTextItem,
QGraphicsPathItem
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .schematic_scene import SchematicScene


PORT_RADIUS = 5.0
COMP_WIDTH = 100.0
COMP_HEIGHT = 40.0


class InlineLabel(QGraphicsTextItem):
    def __init__(self, parent_item: 'ComponentItem'):
        super().__init__("", parent_item)
        self._parent = parent_item
        # Default color follows app palette at runtime
        self.setDefaultTextColor(self._text_color())
        self.setZValue(3)
        self.setTextInteractionFlags(Qt.NoTextInteraction)


    def _text_color(self):
        from PySide6.QtWidgets import QApplication
        return QApplication.instance().palette().text().color()


    def start_edit(self):
        from PySide6.QtGui import QTextCursor
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus()
        c = self.textCursor()
        c.movePosition(QTextCursor.End)
        self.setTextCursor(c)


    def _finish_edit(self):
        raw = self.toPlainText().strip()
        if raw:
            parts = raw.split()
            refdes = parts[0]
            value = " ".join(parts[1:]) if len(parts) > 1 else self._parent.value
            self._parent.set_refdes(refdes)
            self._parent.set_value(value)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self._parent._update_label()


    def focusOutEvent(self, e):
        self._finish_edit(); super().focusOutEvent(e)


    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
            self._finish_edit(); e.accept(); return
        super().keyPressEvent(e)


class PortItem(QGraphicsEllipseItem):
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


class ComponentItem(QGraphicsRectItem):
    def __init__(self, kind: str, pos: QPointF):
        super().__init__(-COMP_WIDTH/2, -COMP_HEIGHT/2, COMP_WIDTH, COMP_HEIGHT)
        self.kind = kind
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(Qt.black, 1.5))
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

        # inline label
        self.label = InlineLabel(self)
        self.label.setZValue(3)

        # ports
        self.port_left = PortItem(self, 'A', QPointF(-COMP_WIDTH/2, 0))
        self.port_right = PortItem(self, 'B', QPointF(COMP_WIDTH/2, 0))
        lk = self.kind.lower()
        if lk.startswith('ground') or lk.startswith('gnd'):
            p = QPointF(0, -COMP_HEIGHT/2)
            self.port_left.setPos(p)
            self.port_right.setPos(p)

        # initial position + label
        self.setPos(pos)
        self._update_label()

        # for undoable moves
        self._press_pos: Optional[QPointF] = None

    def _update_label(self):
        from PySide6.QtWidgets import QApplication
        text = (self.refdes + (" " if self.refdes and self.value else "") + self.value).strip()
        self.label.setDefaultTextColor(QApplication.instance().palette().text().color())
        self.label.setPlainText(text)
        br = self.rect()
        self.label.setPos(-self.label.boundingRect().width()/2, br.top() - 18)
        # keep text upright as component rotates
        self.label.setRotation(-self.rotation())

    def set_refdes(self, refdes: str):
        self.refdes = refdes
        self._update_label()

    def set_value(self, value: str):
        self.value = value
        self._update_label()

    def boundingRect(self) -> QRectF:
        return super().boundingRect().adjusted(-14, -24, 14, 14)

    def paint(self, p: QPainter, opt, widget=None):
        super().paint(p, opt, widget)
        p.setPen(QPen(Qt.black, 1.6))
        r = self.rect()
        k = self.kind.lower()

        if k.startswith('res'):
            path = QPainterPath(QPointF(r.left()+10, 0))
            s = 10
            for i in range(6):
                y = -8 if i % 2 == 0 else 8
                path.lineTo(path.currentPosition().x() + s, y)
            path.lineTo(r.right()-10, 0)
            p.drawPath(path)

        elif k.startswith('cap'):
            p.drawLine(r.left()+20, 0, -5, 0)
            p.drawLine(-5, -12, -5, 12)
            p.drawLine(5, -12, 5, 12)
            p.drawLine(5, 0, r.right()-20, 0)

        elif k.startswith('vsource') or k.startswith('vs'):
            p.setBrush(Qt.NoBrush)
            p.drawLine(r.left()+15, 0, r.left()+30, 0)
            p.drawEllipse(QPointF(0,0), 12, 12)
            p.drawLine(-6, 0, 6, 0)
            p.drawLine(0, -6, 0, 6)
            p.drawLine(r.right()-30, 0, r.right()-15, 0)

        elif k.startswith('ind'):
            path = QPainterPath(QPointF(r.left()+15, 0))
            step = (r.width()-30)/6.0
            x = r.left()+15
            for _ in range(4):
                cx1 = x + step*0.5
                cx2 = x + step
                path.quadTo(QPointF(cx1, -10), QPointF(cx2, 0))
                x += step
            p.drawPath(path)

        elif k.startswith('dio'):
            tri_left = r.left()+20
            tri_right = r.right()-25
            path = QPainterPath(QPointF(tri_left, -12))
            path.lineTo(tri_left, 12)
            path.lineTo(tri_right, 0)
            path.closeSubpath()
            p.drawPath(path)
            p.drawLine(tri_right, -14, tri_right, 14)

        elif k.startswith('ground') or k.startswith('gnd'):
            base_y = r.bottom()-8
            p.drawLine(0, base_y-18, 0, base_y-6)
            p.drawLine(-14, base_y-6, 14, base_y-6)
            p.drawLine(-10, base_y, 10, base_y)
            p.drawLine(-6, base_y+6, 6, base_y+6)

        elif k.startswith('isource') or k.startswith('current'):
            p.setBrush(Qt.NoBrush)
            p.drawLine(r.left()+15, 0, r.left()+30, 0)
            p.drawEllipse(QPointF(0,0), 12, 12)
            p.drawLine(0, 8, 0, -8)
            p.drawLine(0, -8, -4, -2)
            p.drawLine(0, -8, 4, -2)
            p.drawLine(r.right()-30, 0, r.right()-15, 0)

        else:
            p.drawText(self.rect(), Qt.AlignCenter, self.kind)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            scene = self.scene()
            if scene and getattr(scene, 'snap_on', False):
                g = getattr(scene, 'grid_size', 20)
                p = value
                return QPointF(round(p.x()/g)*g, round(p.y()/g)*g)

        if change in (QGraphicsItem.ItemPositionHasChanged, QGraphicsItem.ItemTransformHasChanged):
            if hasattr(self, 'port_left') and hasattr(self, 'port_right'):
                for port in (self.port_left, self.port_right):
                    for w in list(port.wires):
                        w.update_path()
            self._update_label()
        return super().itemChange(change, value)

    def rotate_cw(self):
        self.setRotation((self.rotation() + 90) % 360)
        for port in (self.port_left, self.port_right):
            for w in list(port.wires):
                w.update_path()
        self._update_label()

    def rotate_ccw(self):
        self.setRotation((self.rotation() - 90) % 360)
        for port in (self.port_left, self.port_right):
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
        if sc and getattr(sc, "request_properties", None):
            sc.request_properties(self)  # MainWindow._show_properties_for
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


class WireItem(QGraphicsPathItem):
    def __init__(self, a: PortItem, b: PortItem):
        super().__init__()
        self.setZValue(0)
        self.setPen(QPen(Qt.black, 2))
        self.port_a = a
        self.port_b = b
        self.port_a.add_wire(self)
        self.port_b.add_wire(self)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.update_path()

    def update_path(self):
        p1 = self.port_a.scenePos()
        p2 = self.port_b.scenePos()
        mid_x = (p1.x() + p2.x()) / 2
        path = QPainterPath(p1)
        path.lineTo(mid_x, p1.y())
        path.lineTo(mid_x, p2.y())
        path.lineTo(p2)
        self.setPath(path)

    # helpers for undo/redo
    def attach(self, scene: 'SchematicScene'):
        self.port_a.add_wire(self)
        self.port_b.add_wire(self)
        scene.addItem(self)
        self.update_path()

    def detach(self, scene: 'SchematicScene'):
        self.port_a.remove_wire(self)
        self.port_b.remove_wire(self)
        scene.removeItem(self)
