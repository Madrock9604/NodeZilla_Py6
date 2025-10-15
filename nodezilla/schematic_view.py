# ========================================
# File: nodezilla/schematic_view.py
# ========================================
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsView

class SchematicView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self._panning = False
        self._pan_start = QPointF()

    def wheelEvent(self, e):
        z_in = 1.15; z_out = 1/1.15
        old = self.mapToScene(e.position().toPoint())
        f = z_in if e.angleDelta().y() > 0 else z_out
        self.scale(f, f)
        new = self.mapToScene(e.position().toPoint())
        d = new - old; self.translate(d.x(), d.y())

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._panning = True; self._pan_start = e.position(); self.setCursor(Qt.ClosedHandCursor)
            e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._panning:
            d = e.position() - self._pan_start; self._pan_start = e.position(); self.translate(d.x(), d.y())
            e.accept(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton and self._panning:
            self._panning = False; self.setCursor(Qt.ArrowCursor); e.accept(); return
        super().mouseReleaseEvent(e)

    def fit_all(self):
        items = self.scene().items()
        if not items:
            self.fitInView(QRectF(-500, -500, 1000, 1000), Qt.KeepAspectRatio); return
        rect = None
        for it in items:
            r = it.sceneBoundingRect(); rect = r if rect is None else rect.united(r)
        self.fitInView(rect.adjusted(-50, -50, 50, 50), Qt.KeepAspectRatio)