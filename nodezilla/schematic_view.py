# ========================================
# File: nodezilla/schematic_view.py
# ========================================
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsView

class SchematicView(QGraphicsView):
    """QGraphicsView with zoom + right-drag panning."""
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setMouseTracking(True)
        if self.viewport():
            self.viewport().setMouseTracking(True)
        self._panning = False
        self._pan_start = QPointF()
        self._pan_drag_mode = QGraphicsView.RubberBandDrag

    def wheelEvent(self, e):
        """Zoom around the mouse cursor."""
        z_in = 1.15; z_out = 1/1.15
        old = self.mapToScene(e.position().toPoint())
        f = z_in if e.angleDelta().y() > 0 else z_out
        self.scale(f, f)
        new = self.mapToScene(e.position().toPoint())
        d = new - old; self.translate(d.x(), d.y())

    def mousePressEvent(self, e):
        """Right-drag to pan; left-click empty area clears selection."""
        if e.button() == Qt.RightButton:
            sc = self.scene()
            if sc is not None:
                mode = getattr(sc, "mode", None)
                modes = getattr(sc, "Mode", object())
                # In placement/wiring flows, let scene consume right-click as cancel.
                if mode in (
                    getattr(modes, "PLACE", None),
                    getattr(modes, "WIRE", None),
                ):
                    super().mousePressEvent(e)
                    return
            if sc is not None and getattr(sc, "mode", None) == getattr(sc, "Mode", object()).WIRE and getattr(sc, "_routing", False):
                # Fallback: let right-click cancel wire routing in the scene.
                super().mousePressEvent(e)
                return
            self._panning = True
            self._pan_start = e.position()
            self._pan_drag_mode = self.dragMode()
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.ClosedHandCursor)
            e.accept(); return
        if e.button() == Qt.LeftButton:
            if self.itemAt(e.position().toPoint()) is None:
                sc = self.scene()
                if sc is not None:
                    sc.clearSelection()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        """Pan by scrolling the view (stable with drag modes)."""
        if self._panning:
            d = e.position() - self._pan_start
            self._pan_start = e.position()
            h = self.horizontalScrollBar()
            v = self.verticalScrollBar()
            h.setValue(h.value() - int(d.x()))
            v.setValue(v.value() - int(d.y()))
            e.accept(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton and self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            self.setDragMode(self._pan_drag_mode)
            e.accept(); return
        super().mouseReleaseEvent(e)

    def fit_all(self):
        items = self.scene().items()
        if not items:
            self.fitInView(QRectF(-500, -500, 1000, 1000), Qt.KeepAspectRatio); return
        rect = None
        for it in items:
            r = it.sceneBoundingRect(); rect = r if rect is None else rect.united(r)
        self.fitInView(rect.adjusted(-50, -50, 50, 50), Qt.KeepAspectRatio)
