from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import re
from typing import Any

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QComboBox,
    QGroupBox,
    QFormLayout,
    QGridLayout,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QHeaderView,
    QFrame,
)

from .paths import user_pl_path, user_hardware_cards_dir, user_hardware_configs_dir, bundled_root


MAX_MAIN_BOARDS = 4
PORTS_PER_BOARD = 64
EMPTY_CARD_LABEL = "(empty)"
CARD_ROLE_NAME = Qt.UserRole + 1
BOARD_POSITION_ORDER = ["Top Left", "Top Right", "Bottom Left", "Bottom Right"]
DEFAULT_POSITION_FOR_BOARD = {
    1: "Top Right",
    2: "Top Left",
    3: "Bottom Left",
    4: "Bottom Right",
}


class BoardVisualWidget(QWidget):
    """Small illustrative board tile that roughly matches the real hardware stack."""

    def __init__(self, position_name: str):
        super().__init__()
        self.position_name = position_name
        self._board_number = 0
        self._card_name = EMPTY_CARD_LABEL
        self.setMinimumSize(250, 215)
        self.setMaximumHeight(230)

    def set_state(self, board_number: int | None, card_name: str):
        self._board_number = int(board_number or 0)
        self._card_name = str(card_name or EMPTY_CARD_LABEL)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(6, 6, -6, -6)
        side = min(rect.width() - 24, rect.height() - 26)
        board_rect = QRectF(
            rect.center().x() - side / 2.0,
            rect.top() + 8,
            side,
            side,
        )
        card_rect = QRectF(
            board_rect.left() + board_rect.width() * 0.12,
            board_rect.top() + board_rect.height() * 0.10,
            board_rect.width() * 0.76,
            board_rect.height() * 0.72,
        )

        # NodeZilla base board
        painter.setPen(QPen(QColor("#6f9352"), 2))
        painter.setBrush(QColor("#2f631f"))
        painter.drawRoundedRect(board_rect, 16, 16)

        # top component card
        painter.setPen(QPen(QColor("#674d1c"), 1.5))
        painter.setBrush(QColor("#191206"))
        painter.drawRoundedRect(card_rect, 12, 12)

        # connector fingers
        finger_pen = QPen(QColor("#f2e35f"), 3)
        painter.setPen(finger_pen)
        for x in range(12):
            fx = board_rect.left() + 18 + x * ((board_rect.width() - 36) / 11.0)
            painter.drawLine(fx, board_rect.top() + 2, fx, board_rect.top() + 16)
            painter.drawLine(fx, board_rect.bottom() - 16, fx, board_rect.bottom() - 2)
        for y in range(7):
            fy = board_rect.top() + 24 + y * ((board_rect.height() - 48) / 6.0)
            painter.drawLine(board_rect.left() + 2, fy, board_rect.left() + 18, fy)
            painter.drawLine(board_rect.right() - 18, fy, board_rect.right() - 2, fy)

        # AD side accent on the right edge
        ad_rect = QRectF(board_rect.right() - 24, board_rect.top() + board_rect.height() * 0.28, 22, board_rect.height() * 0.30)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(120, 150, 95, 180))
        painter.drawRoundedRect(ad_rect, 6, 6)

        painter.setPen(QColor("#f4f4f0"))
        font = painter.font()
        font.setPointSize(15)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(board_rect.left() + 12, board_rect.bottom() - 34, board_rect.width() * 0.42, 24),
            Qt.AlignLeft | Qt.AlignVCenter,
            "NodeZilla",
        )

        card_font = QFont(font)
        card_font.setPointSize(13)
        painter.setFont(card_font)
        painter.drawText(
            QRectF(card_rect.left() + 12, card_rect.bottom() - 28, card_rect.width() - 24, 20),
            Qt.AlignLeft | Qt.AlignVCenter,
            "Component Card",
        )

        badge_rect = QRectF(board_rect.right() - 94, board_rect.top() + 8, 82, 26)
        painter.setPen(QPen(QColor("#d0d7df"), 1))
        painter.setBrush(QColor(28, 32, 38, 220))
        painter.drawRoundedRect(badge_rect, 10, 10)
        painter.setPen(QColor("#ffffff"))
        badge_font = QFont(card_font)
        badge_font.setPointSize(10)
        painter.setFont(badge_font)
        badge = f"Board {self._board_number}" if self._board_number else "Unused"
        painter.drawText(badge_rect, Qt.AlignCenter, badge)

        subtle_font = QFont(card_font)
        subtle_font.setPointSize(9)
        subtle_font.setBold(False)
        painter.setFont(subtle_font)
        painter.setPen(QColor("#dce4d7"))
        painter.drawText(
            QRectF(board_rect.left(), board_rect.top() - 1, board_rect.width(), 16),
            Qt.AlignCenter,
            self.position_name,
        )

        status_rect = QRectF(board_rect.left() + 8, board_rect.top() + 8, board_rect.width() * 0.44, 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 34))
        painter.drawRoundedRect(status_rect, 8, 8)
        painter.setPen(QColor("#f1f4e8"))
        status_label = "AD side on the right" if self.position_name == "Top Right" else "Ports follow selected board #"
        painter.drawText(status_rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, status_label)



