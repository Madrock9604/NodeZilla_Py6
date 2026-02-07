from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QLabel

from .component_library import ComponentLibrary, load_component_library


class ComponentPanel(QWidget):
    place_requested = Signal(str)

    def __init__(self, library: ComponentLibrary | None = None):
        super().__init__()
        self._library = library or load_component_library()
        self._all = self._library.sorted_components()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search components...")
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SingleSelection)

        layout.addWidget(QLabel("Component Library"))
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)

        self.search.textChanged.connect(self._populate)
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._populate("")

    def _populate(self, text: str):
        needle = (text or "").strip().lower()
        self.list.clear()
        for comp in self._all:
            hay = f"{comp.display_name} {comp.kind} {comp.category}".lower()
            if needle and needle not in hay:
                continue
            label = comp.display_name
            if comp.shortcut:
                label = f"{label}  ({comp.shortcut})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, comp.kind)
            item.setToolTip(f"{comp.category} • {comp.kind} • {len(comp.ports)} pins")
            self.list.addItem(item)

    def reload_library(self):
        self._library = load_component_library(force_reload=True)
        self._all = self._library.sorted_components()
        self._populate(self.search.text())

    def showEvent(self, e):
        self.reload_library()
        super().showEvent(e)

    def _on_item_clicked(self, item: QListWidgetItem):
        kind = item.data(Qt.UserRole)
        if kind:
            self.place_requested.emit(str(kind))

    def _on_item_double_clicked(self, item: QListWidgetItem):
        self._on_item_clicked(item)
