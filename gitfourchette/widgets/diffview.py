from allqt import *
from diffmodel import DiffModel
from stagingstate import StagingState
from globalstatus import globalstatus
from util import bisect, excMessageBox
import enum
import git
import patch
import settings
import trash


@enum.unique
class PatchPurpose(enum.IntEnum):
    STAGE = enum.auto()
    UNSTAGE = enum.auto()
    DISCARD = enum.auto()


class DiffView(QTextEdit):
    patchApplied: Signal = Signal()

    lineData: list[patch.LineData]
    currentStagingState: StagingState
    currentChange: git.Diff
    currentGitRepo: git.Repo

    def __init__(self, parent=None):
        super().__init__(parent)
        #self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)

    def replaceDocument(self, repo: git.Repo, diff: git.Diff, stagingState: StagingState, dm: DiffModel):
        oldDocument = self.document()
        if oldDocument:
            oldDocument.deleteLater()  # avoid leaking memory/objects, even though we do set QTextDocument's parent to this QTextEdit

        self.currentStagingState = stagingState
        self.currentGitRepo = repo
        self.currentChange = diff

        self.setDocument(dm.document)
        self.lineData = dm.lineData

        # now reset defaults that are lost when changing documents
        self.setTabStopDistance(settings.monoFontMetrics.horizontalAdvance(' ' * settings.prefs.diff_tabSpaces))
        if dm.forceWrap or settings.prefs.diff_wordWrap:
            self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        else:
            self.setWordWrapMode(QTextOption.NoWrap)

        self.setCursorWidth(2)

    def contextMenuEvent(self, event: QContextMenuEvent):
        menu: QMenu = self.createStandardContextMenu()
        before = menu.actions()[0]

        actions = []

        if self.currentStagingState in [None, StagingState.COMMITTED, StagingState.UNTRACKED]:
            pass
        elif self.currentStagingState == StagingState.UNSTAGED:
            action1 = QAction("Stage Lines", self)
            action1.triggered.connect(self.stageLines)
            action2 = QAction("Discard Lines", self)
            action2.triggered.connect(self.discardLines)
            actions = [action1, action2]
        elif self.currentStagingState == StagingState.STAGED:
            action1 = QAction("Unstage Lines", self)
            action1.triggered.connect(self.unstageLines)
            actions = [action1]
        else:
            QMessageBox.warning(self, "DiffView", F"Unknown staging state: {self.currentStagingState}")

        if actions:
            for a in actions:
                menu.insertAction(before, a)
            menu.insertSeparator(before)

        menu.exec_(event.globalPos())

    def _applyLines(self, operation: PatchPurpose):
        cursor = self.textCursor()
        posStart = cursor.selectionStart()
        posEnd = cursor.selectionEnd()

        if posEnd - posStart > 0:
            posEnd -= 1

        biStart = bisect(self.lineData, posStart, key=lambda ld: ld.cursorStart)
        biEnd = bisect(self.lineData, posEnd, biStart, key=lambda ld: ld.cursorStart)

        if operation == PatchPurpose.DISCARD:
            reverse = True
            cached = False
        elif operation == PatchPurpose.STAGE:
            reverse = False
            cached = True
        elif operation == PatchPurpose.UNSTAGE:
            reverse = True
            cached = True
        else:
            raise ValueError(F"unsupported operation for _applyLines")

        print(F"{operation} lines:  cursor({posStart}-{posEnd})  bisect({biStart}-{biEnd})")

        biStart -= 1

        patchData = patch.makePatchFromLines(
            self.currentChange.a_path,
            self.currentChange.b_path,
            self.lineData,
            biStart,
            biEnd,
            plusLinesAreContext=reverse)

        if not patchData:
            QMessageBox.information(self, "Nothing to patch", "Select one or more red or green lines before applying a partial patch.")
            return

        if operation == PatchPurpose.DISCARD:
            trash.trashRawPatch(self.currentGitRepo, patchData)

        try:
            patch.applyPatch(self.currentGitRepo, patchData, cached=cached, reverse=reverse)
        except git.GitCommandError as e:
            excMessageBox(e, F"{operation.title()}: Apply Patch", F"Failed to apply patch for operation “{operation}”.", parent=self)

        self.patchApplied.emit()

    def stageLines(self):
        self._applyLines(PatchPurpose.STAGE)

    def unstageLines(self):
        self._applyLines(PatchPurpose.UNSTAGE)

    def discardLines(self):
        self._applyLines(PatchPurpose.DISCARD)

    def keyPressEvent(self, event: QKeyEvent):
        k = event.key()
        if k in settings.KEYS_ACCEPT:
            if self.currentStagingState == StagingState.UNSTAGED:
                self.stageLines()
            else:
                QApplication.beep()
        elif k in settings.KEYS_REJECT:
            if self.currentStagingState == StagingState.STAGED:
                self.unstageLines()
            elif self.currentStagingState == StagingState.UNSTAGED:
                self.discardLines()
            else:
                QApplication.beep()
        else:
            super().keyPressEvent(event)
