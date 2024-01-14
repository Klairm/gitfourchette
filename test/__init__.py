import logging
import os

# Verbose logging by default in unit tests
logging.basicConfig(level=logging.DEBUG)

# Keep QT_API env var (used by our qt.py module) in sync with Qt binding used by pytest-qt
if os.environ.get("PYTEST_QT_API") and os.environ.get("QT_API"):
    # PYTEST_QT_API takes precedence over QT_API
    os.environ["QT_API"] = os.environ["PYTEST_QT_API"]
elif os.environ.get("QT_API"):
    os.environ["PYTEST_QT_API"] = os.environ["QT_API"]
else:
    from pytestqt.qt_compat import qt_api
    qt_api.set_qt_api("")  # Triggers api load
    os.environ["PYTEST_QT_API"] = qt_api.pytest_qt_api
    os.environ["QT_API"] = qt_api.pytest_qt_api

# Force qtpy (if used) to honor QT_API
os.environ["FORCE_QT_API"] = "1"

from gitfourchette.qt import *
