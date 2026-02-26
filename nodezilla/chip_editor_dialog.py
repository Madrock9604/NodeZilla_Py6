from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QEvent, QPointF, QTimer
from PySide6.QtGui import QUndoStack, QIcon, QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QWidget,
    QToolButton,
    QSpinBox,
    QSplitter,
)

from .schematic_scene import SchematicScene
from .schematic_view import SchematicView
from .component_panel import ComponentPanel
from .component_library import load_component_library
from .graphics_items import ComponentItem, WireItem, CommentTextItem
from .commands import DeleteItemsCommand, RotateComponentCommand, MirrorComponentCommand
from .properties_panel import PropertiesPanel


class _ChipToolIsland(QWidget):
    """Top-pinned compact controls for chip editor canvas."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("ChipToolIsland")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "#ChipToolIsland {"
            "background: rgba(38, 38, 42, 215);"
            "border: 1px solid rgba(120, 120, 130, 180);"
            "border-radius: 10px;"
            "}"
        )
        from PySide6.QtWidgets import QGridLayout
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(8, 6, 8, 6)
        self._layout.setSpacing(6)
        self._widgets = []
        parent.installEventFilter(self)

    def set_controls(self, widgets):
        self._widgets = list(widgets)
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self._layout.removeWidget(w)
        self._layout_horizontal_wrapped()
        self.adjustSize()
        self.reset_default_geometry()

    def _layout_horizontal_wrapped(self):
        p = self.parentWidget()
        available = max(280, (p.width() - 12) if p is not None else 1000)
        hspace = int(self._layout.horizontalSpacing() if self._layout.horizontalSpacing() >= 0 else 6)

        def place_for_budget(budget: int):
            while self._layout.count():
                item = self._layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    self._layout.removeWidget(w)
            row = 0
            col = 0
            used = 0
            for w in self._widgets:
                hint_w = max(24, int(w.minimumSizeHint().width()))
                if col > 0 and (used + hspace + hint_w) > budget:
                    row += 1
                    col = 0
                    used = 0
                self._layout.addWidget(w, row, col)
                used += (hspace if col > 0 else 0) + hint_w
                col += 1

        budget = max(200, available - 20)
        for _ in range(5):
            place_for_budget(budget)
            self.adjustSize()
            if self.sizeHint().width() <= available:
                break
            budget = max(160, budget - 30)

    def reset_default_geometry(self):
        p = self.parentWidget()
        if p is None:
            return
        x = max(8, int((p.width() - self.width()) / 2))
        x = min(x, max(8, p.width() - self.width() - 8))
        self.move(x, 8)

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() in (QEvent.Resize, QEvent.Show):
            self._layout_horizontal_wrapped()
            self.adjustSize()
            self.reset_default_geometry()
        return super().eventFilter(obj, event)


class ChipEditorDialog(QDialog):
    """Chip internal editor with embedded component panel and floating controls."""

    activated = Signal(object, object)  # scene, view
    closed = Signal(object)  # self

    def __init__(self, component, parent=None):
        super().__init__(parent)
        self.component = component
        self.setWindowTitle(f"Chip: {component.refdes or component.kind}")
        self.resize(1100, 700)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self._status = QLabel("")
        self._undo_stack = QUndoStack(self)
        self._clipboard_payload: dict | None = None
        self._paste_serial: int = 0
        self.scene = SchematicScene(self._status, self._undo_stack)
        self.view = SchematicView(self.scene)
        self.scene.attach_view(self.view)
        self._library = load_component_library(force_reload=True)
        self.component_panel = ComponentPanel(self._library)
        self.component_panel.place_requested.connect(self.scene.set_mode_place)
        self.props_panel = PropertiesPanel()
        self.props_panel.set_callbacks(self._apply_properties)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(self.component_panel)
        splitter.addWidget(self.view)
        splitter.addWidget(self.props_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([260, 760, 280])
        layout.addWidget(splitter, 1)

        self._build_tool_island()
        self._build_shortcuts()
        self._install_component_shortcuts()

        parent_scene = component.scene()
        parent_theme = getattr(parent_scene, "theme", None)
        if parent_theme is not None:
            self.scene.apply_theme(parent_theme)
        self.scene.grid_size = int(getattr(parent_scene, "grid_size", 20))
        self.scene.grid_on = bool(getattr(parent_scene, "grid_on", True))
        self.scene.snap_on = bool(getattr(parent_scene, "snap_on", True))
        self.scene.grid_style = str(getattr(parent_scene, "grid_style", "dots"))

        data = component.chip_data() if hasattr(component, "chip_data") else {}
        if isinstance(data, dict) and data:
            self.scene.load(data)
        self._ensure_chip_io_netlabels()
        self.scene.request_properties = self._show_properties_for
        self.scene.selectionChanged.connect(self._on_selection_changed)

        self.view.installEventFilter(self)
        self.view.viewport().installEventFilter(self)
        self.view.horizontalScrollBar().valueChanged.connect(lambda _v: self._tool_island.reset_default_geometry())
        self.view.verticalScrollBar().valueChanged.connect(lambda _v: self._tool_island.reset_default_geometry())

    def _build_tool_island(self):
        island = _ChipToolIsland(self.view.viewport())
        self._tool_island = island

        def btn(text: str, cb, *, icon_name: str = ""):
            b = QToolButton(island)
            b.setAutoRaise(False)
            b.setToolTip(text)
            b.setMinimumHeight(28)
            b.setMinimumWidth(34)
            icon = QIcon.fromTheme(icon_name) if icon_name else QIcon()
            if not icon.isNull():
                b.setIcon(icon)
                b.setText("")
            else:
                b.setText(text)
            b.clicked.connect(cb)
            return b

        select_btn = btn("Select", self.scene.set_mode_select, icon_name="cursor-arrow")
        components_btn = btn("Components", self._focus_component_panel, icon_name="folder")
        wire_btn = btn("Wire", self.scene.set_mode_wire, icon_name="draw-line")
        text_btn = btn("Text", self.scene.set_mode_text, icon_name="insert-text")
        net_label_btn = btn("Net Label", lambda: self.scene.set_mode_place("NetLabel"), icon_name="tag")
        delete_btn = btn("Delete", self._delete_selected, icon_name="edit-delete")
        rot_cw_btn = btn("Rotate CW", lambda: self._rotate_selected(90), icon_name="object-rotate-right")
        rot_ccw_btn = btn("Rotate CCW", lambda: self._rotate_selected(-90), icon_name="object-rotate-left")
        grid_btn = btn("Grid", self._toggle_grid, icon_name="view-grid")
        snap_btn = btn("Snap", self._toggle_snap, icon_name="snap-to-grid")
        fit_btn = btn("Fit", self.view.fit_all, icon_name="zoom-fit-best")
        zoom_in_btn = btn("Zoom In", lambda: self.view.scale(1.15, 1.15), icon_name="zoom-in")
        zoom_out_btn = btn("Zoom Out", lambda: self.view.scale(1 / 1.15, 1 / 1.15), icon_name="zoom-out")

        grid_label = QLabel("Grid:", island)
        self._grid_spin = QSpinBox(island)
        self._grid_spin.setRange(5, 200)
        self._grid_spin.setSingleStep(5)
        self._grid_spin.setSuffix(" px")
        self._grid_spin.setValue(self.scene.grid_size)
        self._grid_spin.valueChanged.connect(self._change_grid_size)

        island.set_controls([
            select_btn, components_btn, wire_btn, text_btn, net_label_btn, delete_btn,
            rot_cw_btn, rot_ccw_btn, grid_btn, snap_btn, grid_label, self._grid_spin,
            fit_btn, zoom_in_btn, zoom_out_btn,
        ])
        island.show()

    def _ensure_chip_io_netlabels(self):
        """Expose chip pins inside as numbered NetLabel anchors (1..N)."""
        if not hasattr(self.component, "chip_pin_count"):
            return
        n = int(self.component.chip_pin_count())
        expected = [str(i) for i in range(1, n + 1)]
        existing = set()
        for it in self.scene.items():
            if isinstance(it, ComponentItem) and it.kind == "NetLabel":
                name = (it.value or "").strip()
                if name:
                    existing.add(name)
        missing = [n for n in expected if n not in existing]
        if not missing:
            return

        def spread_y(count: int) -> list[float]:
            if count <= 1:
                return [0.0]
            span = max(80.0, (count - 1) * 24.0)
            top = -span / 2.0
            step = span / float(count - 1)
            return [top + i * step for i in range(count)]

        left_n = (n + 1) // 2
        right_n = n - left_n
        left_names = [str(i) for i in range(1, left_n + 1)]
        right_names = [str(i) for i in range(left_n + 1, n + 1)]
        left_pos = {name: y for name, y in zip(left_names, spread_y(left_n))}
        right_pos = {name: y for name, y in zip(right_names, spread_y(right_n))}

        for name in missing:
            if name in left_pos:
                pos = QPointF(-300.0, left_pos.get(name, 0.0))
            else:
                pos = QPointF(300.0, right_pos.get(name, 0.0))
            c = ComponentItem("NetLabel", pos)
            c.set_refdes(self.scene._next_refdes("NetLabel"))
            c.set_value(name)
            self.scene.addItem(c)
            if self.scene.theme and hasattr(c, "apply_theme"):
                c.apply_theme(self.scene.theme)

    def _focus_component_panel(self):
        self.component_panel.show()
        if hasattr(self.component_panel, "search"):
            self.component_panel.search.setFocus()

    def _build_shortcuts(self):
        def add_shortcut(seq, cb):
            act = QAction(self)
            act.setShortcut(seq)
            act.triggered.connect(cb)
            self.addAction(act)
            return act

        self._act_undo = add_shortcut(QKeySequence.Undo, lambda: self.scene.undo_stack.undo())
        self._act_redo = add_shortcut(QKeySequence.Redo, lambda: self.scene.undo_stack.redo())
        self._act_toggle_grid = add_shortcut(QKeySequence("Ctrl+G"), self._toggle_grid)
        self._act_toggle_snap = add_shortcut(QKeySequence("Ctrl+Shift+S"), self._toggle_snap)
        self._act_wire = add_shortcut(QKeySequence("W"), self.scene.set_mode_wire)
        self._act_text = add_shortcut(QKeySequence("T"), self.scene.set_mode_text)
        self._act_components = add_shortcut(QKeySequence("A"), self._focus_component_panel)
        self._act_fit = add_shortcut(QKeySequence("F"), self.view.fit_all)
        self._act_escape = add_shortcut(QKeySequence(Qt.Key_Escape), self._on_shortcut_escape_to_select)
        self._act_place_rot_ccw = add_shortcut(QKeySequence("Shift+R"), self._on_shortcut_place_rotate_ccw)
        self._act_place_mirror_x = add_shortcut(QKeySequence("X"), self._on_shortcut_place_mirror_x)
        self._act_place_mirror_y = add_shortcut(QKeySequence("Y"), self._on_shortcut_place_mirror_y)
        self._act_select = add_shortcut(QKeySequence("V"), self.scene.set_mode_select)
        self._act_net_label = add_shortcut(QKeySequence("N"), lambda: self.scene.set_mode_place("NetLabel"))
        self._act_delete = add_shortcut(QKeySequence.Delete, self._delete_selected)
        self._act_rot_cw = add_shortcut(QKeySequence("]"), lambda: self._rotate_selected(90))
        self._act_rot_ccw = add_shortcut(QKeySequence("["), lambda: self._rotate_selected(-90))
        self._act_zoom_in = add_shortcut(QKeySequence("+"), lambda: self.view.scale(1.15, 1.15))
        self._act_zoom_out = add_shortcut(QKeySequence("-"), lambda: self.view.scale(1 / 1.15, 1 / 1.15))
        self._act_cut = add_shortcut(QKeySequence.Cut, self._on_shortcut_cut)
        self._act_copy = add_shortcut(QKeySequence.Copy, self._on_shortcut_copy)
        self._act_paste = add_shortcut(QKeySequence.Paste, self._on_shortcut_paste)

    def _show_properties_for(self, comp: ComponentItem):
        self.scene.clearSelection()
        comp.setSelected(True)
        self.props_panel.show_component(comp)
        self.props_panel.refdes_edit.setFocus()

    def _on_selection_changed(self):
        selected = self.scene.selectedItems()
        texts = [it for it in selected if isinstance(it, CommentTextItem)]
        if texts:
            self.props_panel.show_text(texts[0])
            return
        wires = [it for it in selected if isinstance(it, WireItem)]
        if wires:
            self.props_panel.show_wire(wires[0])
            return
        comps = [it for it in selected if isinstance(it, ComponentItem)]
        self.props_panel.show_component(comps[0] if comps else None)

    def _apply_properties(
        self,
        kind: str | None,
        refdes: str | None,
        value: str | None,
        wire_color: str,
        text_value: str = "",
        text_size: int = 12,
        text_bold: bool = False,
        text_italic: bool = False,
        text_color: str = "",
        text_family: str = "",
    ):
        if kind == "text":
            texts = [it for it in self.scene.selectedItems() if isinstance(it, CommentTextItem)]
            for t in texts:
                if text_value is not None:
                    t.setPlainText(text_value)
                f = t.font()
                if text_family:
                    f.setFamily(text_family)
                f.setPointSize(max(1, int(text_size)))
                f.setBold(bool(text_bold))
                f.setItalic(bool(text_italic))
                t.setFont(f)
                if hasattr(t, "set_text_color"):
                    from PySide6.QtGui import QColor
                    t.set_text_color(QColor(text_color) if text_color else None)
            return
        if kind == "wire":
            wires = [it for it in self.scene.selectedItems() if isinstance(it, WireItem)]
            for w in wires:
                if hasattr(w, "set_wire_color"):
                    w.set_wire_color(wire_color or None)
            return
        comps = [it for it in self.scene.selectedItems() if isinstance(it, ComponentItem)]
        for c in comps:
            if refdes is not None:
                c.set_refdes(refdes)
            if value is not None:
                c.set_value(value)

    def _install_component_shortcuts(self):
        for act in getattr(self, "_component_shortcut_actions", []):
            self.removeAction(act)
        self._component_shortcut_actions = []
        for comp in self._library.sorted_components():
            if not comp.shortcut:
                continue
            act = QAction(f"Place {comp.display_name}", self)
            act.setShortcut(comp.shortcut)
            act.triggered.connect(
                lambda _=False, k=comp.kind, s=str(comp.shortcut): self._handle_component_shortcut(k, s)
            )
            self.addAction(act)
            self._component_shortcut_actions.append(act)

    def _handle_component_shortcut(self, kind: str, shortcut: str):
        key = shortcut.strip().upper()
        if self._in_place_mode_with_ghost() and key == "R":
            g = self.scene._ghost_item
            g.setRotation((g.rotation() + 90) % 360)
            return
        self.scene.set_mode_place(kind)

    def _toggle_grid(self):
        self.scene.grid_on = not self.scene.grid_on
        self.scene.update()

    def _toggle_snap(self):
        self.scene.snap_on = not self.scene.snap_on

    def _change_grid_size(self, value: int):
        self.scene.grid_size = max(1, int(value))
        self.scene.update()

    def _delete_selected(self):
        sel = list(self.scene.selectedItems())
        if sel:
            self.scene.undo_stack.push(DeleteItemsCommand(self.scene, sel))

    def _rotate_selected(self, angle: int):
        comps = [it for it in self.scene.selectedItems() if isinstance(it, ComponentItem)]
        for c in comps:
            old = c.rotation()
            if angle > 0:
                c.rotate_cw()
            else:
                c.rotate_ccw()
            self.scene.undo_stack.push(RotateComponentCommand(c, old, c.rotation()))

    def _on_shortcut_escape_to_select(self):
        self.scene.clearSelection()
        self.scene.set_mode_select()

    def _in_place_mode_with_ghost(self) -> bool:
        return (
            getattr(self.scene, "mode", None) == getattr(self.scene, "Mode", object()).PLACE
            and getattr(self.scene, "_ghost_item", None) is not None
        )

    def _on_shortcut_place_rotate_ccw(self):
        if not self._in_place_mode_with_ghost():
            return
        g = self.scene._ghost_item
        g.setRotation((g.rotation() - 90) % 360)

    def _on_shortcut_place_mirror_x(self):
        if self._in_place_mode_with_ghost():
            g = self.scene._ghost_item
            if hasattr(g, "toggle_mirror_x"):
                g.toggle_mirror_x()
            return
        self._mirror_selected("x")

    def _on_shortcut_place_mirror_y(self):
        if self._in_place_mode_with_ghost():
            g = self.scene._ghost_item
            if hasattr(g, "toggle_mirror_y"):
                g.toggle_mirror_y()
            return
        self._mirror_selected("y")

    def _mirror_selected(self, axis: str):
        comps = [it for it in self.scene.selectedItems() if isinstance(it, ComponentItem)]
        if not comps:
            return
        for c in comps:
            ms = c.mirror_state() if hasattr(c, "mirror_state") else {"mx": 1.0, "my": 1.0}
            old = (float(ms.get("mx", 1.0)), float(ms.get("my", 1.0)))
            if axis == "x":
                new = (-old[0], old[1])
            else:
                new = (old[0], -old[1])
            self.scene.undo_stack.push(MirrorComponentCommand(c, old, new))

    def _on_shortcut_copy(self):
        selected = list(self.scene.selectedItems())
        comps = [it for it in selected if isinstance(it, ComponentItem)]
        wires_selected = [it for it in selected if isinstance(it, WireItem)]
        if not comps and not wires_selected:
            return

        comp_map = {c: i for i, c in enumerate(comps)}
        payload = {"components": [], "wires": []}
        for c in comps:
            payload["components"].append({
                "kind": c.kind,
                "pos": [float(c.scenePos().x()), float(c.scenePos().y())],
                "rotation": float(c.rotation()),
                "mirror": c.mirror_state() if hasattr(c, "mirror_state") else {"mx": 1.0, "my": 1.0},
                "value": c.value,
                "labels": c.labels_state() if hasattr(c, "labels_state") else {},
                "chip": c.chip_data() if hasattr(c, "chip_data") else {},
            })

        all_wires = [it for it in self.scene.items() if isinstance(it, WireItem)]
        for w in all_wires:
            include = (w in wires_selected)
            if not include:
                pa = getattr(w, "port_a", None)
                pb = getattr(w, "port_b", None)
                include = bool(
                    pa is not None and pb is not None and
                    pa.parentItem() in comp_map and pb.parentItem() in comp_map
                )
            if not include:
                continue
            entry = {
                "points": [{"x": float(p.x()), "y": float(p.y())} for p in getattr(w, "_pts", [])],
                "mode": getattr(w, "route_mode", "orth"),
            }
            if hasattr(w, "wire_color_hex"):
                c = w.wire_color_hex()
                if c:
                    entry["color"] = c
            pa = getattr(w, "port_a", None)
            pb = getattr(w, "port_b", None)
            if pa is not None and pa.parentItem() in comp_map:
                entry["a"] = [comp_map[pa.parentItem()], getattr(pa, "name", "A")]
            elif getattr(w, "_start_point", None) is not None:
                entry["a_point"] = {"x": float(w._start_point.x()), "y": float(w._start_point.y())}
            if pb is not None and pb.parentItem() in comp_map:
                entry["b"] = [comp_map[pb.parentItem()], getattr(pb, "name", "B")]
            elif getattr(w, "_end_point", None) is not None:
                entry["b_point"] = {"x": float(w._end_point.x()), "y": float(w._end_point.y())}
            if "a" in entry or "a_point" in entry or "b" in entry or "b_point" in entry:
                payload["wires"].append(entry)

        self._clipboard_payload = payload
        self._paste_serial = 0

    def _on_shortcut_cut(self):
        self._on_shortcut_copy()
        self._delete_selected()

    def _on_shortcut_paste(self):
        payload = self._clipboard_payload or {}
        comps_data = payload.get("components", [])
        wires_data = payload.get("wires", [])
        if not comps_data and not wires_data:
            return
        self._paste_serial += 1
        d = float(getattr(self.scene, "grid_size", 20) * self._paste_serial)
        delta = QPointF(d, d)

        new_comps: list[ComponentItem] = []
        for cdata in comps_data:
            kind = cdata.get("kind", "")
            pos = cdata.get("pos", [0.0, 0.0])
            c = ComponentItem(kind, QPointF(float(pos[0]), float(pos[1])) + delta)
            c.setRotation(float(cdata.get("rotation", 0.0)))
            m = cdata.get("mirror", {})
            if hasattr(c, "set_mirror"):
                c.set_mirror(float(m.get("mx", 1.0)), float(m.get("my", 1.0)))
            c.set_refdes(self.scene._next_refdes(kind))
            self.scene._bump_refseq(kind)
            c.set_value(str(cdata.get("value", "")))
            if hasattr(c, "set_chip_data"):
                c.set_chip_data(cdata.get("chip", {}))
            if hasattr(c, "apply_labels_state"):
                c.apply_labels_state(cdata.get("labels", {}))
            self.scene.addItem(c)
            if self.scene.theme and hasattr(c, "apply_theme"):
                c.apply_theme(self.scene.theme)
            new_comps.append(c)

        for wdata in wires_data:
            ai, aside = wdata.get("a", [None, None])
            bi, bside = wdata.get("b", [None, None])
            pa = pb = None
            a_point = b_point = None
            if ai is not None and isinstance(ai, int) and 0 <= ai < len(new_comps):
                ca = new_comps[ai]
                pa = next((p for p in getattr(ca, "ports", []) if getattr(p, "name", None) == aside), None)
            elif isinstance(wdata.get("a_point"), dict):
                ap = wdata["a_point"]
                a_point = QPointF(float(ap.get("x", 0.0)), float(ap.get("y", 0.0))) + delta
            if bi is not None and isinstance(bi, int) and 0 <= bi < len(new_comps):
                cb = new_comps[bi]
                pb = next((p for p in getattr(cb, "ports", []) if getattr(p, "name", None) == bside), None)
            elif isinstance(wdata.get("b_point"), dict):
                bp = wdata["b_point"]
                b_point = QPointF(float(bp.get("x", 0.0)), float(bp.get("y", 0.0))) + delta

            w = WireItem(
                pa, pb,
                start_point=a_point,
                end_point=b_point,
                theme=getattr(self.scene, "theme", None),
                route_mode=wdata.get("mode", "orth"),
            )
            pts = [QPointF(float(p.get("x", 0.0)), float(p.get("y", 0.0))) + delta for p in wdata.get("points", [])]
            if pts:
                w.set_points(pts)
            color_hex = wdata.get("color", "")
            if color_hex and hasattr(w, "set_wire_color"):
                w.set_wire_color(color_hex)
            self.scene.addItem(w)
            if self.scene.theme and hasattr(w, "apply_theme"):
                w.apply_theme(self.scene.theme)

    def eventFilter(self, obj, event):
        if obj in (self.view, self.view.viewport()):
            if event.type() in (QEvent.MouseButtonPress, QEvent.FocusIn):
                self.activated.emit(self.scene, self.view)
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.view.fit_all)
        self.activated.emit(self.scene, self.view)

    def closeEvent(self, event):
        if hasattr(self.component, "set_chip_data"):
            self.component.set_chip_data(self.scene.serialize())
        self.closed.emit(self)
        super().closeEvent(event)
