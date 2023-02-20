from gitfourchette import porcelain
from gitfourchette import settings
from gitfourchette import tasks
from gitfourchette.benchmark import Benchmark
from gitfourchette.filewatcher import FileWatcher
from gitfourchette.globalstatus import globalstatus
from gitfourchette.navhistory import NavHistory, NavPos
from gitfourchette.qt import *
from gitfourchette.repostate import RepoState
from gitfourchette.stagingstate import StagingState
from gitfourchette.trash import Trash
from gitfourchette.util import (excMessageBox, excStrings, QSignalBlockerContext, shortHash,
                                showWarning, showInformation, askConfirmation, stockIcon)
from gitfourchette.widgets.brandeddialog import showTextInputDialog
from gitfourchette.widgets.conflictview import ConflictView
from gitfourchette.widgets.diffmodel import DiffModel, DiffModelError, DiffConflict, DiffImagePair, ShouldDisplayPatchAsImageDiff
from gitfourchette.widgets.diffview import DiffView
from gitfourchette.widgets.filelist import FileList, DirtyFiles, StagedFiles, CommittedFiles, FileListModel
from gitfourchette.widgets.graphview import GraphView
from gitfourchette.widgets.pushdialog import PushDialog
from gitfourchette.widgets.qelidedlabel import QElidedLabel
from gitfourchette.widgets.remotelinkprogressdialog import RemoteLinkProgressDialog
from gitfourchette.widgets.richdiffview import RichDiffView
from gitfourchette.widgets.sidebar import Sidebar
from gitfourchette.workqueue import WorkQueue
from html import escape
import os
import pygit2
import typing


def sanitizeSearchTerm(x):
    if not x:
        return None
    return x.strip().lower()


