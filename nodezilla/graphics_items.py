# ========================================
# File: nodezilla/graphics_items.py
# ========================================
from __future__ import annotations
from pathlib import Path
from typing import Optional, List
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QBrush, QPen, QPainterPath, QPainterPathStroker
from PySide6.QtWidgets import (
QGraphicsItem, QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsTextItem,
QGraphicsPathItem, QGraphicsColorizeEffect
)
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .schematic_scene import SchematicScene
from .theme import Theme

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

    def apply_theme(self, theme: Theme):
        from PySide6.QtGui import QBrush, QPen
        # Keep port fill visible against bg; white works in both themes
        self.setBrush(QBrush(Qt.white))
        self.setPen(QPen(theme.component_stroke, 1.25))

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
        self.setBrush(QBrush(Qt.NoBrush))
        self.setPen(QPen(Qt.NoPen))
        self._theme: Theme | None = None
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

        # svg symbol
        self.symbol_item: QGraphicsSvgItem | None = None
        self._symbol_effect: QGraphicsColorizeEffect | None = None

        # ports
        self.ports: List[PortItem] = []
        lk = self.kind.lower()
        is_ground = lk.startswith('ground') or lk.startswith('gnd')

        # Most parts are 2-terminal; ground should be 1-terminal.
        if is_ground:
            p = PortItem(self, 'A', QPointF(0, -COMP_HEIGHT/2))
            self.ports = [p]
            self.port_left = p
            self.port_right = None
        else:
            self.port_left = PortItem(self, 'A', QPointF(-COMP_WIDTH/2, 0))
            self.port_right = PortItem(self, 'B', QPointF(COMP_WIDTH/2, 0))
            self.ports = [self.port_left, self.port_right]

        # initial position + label
        self.setPos(pos)
        self._update_label()

        # symbol artwork
        self._load_symbol_graphic()

        # for undoable moves
        self._press_pos: Optional[QPointF] = None

    def apply_theme(self, theme: Theme):
        self._theme = theme
        #self.setBrush(QBrush(theme.component_fill))
        #self.setPen(QPen(theme.component_stroke, 1.5))
        # label: whichever attribute holds it (adjust name if different)
        if hasattr(self, "label") and self.label is not None:
            self.label.setDefaultTextColor(theme.text)
        self._apply_symbol_theme()

    def _update_label(self):
        from PySide6.QtWidgets import QApplication
        text = (self.refdes + (" " if self.refdes and self.value else "") + self.value).strip()
        sc = self.scene()
        theme = getattr(sc, "theme", None) if sc else None
        if theme:
            self.label.setDefaultTextColor(theme.text)
        else:
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
        # Ensure the component's bounding rect encloses its body *and* children (symbol SVG, ports, labels).
        base = QRectF(-COMP_WIDTH / 2, -COMP_HEIGHT / 2, COMP_WIDTH, COMP_HEIGHT)
        r = base.united(self.childrenBoundingRect())
        return r.adjusted(-14, -24, 14, 14)


    def _symbol_path_for_kind(self) -> Optional[Path]:
        base = Path(__file__).resolve().parent.parent / "assets" / "svg"
        k = self.kind.lower()
        mapping = (
            ("res", "resistor"),
            ("cap", "capacitor"),
            ("ind", "inductor"),
            (("vsource", "vs"), "voltage_source"),
            (("isource", "current"), "current_source"),
            ("dio", "diode"),
            (("ground", "gnd"), "ground"),
        )

        for prefixes, filename in mapping:
            if isinstance(prefixes, str):
                prefixes = (prefixes,)
            if any(k.startswith(pfx) for pfx in prefixes):
                candidate = base / f"{filename}.svg"
                return candidate if candidate.exists() else None
        return None
    
    def _load_symbol_graphic(self):
        path = self._symbol_path_for_kind()
        if path is None:
            self.symbol_item = None
            return
        
        self.symbol_item = QGraphicsSvgItem(str(path), self)
        self.symbol_item.setZValue(2)
        self._fit_symbol_to_body()
        self._apply_symbol_theme()

    def _fit_symbol_to_body(self):
        if not self.symbol_item:
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

        # Reposition ports to the ends of the symbol terminals
        # Map the symbol's bounding rect into the ComponentItem's local coordinates
        br_local = self.symbol_item.boundingRect()
        br_mapped = self.symbol_item.mapRectToParent(br_local)

        # 2-terminal parts: left/right; 1-terminal (GND): top-center.
        if getattr(self, "ports", None) and len(self.ports) == 1:
            top_center = QPointF(br_mapped.center().x(), br_mapped.top())
            self.ports[0].setPos(top_center)
        else:
            left_center = QPointF(br_mapped.left(), br_mapped.center().y())
            right_center = QPointF(br_mapped.right(), br_mapped.center().y())
            if getattr(self, "port_left", None) is not None:
                self.port_left.setPos(left_center)
            if getattr(self, "port_right", None) is not None:
                self.port_right.setPos(right_center)

    def _apply_symbol_theme(self):
        if not self.symbol_item:
            return

        if self._theme:
            #Lazily create the colorize effect without a QGraphicsItem parent
            if self._symbol_effect is None:
                self._symbol_effect = QGraphicsColorizeEffect()
                #Attach the effect to the SVG item
                self.symbol_item.setGraphicsEffect(self._symbol_effect)

            self._symbol_effect.setColor(self._theme.component_stroke)
            self._symbol_effect.setStrength(1.0)
        else:
            #clear any existing effect if there is no theme
            if self.symbol_item.graphicsEffect():
                self.symbol_item.setGraphicsEffect(None)
            self._symbol_effect = None

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
        if sc and getattr(sc, "request_properties", None):
            sc.request_properties(self)  # MainWindow._show_properties_for
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