@dataclass
class HardwareCardEntry:
    refdes: str
    ports: list[int] = field(default_factory=list)
    value: str = ""
    part: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HardwareCardEntry":
        refdes = str(payload.get("refdes", "")).strip()
        raw_ports = payload.get("ports", []) or []
        ports: list[int] = []
        for raw in raw_ports:
            ports.append(int(raw))
        return cls(
            refdes=refdes,
            ports=ports,
            value=str(payload.get("value", "")).strip(),
            part=str(payload.get("part", "")).strip(),
        )

    def to_pl_line(self, *, port_offset: int) -> str:
        if not self.refdes:
            return ""
        mapped_ports = [str(port_offset + int(p)) for p in self.ports]
        tokens = [self.refdes, *mapped_ports]
        suffix = self.value or self.part
        if suffix:
            tokens.append(suffix)
        return " ".join(tokens)


@dataclass
class HardwareCardDef:
    name: str
    path: Path
    description: str = ""
    components: list[HardwareCardEntry] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> "HardwareCardDef":
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            raise ValueError(f"Card file is not a JSON object: {path}")
        name = str(payload.get("name", path.stem)).strip() or path.stem
        description = str(payload.get("description", "")).strip()
        components = [HardwareCardEntry.from_dict(c) for c in (payload.get("components", []) or [])]
        return cls(name=name, path=path, description=description, components=components)

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "components": [
                {
                    "refdes": entry.refdes,
                    "ports": list(entry.ports),
                    "value": entry.value,
                    "part": entry.part,
                }
                for entry in self.components
            ],
        }


@dataclass
class HardwareBuildResult:
    text: str
    board_cards: list[str]
    output_path: Path


