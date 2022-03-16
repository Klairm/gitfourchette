from gitfourchette.qt import *
from gitfourchette.util import excMessageBox, NonCriticalOperation
import os
import signal
import sys


def excepthook(exctype, value, tb):
    sys.__excepthook__(exctype, value, tb)  # run default excepthook
    excMessageBox(value, printExc=False)


def makeCommandLineParser() -> QCommandLineParser:
    parser = QCommandLineParser()
    parser.addHelpOption()
    parser.addVersionOption()
    parser.addOption(QCommandLineOption(["test-mode"], "Prevents loading/saving of user preferences."))
    parser.addPositionalArgument("repos", "Paths to repositories to open on launch.", "[repos...]")
    return parser


def main():
    # allow interrupting with Control-C
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # inject our own exception hook to show an error dialog in case of unhandled exceptions
    sys.excepthook = excepthook

    # initialize Qt before importing app modules so fonts are loaded correctly
    app = QApplication(sys.argv)
    app.setApplicationName("GitFourchette")  # used by QStandardPaths
    # Don't use app.setOrganizationName because it changes QStandardPaths.
    app.setApplicationVersion("1.0.0")

    # Initialize command line options
    commandLine = makeCommandLineParser()
    commandLine.process(app)

    # Initialize assets
    with NonCriticalOperation("Initialize assets"):
        from gitfourchette import assets_rc
        app.setWindowIcon(QIcon(":/gitfourchette.png"))

    # Apply application-wide stylesheet
    with NonCriticalOperation("Apply application-wide stylesheet"):
        styleSheetFile = QFile(":/style.qss")
        if styleSheetFile.open(QFile.ReadOnly):
            styleSheet = styleSheetFile.readAll().data().decode("utf-8")
            app.setStyleSheet(styleSheet)
            styleSheetFile.close()

    # Hack so we don't get out-of-place Tahoma on Windows.
    # TODO: Check whether Qt 6 has made this unnecessary (when it comes out).
    if QSysInfo.productType() == "windows":
        # Get QMenu's font (which is correct) instead of using raw Segoe UI,
        # because some locales might use a different font than Segoe.
        sysFont = QApplication.font("QMenu")
        QApplication.setFont(sysFont)

    # Initialize settings
    from gitfourchette import settings
    if commandLine.isSet("test-mode"):
        settings.TEST_MODE = True
    else:
        # Load settings
        with NonCriticalOperation(F"Loading {settings.prefs.filename}"):
            settings.prefs.load()
            if settings.prefs.qtStyle:
                app.setStyle(settings.prefs.qtStyle)

        # Load history
        with NonCriticalOperation(F"Loading {settings.history.filename}"):
            settings.history.load()

    # Initialize main window
    from gitfourchette.widgets.mainwindow import MainWindow
    window = MainWindow()
    window.show()

    # Initialize session
    session = settings.Session()
    if not settings.TEST_MODE:
        with NonCriticalOperation(F"Loading {session.filename}"):
            session.load()

    # Open paths passed in via the command line
    pathList = commandLine.positionalArguments()
    if pathList:
        session.tabs += [os.path.abspath(p) for p in pathList]
        session.activeTabIndex = len(session.tabs) - 1

    # Restore session
    with NonCriticalOperation("Restoring session"):
        window.restoreSession(session)

    # Keep the app running
    app.exec_()


if __name__ == "__main__":
    main()
