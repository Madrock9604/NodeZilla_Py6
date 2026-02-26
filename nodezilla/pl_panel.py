from __future__ import annotations

from pathlib import Path
import os
import re
import sys

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from PySide6.QtGui import QColor, QBrush

from . import Program as P
from .component_library import load_component_library


class PlPanel(QWidget):
    """Dock panel that lists components parsed from PL.txt."""
    place_requested = Signal(dict)
    verify_requested = Signal()

    def __init__(self):
        super().__init__()
        self._pl_path: Path | None = None
        self._rows_payload: list[dict] = []

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.verify_btn = QPushButton("Verify Availability")
        top.addStretch(1)
        top.addWidget(self.verify_btn, 0)
        top.addWidget(self.refresh_btn, 0)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "Type", "Name", "Value/Part", "Used"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        root.addLayout(top)
        root.addWidget(self.table, 1)

        self.refresh_btn.clicked.connect(self.refresh)
        self.verify_btn.clicked.connect(self.verify_requested.emit)
        self.table.cellClicked.connect(self._row_clicked)
        self.refresh()

    def _set_row_used_style(self, row: int, used: bool):
        # Subtle green tint for used rows.
        bg = QBrush(QColor(36, 92, 44, 120)) if used else QBrush()
        cols = self.table.columnCount()
        for c in range(cols):
            item = self.table.item(row, c)
            if item is None:
                continue
            item.setBackground(bg)

    def _fallback_pl_candidates(self) -> list[Path]:
        out: list[Path] = []
        env_path = os.environ.get("NODEZILLA_PL_PATH", "").strip()
        if env_path:
            out.append(Path(env_path).expanduser())
        out.append(Path.cwd() / "PL.txt")
        out.append(Path(__file__).resolve().parent.parent / "PL.txt")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            out.append(Path(meipass) / "PL.txt")
        exe = Path(sys.executable).resolve()
        out.append(exe.parent / "PL.txt")
        out.append(exe.parent.parent / "Resources" / "PL.txt")
        out.append(exe.parent.parent.parent / "PL.txt")
        out.append(Path.home() / "Library" / "Application Support" / "NodeZilla" / "PL.txt")
        uniq = []
        seen = set()
        for p in out:
            s = str(p)
            if s in seen:
                continue
            seen.add(s)
            uniq.append(p)
        return uniq

    def _resolve_pl_path(self) -> Path | None:
        # Reuse Program.py resolver when available.
        resolver = getattr(P, "_resolve_pl_for_read", None)
        if callable(resolver):
            try:
                p = resolver()
                if p is not None:
                    return Path(p)
            except Exception:
                pass
        for p in self._fallback_pl_candidates():
            try:
                if p.exists() and p.is_file():
                    return p
            except Exception:
                continue
        return None

    def refresh(self):
        self._pl_path = self._resolve_pl_path()
        if self._pl_path is None:
            self.table.setRowCount(0)
            self._rows_payload = []
            return
        rows = []
        try:
            dataset = P.CreateComponentDataSet.MakeDataSet()
        except Exception:
            dataset = []
        self._rows_payload = []
        for comp in dataset:
            value_or_part = getattr(comp, "value", "")
            if str(value_or_part) == "NA":
                value_or_part = getattr(comp, "partnum", "")
            payload = {
                "id": int(getattr(comp, "ID", -1)),
                "type": str(getattr(comp, "type", "")),
                "name": str(getattr(comp, "name", "")),
                "value_or_part": str(value_or_part),
                "used": bool(getattr(comp, "used", False)),
            }
            self._rows_payload.append(payload)
            rows.append(
                (
                    str(payload["id"]),
                    payload["type"],
                    payload["name"],
                    payload["value_or_part"],
                    "yes" if payload["used"] else "no",
                )
            )

        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self.table.setItem(r, c, QTableWidgetItem(text))
            self._set_row_used_style(r, bool(self._rows_payload[r].get("used", False)))

    def _row_clicked(self, row: int, _col: int):
        if row < 0 or row >= len(self._rows_payload):
            return
        self.place_requested.emit(dict(self._rows_payload[row]))

    def mark_used(self, component_id: int, used: bool = True):
        for r, payload in enumerate(self._rows_payload):
            if int(payload.get("id", -1)) != int(component_id):
                continue
            payload["used"] = bool(used)
            item = self.table.item(r, 4)
            if item is None:
                self.table.setItem(r, 4, QTableWidgetItem("yes" if used else "no"))
            else:
                item.setText("yes" if used else "no")
            self._set_row_used_style(r, bool(used))
            return

    @staticmethod
    def _norm_value(v: str):
        s = str(v or "").strip()
        if not s:
            return ("txt", "")
        # Engineering notation parser:
        # examples: 10nF, 1uF, 2.2k, 3.3e-8, 1mH, 470R
        sv = s.replace("µ", "u").replace("Ω", "ohm").strip()
        m = re.match(
            r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Z]+)?\s*$",
            sv,
        )
        if m:
            try:
                base = float(m.group(1))
                suffix = (m.group(2) or "").strip()
                if suffix:
                    sl = suffix.lower()
                    # Accept optional unit text after multiplier prefix (nF, uH, kOhm, etc.).
                    if sl.startswith("meg"):
                        mult = 1e6
                    else:
                        prefix = sl[0]
                        mult_map = {
                            "g": 1e9,
                            "k": 1e3,
                            "m": 1e-3,
                            "u": 1e-6,
                            "n": 1e-9,
                            "p": 1e-12,
                            "f": 1e-15,
                            "r": 1.0,  # common resistor notation (e.g., 4R7)
                            "v": 1.0,
                            "a": 1.0,
                            "h": 1.0,
                            "o": 1.0,  # ohm
                        }
                        mult = mult_map.get(prefix, 1.0)
                    return ("num", round(base * mult, 15))
                return ("num", round(base, 15))
            except Exception:
                pass
        try:
            return ("num", round(float(s), 15))
        except Exception:
            return ("txt", s.lower())

    @staticmethod
    def _canonical_type_name(raw_type: str) -> str:
        t = str(raw_type or "").strip().lower()
        if t in {"resistor", "r"}:
            return "resistor"
        if t in {"capacitor", "c"}:
            return "capacitor"
        if t in {"inductor", "l"}:
            return "inductor"
        if t in {"diode", "d"}:
            return "diode"
        if t in {"instrument", "x"}:
            return "instrument"
        return t

    @classmethod
    def _component_type_name(cls, comp, comp_def) -> str:
        kind = str(getattr(comp, "kind", "")).strip().lower()
        display = str(getattr(comp_def, "display_name", "")).strip().lower() if comp_def else ""
        spice = str(getattr(comp_def, "spice_type", "")).strip().upper() if comp_def else ""
        if spice == "R" or "resistor" in kind or "resistor" in display:
            return "resistor"
        if spice == "C" or "capacitor" in kind or "capacitor" in display:
            return "capacitor"
        if spice == "L" or "inductor" in kind or "inductor" in display:
            return "inductor"
        if spice == "D" or "diode" in kind or "diode" in display:
            return "diode"
        if spice == "X" or "instrument" in display or "wavegen" in kind or "oscope" in kind:
            return "instrument"
        return cls._canonical_type_name(kind or display)

    @classmethod
    def signature_from_kind_value(cls, kind: str, value: str):
        lib = load_component_library()
        cdef = lib.get(str(kind or "").strip())
        class _Tmp:
            pass
        tmp = _Tmp()
        tmp.kind = str(kind or "")
        tmp.value = str(value or "")
        return cls._component_signature(tmp, cdef)

    @classmethod
    def _row_signature(cls, row_payload: dict):
        return (
            cls._canonical_type_name(str(row_payload.get("type", ""))),
            cls._norm_value(row_payload.get("value_or_part", "")),
        )

    @classmethod
    def _component_signature(cls, comp, comp_def):
        return (
            cls._component_type_name(comp, comp_def),
            cls._norm_value(getattr(comp, "value", "")),
        )

    def _requested_row_indices_by_signature(self) -> dict:
        out = {}
        for idx, row in enumerate(self._rows_payload):
            sig = self._row_signature(row)
            out.setdefault(sig, []).append(idx)
        return out

    def requested_count_for_signature(self, sig) -> int:
        by_sig = self._requested_row_indices_by_signature()
        return len(by_sig.get(sig, []))

    def component_signature(self, comp):
        lib = load_component_library()
        cdef = lib.get(str(getattr(comp, "kind", "")).strip())
        return self._component_signature(comp, cdef)

    def is_physical_component(self, comp) -> bool:
        lib = load_component_library()
        cdef = lib.get(str(getattr(comp, "kind", "")).strip())
        if cdef is None:
            return True
        if bool(getattr(cdef, "is_chip", False)):
            return False
        return str(getattr(cdef, "comp_type", "component")).lower() != "net"

    def sync_used_from_components(self, components: list):
        """Recompute Used column from live schematic components.

        A PL row is considered used by pool allocation of matching
        (type, value/part) signatures. Names are not required to match.
        """
        lib = load_component_library()
        placed_counts = {}
        for comp in components:
            cdef = lib.get(str(getattr(comp, "kind", "")).strip())
            if cdef is not None and (
                str(getattr(cdef, "comp_type", "component")).lower() == "net"
                or bool(getattr(cdef, "is_chip", False))
            ):
                continue
            sig = self._component_signature(comp, cdef)
            placed_counts[sig] = int(placed_counts.get(sig, 0)) + 1

        by_sig = self._requested_row_indices_by_signature()
        used_rows = set()
        for sig, row_indices in by_sig.items():
            used_n = min(len(row_indices), int(placed_counts.get(sig, 0)))
            for i in row_indices[:used_n]:
                used_rows.add(i)

        for r, row_payload in enumerate(self._rows_payload):
            used = r in used_rows
            row_payload["used"] = bool(used)
            item = self.table.item(r, 4)
            if item is None:
                self.table.setItem(r, 4, QTableWidgetItem("yes" if used else "no"))
            else:
                item.setText("yes" if used else "no")
            self._set_row_used_style(r, bool(used))