class HardwarePlBuilder:
    def __init__(self, cards_root: Path | None = None):
        self.cards_root = Path(cards_root or user_hardware_cards_dir()).resolve()
        self._bundled_cards_root = (bundled_root() / "assets" / "hardware_cards").resolve()

    def load_cards(self) -> list[HardwareCardDef]:
        by_name: dict[str, HardwareCardDef] = {}
        roots: list[Path] = []
        if self.cards_root.exists():
            roots.append(self.cards_root)
        if self._bundled_cards_root.exists() and self._bundled_cards_root != self.cards_root:
            roots.append(self._bundled_cards_root)
        for root in roots:
            for path in sorted(root.rglob("*.json"), key=lambda p: p.name.lower()):
                try:
                    card = HardwareCardDef.from_file(path)
                except Exception:
                    continue
                if card.name in by_name and root == self._bundled_cards_root:
                    continue
                by_name[card.name] = card
        return sorted(by_name.values(), key=lambda c: c.name.lower())

    def generate(
        self,
        board_card_names: list[str],
        *,
        output_path: Path | None = None,
        cards: list[HardwareCardDef] | None = None,
    ) -> HardwareBuildResult:
        cards = list(cards or self.load_cards())
        by_name = {c.name: c for c in cards}
        lines: list[str] = []
        selected: list[str] = []
        for board_index, card_name in enumerate(board_card_names):
            name = str(card_name or "").strip()
            if not name or name == EMPTY_CARD_LABEL:
                selected.append(EMPTY_CARD_LABEL)
                continue
            card = by_name.get(name)
            if card is None:
                raise RuntimeError(f"Hardware card '{name}' was not found.")
            selected.append(name)
            offset = board_index * PORTS_PER_BOARD
            lines.append(f"* Board {board_index + 1}: {card.name}")
            for entry in card.components:
                line = entry.to_pl_line(port_offset=offset)
                if line:
                    lines.append(line)
            lines.append("")
        text = "\n".join(lines).rstrip() + "\n"
        target = Path(output_path or user_pl_path())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return HardwareBuildResult(text=text, board_cards=selected, output_path=target)


