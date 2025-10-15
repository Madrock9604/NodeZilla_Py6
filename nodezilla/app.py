# ========================================
# File: nodezilla/app.py
# ========================================
from PySide6.QtWidgets import QApplication
from .main_window import MainWindow


def main():
    app = QApplication([])
    w = MainWindow()
    w.show()
    return app.exec()