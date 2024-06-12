import logging
import typing
from typing import Literal

from gitfourchette import settings, tasks
from gitfourchette.diffview.diffview import DiffView
from gitfourchette.diffview.specialdiffview import SpecialDiffView
from gitfourchette.filelists.committedfiles import CommittedFiles
from gitfourchette.filelists.dirtyfiles import DirtyFiles
from gitfourchette.filelists.filelist import FileList
from gitfourchette.filelists.stagedfiles import StagedFiles
from gitfourchette.forms.banner import Banner
from gitfourchette.forms.conflictview import ConflictView
from gitfourchette.globalshortcuts import GlobalShortcuts
from gitfourchette.nav import NavContext
from gitfourchette.qt import *
from gitfourchette.tasks import TaskBook
from gitfourchette.toolbox import *

FileStackPage = Literal["workdir", "commit"]
DiffStackPage = Literal["text", "special", "conflict"]

FILEHEADER_HEIGHT = 20

logger = logging.getLogger(__name__)


class DiffArea(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("CommitExplorer")

        fileStack = self._makeFileStack()
        diffContainer = self._makeDiffContainer()

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setObjectName("Split_DiffArea")

        diffBanner = Banner(self, orientation=Qt.Orientation.Horizontal)
        diffBanner.setProperty("class", "diff")
        diffBanner.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(QMargins())
        layout.setSpacing(0)
        layout.addWidget(diffBanner)
        layout.addWidget(splitter, 1)

        splitter.addWidget(fileStack)
        splitter.addWidget(diffContainer)
        splitter.setSizes([100, 300])
        splitter.setStretchFactor(0, 0)  # don't auto-stretch file lists when resizing window
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)

        self.fileStack = fileStack
        self.diffBanner = diffBanner

    # -------------------------------------------------------------------------
    # Constructor helpers

    def _makeFileStack(self):
        dirtyContainer = self._makeDirtyContainer()
        stageContainer = self._makeStageContainer()
        committedFilesContainer = self._makeCommittedFilesContainer()

        stagingSplitter = QSplitter(Qt.Orientation.Vertical, self)
        stagingSplitter.addWidget(dirtyContainer)
        stagingSplitter.addWidget(stageContainer)
        stagingSplitter.setObjectName("Split_Staging")
        stagingSplitter.setChildrenCollapsible(False)

        fileStack = QStackedWidget()
        fileStack.addWidget(stagingSplitter)
        fileStack.addWidget(committedFilesContainer)
        return fileStack

    def _makeDirtyContainer(self):
        header = QElidedLabel(" ")
        header.setObjectName("dirtyHeader")
        header.setToolTip(self.tr("Unstaged files: will not be included in the commit unless you stage them."))
        header.setMinimumHeight(FILEHEADER_HEIGHT)

        dirtyFiles = DirtyFiles(self)

        stageButton = QToolButton()
        stageButton.setObjectName("stageButton")
        stageButton.setText(self.tr("Stage"))
        stageButton.setToolTip(self.tr("Stage selected files"))
        stageButton.setMaximumHeight(FILEHEADER_HEIGHT)
        stageButton.setEnabled(False)
        stageButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        appendShortcutToToolTip(stageButton, GlobalShortcuts.stageHotkeys[0])

        stageMenu = ActionDef.makeQMenu(stageButton, [ActionDef(self.tr("Discard..."), dirtyFiles.discard)])
        stageButton.setMenu(stageMenu)
        stageButton.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

        container = QWidget()
        layout = QGridLayout(container)
        layout.setSpacing(0)  # automatic frameless list views on KDE Plasma 6 Breeze
        layout.setContentsMargins(QMargins())
        layout.addWidget(header,                0, 0)
        layout.addWidget(stageButton,           0, 1)
        layout.addItem(QSpacerItem(1, 1),       1, 0)
        layout.addWidget(dirtyFiles.searchBar,  2, 0, 1, 2)
        layout.addWidget(dirtyFiles,            3, 0, 1, 2)
        layout.setRowStretch(3, 100)

        stageButton.clicked.connect(dirtyFiles.stage)
        dirtyFiles.selectedCountChanged.connect(lambda n: stageButton.setEnabled(n > 0))

        self.dirtyFiles = dirtyFiles
        self.dirtyHeader = header
        self.stageButton = stageButton

        return container

    def _makeStageContainer(self):
        header = QElidedLabel(" ")
        header.setObjectName("stagedHeader")
        header.setToolTip(self.tr("Staged files: will be included in the commit."))
        header.setMinimumHeight(FILEHEADER_HEIGHT)

        stagedFiles = StagedFiles(self)

        unstageButton = QToolButton()
        unstageButton.setObjectName("unstageButton")
        unstageButton.setText(self.tr("Unstage"))
        unstageButton.setToolTip(self.tr("Unstage selected files"))
        unstageButton.setMaximumHeight(FILEHEADER_HEIGHT)
        unstageButton.setEnabled(False)
        unstageButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        appendShortcutToToolTip(unstageButton, GlobalShortcuts.discardHotkeys[0])

        commitButtonsContainer = QWidget()
        commitButtonsLayout = QHBoxLayout(commitButtonsContainer)
        commitButtonsLayout.setContentsMargins(0, 0, 0, 0)
        commitButton = QPushButton(self.tr("Commit"))
        amendButton = QPushButton(self.tr("Amend"))
        commitButtonsLayout.addWidget(commitButton)
        commitButtonsLayout.addWidget(amendButton)

        unifiedCommitButton = QToolButton()
        unifiedCommitButton.setText(self.tr("Commit..."))
        unifiedCommitButtonMenu = ActionDef.makeQMenu(unifiedCommitButton, [TaskBook.action(self, tasks.AmendCommit)])
        unifiedCommitButtonMenu = ActionDef.makeQMenu(unifiedCommitButton, [ActionDef(self.tr("Amend Last Commit...Amend Commit...Amend..."), amendButton.click)])

        def unifiedCommitButtonMenuAboutToShow():
            unifiedCommitButtonMenu.setMinimumWidth(unifiedCommitButton.width())
            unifiedCommitButtonMenu.setMaximumWidth(unifiedCommitButton.width())

        def unifiedCommitButtonMenuAboutToHide():
            unifiedCommitButtonMenu.setMinimumWidth(0)

        unifiedCommitButtonMenu.aboutToShow.connect(unifiedCommitButtonMenuAboutToShow)
        unifiedCommitButtonMenu.aboutToHide.connect(unifiedCommitButtonMenuAboutToHide)
        unifiedCommitButton.setMenu(unifiedCommitButtonMenu)
        unifiedCommitButton.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        unifiedCommitButton.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        commitButtonsStack = QStackedWidget()
        commitButtonsStack.addWidget(commitButtonsContainer)
        commitButtonsStack.addWidget(unifiedCommitButton)

        # QToolButtons are unsightly on macOS
        commitButtonsStack.setCurrentIndex(0 if settings.qtIsNativeMacosStyle() else 1)

        # Connect signals
        unstageButton.clicked.connect(stagedFiles.unstage)
        stagedFiles.selectedCountChanged.connect(lambda n: unstageButton.setEnabled(n > 0))

        # Lay out container
        container = QWidget()
        layout = QGridLayout(container)
        layout.setContentsMargins(QMargins())
        layout.setSpacing(0)  # automatic frameless list views on KDE Plasma 6 Breeze
        layout.addWidget(header,                0, 0)
        layout.addWidget(unstageButton,         0, 1)
        layout.addItem(QSpacerItem(1, 1),       1, 0)
        layout.addWidget(stagedFiles.searchBar, 2, 0, 1, 2)  # row col rowspan colspan
        layout.addWidget(stagedFiles,           3, 0, 1, 2)
        layout.addWidget(commitButtonsStack,    4, 0, 1, 2)
        layout.setRowStretch(3, 100)

        # Save references
        self.stagedHeader = header
        self.stagedFiles = stagedFiles
        self.unstageButton = unstageButton
        self.commitButton = commitButton
        self.amendButton = amendButton
        self.unifiedCommitButton = unifiedCommitButton

        return container

    def _makeCommittedFilesContainer(self):
        committedFiles = CommittedFiles(self)

        header = QElidedLabel(" ")
        header.setObjectName("committedHeader")
        header.setMinimumHeight(FILEHEADER_HEIGHT)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(QMargins())
        layout.setSpacing(0)  # automatic frameless list views on KDE Plasma 6 Breeze
        layout.addWidget(header)
        layout.addWidget(committedFiles.searchBar)
        layout.addSpacing(1)
        layout.addWidget(committedFiles)

        self.committedFiles = committedFiles
        self.committedHeader = header
        return container

    def _makeDiffContainer(self):
        header = QLabel(" ")
        header.setObjectName("diffHeader")
        header.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumHeight(FILEHEADER_HEIGHT)
        header.setContentsMargins(0, 0, 4, 0)
        # Don't let header dictate window width if displaying long filename
        header.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)

        diff = DiffView()

        diffViewContainer = QWidget()
        diffViewContainerLayout = QVBoxLayout(diffViewContainer)
        diffViewContainerLayout.setSpacing(0)
        diffViewContainerLayout.setContentsMargins(0, 0, 0, 0)
        diffViewContainerLayout.addWidget(diff)
        diffViewContainerLayout.addWidget(diff.searchBar)

        specialDiff = SpecialDiffView()

        conflict = ConflictView()
        conflictScroll = QScrollArea()
        conflictScroll.setWidget(conflict)
        conflictScroll.setWidgetResizable(True)

        stack = QStackedWidget()
        # Add widgets in same order as DiffStackPage
        stack.addWidget(diffViewContainer)
        stack.addWidget(specialDiff)
        stack.addWidget(conflictScroll)
        stack.setCurrentIndex(0)

        stackContainer = QWidget()
        layout = QVBoxLayout(stackContainer)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(1)
        layout.addWidget(header)
        layout.addWidget(stack)

        self.diffHeader = header
        self.diffStack = stack
        self.conflictView = conflict
        self.specialDiffView = specialDiff
        self.diffView = diff

        return stackContainer

    # -------------------------------------------------------------------------
    # File navigation

    def selectNextFile(self, down=True):
        page = self.fileStackPage()
        if page == "commit":
            widgets = [self.committedFiles]
        elif page == "workdir":
            widgets = [self.dirtyFiles, self.stagedFiles]
        else:
            logger.warning(f"Unknown FileStackPage {page})")
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
            with QSignalBlockerContext(widgets[leader]):
                widgets[leader].clearSelection()
            widgets[leader].selectRow(row)
        else:
            # No valid selection
            QApplication.beep()

            # Focus on the widget that has some selected files in it
            for w in widgets:
                if len(w.selectedIndexes()) > 0:
                    w.setFocus()
                    break

    def fileListByContext(self, context: NavContext) -> FileList:
        if context == NavContext.STAGED:
            return self.stagedFiles
        elif context == NavContext.UNSTAGED:
            return self.dirtyFiles
        else:
            return self.committedFiles

    # -------------------------------------------------------------------------
    # Clear

    def clear(self):
        with QSignalBlockerContext(self.committedFiles, self.dirtyFiles, self.stagedFiles):
            self.committedFiles.clear()
            self.dirtyFiles.clear()
            self.stagedFiles.clear()
            self.clearDocument()

    def clearDocument(self, sourceFileList: FileList | None = None):
        # Ignore clear request if it comes from a widget that doesn't have focus
        if sourceFileList and not sourceFileList.hasFocus():
            return

        self.setDiffStackPage("special")
        self.specialDiffView.clear()
        self.diffView.clear()  # might as well free up any memory taken by DiffView document
        self.diffHeader.setText(" ")

    # -------------------------------------------------------------------------
    # Stacked widget helpers

    @property
    def _fileStackPageValues(self):
        return typing.get_args(FileStackPage)

    def fileStackPage(self) -> FileStackPage:
        return self._fileStackPageValues[self.fileStack.currentIndex()]

    def setFileStackPage(self, p: FileStackPage):
        self.fileStack.setCurrentIndex(self._fileStackPageValues.index(p))

    def setFileStackPageByContext(self, context: NavContext):
        page: FileStackPage = "workdir" if context.isWorkdir() else "commit"
        self.setFileStackPage(page)

    @property
    def _diffStackPageValues(self):
        return typing.get_args(DiffStackPage)

    def diffStackPage(self) -> DiffStackPage:
        return self._diffStackPageValues[self.diffStack.currentIndex()]

    def setDiffStackPage(self, p: DiffStackPage):
        self.diffStack.setCurrentIndex(self._diffStackPageValues.index(p))

