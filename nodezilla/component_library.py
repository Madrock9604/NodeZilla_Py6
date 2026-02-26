from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Iterable
import json
from .paths import user_assets_root


# Component library data model.
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
    # comp_type: "component" for normal parts, "net" for net labels (GND/VDD/etc).
    comp_type: str = "component"  # component | net
    net_name: str = ""
    show_value: bool = True
    value_label: str = "Value"
    # SPICE type letter (R, C, L, D, V, I, X, ...). Optional.
    spice_type: str = ""
    # Default value/part number assigned when placing a new component.
    default_value: str = ""
    # Hierarchical "black-box" component that owns an internal schematic.
    is_chip: bool = False
    # Optional chip template path under assets/chips (e.g., "library/my_chip.json").
    chip_template: str = ""
    # Visibility in component library panel.
    visible: bool = True


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
    return user_assets_root()


def _library_root() -> Path:
    return _assets_root() / "components" / "library"


def _parse_component(
    entry: dict,
    *,
    fallback_category: str = "General",
    override_category: str | None = None,
) -> ComponentDef:
    """Parse a single component JSON dict into a ComponentDef."""
    ports = []
    for p in entry.get("ports", []):
        name = str(p.get("name", "")).strip() or "P"
        ports.append(PortDef(name=name, x=float(p.get("x", 0.0)), y=float(p.get("y", 0.0))))
    if not ports:
        ports = [PortDef("A", -50.0, 0.0), PortDef("B", 50.0, 0.0)]
    kind = str(entry.get("kind", "")).strip()
    display_name = str(entry.get("display_name", kind)).strip() or kind
    prefix = str(entry.get("prefix", kind[:1].upper() if kind else "X")).strip() or "X"
    category = str(entry.get("category", fallback_category)).strip() or fallback_category
    if override_category:
        category = override_category
    # Derive defaults for part-number style components if not provided.
    spice_type = str(entry.get("spice_type", "")).strip()
    value_label = str(entry.get("value_label", "Value")).strip() or "Value"
    default_value = str(entry.get("default_value", "")).strip()
    if not default_value:
        label = value_label.lower()
        if "part" in label or spice_type.upper() in {"D", "Q", "U", "X"}:
            default_value = display_name
    return ComponentDef(
        kind=kind,
        display_name=display_name,
        prefix=prefix,
        ports=ports,
        symbol=str(entry.get("symbol", "")).strip(),
        category=category,
        shortcut=str(entry.get("shortcut", "")).strip(),
        auto_align_terminals=bool(entry.get("auto_align_terminals", len(ports) <= 2)),
        auto_scale_symbol=bool(entry.get("auto_scale_symbol", True)),
        comp_type=str(entry.get("type", "component")).strip() or "component",
        net_name=str(entry.get("net_name", "")).strip(),
        show_value=bool(entry.get("show_value", True)),
        value_label=value_label,
        spice_type=spice_type,
        default_value=default_value,
        is_chip=bool(entry.get("is_chip", False)),
        chip_template=str(entry.get("chip_template", "")).strip(),
        visible=bool(entry.get("visible", True)),
    )


_CACHE: ComponentLibrary | None = None


def _iter_component_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.json") if p.is_file()]


def load_component_library(path: Path | None = None, *, force_reload: bool = False) -> ComponentLibrary:
    """Load components from the library tree (folder-per-component)."""
    global _CACHE
    if _CACHE is not None and path is None and not force_reload:
        return _CACHE

    components: List[ComponentDef] = []

    def _load_from(path: Path, fallback_category: str = "General"):
        try:
            data = json.loads(path.read_text())
            # Single component file
            if isinstance(data, dict) and "kind" in data:
                c = _parse_component(
                    data,
                    fallback_category=fallback_category,
                    override_category=fallback_category,
                )
                if c.kind:
                    components.append(c)
                return
        except Exception:
            pass

    if path is not None:
        _load_from(path)
    else:
        root = _library_root()
        for file_path in _iter_component_files(root):
            rel = file_path.relative_to(root)
            # category from folder path (allow nested folders)
            parts = [p.replace("_", " ") for p in rel.parent.parts if p]
            fallback_category = " / ".join(parts) if parts else "General"
            _load_from(file_path, fallback_category=fallback_category)

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


def find_component_file(kind: str) -> Optional[Path]:
    """Locate the JSON file that defines a given component kind."""
    root = _library_root()
    if not root.exists():
        return None
    for p in root.rglob("*.json"):
        try:
            data = json.loads(p.read_text())
            if isinstance(data, dict) and str(data.get("kind", "")).strip() == kind:
                return p
        except Exception:
            continue
    return None