class WireItem(QGraphicsPathItem):
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
        self._pts: List[QPointF] = list(points) if points else []  # waypoints only (no endpoints)
        self._handles: list[_Handle] = []

        # Optional "escape" length (in scene units) to step away from the component body.
        self._cap_len = float(cap_len) if cap_len is not None else 0.0

        self._attach_end_waypoints = bool(attach_end_waypoints)
        self._cap_vec_a: QPointF | None = None
        self._cap_vec_b: QPointF | None = None

        # Track whether the user explicitly edited the route (via handles)
        self._user_locked = False
        # Remember whether the single-corner route is HV (horizontal then vertical) or VH
        self._corner_mode: str | None = None  # 'HV' or 'VH'

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

    # --- theming ---
    def apply_theme(self, theme: Theme, selected: bool | None = None):
        if selected is None:
            selected = self.isSelected()
        pen = self.pen()
        pen.setCosmetic(True)
        pen.setColor(theme.wire_selected if selected else theme.wire)
        self.setPen(pen)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
            sc = self.scene(); theme = getattr(sc, "theme", None)
            if theme: self.apply_theme(theme, selected=bool(value))
            # show/hide waypoint handles when selected toggles
            for h in self._handles: h.setVisible(bool(value))
        elif change == QGraphicsItem.ItemSceneHasChanged:
            sc = self.scene(); theme = getattr(sc, "theme", None)
            if theme: self.apply_theme(theme)
        return super().itemChange(change, value)

    # --- geometry ---
    def _endpoint_pos(self, port: Optional[PortItem], fallback: QPointF | None) -> QPointF:
        if port is not None:
            return port.scenePos()
        if fallback is not None:
            return QPointF(fallback)
        return QPointF()

    def _cap_point(self, port: Optional[PortItem], length: float) -> Optional[QPointF]:
        """Return a small escape point away from the component body, so wires do not run through symbols.

        IMPORTANT: do NOT use the component bounding-box center for direction (labels/children shift it).
        Instead, derive direction from the port LOCAL position in the component coordinate system, map
        that direction to scene (handles rotation), and finally snap it to the dominant scene axis.
        """
        if port is None or length <= 0:
            return None
        parent = port.parentItem()
        if parent is None:
            return None

        lp = port.pos()
        if abs(lp.x()) >= abs(lp.y()):
            d_local = QPointF(-1.0 if lp.x() < 0 else 1.0, 0.0)
        else:
            d_local = QPointF(0.0, -1.0 if lp.y() < 0 else 1.0)

        p0 = parent.mapToScene(QPointF(0.0, 0.0))
        p1 = parent.mapToScene(d_local)
        v = p1 - p0
        if abs(v.x()) >= abs(v.y()):
            dx, dy = (1.0, 0.0) if v.x() >= 0 else (-1.0, 0.0)
        else:
            dx, dy = (0.0, 1.0) if v.y() >= 0 else (0.0, -1.0)

        sp = port.scenePos()
        return QPointF(sp.x() + dx * float(length), sp.y() + dy * float(length))

    def _anchor_for_port(self, port: Optional[PortItem], fallback: QPointF | None, pad: float, cap_len: float) -> QPointF:
        """Return an anchor outside the component body for routing."""
        end = self._endpoint_pos(port, fallback)
        if port is None:
            return end

        parent = port.parentItem()
        if parent is None:
            return end

        lp = port.pos()
        if abs(lp.x()) >= abs(lp.y()):
            d_local = QPointF(-1.0 if lp.x() < 0 else 1.0, 0.0)
        else:
            d_local = QPointF(0.0, -1.0 if lp.y() < 0 else 1.0)

        p0 = parent.mapToScene(QPointF(0.0, 0.0))
        p1 = parent.mapToScene(d_local)
        v = p1 - p0
        if abs(v.x()) >= abs(v.y()):
            dx, dy = (-1.0, 0.0) if v.x() < 0 else (1.0, 0.0)
        else:
            dx, dy = (0.0, -1.0) if v.y() < 0 else (0.0, 1.0)

        try:
            local = parent.boundingRect().united(parent.childrenBoundingRect())
            obst = parent.mapRectToScene(local).normalized().adjusted(-pad, -pad, pad, pad)
        except Exception:
            obst = parent.sceneBoundingRect().normalized().adjusted(-pad, -pad, pad, pad)

        gs = float(getattr(self.scene(), "grid_size", 20) or 20)
        length = float(cap_len)
        p = QPointF(end.x() + dx * length, end.y() + dy * length)
        it = 0
        while obst.contains(p) and it < 128:
            length += gs
            p = QPointF(end.x() + dx * length, end.y() + dy * length)
            it += 1
        return p

    def _path_hits_obstacles(self, rects: list[QRectF]) -> bool:
        sc = self.scene()
        if not sc or not hasattr(sc, "_segment_clear"):
            return False
        gs = float(getattr(sc, "grid_size", 20) or 20)
        pad = max(6.0, gs * 0.35)
        cap_len = gs * 1.5
        anchors = [
            self._anchor_for_port(self.port_a, self._start_point, pad, cap_len),
            *self._pts,
            self._anchor_for_port(self.port_b, self._end_point, pad, cap_len),
        ]
        pts = self._manhattan_points_for(anchors)
        for i in range(len(pts) - 1):
            if not sc._segment_clear(pts[i], pts[i + 1], rects):
                return True
        return False

    def _manhattan_points_for(self, pts: list[QPointF]) -> list[QPointF]:
        """Return points with only 90° segments (insert doglegs as needed)."""
        if not pts:
            return []
        out = [pts[0]]
        for q in pts[1:]:
            p = out[-1]
            if p.x() == q.x() or p.y() == q.y():
                out.append(q)
            else:
                out.append(QPointF(q.x(), p.y()))
                out.append(q)
        return out

    def _reroute_around_obstacles(self, rects: list[QRectF]) -> bool:
        sc = self.scene()
        if not sc or not hasattr(sc, "_route_orthogonal"):
            return False
        if not rects:
            return False

        gs = float(getattr(sc, "grid_size", 20) or 20)
        pad = max(6.0, gs * 0.35)
        cap_len = gs * 1.5

        a_end = self._endpoint_pos(self.port_a, self._start_point)
        b_end = self._endpoint_pos(self.port_b, self._end_point)
        a_anchor = self._anchor_for_port(self.port_a, self._start_point, pad, cap_len)
        b_anchor = self._anchor_for_port(self.port_b, self._end_point, pad, cap_len)

        anchors: list[QPointF] = []
        snap = getattr(sc, "_snap_point", None)
        def _snap(p: QPointF) -> QPointF:
            return snap(p) if snap and getattr(sc, "snap_on", True) else p

        anchors.append(_snap(a_anchor))
        for p in self._pts:
            anchors.append(_snap(p))
        anchors.append(_snap(b_anchor))

        routed: list[QPointF] = []
        for i in range(len(anchors) - 1):
            seg = sc._route_orthogonal(anchors[i], anchors[i + 1], rects)
            if not routed:
                routed.extend(seg)
            else:
                routed.extend(seg[1:])

        routed = sc._simplify_points(routed) if hasattr(sc, "_simplify_points") else routed
        if not routed:
            return False

        pts = routed[:]
        if self.port_a is None and pts and (pts[0] - a_end).manhattanLength() < 1e-6:
            pts = pts[1:]
        if self.port_b is None and pts and (pts[-1] - b_end).manhattanLength() < 1e-6:
            pts = pts[:-1]

        if pts != self._pts:
            self._pts = pts
            self._corner_mode = None
            return True
        return False

    def _endpoint_components(self) -> set[ComponentItem]:
        comps: set[ComponentItem] = set()
        if self.port_a is not None:
            parent = self.port_a.parentItem()
            if isinstance(parent, ComponentItem):
                comps.add(parent)
        if self.port_b is not None:
            parent = self.port_b.parentItem()
            if isinstance(parent, ComponentItem):
                comps.add(parent)
        return comps

    def points(self) -> list[QPointF]:
        # Endpoints (port positions) + optional escape caps + waypoints
        a_end = self._endpoint_pos(self.port_a, self._start_point)
        b_end = self._endpoint_pos(self.port_b, self._end_point)

        out: list[QPointF] = [a_end]

        cap_a = self._cap_point(self.port_a, self._cap_len) if (self.port_a is not None and self._cap_len > 0) else None
        if cap_a is not None and (cap_a - a_end).manhattanLength() > 1e-6:
            out.append(cap_a)

        out.extend(self._pts)

        cap_b = self._cap_point(self.port_b, self._cap_len) if (self.port_b is not None and self._cap_len > 0) else None
        if cap_b is not None and (cap_b - b_end).manhattanLength() > 1e-6:
            out.append(cap_b)

        out.append(b_end)

        # drop consecutive duplicates
        cleaned = [out[0]]
        for p in out[1:]:
            if (p - cleaned[-1]).manhattanLength() > 1e-6:
                cleaned.append(p)
        return cleaned

    def set_points(self, pts: list[QPointF]):
        self._pts = pts[:] if pts else []
        self._corner_mode = None
        self._user_locked = False
        self.update_path()
        self._rebuild_handles()

    def update_path(self):
        # Keep endcap waypoints attached to moving ports (if enabled).
        if getattr(self, "_attach_end_waypoints", False):
            a_end = self._endpoint_pos(self.port_a, self._start_point)
            b_end = self._endpoint_pos(self.port_b, self._end_point)
            if self.port_a is not None and len(self._pts) >= 1:
                if self._cap_vec_a is None:
                    self._cap_vec_a = self._pts[0] - a_end
                self._pts[0] = a_end + self._cap_vec_a
            if self.port_b is not None and len(self._pts) >= 1:
                if self._cap_vec_b is None:
                    self._cap_vec_b = self._pts[-1] - b_end
                self._pts[-1] = b_end + self._cap_vec_b

        # Auto-adjust a single corner waypoint so wires shrink/grow when endpoints move.
        if (not self._user_locked) and len(self._pts) == 1:
            pts_full = self.points()
            if len(pts_full) >= 2:
                # use the "routable" endpoints: if caps exist, they are included in pts_full
                a = pts_full[0]
                b = pts_full[-1]
                # If aligned, no corner needed
                if abs(a.x() - b.x()) < 1e-6 or abs(a.y() - b.y()) < 1e-6:
                    self._pts = []
                else:
                    w = self._pts[0]
                    if self._corner_mode is None:
                        # infer based on current waypoint shape
                        if abs(w.x() - b.x()) < 1e-6 and abs(w.y() - a.y()) < 1e-6:
                            self._corner_mode = "HV"
                        elif abs(w.x() - a.x()) < 1e-6 and abs(w.y() - b.y()) < 1e-6:
                            self._corner_mode = "VH"
                        else:
                            self._corner_mode = "HV"
                    self._pts[0] = QPointF(b.x(), a.y()) if self._corner_mode == "HV" else QPointF(a.x(), b.y())

        # If auto-routed and obstacles now intersect the path, reroute to avoid components.
        sc = self.scene()
        rerouted = False
        if sc and not self._user_locked and hasattr(sc, "_obstacle_rects"):
            gs = float(getattr(sc, "grid_size", 20) or 20)
            pad = max(6.0, gs * 0.35)
            rects = sc._obstacle_rects(pad=pad, exclude=set())
            if rects and self._path_hits_obstacles(rects):
                rerouted = self._reroute_around_obstacles(rects)
                if rerouted and self.isSelected():
                    self._rebuild_handles()

        pts = self._manhattan_points()
        if not pts:
            self.setPath(QPainterPath()); return
        p = QPainterPath(pts[0])
        for q in pts[1:]:
            p.lineTo(q)
        self.setPath(p)
        sc = self.scene()
        if sc and hasattr(sc, "_rebuild_junction_markers"):
            sc._rebuild_junction_markers()

    def shape(self):
        stroker = QPainterPathStroker()
        stroker.setWidth(12)            # easier to click
        return stroker.createStroke(self.path())

    # --- handles for waypoints ---
    def _rebuild_handles(self):
        sc = self.scene()
        for h in self._handles:
            if sc: sc.removeItem(h)
        self._handles.clear()
        for i, pt in enumerate(self._pts):
            h = _Handle(self, i)
            h.setParentItem(self)
            h.setPos(pt)
            h.setVisible(self.isSelected())  # follows selection
            self._handles.append(h)

    # --- helpers for inserting a waypoint on nearest segment ---
    def closest_segment_point(self, p: QPointF) -> tuple[int, QPointF]:
        pts = self._manhattan_points()
        best_d2 = float("inf"); insert_idx = 0; best_q = pts[0]
        for i in range(len(pts)-1):
            a, b = pts[i], pts[i+1]
            abx, aby = (b.x()-a.x()), (b.y()-a.y())
            ab2 = abx*abx + aby*aby
            if ab2 == 0:
                q = a
            else:
                t = ((p.x()-a.x())*abx + (p.y()-a.y())*aby) / ab2
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                q = QPointF(a.x()+t*abx, a.y()+t*aby)
            d2 = (q.x()-p.x())**2 + (q.y()-p.y())**2
            if d2 < best_d2:
                best_d2, best_q, insert_idx = d2, q, i
        # insert between pts[insert_idx] and pts[insert_idx+1]
        return insert_idx, best_q

    def detach(self, scene):
        """Detach from ports and scene-safe cleanup."""
        # remove from each port’s wire list (if your PortItem exposes remove_wire)
        if hasattr(self.port_a, "remove_wire"):
            try: self.port_a.remove_wire(self)
            except Exception: pass
        if hasattr(self.port_b, "remove_wire"):
            try: self.port_b.remove_wire(self)
            except Exception: pass
        # drop handles if you added waypoint handles
        for h in getattr(self, "_handles", []):
            try:
                if scene: scene.removeItem(h)
            except Exception:
                pass
        self._handles = []

    def attach(self):
        """Reattach to ports (used by undo)."""
        if hasattr(self.port_a, "add_wire"):
            self.port_a.add_wire(self)
        if hasattr(self.port_b, "add_wire"):
            self.port_b.add_wire(self)

    def _manhattan_points(self):
        """Return points with only 90° segments (insert doglegs as needed)."""
        pts = self.points()
        if not pts: return []
        out = [pts[0]]
        for q in pts[1:]:
            p = out[-1]
            if p.x() == q.x() or p.y() == q.y():
                # already orthogonal
                out.append(q)
            else:
                # insert a right-angle corner: horizontal then vertical
                out.append(QPointF(q.x(), p.y()))
                out.append(q)
        return out

    def setSelected(self, sel: bool):
        super().setSelected(sel)
        # If no explicit waypoints yet, materialize the current L-corner
        if sel and not self._pts:
            pts_full = self.points()
            if len(pts_full) >= 2:
                a = pts_full[0]
                b = pts_full[-1]
                if a.x() != b.x() and a.y() != b.y():
                    corner = QPointF(b.x(), a.y())  # same “L” you display
                    self.set_points([corner])       # becomes draggable
        # show/hide handles
        for h in getattr(self, "_handles", []):
            h.setVisible(sel)