class RepoWidget(QWidget):
    nameChange: Signal = Signal()

    state: RepoState
    pathPending: str | None  # path of the repository if it isn't loaded yet (state=None)

    previouslySearchedTerm: str
    previouslySearchedTermInDiff: str

    navPos: NavPos
    navHistory: NavHistory

    scheduledRefresh: QTimer

    @property
    def repo(self) -> pygit2.Repository:
        return self.state.repo

    @property
    def isLoaded(self):
        return self.state is not None

    @property
    def workdir(self):
        if self.state:
            return os.path.normpath(self.state.repo.workdir)
        else:
            return self.pathPending

    @property
    def fileWatcher(self) -> FileWatcher:
        return self.state.fileWatcher

    def __init__(self, parent, sharedSplitterStates=None):
        super().__init__(parent)

        # Use workQueue to schedule operations on the repository
        # to run on a thread separate from the UI thread.
        self.workQueue = WorkQueue(self, maxThreadCount=1)   # TODO: Get rid of this, ultimately
        self.repoTaskRunner = tasks.RepoTaskRunner(self)
        self.repoTaskRunner.refreshPostTask.connect(self.refreshPostTask)

        self.state = None
        self.pathPending = None

        self.scheduledRefresh = QTimer(self)
        self.scheduledRefresh.setSingleShot(True)
        self.scheduledRefresh.setInterval(1000)
        self.scheduledRefresh.timeout.connect(self.quickRefresh)

        self.navPos = NavPos()
        self.navHistory = NavHistory()

        self.sidebar = Sidebar(self)
        self.graphView = GraphView(self)
        self.filesStack = QStackedWidget()
        self.diffStack = QStackedWidget()
        self.committedFiles = CommittedFiles(self)
        self.dirtyFiles = DirtyFiles(self)
        self.stagedFiles = StagedFiles(self)
        self.diffView = DiffView(self)
        self.richDiffView = RichDiffView(self)
        self.conflictView = ConflictView(self)

        # The staged files and unstaged files view are mutually exclusive.
        self.stagedFiles.entryClicked.connect(self.dirtyFiles.clearSelectionSilently)
        self.dirtyFiles.entryClicked.connect(self.stagedFiles.clearSelectionSilently)

        # Refresh file list views after applying a patch from the diff view (partial line patch)
        self.diffView.patchApplied.connect(lambda: self.refreshWorkdirViewAsync(allowUpdateIndex=True))
        # Note that refreshing the file list views may, in turn, re-select a file from the appropriate file view,
        # which will trigger the diff view to be refreshed as well.

        for v in [self.dirtyFiles, self.stagedFiles, self.committedFiles]:
            v.nothingClicked.connect(self.diffView.clear)
            v.entryClicked.connect(self.loadPatchAsync)

        self.conflictView.hardSolve.connect(lambda path, oid: self.hardSolveConflictAsync(path, oid))
        self.conflictView.markSolved.connect(lambda path: self.markConflictSolvedAsync(path))
        self.conflictView.openFile.connect(lambda path: self.openConflictFile(path))

        self.graphView.emptyClicked.connect(self.setNoCommitSelected)
        self.graphView.commitClicked.connect(self.loadCommitAsync)
        self.graphView.uncommittedChangesClicked.connect(self.refreshWorkdirViewAsync)

        self.sidebar.commitClicked.connect(self.graphView.selectCommit)
        self.sidebar.pushBranch.connect(self.startPushFlow)
        self.sidebar.refClicked.connect(self.selectRef)
        self.sidebar.uncommittedChangesClicked.connect(self.graphView.selectUncommittedChanges)
        self.sidebar.toggleHideBranch.connect(self.toggleHideBranch)
        self.sidebar.openSubmoduleRepo.connect(self.openSubmoduleRepo)
        self.sidebar.openSubmoduleFolder.connect(self.openSubmoduleFolder)

        # ----------------------------------

        self.splitterStates = sharedSplitterStates or {}

        self.dirtyLabel = QElidedLabel(self.tr("Loading dirty files..."))
        self.stageLabel = QElidedLabel(self.tr("Loading staged files..."))

        self.previouslySearchedTerm = None
        self.previouslySearchedTermInDiff = None

        dirtyContainer = QWidget()
        dirtyContainer.setLayout(QVBoxLayout())
        dirtyContainer.layout().setContentsMargins(0, 0, 0, 0)
        dirtyContainer.layout().addWidget(self.dirtyLabel)
        dirtyContainer.layout().addWidget(self.dirtyFiles)
        stageContainer = QWidget()
        stageContainer.setLayout(QVBoxLayout())
        stageContainer.layout().setContentsMargins(0, 0, 0, 0)
        stageContainer.layout().addWidget(self.stageLabel)
        stageContainer.layout().addWidget(self.stagedFiles)
        commitButtonsContainer = QWidget()
        commitButtonsContainer.setLayout(QHBoxLayout())
        commitButtonsContainer.layout().setContentsMargins(0, 0, 0, 0)
        stageContainer.layout().addWidget(commitButtonsContainer)
        self.commitButton = QPushButton(self.tr("&Commit"))
        self.amendButton = QPushButton(self.tr("&Amend"))
        commitButtonsContainer.layout().addWidget(self.commitButton)
        commitButtonsContainer.layout().addWidget(self.amendButton)
        self.stageSplitter = QSplitter(Qt.Orientation.Vertical)
        self.stageSplitter.addWidget(dirtyContainer)
        self.stageSplitter.addWidget(stageContainer)

        self.filesStack.addWidget(self.committedFiles)
        self.filesStack.addWidget(self.stageSplitter)
        self.filesStack.setCurrentWidget(self.committedFiles)

        self.diffStack.addWidget(self.diffView)
        self.diffStack.addWidget(self.richDiffView)
        self.diffStack.addWidget(self.conflictView)
        self.diffStack.setCurrentWidget(self.diffView)

        bottomSplitter = QSplitter(Qt.Orientation.Horizontal)
        bottomSplitter.addWidget(self.filesStack)
        bottomSplitter.addWidget(self.diffStack)
        bottomSplitter.setSizes([100, 300])

        mainSplitter = QSplitter(Qt.Orientation.Vertical)
        mainSplitter.addWidget(self.graphView)
        mainSplitter.addWidget(bottomSplitter)
        mainSplitter.setSizes([100, 150])

        sideSplitter = QSplitter(Qt.Orientation.Horizontal)
        sideSplitter.addWidget(self.sidebar)
        sideSplitter.addWidget(mainSplitter)
        sideSplitter.setSizes([100, 500])
        sideSplitter.setStretchFactor(0, 0)  # don't auto-stretch sidebar when resizing window
        sideSplitter.setStretchFactor(1, 1)

        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(sideSplitter)

        # object names are required for state saving to work
        mainSplitter.setObjectName("MainSplitter")
        bottomSplitter.setObjectName("BottomSplitter")
        self.stageSplitter.setObjectName("StageSplitter")
        sideSplitter.setObjectName("SideSplitter")
        self.splittersToSave = [mainSplitter, bottomSplitter, self.stageSplitter, sideSplitter]
        # save splitter state in splitterMoved signal
        for splitter in self.splittersToSave:
            splitter.splitterMoved.connect(lambda pos, index, splitter=splitter: self.saveSplitterState(splitter))

        # remove frames for a cleaner look
        #for w in self.graphView, self.diffView, self.dirtyView, self.stageView, self.changedFilesView, self.sidebar:
        #    w.setFrameStyle(QFrame.Shape.NoFrame)
        self.sidebar.setFrameStyle(QFrame.Shape.NoFrame)

        # ----------------------------------
        # Connect signals to async tasks

        self.connectTask(self.amendButton.clicked,              tasks.AmendCommit, argc=0)
        self.connectTask(self.commitButton.clicked,             tasks.NewCommit, argc=0)
        self.connectTask(self.dirtyFiles.discardFiles,          tasks.DiscardFiles)
        self.connectTask(self.dirtyFiles.stageFiles,            tasks.StageFiles)
        self.connectTask(self.graphView.checkoutCommit,         tasks.CheckoutCommit)
        self.connectTask(self.graphView.newBranchFromCommit,    tasks.NewBranchFromCommit)
        self.connectTask(self.graphView.resetHead,              tasks.ResetHead)
        self.connectTask(self.graphView.revertCommit,           tasks.RevertCommit)
        self.connectTask(self.sidebar.applyStash,               tasks.ApplyStash)
        self.connectTask(self.sidebar.commit,                   tasks.NewCommit)
        self.connectTask(self.sidebar.deleteBranch,             tasks.DeleteBranch)
        self.connectTask(self.sidebar.deleteRemote,             tasks.DeleteRemote)
        self.connectTask(self.sidebar.deleteRemoteBranch,       tasks.DeleteRemoteBranch)
        self.connectTask(self.sidebar.dropStash,                tasks.DropStash)
        self.connectTask(self.sidebar.editRemote,               tasks.EditRemote)
        self.connectTask(self.sidebar.editTrackingBranch,       tasks.EditTrackedBranch)
        self.connectTask(self.sidebar.fetchRemote,              tasks.FetchRemote)
        self.connectTask(self.sidebar.fetchRemoteBranch,        tasks.FetchRemoteBranch)
        self.connectTask(self.sidebar.newBranch,                tasks.NewBranch)
        self.connectTask(self.sidebar.newBranchFromLocalBranch, tasks.NewBranchFromLocalBranch)
        self.connectTask(self.sidebar.newRemote,                tasks.NewRemote)
        self.connectTask(self.sidebar.newStash,                 tasks.NewStash)
        self.connectTask(self.sidebar.newTrackingBranch,        tasks.NewTrackingBranch)
        self.connectTask(self.sidebar.popStash,                 tasks.PopStash)
        self.connectTask(self.sidebar.pullBranch,               tasks.PullBranch)
        self.connectTask(self.sidebar.renameBranch,             tasks.RenameBranch)
        self.connectTask(self.sidebar.renameRemoteBranch,       tasks.RenameRemoteBranch)
        self.connectTask(self.sidebar.switchToBranch,           tasks.SwitchBranch)
        self.connectTask(self.stagedFiles.unstageFiles,         tasks.UnstageFiles)

    # -------------------------------------------------------------------------

    def runTask(self, taskClass: typing.Type[tasks.RepoTask], *args):
        task = taskClass(self, *args)
        self.repoTaskRunner.put(task)
        return task

    def connectTask(self, signal: Signal, taskClass: typing.Type[tasks.RepoTask], argc: int = -1):
        def createTask(*args):
            if argc >= 0:
                args = args[:argc]
            return self.runTask(taskClass, *args)
        signal.connect(createTask)

    def setRepoState(self, state: RepoState):
        if state:
            self.state = state
            self.state.fileWatcher.setParent(self)
            self.state.fileWatcher.directoryChanged.connect(self.onDirectoryChange)
            self.state.fileWatcher.indexChanged.connect(self.onIndexChange)
        else:
            self.state = None

    def installFileWatcher(self, intervalMS=100):
        self.state.fileWatcher.boot(intervalMS)
        self.scheduledRefresh.setInterval(intervalMS)

    def stopFileWatcher(self):
        self.state.fileWatcher.shutdown()

    def onDirectoryChange(self):
        globalstatus.setText(self.tr("Detected external change..."))

        if self.scheduledRefresh.interval() == 0:
            # Just fire it now if instantaneous
            # TODO: Do we need this one? There's already a delay in FSW
            self.scheduledRefresh.timeout.emit()
        else:
            self.scheduledRefresh.stop()
            self.scheduledRefresh.start()

    def onIndexChange(self):
        if self.isStageViewShown:
            self.quickRefresh()

    # -------------------------------------------------------------------------

    def saveSplitterState(self, splitter: QSplitter):
        self.splitterStates[splitter.objectName()] = splitter.saveState()

    def restoreSplitterStates(self):
        for splitter in self.splittersToSave:
            try:
                splitter.restoreState(self.splitterStates[splitter.objectName()])
            except KeyError:
                pass

    # -------------------------------------------------------------------------

    def saveFilePositions(self):
        if self.diffStack.currentWidget() == self.diffView:
            self.navPos.diffScroll = self.diffView.verticalScrollBar().value()
            self.navPos.diffCursor = self.diffView.textCursor().position()
        else:
            self.navPos.diffScroll = 0
            self.navPos.diffCursor = 0
        self.navHistory.push(self.navPos)

    def restoreSelectedFile(self):
        pos = self.navPos

        if not pos or not pos.context:
            return False

        if pos.context in ["UNSTAGED", "UNTRACKED"]:
            fl = self.dirtyFiles
        elif pos.context == "STAGED":
            fl = self.stagedFiles
        else:
            assert len(pos.context) == 40, "expecting an OID here"
            fl = self.committedFiles

        return fl.selectFile(pos.file)

    def restoreDiffPosition(self):
        cursorPosition = self.navPos.diffCursor
        scrollPosition = self.navPos.diffScroll

        newTextCursor = QTextCursor(self.diffView.textCursor())
        newTextCursor.setPosition(cursorPosition)
        self.diffView.setTextCursor(newTextCursor)

        self.diffView.verticalScrollBar().setValue(scrollPosition)

    def navigateTo(self, pos: NavPos):
        if not pos or not pos.context:
            QApplication.beep()
            return False

        self.navPos = pos

        self.navHistory.setRecent(pos)

        self.navHistory.lock()

        if self.navPos.context in ["UNSTAGED", "STAGED", "UNTRACKED"]:
            if self.graphView.currentCommitOid is not None:
                self.graphView.selectUncommittedChanges()
                success = True
            else:
                success = self.restoreSelectedFile()
                self.navHistory.unlock()
        else:
            oid = pygit2.Oid(hex=self.navPos.context)
            if self.graphView.currentCommitOid != oid:
                success = self.graphView.selectCommit(oid)
            else:
                success = self.restoreSelectedFile()
                self.navHistory.unlock()

        return success

    def navigateBack(self):
        if self.navHistory.isAtTopOfStack:
            self.saveFilePositions()

        startPos = self.navPos.copy()

        while not self.navHistory.isAtBottomOfStack:
            pos = self.navHistory.navigateBack()
            success = self.navigateTo(pos)

            if success and pos != startPos:
                break

    def navigateForward(self):
        startPos = self.navPos.copy()

        while not self.navHistory.isAtTopOfStack:
            pos = self.navHistory.navigateForward()
            success = self.navigateTo(pos)

            if success and pos != startPos:
                break

    # -------------------------------------------------------------------------

    def selectNextFile(self, down=True):
        if self.filesStack.currentWidget() == self.committedFiles:
            widgets = [self.committedFiles]
        elif self.filesStack.currentWidget() == self.stageSplitter:
            widgets = [self.dirtyFiles, self.stagedFiles]
        else:
            return

        numWidgets = len(widgets)
        selections = [w.selectedIndexes() for w in widgets]
        lengths = [w.model().rowCount() for w in widgets]

        # find widget to start from: topmost widget that has any selection
        leader = -1
        for i, selection in enumerate(selections):
            if selection:
                leader = i
                break

        if leader < 0:
            # selection empty; pick first non-empty widget as leader
            leader = 0
            row = 0
            while (leader < numWidgets) and (lengths[leader] == 0):
                leader += 1
        else:
            # get selected row in leader widget - TODO: this may not be accurate when multiple rows are selected
            row = selections[leader][-1].row()

            if down:
                row += 1
                while (leader < numWidgets) and (row >= lengths[leader]):
                    # out of rows in leader widget; jump to first row in next widget
                    leader += 1
                    row = 0
            else:
                row -= 1
                while (leader >= 0) and (row < 0):
                    # out of rows in leader widget; jump to last row in prev widget
                    leader -= 1
                    if leader >= 0:
                        row = lengths[leader] - 1

        # if we have a new valid selection, apply it, otherwise bail
        if 0 <= leader < numWidgets and 0 <= row < lengths[leader]:
            widgets[leader].setFocus()
            widgets[leader].clearSelectionSilently()
            widgets[leader].selectRow(row)
        else:
            QApplication.beep()

    # -------------------------------------------------------------------------

    def getTitle(self):
        if self.state:
            return self.state.shortName
        elif self.pathPending:
            return F"({settings.history.getRepoNickname(self.pathPending)})"
        else:
            return "???"

    def closeEvent(self, event: QCloseEvent):
        """ Called when closing a repo tab """
        self.cleanup()

    def cleanup(self):
        if self.state and self.state.repo:
            self.committedFiles.clear()
            self.dirtyFiles.clear()
            self.stagedFiles.clear()
            self.graphView.clear()
            self.clearDiffView()
            # Save path if we want to reload the repo later
            self.pathPending = os.path.normpath(self.state.repo.workdir)
            self.state.repo.free()
        if self.state and self.state.fileWatcher:
            self.state.fileWatcher.shutdown()
        self.setRepoState(None)

    def clearDiffView(self):
        self.diffView.clear()
        self.diffStack.setCurrentWidget(self.diffView)

    def setPendingWorkdir(self, path):
        self.pathPending = os.path.normpath(path)

    def renameRepo(self):
        def onAccept(newName):
            settings.history.setRepoNickname(self.workdir, newName)
            settings.history.write()
            self.nameChange.emit()
        showTextInputDialog(
            self,
            self.tr("Edit repo nickname"),
            self.tr("Enter new nickname for repo, or enter blank line to reset:"),
            settings.history.getRepoNickname(self.workdir),
            onAccept,
            okButtonText=self.tr("Rename", "edit repo nickname"))

    def setNoCommitSelected(self):
        self.saveFilePositions()
        self.navPos = NavPos()

        self.filesStack.setCurrentWidget(self.stageSplitter)
        self.committedFiles.clear()

        self.clearDiffView()

    def refreshWorkdirViewAsync(self, forceSelectFile: NavPos = None, allowUpdateIndex: bool = False):
        rw = self

        class RefreshWorkdir(tasks.RepoTask):
            def name(self):
                return translate("Operation", "Refresh working directory")

            def execute(self):
                porcelain.refreshIndex(self.repo)
                self.dirtyDiff = porcelain.diffWorkdirToIndex(self.repo, allowUpdateIndex)
                self.stageDiff = porcelain.diffIndexToHead(self.repo)

            def postExecute(self, success: bool):
                if success:
                    rw._fillWorkdirView(self.dirtyDiff, self.stageDiff, forceSelectFile)

        return self.runTask(RefreshWorkdir)

    def _fillWorkdirView(self, dirtyDiff: pygit2.Diff, stageDiff: pygit2.Diff, forceSelectFile: NavPos):
        """Fill Staged/Unstaged views with uncommitted changes"""

        stagedESR = self.stagedFiles.earliestSelectedRow()
        dirtyESR = self.dirtyFiles.earliestSelectedRow()
        self.saveFilePositions()

        # Reset dirty & stage views. Block their signals as we refill them to prevent updating the diff view.
        with QSignalBlockerContext(self.dirtyFiles), QSignalBlockerContext(self.stagedFiles):
            self.dirtyFiles.clear()
            self.stagedFiles.clear()
            self.dirtyFiles.setContents([dirtyDiff])
            self.stagedFiles.setContents([stageDiff])

        nDirty = self.dirtyFiles.model().rowCount()
        nStaged = self.stagedFiles.model().rowCount()
        self.dirtyLabel.setText(self.tr("%n dirty file(s):", "", nDirty))
        self.stageLabel.setText(self.tr("%n file(s) staged for commit:", "", nStaged))

        # Switch to correct card in filesStack to show dirtyView and stageView
        self.filesStack.setCurrentWidget(self.stageSplitter)

        if forceSelectFile:  # for Revert Hunk from DiffView
            self.navPos = forceSelectFile

        # After patchApplied.emit has caused a refresh of the dirty/staged file views,
        # restore selected row in appropriate file list view so the user can keep hitting
        # enter (del) to stage (unstage) a series of files.
        if not self.restoreSelectedFile():
            if stagedESR >= 0:
                self.stagedFiles.selectRow(min(stagedESR, self.stagedFiles.model().rowCount()-1))
            elif dirtyESR >= 0:
                self.dirtyFiles.selectRow(min(dirtyESR, self.dirtyFiles.model().rowCount()-1))

        # If no file is selected in either FileListView, clear the diffView of any residual diff.
        if 0 == (len(self.dirtyFiles.selectedIndexes()) + len(self.stagedFiles.selectedIndexes())):
            self.clearDiffView()

        self.navHistory.unlock()

    def loadCommitAsync(self, oid: pygit2.Oid):
        task = tasks.LoadCommit(self, oid)
        task.success.connect(lambda: self._loadCommit(oid, task.diffs))
        self.repoTaskRunner.put(task)

    def _loadCommit(self, oid: pygit2.Oid, parentDiffs: list[pygit2.Diff]):
        """Load commit details into Changed Files view"""

        self.saveFilePositions()

        # Reset committed files view.
        # Block its signals as we refill it to prevent updating the diff.
        with QSignalBlockerContext(self.committedFiles):
            self.committedFiles.clear()
            self.committedFiles.setCommit(oid)
            self.committedFiles.setContents(parentDiffs)

        self.navPos = self.navHistory.findContext(oid.hex)
        if not self.navPos:
            self.navPos = NavPos(context=oid.hex, file=self.committedFiles.getFirstPath())

        # Show message if commit is empty
        if self.committedFiles.flModel.rowCount() == 0:
            self.diffStack.setCurrentWidget(self.richDiffView)
            self.richDiffView.displayDiffModelError(DiffModelError(self.tr("Empty commit.")))

        # Switch to correct card in filesStack to show changedFilesView
        self.filesStack.setCurrentWidget(self.committedFiles)

        # Select the best file in this commit - which may trigger loadPatchAsync
        self.restoreSelectedFile()

    def loadPatchAsync(self, patch: pygit2.Patch, stagingState: StagingState):
        task = tasks.LoadPatch(self, patch, stagingState)
        task.success.connect(lambda: self._loadPatch(patch, stagingState, task.result))
        self.repoTaskRunner.put(task)

    def _loadPatch(self, patch, stagingState, result):
        """Load a file diff into the Diff View"""

        self.saveFilePositions()

        if stagingState == StagingState.COMMITTED:
            assert len(self.navPos.context) == 40
            posContext = self.navPos.context
        else:
            posContext = stagingState.name
        posFile = patch.delta.new_file.path
        self.navPos = self.navHistory.findFileInContext(posContext, posFile)
        if not self.navPos:
            self.navPos = NavPos(posContext, posFile)

        if type(result) == DiffConflict:
            self.diffStack.setCurrentWidget(self.conflictView)
            self.conflictView.displayConflict(result)
        elif type(result) == DiffModelError:
            self.diffStack.setCurrentWidget(self.richDiffView)
            self.richDiffView.displayDiffModelError(result)
        elif type(result) == DiffModel:
            self.diffStack.setCurrentWidget(self.diffView)
            self.diffView.replaceDocument(self.repo, patch, stagingState, result)
            self.restoreDiffPosition()  # restore position after we've replaced the document
        elif type(result) == DiffImagePair:
            self.diffStack.setCurrentWidget(self.richDiffView)
            self.richDiffView.displayImageDiff(patch.delta, result.oldImage, result.newImage)
        else:
            self.diffStack.setCurrentWidget(self.richDiffView)
            self.richDiffView.displayDiffModelError(DiffModelError(
                self.tr("Can’t display diff of type {0}.").format(escape(str(type(result)))),
                icon=QStyle.StandardPixmap.SP_MessageBoxCritical))

    def startPushFlow(self, branchName: str = ""):
        pushDialog = PushDialog.startPushFlow(self, self.repo, branchName)
        pushDialog.pushSuccessful.connect(self.quickRefreshWithSidebar)

    def openSubmoduleRepo(self, submoduleKey: str):
        path = porcelain.getSubmoduleWorkdir(self.repo, submoduleKey)
        self.window().openRepo(path)
        self.window().saveSession()

    def openSubmoduleFolder(self, submoduleKey: str):
        path = porcelain.getSubmoduleWorkdir(self.repo, submoduleKey)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # -------------------------------------------------------------------------
    # Conflicts

    def hardSolveConflictAsync(self, path: str, keepOid: pygit2.Oid):
        repo = self.repo

        def work():
            porcelain.refreshIndex(repo)
            assert (repo.index.conflicts is not None) and (path in repo.index.conflicts)

            trash = Trash(repo)
            trash.backupFile(path)

            # TODO: we should probably set the modes correctly and stuff as well
            blob: pygit2.Blob = repo[keepOid].peel(pygit2.Blob)
            with open(os.path.join(repo.workdir, path), "wb") as f:
                f.write(blob.data)

            del repo.index.conflicts[path]
            assert (repo.index.conflicts is None) or (path not in repo.index.conflicts)
            repo.index.write()

        def then(_):
            self.quickRefreshWithSidebar()

        opName = translate("Operation", "Hard solve conflict")
        self.workQueue.put(work, then, opName)

    def markConflictSolvedAsync(self, path: str):
        repo = self.repo

        def work():
            porcelain.refreshIndex(repo)
            assert (repo.index.conflicts is not None) and (path in repo.index.conflicts)

            del repo.index.conflicts[path]
            assert (repo.index.conflicts is None) or (path not in repo.index.conflicts)
            repo.index.write()

        def then(_):
            self.quickRefreshWithSidebar()

        opName = translate("Operation", "Mark conflict solved")
        self.workQueue.put(work, then, opName)

    def openConflictFile(self, path: str):
        fullPath = os.path.join(self.repo.workdir, path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(fullPath))

    # -------------------------------------------------------------------------
    # Find, find next

    def _search(self, searchRange):
        message = self.previouslySearchedTerm
        message = sanitizeSearchTerm(message)
        if not message:
            showWarning(self, self.tr("Find Commit"), self.tr("Invalid search term."))
            return

        likelyHash = False
        if len(message) <= 40:
            try:
                int(message, 16)
                likelyHash = True
            except ValueError:
                pass

        model = self.graphView.model()

        for i in searchRange:
            modelIndex = model.index(i, 0)
            meta = model.data(modelIndex)
            if meta is None:
                continue
            if (message in meta.message.lower()) or (likelyHash and message in meta.oid.hex):
                self.graphView.setCurrentIndex(modelIndex)
                return

        showInformation(self, self.tr("Find Commit"), self.tr("No more occurrences of “{0}”.").format(escape(message)))

    def findFlow(self):
        def onAccept(verbatimTerm):
            self.previouslySearchedTerm = verbatimTerm
            self._search(range(0, self.graphView.model().rowCount()))
        showTextInputDialog(
            self,
            self.tr("Find Commit"),
            self.tr("Search for partial commit hash or message:"),
            self.previouslySearchedTerm,
            onAccept)

    def _findNextOrPrevious(self, findNext):
        if not sanitizeSearchTerm(self.previouslySearchedTerm):
            showWarning(self, self.tr("Find Commit"), self.tr("Please use “Find” to specify a search term before using “Find Next” or “Find Previous”."))
            return
        if len(self.graphView.selectedIndexes()) == 0:
            showWarning(self, self.tr("Find Commit"), self.tr("Please select a commit from whence to resume the search."))
            return
        start = self.graphView.currentIndex().row()
        if findNext:
            self._search(range(1 + start, self.graphView.model().rowCount()))
        else:
            self._search(range(start - 1, -1, -1))

    def findNext(self):
        self._findNextOrPrevious(True)

    def findPrevious(self):
        self._findNextOrPrevious(False)

    # -------------------------------------------------------------------------
    # Find in diff, find next in diff

    def _searchDiff(self, forward=True):
        message = self.previouslySearchedTermInDiff
        message = sanitizeSearchTerm(message)
        if not message:
            showWarning(self, self.tr("Find in Diff"), self.tr("Invalid search term."))
            return

        doc: QTextDocument = self.diffView.document()
        newCursor = doc.find(message, self.diffView.textCursor())
        if newCursor:
            self.diffView.setTextCursor(newCursor)
            return

        showInformation(self, self.tr("Find in Diff"), self.tr("No more occurrences of “{0}”.").format(escape(message)))

    def findInDiffFlow(self):
        def onAccept(verbatimTerm):
            self.previouslySearchedTermInDiff = verbatimTerm
            self._searchDiff()
        showTextInputDialog(
            self,
            self.tr("Find in Diff"),
            self.tr("Search for text in current diff:"),
            self.previouslySearchedTermInDiff,
            onAccept)

    def _findInDiffNextOrPrevious(self, findNext):
        if not sanitizeSearchTerm(self.previouslySearchedTermInDiff):
            showWarning(
                self,
                self.tr("Find in Diff"),
                self.tr("Please use “Find in Diff” to specify a search term before using “Find Next” or “Find Previous”."))
            return
        self._searchInDiff(findNext)

    def findInDiffNext(self):
        self._findNextOrPrevious(True)

    def findInDiffPrevious(self):
        self._findNextOrPrevious(False)

    # -------------------------------------------------------------------------

    def toggleHideBranch(self, branchName: str):
        assert branchName.startswith("refs/")
        self.state.toggleHideBranch(branchName)
        self.graphView.setHiddenCommits(self.state.hiddenCommits)

    # -------------------------------------------------------------------------

    @property
    def isStageViewShown(self):
        return self.filesStack.currentWidget() == self.stageSplitter

    def quickRefresh(self):
        self.scheduledRefresh.stop()

        with Benchmark("Refresh refs-by-commit cache"):
            self.state.refreshRefsByCommitCache()

        with Benchmark("Load tainted commits only"):
            nRemovedRows, nAddedRows = self.state.loadTaintedCommitsOnly()

        with Benchmark(F"Refresh top of graphview ({nRemovedRows} removed, {nAddedRows} added)"):
            # Hidden commits may have changed in RepoState.loadTaintedCommitsOnly!
            # If new commits are part of a hidden branch, we've got to invalidate the CommitFilter.
            self.graphView.setHiddenCommits(self.state.hiddenCommits)

            if nRemovedRows >= 0:
                self.graphView.refreshTopOfCommitSequence(nRemovedRows, nAddedRows, self.state.commitSequence)
            else:
                self.graphView.setCommitSequence(self.state.commitSequence)

        if self.isStageViewShown:
            self.refreshWorkdirViewAsync()
        globalstatus.clearProgress()

        self.refreshWindowTitle()

    def quickRefreshWithSidebar(self):
        self.quickRefresh()
        self.sidebar.refresh(self.state)

    def refreshWindowTitle(self):
        shortname = self.state.shortName
        repo = self.repo
        inBrackets = ""
        if repo.head_is_unborn:
            inBrackets = self.tr("unborn HEAD")
        elif repo.is_empty:  # getActiveBranchShorthand won't work on an empty repo
            inBrackets = self.tr("repo is empty")
        elif repo.head_is_detached:
            oid = porcelain.getHeadCommitOid(repo)
            inBrackets = self.tr("detached HEAD @ {0}").format(shortHash(oid))
        else:
            inBrackets = porcelain.getActiveBranchShorthand(repo)

        suffix = QApplication.applicationDisplayName()
        if settings.prefs.debug_showPID:
            suffix += F" (PID {os.getpid()}, {qtBindingName})"

        self.window().setWindowTitle(F"{shortname} [{inBrackets}] — {suffix}")

    # -------------------------------------------------------------------------

    def selectRef(self, refName: str):
        oid = porcelain.getCommitOidFromReferenceName(self.repo, refName)
        self.graphView.selectCommit(oid)

    """
    def selectTag(self, tagName: str):
        oid = porcelain.getCommitOidFromTagName(self.repo, tagName)
        self.selectCommit(oid)
    """

    # -------------------------------------------------------------------------

    def openRescueFolder(self):
        trash = Trash(self.repo)
        if trash.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(trash.trashDir))
        else:
            showInformation(
                self,
                self.tr("Open Rescue Folder"),
                self.tr("There’s no rescue folder for this repository. Perhaps you haven’t discarded a change with {0} yet.").format(QApplication.applicationDisplayName()))

    def clearRescueFolder(self):
        trash = Trash(self.repo)
        sizeOnDisk, patchCount = trash.getSize()

        if patchCount <= 0:
            showInformation(
                self,
                self.tr("Clear Rescue Folder"),
                self.tr("There are no discarded changes to delete."))
            return

        humanSize = self.locale().formattedDataSize(sizeOnDisk)

        askPrompt = (
            self.tr("Do you want to permanently delete <b>%n</b> discarded patch(es)?", "", patchCount) + "<br>" +
            self.tr("This will free up {0} on disk.").format(humanSize) + "<br>" +
            translate("Global", "This cannot be undone!"))

        askConfirmation(
            parent=self,
            title=self.tr("Clear rescue folder"),
            text=askPrompt,
            callback=lambda: trash.clear(),
            okButtonText=self.tr("Delete permanently"),
            okButtonIcon=stockIcon(QStyle.StandardPixmap.SP_DialogDiscardButton))

    # -------------------------------------------------------------------------

    def refreshPostTask(self, what: tasks.TaskAffectsWhat):
        if what != tasks.TaskAffectsWhat.NOTHING:
            self.quickRefreshWithSidebar()
