from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QLabel, QTreeWidget, QTreeWidgetItem

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
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)

        layout.addWidget(QLabel("Component Library"))
        layout.addWidget(self.search)
        layout.addWidget(self.tree, 1)

        self.search.textChanged.connect(self._populate)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._populate("")

    def _populate(self, text: str):
        """Build the category tree and insert component items."""
        needle = (text or "").strip().lower()
        self.tree.clear()
        categories: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for comp in self._all:
            hay = f"{comp.display_name} {comp.kind} {comp.category}".lower()
            if needle and needle not in hay:
                continue
            cat = comp.category or "General"
            path = [p.strip() for p in cat.replace(" / ", "/").split("/") if p.strip()]
            if not path:
                path = ["General"]
            parent = None
            key_path: list[str] = []
            for part in path:
                key_path.append(part)
                key = tuple(key_path)
                node = categories.get(key)
                if node is None:
                    node = QTreeWidgetItem([part])
                    node.setData(0, Qt.UserRole, None)
                    if parent is None:
                        self.tree.addTopLevelItem(node)
                    else:
                        parent.addChild(node)
                    categories[key] = node
                parent = node
            label = comp.display_name
            if comp.shortcut:
                label = f"{label}  ({comp.shortcut})"
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.UserRole, comp.kind)
            item.setToolTip(0, f"{comp.category} • {comp.kind} • {len(comp.ports)} pins")
            parent.addChild(item)

        # Expand matching categories for better UX
        for cat_item in categories.values():
            cat_item.setExpanded(True)

    def reload_library(self):
        self._library = load_component_library(force_reload=True)
        self._all = self._library.sorted_components()
        self._populate(self.search.text())

    def showEvent(self, e):
        self.reload_library()
        super().showEvent(e)

    def _on_item_clicked(self, item: QTreeWidgetItem, _col: int = 0):
        kind = item.data(0, Qt.UserRole)
        if kind:
            self.place_requested.emit(str(kind))

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _col: int = 0):
        self._on_item_clicked(item, _col)
