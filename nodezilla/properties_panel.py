# ========================================
# File: nodezilla/properties_panel.py
# ========================================
from typing import Optional
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QFormLayout, QLineEdit, QPushButton, QLabel, QHBoxLayout
from PySide6.QtWidgets import QColorDialog
from .graphics_items import ComponentItem, WireItem
from .component_library import load_component_library

class PropertiesPanel(QWidget):
    """Context-aware properties panel for components and wires."""
    def __init__(self):
        super().__init__()
        self.form = QFormLayout(self)
        self.kind_label = QLabel("No selection")
        self.refdes_label = QLabel("RefDes")
        self.refdes_edit = QLineEdit()
        self.value_edit = QLineEdit()
        self.value_label = QLabel("Value")
        self.wire_color_edit = QLineEdit()
        self.wire_color_edit.setPlaceholderText("#RRGGBB (empty = theme default)")
        self.pick_color_btn = QPushButton("Pick...")
        color_row = QWidget()
        color_row_layout = QHBoxLayout(color_row)
        color_row_layout.setContentsMargins(0, 0, 0, 0)
        color_row_layout.addWidget(self.wire_color_edit, 1)
        color_row_layout.addWidget(self.pick_color_btn, 0)
        self._color_row = color_row
        self.wire_color_label = QLabel("Wire Color")
        self.apply_btn = QPushButton("Apply to Selection")

        self.form.addRow("Selection", self.kind_label)
        self.form.addRow(self.refdes_label, self.refdes_edit)
        self.form.addRow(self.value_label, self.value_edit)
        self.form.addRow(self.wire_color_label, color_row)
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
            refdes = self.refdes_edit.text().strip() if self.refdes_edit.isEnabled() else None
            value = self.value_edit.text().strip() if self.value_edit.isEnabled() else None
            self._on_apply(
                self._selection_kind,
                refdes,
                value,
                self.wire_color_edit.text().strip(),
            )

    def _set_mode(self, mode: str | None):
        self._selection_kind = mode
        is_comp = mode == "component"
        is_wire = mode == "wire"
        self.refdes_label.setVisible(is_comp)
        self.refdes_edit.setVisible(is_comp)
        self.refdes_edit.setEnabled(is_comp)
        self.value_edit.setEnabled(is_comp)
        self.value_label.setVisible(is_comp)
        self.value_edit.setVisible(is_comp)
        self.wire_color_label.setVisible(is_wire)
        self._color_row.setVisible(is_wire)
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
            comp_def = getattr(comp, "_comp_def", None) or load_component_library().get(comp.kind)
            # Determine which fields apply to this component type.
            is_net = bool(comp_def and getattr(comp_def, "comp_type", "component") == "net")
            show_value = bool(comp_def.show_value) if comp_def else True
            label = comp_def.value_label if comp_def else "Value"
            if comp_def and comp_def.spice_type.upper() in {"D", "Q", "U", "X"} and label == "Value":
                label = "Part Number"
            if is_net:
                label = "Net Name"
                show_value = True
            self.kind_label.setText("Component")
            self._set_mode("component")
            self.refdes_label.setVisible(not is_net)
            self.refdes_edit.setVisible(not is_net)
            self.refdes_edit.setEnabled(not is_net)
            self.value_label.setText(label)
            self.value_edit.setVisible(show_value)
            self.value_label.setVisible(show_value)
            self.value_edit.setEnabled(show_value)
            self.refdes_edit.setText(comp.refdes if not is_net else "")
            self.value_edit.setText(comp.value if show_value else "")
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
