from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys


APP_NAME = "NodeZilla"


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def user_root() -> Path:
    # User-visible location (not hidden app support) per request.
    return Path.home() / "Documents" / APP_NAME


def user_examples_dir() -> Path:
    return user_root() / "Examples"


def user_projects_dir() -> Path:
    return user_root() / "Projects"


def user_assets_root() -> Path:
    return user_root() / "assets"


def user_library_root() -> Path:
    return user_assets_root() / "components" / "library"


def user_symbols_root() -> Path:
    return user_assets_root() / "symbols"


def user_chips_root() -> Path:
    return user_assets_root() / "chips"


def user_pl_path() -> Path:
    return user_root() / "PL.txt"


def _copy_missing_tree(src: Path, dst: Path):
    if not src.exists():
        return
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        t = dst / rel
        if p.is_dir():
            t.mkdir(parents=True, exist_ok=True)
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        if not t.exists():
            shutil.copy2(p, t)


def ensure_user_workspace():
    root = user_root()
    root.mkdir(parents=True, exist_ok=True)
    user_projects_dir().mkdir(parents=True, exist_ok=True)
    user_assets_root().mkdir(parents=True, exist_ok=True)
    user_library_root().mkdir(parents=True, exist_ok=True)
    user_symbols_root().mkdir(parents=True, exist_ok=True)
    user_chips_root().mkdir(parents=True, exist_ok=True)

    b = bundled_root()
    _copy_missing_tree(b / "Examples", user_examples_dir())
    _copy_missing_tree(b / "assets" / "components" / "library", user_library_root())
    _copy_missing_tree(b / "assets" / "symbols", user_symbols_root())
    _copy_missing_tree(b / "assets" / "chips", user_chips_root())

    pl = user_pl_path()
    if not pl.exists():
        bundled_pl = b / "PL.txt"
        if bundled_pl.exists():
            shutil.copy2(bundled_pl, pl)
        else:
            pl.write_text("")