class _Handle(QGraphicsEllipseItem):
    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            scene = self.scene()
            mouse = value
            # Snap if your scene supports it
            if hasattr(scene, "_snap_point"):
                mouse = scene._snap_point(mouse)

            w = self.wire
            w._user_locked = True
            i = self.idx
            # neighbors (endpoints come from ports)
            prev_pt = w._endpoint_pos(w.port_a, w._start_point) if i == 0 else w._pts[i-1]
            next_pt = w._endpoint_pos(w.port_b, w._end_point) if i == len(w._pts)-1 else w._pts[i+1]

            # Constrain to Manhattan “cross”: choose the closer option
            cand1 = QPointF(prev_pt.x(), mouse.y())  # keep x aligned with prev, move y
            cand2 = QPointF(mouse.x(), next_pt.y())  # keep y aligned with next, move x
            # Pick the one closer to the mouse
            d1 = (cand1.x()-mouse.x())**2 + (cand1.y()-mouse.y())**2
            d2 = (cand2.x()-mouse.x())**2 + (cand2.y()-mouse.y())**2
            newpos = cand1 if d1 <= d2 else cand2

            w._pts[i] = newpos
            w.update_path()
            return newpos  # tell Qt the new snapped/orthogonal position
        return super().itemChange(change, value)
