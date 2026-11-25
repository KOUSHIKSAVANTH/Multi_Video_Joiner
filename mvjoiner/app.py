"""Application entry point."""

from __future__ import annotations

import sys


def main() -> None:
    from PySide6.QtWidgets import QApplication

    from . import APP_ID, APP_NAME, __version__
    from .theme import app_icon, apply_theme
    from .ui import MainWindow

    if sys.platform == "win32":
        # Give the app its own taskbar identity (and icon) instead of python.exe's.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.{__version__}")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ID)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    apply_theme(app)

    window = MainWindow()
    window.show()

    # Allow "Open with…" on a saved project file.
    for arg in sys.argv[1:]:
        if arg.lower().endswith(".mvj"):
            window.load_project_file(arg)
            break

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
