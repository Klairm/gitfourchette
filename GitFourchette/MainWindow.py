import git
from PySide2.QtCore import *
from PySide2.QtGui import *
from PySide2.QtWidgets import *
import globals
import os
import traceback
from pathlib import Path

def splural(n: int) -> str:
    return '' if n == 1 else 's'

def compactPath(path: str) -> str:
    home = str(Path.home())
    if path.startswith(str(home)):
        path = "~" + path[len(home):]
    return path


class RepoState:
    dir: str
    repo: git.Repo
    index: git.IndexFile
    settings: QSettings

    def __init__(self, dir):
        self.dir = os.path.abspath(dir)
        self.repo = git.Repo(dir)
        self.index = self.repo.index
        self.settings = QSettings(self.repo.common_dir + "/fourchette.ini", QSettings.Format.IniFormat)
        self.settings.setValue("GitFourchette", globals.VERSION)


class MainWindow(QMainWindow):
    state: RepoState

    def __init__(self):
        super(__class__, self).__init__()

        import DiffView, GraphView, TreeView

        self.ready = False
        self.state = None

        self.resize(globals.appSettings.value("MainWindow/size", QSize(800, 600)))
        self.move(globals.appSettings.value("MainWindow/position", QPoint(50, 50)))

        menubar = QMenuBar()
        fileMenu = menubar.addMenu("&File")
        repoMenu = menubar.addMenu("&Repo")
        self.createFileMenu(fileMenu)
        self.createRepoMenu(repoMenu)

        helpMenu = menubar.addMenu("&Help")
        helpMenu.addAction(F"About {globals.PROGRAM_NAME}", self.about)
        helpMenu.addAction("About Qt", QMessageBox.aboutQt)
        helpMenu.addSeparator()
        helpMenu.addAction("Memory", self.memInfo)

        self.graphView = GraphView.GraphView(self)
        self.filesStack = QStackedWidget()
        self.diffView = DiffView.DiffView(self)
        self.changedFilesView = TreeView.TreeView(self)
        self.dirtyView = TreeView.UnstagedView(self)
        self.stageView = TreeView.StagedView(self)

        self.setMenuBar(menubar)
        centralWidget = QWidget()

        windowVBox = QVBoxLayout()
        centralWidget.setLayout(windowVBox)
        # windowVBox.setSpacing(0)
        # windowVBox.setContentsMargins(0, 0, 0, 0)

        self.dirtyLabel = QLabel("Dirty Files")
        self.stageLabel = QLabel("Files Staged For Commit")

        dirtyContainer = QWidget()
        dirtyContainer.setLayout(QVBoxLayout())
        dirtyContainer.layout().setContentsMargins(0,0,0,0)
        dirtyContainer.layout().addWidget(self.dirtyLabel)
        dirtyContainer.layout().addWidget(self.dirtyView)
        stageContainer = QWidget()
        stageContainer.setLayout(QVBoxLayout())
        stageContainer.layout().setContentsMargins(0, 0, 0, 0)
        stageContainer.layout().addWidget(self.stageLabel)
        stageContainer.layout().addWidget(self.stageView)
        commitButton = QPushButton("Commit")
        commitButton.clicked.connect(self.commitFlow)
        stageContainer.layout().addWidget(commitButton)
        stageSplitter = QSplitter(Qt.Vertical)
        stageSplitter.setHandleWidth(globals.splitterHandleWidth)
        stageSplitter.addWidget(dirtyContainer)
        stageSplitter.addWidget(stageContainer)

        self.filesStack.addWidget(self.changedFilesView)
        self.filesStack.addWidget(stageSplitter)
        self.filesStack.setCurrentIndex(0)

        bottomSplitter = QSplitter(Qt.Horizontal)
        bottomSplitter.setHandleWidth(globals.splitterHandleWidth)
        bottomSplitter.addWidget(self.filesStack)
        bottomSplitter.addWidget(self.diffView)

        bottomSplitter.setSizes([100, 300])

        mainSplitter = QSplitter(Qt.Vertical)
        mainSplitter.setHandleWidth(globals.splitterHandleWidth)
        mainSplitter.addWidget(self.graphView)
        mainSplitter.addWidget(bottomSplitter)
        mainSplitter.setSizes([100, 150])

        self.stageSplitter = stageSplitter
        self.bottomSplitter = bottomSplitter
        self.mainSplitter = mainSplitter

        windowVBox.addWidget(mainSplitter)

        mainSplitter.setObjectName("MainSplitter")
        bottomSplitter.setObjectName("BottomSplitter")
        stageSplitter.setObjectName("StageSplitter")
        self.splittersToSave = [mainSplitter, bottomSplitter, stageSplitter]
        for sts in self.splittersToSave:
            k = F"Splitters/{sts.objectName()}"
            if globals.appSettings.contains(k):
                sts.restoreState(globals.appSettings.value(k))

        self.setWindowTitle(globals.PROGRAM_NAME)

        self.setWindowIcon(QIcon("Junk/gf2.png"))
        self.setCentralWidget(centralWidget)

        self.ready = True

    def about(self):
        import sys, PySide2
        about_text = F"""\
        <h2>{globals.PROGRAM_NAME} {globals.VERSION}</h2>
        <p><small>
        {git.Git().version()}<br>
        Python {sys.version}<br>
        GitPython {git.__version__}<br>
        Qt {PySide2.QtCore.__version__}<br>
        PySide2 {PySide2.__version__}
        </small></p>
        <p>
        This is my git frontend.<br>There are many like it but this one is mine.
        </p>
        """
        QMessageBox.about(self, F"About {globals.PROGRAM_NAME}", about_text)

    def memInfo(self):
        import psutil, gc
        gc.collect()
        QMessageBox.information(self, F"Memory usage", F"{psutil.Process(os.getpid()).memory_info().rss:,}")

    def createFileMenu(self, m: QMenu):
        m.clear()

        m.addAction(
            "&Open",
            lambda: self.open(),
            QKeySequence.Open)

        recentMenu = m.addMenu("Open &Recent")
        for historic in globals.getRepoHistory():
            recentMenu.addAction(
                F"{globals.getRepoNickname(historic)} [{compactPath(historic)}]",
                lambda h=historic: self.setRepo(h))

        m.addSeparator()

        m.addAction(
            "&Quit",
            lambda: self.close(),
            QKeySequence.Quit)

    def createRepoMenu(self, m: QMenu):
        m.clear()
        m.addAction("Rename...", lambda: self.renameRepo())

    def renameRepo(self):
        text, ok = QInputDialog().getText(
            self,
            "Rename repo", "Enter new nickname for repo:",
            QLineEdit.Normal,
            globals.getRepoNickname(self.state.dir)
        )
        if ok:
            globals.setRepoNickname(self.state.dir, text)

    def unready(self):
        return LockContext(self)

    def setRepo(self, gitRepoDirPath):
        with self.unready():
            shortname = globals.getRepoNickname(gitRepoDirPath)
            progress = QProgressDialog("Opening repository...", "Abort", 0, 0, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setWindowTitle(shortname)
            progress.setWindowFlags(Qt.Dialog | Qt.Popup)
            progress.setMinimumWidth(2 * progress.fontMetrics().width("000,000,000 commits loaded."))
            QCoreApplication.processEvents()
            progress.show()
            QCoreApplication.processEvents()
            #import time; time.sleep(3)

            oldState = self.state
            self.state = None  # for isReady
            try:
                self.state = RepoState(gitRepoDirPath)
                globals.addRepoToHistory(gitRepoDirPath)
                self.graphView.fill(self.state.repo, progress)
                self.setWindowTitle(F"{shortname} [{self.state.repo.active_branch}] — {globals.PROGRAM_NAME}")
                progress.close()
            except git.exc.InvalidGitRepositoryError as e:
                progress.close()
                self.state = oldState
                print(traceback.format_exc())
                QMessageBox.warning(
                    self,
                    "Invalid repository",
                    F"This directory does not appear to be a valid git repository:\n{gitRepoDirPath}")
            except BaseException as e:
                progress.close()
                self.state = oldState
                print(traceback.format_exc())
                QMessageBox.critical(
                    self,
                    "Error",
                    "Exception thrown while opening repository:\n\n" + str(e))

    def isReady(self):
        return self.ready and self.state != None

    def open(self):
        path = QFileDialog.getExistingDirectory(self, "Open repository", globals.appSettings.value(globals.SK_LAST_OPEN, "", type=str))
        if path:
            globals.appSettings.setValue(globals.SK_LAST_OPEN, path)
            self.setRepo(path)

    def closeEvent(self, e):
        # Write window size and position to config file
        globals.appSettings.setValue("MainWindow/size", self.size())
        globals.appSettings.setValue("MainWindow/position", self.pos())
        for sts in self.splittersToSave:
            globals.appSettings.setValue(F"Splitters/{sts.objectName()}", sts.saveState())
        e.accept()

    def fillStageView(self):
        with self.unready():
            self.dirtyView.clear()
            self.dirtyView.fillDiff(self.state.index.diff(None))
            self.dirtyView.fillUntracked(self.state.repo.untracked_files)
            self.stageView.clear()
            self.stageView.fillDiff(self.state.index.diff(self.state.repo.head.commit, R=True)) # R: prevent reversal

            nDirty = self.dirtyView.model().rowCount()
            nStaged = self.stageView.model().rowCount()
            self.dirtyLabel.setText(F"{nDirty} dirty file{splural(nDirty)}:")
            self.stageLabel.setText(F"{nStaged} file{splural(nStaged)} staged for commit:")

            self.filesStack.setCurrentIndex(1)

    def commitFlow(self):
        kDRAFT = "DraftMessage"
        dlg = QInputDialog(self)
        dlg.setInputMode(QInputDialog.TextInput)
        dlg.setWindowTitle("Commit")
        # absurdly-long label text because setMinimumWidth has no effect - we should probably make a custom dialog instead
        dlg.setLabelText("Enter commit message:                                                      ")
        dlg.setTextValue(self.state.settings.value(kDRAFT, ""))
        dlg.setOkButtonText("Commit")
        rc = dlg.exec_()
        message = dlg.textValue()
        if rc == QDialog.DialogCode.Accepted:
            self.state.settings.remove(kDRAFT)
            self.state.index.commit(message)
        else:
            self.state.settings.setValue(kDRAFT, message)


class LockContext(object):
    w: MainWindow

    def __init__(self, w):
        self.w = w

    def __enter__(self):
        self.w.ready = False

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.w.ready = True
