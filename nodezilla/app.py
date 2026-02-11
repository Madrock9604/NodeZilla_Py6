# ========================================
# File: nodezilla/app.py
# ========================================
from PySide6.QtWidgets import QApplication
from nodezilla.main_window import MainWindow
import sys


def main():
    """Application entry point used by run.py.

    Creates one QApplication, mounts the main window, and starts Qt's event loop.
    """
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()
