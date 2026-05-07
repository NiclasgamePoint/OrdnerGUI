"""Main Entry Point für PapaGUI"""
import sys
from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow


def main():
    """Startet die Anwendung"""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
