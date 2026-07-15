import sys
from PySide6.QtWidgets import QApplication

from app.core.logging_config import configure_logging
from app.gui.main_window import MainWindow


def main():
    configure_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
