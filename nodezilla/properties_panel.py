# ========================================
# File: nodezilla/properties_panel.py
# ========================================
from typing import Optional
from PySide6.QtWidgets import QWidget, QFormLayout, QLineEdit, QPushButton
from .graphics_items import ComponentItem

class PropertiesPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.form = QFormLayout(self)
        self.refdes_edit = QLineEdit(); self.value_edit = QLineEdit(); self.apply_btn = QPushButton("Apply to Selection")
        self.form.addRow("RefDes", self.refdes_edit); self.form.addRow("Value", self.value_edit); self.form.addRow(self.apply_btn)
        self.setDisabled(True); self._on_apply = None

    def set_callbacks(self, on_apply):
        self._on_apply = on_apply
        self.apply_btn.clicked.connect(self._apply_clicked)
        self.refdes_edit.returnPressed.connect(self._apply_clicked)
        self.value_edit.returnPressed.connect(self._apply_clicked)

    def _apply_clicked(self):
        if self._on_apply:
            self._on_apply(self.refdes_edit.text().strip(), self.value_edit.text().strip())

    def show_component(self, comp: Optional[ComponentItem]):
        if comp is None:
            self.setDisabled(True); self.refdes_edit.setText(""); self.value_edit.setText("")
        else:
            self.setDisabled(False); self.refdes_edit.setText(comp.refdes); self.value_edit.setText(comp.value)