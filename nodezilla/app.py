# ========================================
# File: nodezilla/app.py
# ========================================
from PySide6.QtWidgets import QApplication
from .main_window import MainWindow
import sys


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()