from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QFileDialog,
    QLabel,
)


class ProjectExplorerPanel(QWidget):
    """Simple explorer for examples and project schematics."""

    open_requested = Signal(str)

    _ROLE_PATH = Qt.UserRole + 1
    _ROLE_FILE = Qt.UserRole + 2
    _EXCLUDED_DIR_NAMES = {
        ".git",
        ".vscode",
        ".idea",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "nodezilla",
        "assets",
        "build",
        "dist",
        "release",
        "Examples",
    }

    def __init__(self, project_root: Path | None = None):
        super().__init__()
        self._project_root = Path(project_root or Path.cwd()).resolve()
        self._examples_root = (Path(__file__).resolve().parent.parent / "Examples").resolve()

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Project Explorer"))
        top.addStretch(1)
        self.choose_btn = QPushButton("Set Projects Folder")
        self.refresh_btn = QPushButton("Refresh")
        top.addWidget(self.choose_btn)
        top.addWidget(self.refresh_btn)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        root.addLayout(top)
        root.addWidget(self.tree, 1)

        self.choose_btn.clicked.connect(self._choose_projects_folder)
        self.refresh_btn.clicked.connect(self.refresh)
        self.refresh()

    def _choose_projects_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select projects folder", str(self._project_root))
        if not folder:
            return
        self._project_root = Path(folder).resolve()
        self.refresh()

    def refresh(self):
        self.tree.clear()
        self._add_root("Examples", self._examples_root)
        self._add_root("Projects", self._project_root)
        self.tree.expandToDepth(1)

    def _add_root(self, label: str, root_path: Path):
        root_item = QTreeWidgetItem([label])
        root_item.setData(0, self._ROLE_PATH, str(root_path))
        root_item.setData(0, self._ROLE_FILE, False)
        self.tree.addTopLevelItem(root_item)
        if not root_path.exists() or not root_path.is_dir():
            missing = QTreeWidgetItem(["(not found)"])
            missing.setData(0, self._ROLE_PATH, "")
            missing.setData(0, self._ROLE_FILE, False)
            root_item.addChild(missing)
            return
        self._populate_dir(root_item, root_path, root_label=label)

    def _populate_dir(self, parent_item: QTreeWidgetItem, folder: Path, *, root_label: str) -> bool:
        """Populate one directory. Returns True if any visible child was added."""
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except Exception:
            return False
        added_any = False
        for p in entries:
            if p.name.startswith("."):
                continue
            if p.is_dir():
                if self._is_excluded_dir(p, root_label):
                    continue
                item = QTreeWidgetItem([p.name])
                item.setData(0, self._ROLE_PATH, str(p))
                item.setData(0, self._ROLE_FILE, False)
                has_children = self._populate_dir(item, p, root_label=root_label)
                if has_children:
                    parent_item.addChild(item)
                    added_any = True
            else:
                if p.suffix.lower() != ".json":
                    continue
                item = QTreeWidgetItem([p.name])
                item.setData(0, self._ROLE_PATH, str(p))
                item.setData(0, self._ROLE_FILE, True)
                parent_item.addChild(item)
                added_any = True
        return added_any

    def _is_excluded_dir(self, folder: Path, root_label: str) -> bool:
        if root_label != "Projects":
            return False
        # Exclude known source/build folders anywhere in the selected project tree.
        lowered_parts = {part.lower() for part in folder.parts}
        excluded = {name.lower() for name in self._EXCLUDED_DIR_NAMES}
        return bool(lowered_parts & excluded)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int):
        path = str(item.data(0, self._ROLE_PATH) or "").strip()
        is_file = bool(item.data(0, self._ROLE_FILE))
        if not is_file or not path:
            return
        self.open_requested.emit(path)
