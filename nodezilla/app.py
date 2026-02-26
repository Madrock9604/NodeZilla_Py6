from __future__ import annotations

import faulthandler
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from nodezilla.main_window import MainWindow
from nodezilla.paths import ensure_user_workspace


def _startup_log_path() -> Path:
    # User-visible log location for packaged app runs (Finder launches).
    return Path.home() / "Library" / "Logs" / "NodeZilla" / "startup.log"


def _append_startup_log(text: str):
    try:
        p = _startup_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
    except Exception:
        pass


def main():
    """Application entry point used by run.py.

    Creates one QApplication, mounts the main window, and starts Qt's event loop.
    """
    # Capture hard crashes (e.g., segfaults in native libs) into a log file.
    try:
        p = _startup_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        _fh = p.open("a", encoding="utf-8")
        faulthandler.enable(_fh)
        _append_startup_log(f"[{datetime.now().isoformat()}] NodeZilla start")
    except Exception:
        pass

    def _excepthook(exc_type, exc_value, exc_tb):
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _append_startup_log(tb)
        try:
            QMessageBox.critical(
                None,
                "NodeZilla Startup Error",
                "NodeZilla hit an error during startup.\n\n"
                f"Details were written to:\n{_startup_log_path()}",
            )
        except Exception:
            pass

    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    ensure_user_workspace()
    w = MainWindow()
    w.show()
    return app.exec()
