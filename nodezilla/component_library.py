from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import json


@dataclass(frozen=True)
class PortDef:
    name: str
    x: float
    y: float


@dataclass(frozen=True)
class ComponentDef:
    kind: str
    display_name: str
    prefix: str
    ports: List[PortDef]
    symbol: str = ""
    category: str = "General"
    shortcut: str = ""
    auto_align_terminals: bool = True
    auto_scale_symbol: bool = True


class ComponentLibrary:
    def __init__(self, components: List[ComponentDef]):
        self._components = list(components)
        self._by_kind: Dict[str, ComponentDef] = {c.kind: c for c in components}

    def all(self) -> List[ComponentDef]:
        return list(self._components)

    def get(self, kind: str) -> Optional[ComponentDef]:
        return self._by_kind.get(kind)

    def sorted_components(self) -> List[ComponentDef]:
        return sorted(self._components, key=lambda c: (c.category.lower(), c.display_name.lower()))


def _assets_root() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def _default_library_path() -> Path:
    return _assets_root() / "components" / "defaults.json"


def _custom_library_path() -> Path:
    return _assets_root() / "components" / "custom.json"


def _parse_component(entry: dict) -> ComponentDef:
    ports = []
    for p in entry.get("ports", []):
        name = str(p.get("name", "")).strip() or "P"
        ports.append(PortDef(name=name, x=float(p.get("x", 0.0)), y=float(p.get("y", 0.0))))
    if not ports:
        ports = [PortDef("A", -50.0, 0.0), PortDef("B", 50.0, 0.0)]
    kind = str(entry.get("kind", "")).strip()
    display_name = str(entry.get("display_name", kind)).strip() or kind
    prefix = str(entry.get("prefix", kind[:1].upper() if kind else "X")).strip() or "X"
    return ComponentDef(
        kind=kind,
        display_name=display_name,
        prefix=prefix,
        ports=ports,
        symbol=str(entry.get("symbol", "")).strip(),
        category=str(entry.get("category", "General")).strip() or "General",
        shortcut=str(entry.get("shortcut", "")).strip(),
        auto_align_terminals=bool(entry.get("auto_align_terminals", len(ports) <= 2)),
        auto_scale_symbol=bool(entry.get("auto_scale_symbol", True)),
    )


_CACHE: ComponentLibrary | None = None


def load_component_library(path: Path | None = None, *, force_reload: bool = False) -> ComponentLibrary:
    global _CACHE
    if _CACHE is not None and path is None and not force_reload:
        return _CACHE

    components: List[ComponentDef] = []

    def _load_from(path: Path):
        try:
            data = json.loads(path.read_text())
            raw = data.get("components", [])
            for entry in raw:
                c = _parse_component(entry)
                if c.kind:
                    components.append(c)
        except Exception:
            pass

    if path is not None:
        _load_from(path)
    else:
        default_path = _default_library_path()
        custom_path = _custom_library_path()
        if default_path.exists():
            _load_from(default_path)
        if custom_path.exists():
            _load_from(custom_path)

    # De-dup by kind, favor later entries (custom overrides defaults).
    if components:
        seen: Dict[str, ComponentDef] = {}
        for c in components:
            seen[c.kind] = c
        components = list(seen.values())

    lib = ComponentLibrary(components)
    if path is None:
        _CACHE = lib
    return lib
