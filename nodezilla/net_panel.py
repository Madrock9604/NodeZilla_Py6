# ========================================
# File: nodezilla/net_panel.py
# ========================================
from __future__ import annotations

from typing import Dict, List, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from .graphics_items import WireItem


class NetPanel(QWidget):
    def __init__(self):
        super().__init__()
        self._scene = None
        self._pending_refresh = False
        self._updating = False
        self._nets: List[Dict] = []
        self._ignore_scene_clear = False

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Net", "Connections"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        self.setLayout(layout)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)

    def set_scene(self, scene):
        self._scene = scene
        if scene is not None:
            scene.nets_changed.connect(self._schedule_refresh)
            scene.selectionChanged.connect(self._sync_selection_from_scene)
        self._schedule_refresh()

    def _schedule_refresh(self):
        if self._pending_refresh:
            return
        self._pending_refresh = True
        QTimer.singleShot(0, self._refresh)

    def _refresh(self):
        self._pending_refresh = False
        if self._scene is None:
            return
        nets = self._scene.net_data()
        self._nets = nets
        selected_id = self._selected_net_id()

        self._updating = True
        self.table.setRowCount(0)
        for net in nets:
            row = self.table.rowCount()
            self.table.insertRow(row)

            name_item = QTableWidgetItem(net["name"])
            name_item.setData(Qt.UserRole, net["id"])
            name_item.setData(Qt.UserRole + 1, net["default_name"])
            name_item.setFlags(name_item.flags() | Qt.ItemIsEditable)

            connections = net["connections"]
            wires = net["wires"]
            connection_text = f"{len(connections)} ports • {len(wires)} wires"
            conn_item = QTableWidgetItem(connection_text)
            conn_item.setFlags(conn_item.flags() & ~Qt.ItemIsEditable)

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, conn_item)
        self._updating = False

        if selected_id is not None:
            self._reselect_net(selected_id)

    def _selected_net_id(self) -> Tuple[float, float] | None:
        items = self.table.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.UserRole)

    def _reselect_net(self, net_id: Tuple[float, float]):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.UserRole) == net_id:
                self.table.selectRow(row)
                return

    def _on_selection_changed(self):
        if self._scene is None or self._updating:
            return
        net_id = self._selected_net_id()
        if net_id is None:
            return
        self._ignore_scene_clear = True
        QTimer.singleShot(0, self._reset_ignore_scene_clear)
        self._scene.highlight_net(net_id)

    def _reset_ignore_scene_clear(self):
        self._ignore_scene_clear = False

    def _sync_selection_from_scene(self):
        if self._scene is None or self._updating:
            return
        if self._ignore_scene_clear:
            return
        selected = self._scene.selectedItems()
        if not selected:
            self._clear_selection()
            return
        if any(not isinstance(item, WireItem) for item in selected):
            self._clear_selection()
            return
        net_id = self._selected_net_id()
        if net_id is None:
            return
        net = next((entry for entry in self._nets if entry["id"] == net_id), None)
        if not net:
            self._clear_selection()
            return
        selected_wires = set(selected)
        net_wires = set(net.get("wires", []))
        if selected_wires != net_wires:
            self._clear_selection()
            return
        self._updating = True
        self.table.selectRow(self.table.currentRow())
        self._updating = False

    def _clear_selection(self):
        self._updating = True
        self.table.clearSelection()
        self._updating = False

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._scene is None or self._updating:
            return
        if item.column() != 0:
            return
        net_id = item.data(Qt.UserRole)
        default_name = item.data(Qt.UserRole + 1)
        if net_id is None:
            return
        self._scene.set_net_name(net_id, item.text(), default_name)