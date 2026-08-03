import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from app.core.config import forced_fullscreen
from app.core.logging_config import configure_logging
from app.gui.main_window import MainWindow


def main():
    configure_logging()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    window = MainWindow()
    if forced_fullscreen():
        window.showFullScreen()
    else:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
