# ========================================
# File: nodezilla/properties_panel.py
# ========================================
from typing import Optional
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QWidget, QFormLayout, QLineEdit, QPushButton, QLabel, QHBoxLayout, QCheckBox, QSpinBox, QFontDialog
from PySide6.QtWidgets import QColorDialog
from .graphics_items import ComponentItem, WireItem, CommentTextItem
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
        self.text_label = QLabel("Text")
        self.text_edit = QLineEdit()
        self.text_size_label = QLabel("Text Size")
        self.text_size = QSpinBox()
        self.text_size.setRange(1, 200)
        self.text_style_label = QLabel("Text Style")
        self.text_bold = QCheckBox("Bold")
        self.text_italic = QCheckBox("Italic")
        self.text_color_label = QLabel("Text Color")
        self.text_color_edit = QLineEdit()
        self.text_color_edit.setPlaceholderText("#RRGGBB")
        self.pick_text_color_btn = QPushButton("Pick...")
        text_color_row = QWidget()
        text_color_layout = QHBoxLayout(text_color_row)
        text_color_layout.setContentsMargins(0, 0, 0, 0)
        text_color_layout.addWidget(self.text_color_edit, 1)
        text_color_layout.addWidget(self.pick_text_color_btn, 0)
        self._text_color_row = text_color_row
        self.text_font_label = QLabel("Text Font")
        self.text_font_btn = QPushButton("Choose Font...")
        text_style_row = QWidget()
        text_style_layout = QHBoxLayout(text_style_row)
        text_style_layout.setContentsMargins(0, 0, 0, 0)
        text_style_layout.addWidget(self.text_bold, 0)
        text_style_layout.addWidget(self.text_italic, 0)
        self._text_style_row = text_style_row
        self.apply_btn = QPushButton("Apply to Selection")

        self.form.addRow("Selection", self.kind_label)
        self.form.addRow(self.refdes_label, self.refdes_edit)
        self.form.addRow(self.value_label, self.value_edit)
        self.form.addRow(self.wire_color_label, color_row)
        self.form.addRow(self.text_label, self.text_edit)
        self.form.addRow(self.text_size_label, self.text_size)
        self.form.addRow(self.text_style_label, text_style_row)
        self.form.addRow(self.text_color_label, text_color_row)
        self.form.addRow(self.text_font_label, self.text_font_btn)
        self.form.addRow(self.apply_btn)

        self.setDisabled(True)
        self._on_apply = None
        self._selection_kind: str | None = None
        self._text_font_family: str = ""

    def set_callbacks(self, on_apply):
        self._on_apply = on_apply
        self.apply_btn.clicked.connect(self._apply_clicked)
        self.refdes_edit.returnPressed.connect(self._apply_clicked)
        self.value_edit.returnPressed.connect(self._apply_clicked)
        self.wire_color_edit.returnPressed.connect(self._apply_clicked)
        self.text_edit.returnPressed.connect(self._apply_clicked)
        self.text_color_edit.returnPressed.connect(self._apply_clicked)
        self.text_size.valueChanged.connect(lambda _v: self._apply_clicked())
        self.text_bold.toggled.connect(lambda _v: self._apply_clicked())
        self.text_italic.toggled.connect(lambda _v: self._apply_clicked())
        self.pick_color_btn.clicked.connect(self._pick_wire_color)
        self.pick_text_color_btn.clicked.connect(self._pick_text_color)
        self.text_font_btn.clicked.connect(self._pick_text_font)

    def _apply_clicked(self):
        if self._on_apply:
            refdes = self.refdes_edit.text().strip() if self.refdes_edit.isEnabled() else None
            value = self.value_edit.text().strip() if self.value_edit.isEnabled() else None
            self._on_apply(
                self._selection_kind,
                refdes,
                value,
                self.wire_color_edit.text().strip(),
                self.text_edit.text(),
                int(self.text_size.value()),
                bool(self.text_bold.isChecked()),
                bool(self.text_italic.isChecked()),
                self.text_color_edit.text().strip(),
                self._text_font_family,
            )

    def _set_mode(self, mode: str | None):
        self._selection_kind = mode
        is_comp = mode == "component"
        is_wire = mode == "wire"
        is_text = mode == "text"
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
        self.text_label.setVisible(is_text)
        self.text_edit.setVisible(is_text)
        self.text_edit.setEnabled(is_text)
        self.text_size_label.setVisible(is_text)
        self.text_size.setVisible(is_text)
        self.text_size.setEnabled(is_text)
        self.text_style_label.setVisible(is_text)
        self._text_style_row.setVisible(is_text)
        self.text_bold.setEnabled(is_text)
        self.text_italic.setEnabled(is_text)
        self.text_color_label.setVisible(is_text)
        self._text_color_row.setVisible(is_text)
        self.text_color_edit.setEnabled(is_text)
        self.pick_text_color_btn.setEnabled(is_text)
        self.text_font_label.setVisible(is_text)
        self.text_font_btn.setVisible(is_text)
        self.text_font_btn.setEnabled(is_text)

    def _pick_wire_color(self):
        base = QColor(self.wire_color_edit.text().strip())
        if not base.isValid():
            base = QColor("#0068a6")
        chosen = QColorDialog.getColor(base, self, "Choose Wire Color")
        if chosen.isValid():
            self.wire_color_edit.setText(chosen.name())
            self._apply_clicked()

    def _pick_text_color(self):
        base = QColor(self.text_color_edit.text().strip())
        if not base.isValid():
            base = QColor("#e0e0e0")
        chosen = QColorDialog.getColor(base, self, "Choose Text Color")
        if chosen.isValid():
            self.text_color_edit.setText(chosen.name())
            self._apply_clicked()

    def _pick_text_font(self):
        f = QFont()
        f.setPointSize(int(self.text_size.value()))
        f.setBold(bool(self.text_bold.isChecked()))
        f.setItalic(bool(self.text_italic.isChecked()))
        result = QFontDialog.getFont(f, self, "Choose Text Font")
        # PySide variants can return either (QFont, bool) or (bool, QFont).
        if isinstance(result, tuple) and len(result) >= 2:
            a, b = result[0], result[1]
            if isinstance(a, QFont):
                chosen, ok = a, bool(b)
            else:
                chosen, ok = b, bool(a)
        else:
            return
        if not ok:
            return
        psz = chosen.pointSize() if hasattr(chosen, "pointSize") else 12
        self._text_font_family = chosen.family()
        self.text_size.setValue(max(1, int(psz)))
        self.text_bold.setChecked(chosen.bold())
        self.text_italic.setChecked(chosen.italic())
        self._apply_clicked()

    def show_component(self, comp: Optional[ComponentItem]):
        if comp is None:
            self.setDisabled(True)
            self.kind_label.setText("No selection")
            self._set_mode(None)
            self.refdes_edit.setText("")
            self.value_edit.setText("")
            self.wire_color_edit.setText("")
            self.text_edit.setText("")
            self.text_color_edit.setText("")
        else:
            self.setDisabled(False)
            comp_def = getattr(comp, "_comp_def", None) or load_component_library().get(comp.kind)
            # Determine which fields apply to this component type.
            is_net = bool(comp_def and getattr(comp_def, "comp_type", "component") == "net")
            show_value = bool(comp_def.show_value) if comp_def else True
            label = comp_def.value_label if comp_def else "Value"
            if comp_def:
                st = comp_def.spice_type.upper()
                is_custom = str(getattr(comp_def, "symbol", "")).startswith("custom/")
                # Backward-compatibility for older custom JSON files that still
                # carry value_label="Value" for part-number style parts.
                if label == "Value" and is_custom and st not in {"R", "C", "L"}:
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
            self.text_edit.setText("")
            self.text_color_edit.setText("")

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
        self.text_edit.setText("")
        self.text_color_edit.setText("")

    def show_text(self, item: Optional[CommentTextItem]):
        if item is None:
            self.show_component(None)
            return
        self.setDisabled(False)
        self.kind_label.setText("Text")
        self._set_mode("text")
        self.refdes_edit.setText("")
        self.value_edit.setText("")
        self.wire_color_edit.setText("")
        self.text_edit.setText(item.toPlainText())
        f = item.font()
        self.text_size.blockSignals(True)
        self.text_bold.blockSignals(True)
        self.text_italic.blockSignals(True)
        self.text_size.setValue(max(1, f.pointSize() if f.pointSize() > 0 else 12))
        self.text_bold.setChecked(bool(f.bold()))
        self.text_italic.setChecked(bool(f.italic()))
        self.text_size.blockSignals(False)
        self.text_bold.blockSignals(False)
        self.text_italic.blockSignals(False)
        self.text_color_edit.setText(item.defaultTextColor().name())
        self._text_font_family = f.family()
