from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMessageBox

from .. import __version__
from ..cli import _ensure_daemon
from ..compatibility import relay_home_id
from ..errors import RelayError
from .design_styles import application_palette, application_stylesheet
from .design_typography import application_font
from .main_window import MainWindow


def run_gui(config) -> int:
    app = QApplication.instance() or QApplication([])
    app.setFont(application_font())
    app.setPalette(application_palette())
    app.setStyleSheet(application_stylesheet())
    try:
        _ensure_daemon(config)
    except RelayError as exc:
        if not config.get("daemon_auto_start", True):
            pass
        elif exc.code != "DAEMON_UNAVAILABLE":
            QMessageBox.critical(None, "Relay-agent", exc.message)
    window = MainWindow(config, gui_version=__version__, expected_home_id=relay_home_id(config.home))
    window.show()
    return app.exec()