class HardwareBuilderPanel(QWidget):
    """Configure installed boards and author reusable hardware card templates."""

    pl_generated = Signal(str)

    def __init__(self):
        super().__init__()
        self._builder = HardwarePlBuilder()
        self._cards: list[HardwareCardDef] = []
        self._config_path = user_hardware_configs_dir() / "active_hardware.json"
        self._board_card_combos: list[QComboBox] = []
        self._board_slots: dict[str, dict[str, object]] = {}
        self._current_card_name: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addStretch(1)
        self.refresh_btn = QPushButton("Refresh Cards")
        self.save_cfg_btn = QPushButton("Save Config")
        self.generate_btn = QPushButton("Generate PL")
        header.addWidget(self.refresh_btn)
        header.addWidget(self.save_cfg_btn)
        header.addWidget(self.generate_btn)
        root.addLayout(header)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        left_host = QWidget()
        left_layout = QVBoxLayout(left_host)
        left_layout.setContentsMargins(0, 0, 0, 0)

        card_box = QGroupBox("Card Templates")
        card_layout = QVBoxLayout(card_box)
        card_toolbar = QHBoxLayout()
        self.new_card_btn = QPushButton("New")
        self.duplicate_card_btn = QPushButton("Duplicate")
        self.delete_card_btn = QPushButton("Delete")
        card_toolbar.addWidget(self.new_card_btn)
        card_toolbar.addWidget(self.duplicate_card_btn)
        card_toolbar.addWidget(self.delete_card_btn)
        card_toolbar.addStretch(1)
        card_layout.addLayout(card_toolbar)

        self.card_list = QListWidget()
        self.card_list.setSelectionMode(QAbstractItemView.SingleSelection)
        card_layout.addWidget(self.card_list, 1)
        left_layout.addWidget(card_box, 1)

        editor_box = QGroupBox("Card Definition Editor")
        editor_layout = QVBoxLayout(editor_box)
        form = QFormLayout()
        self.card_name_edit = QLineEdit()
        self.card_description_edit = QTextEdit()
        self.card_description_edit.setFixedHeight(72)
        form.addRow("Card Name", self.card_name_edit)
        form.addRow("Description", self.card_description_edit)
        editor_layout.addLayout(form)

        self.component_table = QTableWidget(0, 4)
        self.component_table.setHorizontalHeaderLabels(["RefDes", "Ports", "Value", "Part"])
        self.component_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.component_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.component_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.component_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.component_table.verticalHeader().setVisible(False)
        editor_layout.addWidget(self.component_table, 1)

        row_actions = QHBoxLayout()
        self.add_row_btn = QPushButton("Add Component")
        self.remove_row_btn = QPushButton("Remove Selected")
        self.save_card_btn = QPushButton("Save Card")
        row_actions.addWidget(self.add_row_btn)
        row_actions.addWidget(self.remove_row_btn)
        row_actions.addStretch(1)
        row_actions.addWidget(self.save_card_btn)
        editor_layout.addLayout(row_actions)
        left_layout.addWidget(editor_box, 2)

        right_host = QWidget()
        right_layout = QVBoxLayout(right_host)
        right_layout.setContentsMargins(0, 0, 0, 0)

        setup_box = QGroupBox("Installed Hardware Configuration")
        setup_layout = QVBoxLayout(setup_box)
        diagram = QWidget()
        diagram_layout = QGridLayout(diagram)
        diagram_layout.setContentsMargins(4, 4, 4, 4)
        diagram_layout.setHorizontalSpacing(8)
        diagram_layout.setVerticalSpacing(8)

        for position_name, (row, col) in {
            "Top Left": (0, 0),
            "Top Right": (0, 1),
            "Bottom Left": (1, 0),
            "Bottom Right": (1, 1),
        }.items():
            diagram_layout.addWidget(self._build_board_position(position_name), row, col)

        setup_layout.addWidget(diagram)
        right_layout.addWidget(setup_box)

        split.addWidget(left_host)
        split.addWidget(right_host)
        split.setSizes([520, 780])

        self.refresh_btn.clicked.connect(self.refresh_cards)
        self.generate_btn.clicked.connect(self.generate_pl)
        self.save_cfg_btn.clicked.connect(self.save_config)
        self.card_list.currentItemChanged.connect(self._on_card_selected)
        self.new_card_btn.clicked.connect(self._new_card)
        self.duplicate_card_btn.clicked.connect(self._duplicate_card)
        self.delete_card_btn.clicked.connect(self._delete_current_card)
        self.add_row_btn.clicked.connect(self._add_component_row)
        self.remove_row_btn.clicked.connect(self._remove_selected_rows)
        self.save_card_btn.clicked.connect(self._save_current_card)
        for combo in self._board_card_combos:
            combo.currentTextChanged.connect(self._update_preview_only)
        for slot in self._board_slots.values():
            board_combo = slot["board_number"]
            if isinstance(board_combo, QComboBox):
                board_combo.currentTextChanged.connect(self._on_board_number_changed)

        self.refresh_cards()
        self._load_saved_config()
        self._apply_default_board_positions()
        self._sync_board_visuals()
        self._update_preview_only()

    def _build_board_position(self, position_name: str) -> QWidget:
        box = QGroupBox(position_name)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        board_art = BoardVisualWidget(position_name)

        board_number_combo = QComboBox()
        board_number_combo.addItem("(unused)", None)
        for i in range(1, MAX_MAIN_BOARDS + 1):
            board_number_combo.addItem(f"Board {i}", i)
        card_combo = QComboBox()
        card_combo.addItem(EMPTY_CARD_LABEL)

        layout.addWidget(board_art)
        board_label = QLabel("NodeZilla")
        board_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(board_label)
        layout.addWidget(board_number_combo)
        card_label = QLabel("Component Card")
        card_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(card_label)
        layout.addWidget(card_combo)

        self._board_card_combos.append(card_combo)
        self._board_slots[position_name] = {
            "board_number": board_number_combo,
            "card": card_combo,
            "art": board_art,
        }
        return box

    def refresh_cards(self):
        self._cards = self._builder.load_cards()
        current_values = [combo.currentText() for combo in self._board_card_combos]
        names = [EMPTY_CARD_LABEL, *[c.name for c in self._cards]]
        for combo, current in zip(self._board_card_combos, current_values):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(names)
            combo.setCurrentText(current if current in names else EMPTY_CARD_LABEL)
            combo.blockSignals(False)
        self._populate_card_list(select_name=self._current_card_name)
        self._sync_board_visuals()
        self._update_preview_only()

    def _populate_card_list(self, *, select_name: str | None = None):
        self.card_list.blockSignals(True)
        self.card_list.clear()
        selected_item: QListWidgetItem | None = None
        for card in self._cards:
            origin = "User" if str(card.path).startswith(str(self._builder.cards_root)) else "Bundled"
            item = QListWidgetItem(f"{card.name} ({origin})")
            item.setData(CARD_ROLE_NAME, card.name)
            self.card_list.addItem(item)
            if select_name and card.name == select_name:
                selected_item = item
        self.card_list.blockSignals(False)
        if selected_item is not None:
            self.card_list.setCurrentItem(selected_item)
        elif self.card_list.count() and self._current_card_name is None:
            self.card_list.setCurrentRow(0)
        elif self.card_list.count() == 0:
            self._new_card(clear_only=True)

    def _board_assignments(self) -> list[tuple[int, str, str]]:
        assignments: list[tuple[int, str, str]] = []
        for position_name in BOARD_POSITION_ORDER:
            slot = self._board_slots[position_name]
            board_combo = slot["board_number"]
            card_combo = slot["card"]
            if not isinstance(board_combo, QComboBox) or not isinstance(card_combo, QComboBox):
                continue
            board_number = board_combo.currentData()
            card_name = card_combo.currentText()
            if board_number in (None, "") or card_name == EMPTY_CARD_LABEL:
                continue
            assignments.append((int(board_number), position_name, card_name))
        return sorted(assignments, key=lambda item: item[0])

    def _board_names(self) -> list[str]:
        ordered = [EMPTY_CARD_LABEL] * MAX_MAIN_BOARDS
        for board_number, _position_name, card_name in self._board_assignments():
            if 1 <= board_number <= MAX_MAIN_BOARDS:
                ordered[board_number - 1] = card_name
        return ordered

    def _on_board_number_changed(self):
        self._enforce_unique_board_numbers()
        self._sync_board_visuals()
        self._update_preview_only()

    def _update_preview_only(self):
        try:
            assignments = self._board_assignments()
            self._sync_board_visuals()
        except Exception as exc:
            QMessageBox.warning(self, "PL Builder", f"Preview error: {exc}")

    def generate_pl(self):
        try:
            result = self._builder.generate(self._board_names(), cards=self._cards)
        except Exception as exc:
            QMessageBox.warning(self, "PL Builder", str(exc))
            return
        self.pl_generated.emit(str(result.output_path))
        QMessageBox.information(self, "PL Builder", f"PL generated at:\n{result.output_path}")

    def save_config(self):
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        positions = []
        for position_name in BOARD_POSITION_ORDER:
            slot = self._board_slots[position_name]
            board_combo = slot["board_number"]
            card_combo = slot["card"]
            if not isinstance(board_combo, QComboBox) or not isinstance(card_combo, QComboBox):
                continue
            positions.append(
                {
                    "position": position_name,
                    "board_number": board_combo.currentData(),
                    "card": card_combo.currentText(),
                }
            )
        payload = {
            "board_count": len(self._board_assignments()),
            "boards": self._board_names(),
            "positions": positions,
        }
        self._config_path.write_text(json.dumps(payload, indent=2))
        QMessageBox.information(self, "PL Builder", f"Hardware configuration saved to:\n{self._config_path}")

    def _load_saved_config(self):
        path = self._config_path
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text())
            positions = list(payload.get("positions", []) or [])
            if positions:
                for item in positions:
                    position_name = str(item.get("position", "")).strip()
                    slot = self._board_slots.get(position_name)
                    if not slot:
                        continue
                    board_combo = slot["board_number"]
                    card_combo = slot["card"]
                    if isinstance(board_combo, QComboBox):
                        board_value = item.get("board_number", None)
                        idx = board_combo.findData(board_value)
                        board_combo.setCurrentIndex(idx if idx >= 0 else 0)
                if isinstance(card_combo, QComboBox):
                    card_value = str(item.get("card", "")).strip()
                    if card_value and card_combo.findText(card_value) >= 0:
                        card_combo.setCurrentText(card_value)
                self._enforce_unique_board_numbers()
                self._sync_board_visuals()
                return
            boards = list(payload.get("boards", []) or [])
            for idx, card_name in enumerate(boards, start=1):
                if not card_name or card_name == EMPTY_CARD_LABEL:
                    continue
                position_name = DEFAULT_POSITION_FOR_BOARD.get(idx)
                slot = self._board_slots.get(position_name or "")
                if not slot:
                    continue
                board_combo = slot["board_number"]
                card_combo = slot["card"]
                if isinstance(board_combo, QComboBox):
                    combo_index = board_combo.findData(idx)
                    board_combo.setCurrentIndex(combo_index if combo_index >= 0 else 0)
                if isinstance(card_combo, QComboBox) and card_combo.findText(str(card_name)) >= 0:
                    card_combo.setCurrentText(str(card_name))
            self._sync_board_visuals()
        except Exception:
            return

    def _apply_default_board_positions(self):
        if self._board_assignments():
            return
        top_right = self._board_slots.get("Top Right")
        if not top_right:
            return
        board_combo = top_right["board_number"]
        if isinstance(board_combo, QComboBox):
            idx = board_combo.findData(1)
            if idx >= 0:
                board_combo.setCurrentIndex(idx)
        self._enforce_unique_board_numbers()
        self._sync_board_visuals()

    def _enforce_unique_board_numbers(self):
        seen: set[int] = set()
        for position_name in BOARD_POSITION_ORDER:
            slot = self._board_slots[position_name]
            board_combo = slot["board_number"]
            if not isinstance(board_combo, QComboBox):
                continue
            board_number = board_combo.currentData()
            if board_number in (None, ""):
                continue
            board_number = int(board_number)
            if board_number in seen:
                board_combo.blockSignals(True)
                board_combo.setCurrentIndex(0)
                board_combo.blockSignals(False)
                continue
            seen.add(board_number)

    def _sync_board_visuals(self):
        for position_name in BOARD_POSITION_ORDER:
            slot = self._board_slots.get(position_name, {})
            board_combo = slot.get("board_number")
            card_combo = slot.get("card")
            art = slot.get("art")
            if not isinstance(board_combo, QComboBox) or not isinstance(card_combo, QComboBox):
                continue
            if isinstance(art, BoardVisualWidget):
                art.set_state(board_combo.currentData(), card_combo.currentText())

    def _on_card_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None):
        if current is None:
            return
        name = current.data(CARD_ROLE_NAME)
        if not name:
            return
        card = next((c for c in self._cards if c.name == name), None)
        if card is None:
            return
        self._load_card_into_editor(card)

    def _load_card_into_editor(self, card: HardwareCardDef):
        self._current_card_name = card.name
        self.card_name_edit.setText(card.name)
        self.card_description_edit.setPlainText(card.description)
        self.component_table.setRowCount(0)
        for entry in card.components:
            row = self.component_table.rowCount()
            self.component_table.insertRow(row)
            self.component_table.setItem(row, 0, QTableWidgetItem(entry.refdes))
            self.component_table.setItem(row, 1, QTableWidgetItem(", ".join(str(p) for p in entry.ports)))
            self.component_table.setItem(row, 2, QTableWidgetItem(entry.value))
            self.component_table.setItem(row, 3, QTableWidgetItem(entry.part))

    def _new_card(self, *, clear_only: bool = False):
        self._current_card_name = None
        self.card_name_edit.clear()
        self.card_description_edit.clear()
        self.component_table.setRowCount(0)
        if not clear_only:
            self.card_name_edit.setFocus()

    def _duplicate_card(self):
        source = self._editor_card_name()
        if not source:
            QMessageBox.information(self, "PL Builder", "Select a card first so we can duplicate it.")
            return
        self.card_name_edit.setText(f"{source} Copy")
        self._current_card_name = None
        self.card_name_edit.setFocus()
        self.card_name_edit.selectAll()

    def _delete_current_card(self):
        name = self._editor_card_name()
        if not name:
            return
        target = self._card_path_for_name(name)
        if not target.exists():
            QMessageBox.information(
                self,
                "PL Builder",
                "This card is bundled with the app. Save an edited copy first if you want to override it from the user library.",
            )
            return
        target.unlink(missing_ok=True)
        self._current_card_name = None
        self.refresh_cards()
        self._new_card(clear_only=True)

    def _add_component_row(self):
        row = self.component_table.rowCount()
        self.component_table.insertRow(row)
        for col, text in enumerate(("", "", "", "")):
            self.component_table.setItem(row, col, QTableWidgetItem(text))
        self.component_table.setCurrentCell(row, 0)

    def _remove_selected_rows(self):
        rows = sorted({index.row() for index in self.component_table.selectionModel().selectedRows()}, reverse=True)
        if not rows and self.component_table.currentRow() >= 0:
            rows = [self.component_table.currentRow()]
        for row in rows:
            self.component_table.removeRow(row)

    def _save_current_card(self):
        try:
            card = self._card_from_editor()
        except Exception as exc:
            QMessageBox.warning(self, "PL Builder", str(exc))
            return
        target = self._card_path_for_name(card.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(card.to_payload(), indent=2))
        self._current_card_name = card.name
        self.refresh_cards()
        self._select_card_by_name(card.name)
        QMessageBox.information(self, "PL Builder", f"Card saved to:\n{target}")

    def _select_card_by_name(self, name: str):
        for i in range(self.card_list.count()):
            item = self.card_list.item(i)
            if item.data(CARD_ROLE_NAME) == name:
                self.card_list.setCurrentItem(item)
                return

    def _editor_card_name(self) -> str:
        return self.card_name_edit.text().strip()

    def _card_path_for_name(self, name: str) -> Path:
        slug = re.sub(r"[^A-Za-z0-9_+\-]+", "_", name.strip()).strip("_") or "hardware_card"
        return self._builder.cards_root / f"{slug}.json"

    def _card_from_editor(self) -> HardwareCardDef:
        name = self._editor_card_name()
        if not name:
            raise ValueError("Card Name is required.")
        description = self.card_description_edit.toPlainText().strip()
        components: list[HardwareCardEntry] = []
        seen_refdes: set[str] = set()
        for row in range(self.component_table.rowCount()):
            refdes = self._table_text(row, 0)
            ports_text = self._table_text(row, 1)
            value = self._table_text(row, 2)
            part = self._table_text(row, 3)
            if not any((refdes, ports_text, value, part)):
                continue
            if not refdes:
                raise ValueError(f"Component row {row + 1} is missing RefDes.")
            if refdes in seen_refdes:
                raise ValueError(f"RefDes '{refdes}' is duplicated in the card definition.")
            seen_refdes.add(refdes)
            ports = self._parse_ports(ports_text, row=row)
            components.append(HardwareCardEntry(refdes=refdes, ports=ports, value=value, part=part))
        return HardwareCardDef(name=name, path=self._card_path_for_name(name), description=description, components=components)

    def _parse_ports(self, ports_text: str, *, row: int) -> list[int]:
        text = str(ports_text or "").strip()
        if not text:
            return []
        parts = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
        ports: list[int] = []
        for part in parts:
            try:
                port = int(part)
            except ValueError as exc:
                raise ValueError(f"Component row {row + 1} has an invalid port '{part}'.") from exc
            if not 0 <= port < PORTS_PER_BOARD:
                raise ValueError(
                    f"Component row {row + 1} uses local port {port}. Card templates must stay within 0-{PORTS_PER_BOARD - 1}."
                )
            ports.append(port)
        return ports

    def _table_text(self, row: int, col: int) -> str:
        item = self.component_table.item(row, col)
        return item.text().strip() if item is not None else ""
