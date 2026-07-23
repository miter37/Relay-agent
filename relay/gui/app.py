from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMessageBox

from .. import __version__
from ..cli import _ensure_daemon
from ..compatibility import relay_home_id
from ..errors import RelayError
from .main_window import MainWindow


def run_gui(config) -> int:
    app = QApplication.instance() or QApplication([])
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
