# ========================================
# File: nodezilla/properties_panel.py
# ========================================
from typing import Optional
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QFormLayout, QLineEdit, QPushButton, QLabel, QHBoxLayout
from PySide6.QtWidgets import QColorDialog
from .graphics_items import ComponentItem, WireItem

class PropertiesPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.form = QFormLayout(self)
        self.kind_label = QLabel("No selection")
        self.refdes_edit = QLineEdit()
        self.value_edit = QLineEdit()
        self.wire_color_edit = QLineEdit()
        self.wire_color_edit.setPlaceholderText("#RRGGBB (empty = theme default)")
        self.pick_color_btn = QPushButton("Pick...")
        color_row = QWidget()
        color_row_layout = QHBoxLayout(color_row)
        color_row_layout.setContentsMargins(0, 0, 0, 0)
        color_row_layout.addWidget(self.wire_color_edit, 1)
        color_row_layout.addWidget(self.pick_color_btn, 0)
        self.apply_btn = QPushButton("Apply to Selection")

        self.form.addRow("Selection", self.kind_label)
        self.form.addRow("RefDes", self.refdes_edit)
        self.form.addRow("Value", self.value_edit)
        self.form.addRow("Wire Color", color_row)
        self.form.addRow(self.apply_btn)

        self.setDisabled(True)
        self._on_apply = None
        self._selection_kind: str | None = None

    def set_callbacks(self, on_apply):
        self._on_apply = on_apply
        self.apply_btn.clicked.connect(self._apply_clicked)
        self.refdes_edit.returnPressed.connect(self._apply_clicked)
        self.value_edit.returnPressed.connect(self._apply_clicked)
        self.wire_color_edit.returnPressed.connect(self._apply_clicked)
        self.pick_color_btn.clicked.connect(self._pick_wire_color)

    def _apply_clicked(self):
        if self._on_apply:
            self._on_apply(
                self._selection_kind,
                self.refdes_edit.text().strip(),
                self.value_edit.text().strip(),
                self.wire_color_edit.text().strip(),
            )

    def _set_mode(self, mode: str | None):
        self._selection_kind = mode
        is_comp = mode == "component"
        is_wire = mode == "wire"
        self.refdes_edit.setEnabled(is_comp)
        self.value_edit.setEnabled(is_comp)
        self.wire_color_edit.setEnabled(is_wire)
        self.pick_color_btn.setEnabled(is_wire)

    def _pick_wire_color(self):
        base = QColor(self.wire_color_edit.text().strip())
        if not base.isValid():
            base = QColor("#0068a6")
        chosen = QColorDialog.getColor(base, self, "Choose Wire Color")
        if chosen.isValid():
            self.wire_color_edit.setText(chosen.name())
            self._apply_clicked()

    def show_component(self, comp: Optional[ComponentItem]):
        if comp is None:
            self.setDisabled(True)
            self.kind_label.setText("No selection")
            self._set_mode(None)
            self.refdes_edit.setText("")
            self.value_edit.setText("")
            self.wire_color_edit.setText("")
        else:
            self.setDisabled(False)
            self.kind_label.setText("Component")
            self._set_mode("component")
            self.refdes_edit.setText(comp.refdes)
            self.value_edit.setText(comp.value)
            self.wire_color_edit.setText("")

    def show_wire(self, wire: Optional[WireItem]):
        if wire is None:
            self.show_component(None)
            return
        self.setDisabled(False)
        self.kind_label.setText("Wire")
        self._set_mode("wire")
        self.refdes_edit.setText("")
        self.value_edit.setText("")
        self.wire_color_edit.setText(wire.wire_color_hex() if hasattr(wire, "wire_color_hex") else "")
