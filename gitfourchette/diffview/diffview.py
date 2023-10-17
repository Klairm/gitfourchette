import enum
import os
import re
from bisect import bisect_left, bisect_right
from typing import Literal

import pygit2
from pygit2 import Patch, Repository, Diff

from gitfourchette import colors
from gitfourchette import log
from gitfourchette import settings
from gitfourchette.diffview.diffdocument import DiffDocument, LineData
from gitfourchette.forms.searchbar import SearchBar
from gitfourchette.globalshortcuts import GlobalShortcuts
from gitfourchette.nav import NavLocator, NavContext
from gitfourchette.qt import *
from gitfourchette.subpatch import extractSubpatch
from gitfourchette.toolbox import *

TAG = "DiffView"


@enum.unique
class PatchPurpose(enum.IntFlag):
    STAGE = enum.auto()
    UNSTAGE = enum.auto()
    DISCARD = enum.auto()

    LINES = enum.auto()
    HUNK = enum.auto()
    FILE = enum.auto()

    VERB_MASK = STAGE | UNSTAGE | DISCARD

    @staticmethod
    def getName(purpose: 'PatchPurpose', verbOnly=False) -> str:
        pp = PatchPurpose
        if verbOnly:
            purpose &= pp.VERB_MASK
        dd = {
            pp.STAGE: translate("PatchPurpose", "Stage"),
            pp.UNSTAGE: translate("PatchPurpose", "Unstage"),
            pp.DISCARD: translate("PatchPurpose", "Discard"),
            pp.LINES | pp.STAGE: translate("PatchPurpose", "Stage lines"),
            pp.LINES | pp.UNSTAGE: translate("PatchPurpose", "Unstage lines"),
            pp.LINES | pp.DISCARD: translate("PatchPurpose", "Discard lines"),
            pp.HUNK | pp.STAGE: translate("PatchPurpose", "Stage hunk"),
            pp.HUNK | pp.UNSTAGE: translate("PatchPurpose", "Unstage hunk"),
            pp.HUNK | pp.DISCARD: translate("PatchPurpose", "Discard hunk"),
            pp.FILE | pp.STAGE: translate("PatchPurpose", "Stage file"),
            pp.FILE | pp.UNSTAGE: translate("PatchPurpose", "Unstage file"),
            pp.FILE | pp.DISCARD: translate("PatchPurpose", "Discard file"),
        }
        return dd.get(purpose, "???")


class DiffGutter(QWidget):
    diffView: 'DiffView'

    def __init__(self, parent):
        super().__init__(parent)
        assert isinstance(parent, DiffView)
        self.diffView = parent

        if MACOS or WINDOWS:
            dpr = 4
        else:
            dpr = 1  # On Linux, Qt doesn't seem to support cursors at non-1 DPR
        pix = QPixmap(f"assets:right_ptr@{dpr}x")
        pix.setDevicePixelRatio(dpr)
        flippedCursor = QCursor(pix, hotX=19, hotY=5)
        self.setCursor(flippedCursor)

        # Enable customContextMenuRequested signal
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def sizeHint(self) -> QSize:
        return QSize(self.diffView.gutterWidth(), 0)

    def paintEvent(self, event: QPaintEvent):
        self.diffView.gutterPaintEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        # Forward mouse wheel to parent widget
        self.parentWidget().wheelEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        # Double click to select clump of lines
        if event.button() == Qt.MouseButton.LeftButton:
            self.diffView.selectClumpOfLinesAt(clickPoint=event.pos())

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.diffView.selectWholeLinesTo(event.pos())
            else:
                self.diffView.selectWholeLineAt(event.pos())

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.diffView.selectWholeLinesTo(event.pos())


class DiffSearchHighlighter(QSyntaxHighlighter):
    def __init__(self, parent):
        super().__init__(parent)

        self.highlightFormat = QTextCharFormat()
        self.highlightFormat.setBackground(colors.yellow)
        self.highlightFormat.setFontWeight(QFont.Weight.Bold)

    def highlightBlock(self, text: str):
        term = self.parent().searchBar.sanitizedSearchTerm
        if not term:
            return
        termLength = len(term)

        text = text.lower()
        textLength = len(text)

        index = 0
        while index < textLength:
            index = text.find(term, index)
            if index < 0:
                break
            self.setFormat(index, termLength, self.highlightFormat)
            index += termLength


class DiffView(QPlainTextEdit):
    applyPatch = Signal(pygit2.Patch, bytes, PatchPurpose)
    revertPatch = Signal(pygit2.Patch, bytes)
    widgetMoved = Signal()
    contextualHelp = Signal(str)

    lineData: list[LineData]
    lineCursorStartCache: list[int]
    lineHunkIDCache: list[int]
    currentLocator: NavLocator
    currentPatch: Patch | None
    currentWorkdirFileStat: os.stat_result | None
    repo: Repository | None

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)

        # First-time init so callbacks don't crash looking for missing attributes
        self.lineData = []
        self.lineCursorStartCache = []
        self.lineHunkIDCache = []
        self.currentLocator = NavLocator()
        self.currentFileStat = None
        self.currentPatch = None
        self.repo = None

        # Highlighter for search terms
        self.highlighter = DiffSearchHighlighter(self)

        self.gutterMaxDigits = 0

        self.gutter = DiffGutter(self)
        self.updateRequest.connect(self.updateGutter)
        self.blockCountChanged.connect(self.updateGutterWidth)
        self.gutter.customContextMenuRequested.connect(lambda p: self.doContextMenu(self.gutter.mapToGlobal(p)))
        # self.cursorPositionChanged.connect(self.highlightCurrentLine)
        self.updateGutterWidth(0)

        # Work around control characters inserted by Qt 6 when copying text
        self.copyAvailable.connect(self.onCopyAvailable)

        # Emit contextual help with non-empty selection
        self.cursorPositionChanged.connect(self.emitSelectionHelp)
        self.selectionChanged.connect(self.emitSelectionHelp)

        self.searchBar = SearchBar(self, self.tr("Find in Diff"))
        # self.searchBar.textChanged.connect(self.onSearchTextChanged)
        self.searchBar.textChanged.connect(self.highlighter.rehighlight)
        self.searchBar.searchNext.connect(lambda: self.search("next"))
        self.searchBar.searchPrevious.connect(lambda: self.search("previous"))
        self.searchBar.hide()
        self.widgetMoved.connect(self.searchBar.snapToParent)

        # Initialize font
        self.refreshPrefs()

    # ---------------------------------------------
    # Qt events

    def contextMenuEvent(self, event: QContextMenuEvent):
        try:
            self.doContextMenu(event.globalPos())
        except Exception as exc:
            # Avoid exceptions in contextMenuEvent at all costs to prevent a crash
            excMessageBox(exc, message="Failed to create DiffView context menu")
            return

    def moveEvent(self, event: QMoveEvent):
        """Emit signal to reposition the search bar"""
        self.widgetMoved.emit()
        super().moveEvent(event)

    def resizeEvent(self, event: QResizeEvent):
        """Update gutter geometry"""
        super().resizeEvent(event)
        self.resizeGutter()

    def keyPressEvent(self, event: QKeyEvent):
        k = event.key()
        navContext = self.currentLocator.context
        if k in GlobalShortcuts.stageHotkeys:
            if navContext == NavContext.UNSTAGED:
                self.stageSelection()
            else:
                QApplication.beep()
        elif k in GlobalShortcuts.discardHotkeys:
            if navContext == NavContext.STAGED:
                self.unstageSelection()
            elif navContext == NavContext.UNSTAGED:
                self.discardSelection()
            else:
                QApplication.beep()
        else:
            super().keyPressEvent(event)

    # ---------------------------------------------
    # Document replacement

    def clear(self):  # override
        # Clear info about the current patch - necessary for document reuse detection to be correct when the user
        # clears the selection in a FileList and then reselects the last-displayed document.
        self.currentLocator = NavLocator()
        self.currentFileStat = None
        self.currentPatch = None

        # Clear the actual contents
        super().clear()

    def replaceDocument(self, repo: Repository, patch: Patch, locator: NavLocator, newDoc: DiffDocument):
        oldDocument = self.document()

        newFileStat = None
        if locator.context.isWorkdir() and repo is not None:
            fullPath = os.path.join(repo.workdir, locator.path)
            newFileStat = os.stat(fullPath, follow_symlinks=True)

        # Detect if we're trying to load exactly the same patch - common occurrence when moving the app back to the
        # foreground. In that case, don't change the document to prevent losing any selected text.
        if self.canReuseCurrentDocument(locator, newFileStat, newDoc):
            assert patch.data == self.currentPatch.data  # this check can be pretty expensive!

            # Delete new document
            assert newDoc.document is not oldDocument  # make sure it's not in use before deleting
            newDoc.document.deleteLater()
            newDoc.document = None  # prevent any callers from using a stale object

            # Bail now - don't change the document
            return

        if oldDocument:
            oldDocument.deleteLater()  # avoid leaking memory/objects, even though we do set QTextDocument's parent to this QTextEdit

        self.repo = repo
        self.currentPatch = patch
        self.currentLocator = locator
        self.currentFileStat = newFileStat

        newDoc.document.setParent(self)
        self.setDocument(newDoc.document)
        self.highlighter.setDocument(newDoc.document)

        self.lineData = newDoc.lineData
        self.lineCursorStartCache = [ld.cursorStart for ld in self.lineData]
        self.lineHunkIDCache = [ld.hunkPos.hunkID for ld in self.lineData]

        # now reset defaults that are lost when changing documents
        self.refreshPrefs()

        if self.currentPatch and len(self.currentPatch.hunks) > 0:
            lastHunk = self.currentPatch.hunks[-1]
            maxNewLine = lastHunk.new_start + lastHunk.new_lines
            maxOldLine = lastHunk.old_start + lastHunk.old_lines
            self.gutterMaxDigits = len(str(max(maxNewLine, maxOldLine)))
        else:
            self.gutterMaxDigits = 0
        self.updateGutterWidth(0)

        # Now restore cursor/scrollbar positions
        self.restorePosition(locator)

    @benchmark
    def canReuseCurrentDocument(
            self,
            newLocator: NavLocator,
            newFileStat: os.stat_result,
            newDocument: DiffDocument
    ) -> bool:
        """Detect if we're trying to reload the same patch that's already being displayed"""

        if not self.currentLocator.isSimilarEnoughTo(newLocator):
            return False

        if len(newDocument.lineData) != len(self.lineData):
            return False

        # If the locator points within a commit, the contents of the patch aren't supposed to ever change
        if not newLocator.context.isWorkdir():
            return True

        # Locator is in workdir
        if self.currentFileStat is None:
            return False

        # Compare file stats
        oldFileStat: os.stat_result = self.currentFileStat
        similarStats = (
                newFileStat.st_mode == oldFileStat.st_mode
                and newFileStat.st_size == oldFileStat.st_size
                and newFileStat.st_ino == oldFileStat.st_ino
                and newFileStat.st_mtime_ns == oldFileStat.st_mtime_ns
                and newFileStat.st_ctime_ns == oldFileStat.st_ctime_ns
        )

        if not similarStats:
            return False

        return similarStats

    # ---------------------------------------------
    # Restore position

    def restorePosition(self, locator: NavLocator):
        pos = locator.diffCursor
        lineNo = locator.diffLineNo

        # Get position at start of line
        try:
            sol = self.lineCursorStartCache[lineNo]
        except IndexError:
            sol = self.lineCursorStartCache[-1]

        # Get position at end of line
        try:
            eol = self.lineCursorStartCache[lineNo+1]
        except IndexError:
            eol = self.getMaxPosition()

        # If cursor position still falls within the same line, keep that position.
        # Otherwise, snap cursor position to start of line.
        if not (sol <= pos < eol):
            pos = sol

        # Unholy kludge to stabilize scrollbar position when QPlainTextEdit has wrapped lines
        vsb = self.verticalScrollBar()
        scrollTo = locator.diffScroll
        if self.lineWrapMode() != QPlainTextEdit.LineWrapMode.NoWrap and locator.diffScroll != 0:
            topCursor = self.textCursor()
            topCursor.setPosition(locator.diffScrollTop)
            self.setTextCursor(topCursor)
            self.centerCursor()
            scrolls = 0
            corner = self.getStableTopLeftCorner()
            while scrolls < 500 and self.cursorForPosition(corner).position() < locator.diffScrollTop:
                scrolls += 1
                scrollTo = vsb.value() + 1
                vsb.setValue(scrollTo)
            # log.info(TAG, f"Stabilized in {scrolls} iterations - final scroll {scrollTo} vs {locator.diffScroll})"
            #               f" - char pos {self.cursorForPosition(corner).position()} vs {locator.diffScrollTop}")

        # Move text cursor
        newTextCursor = self.textCursor()
        newTextCursor.setPosition(pos)
        self.setTextCursor(newTextCursor)

        # Finally, restore the scrollbar
        vsb.setValue(scrollTo)

    def getStableTopLeftCorner(self):
        return QPoint(0, self.fontMetrics().height() // 2)

    def getPreciseLocator(self):
        corner = self.getStableTopLeftCorner()
        cfp: QTextCursor = self.cursorForPosition(corner)

        diffCursor = self.textCursor().position()
        diffLineNo = self.findLineDataIndexAt(diffCursor)
        diffScroll = self.verticalScrollBar().value()
        diffScrollTop = cfp.position()
        locator = self.currentLocator.coarse().replace(
            diffCursor=diffCursor,
            diffLineNo=diffLineNo,
            diffScroll=diffScroll,
            diffScrollTop=diffScrollTop)

        # log.info("DiffView", f"getPreciseLocator: {diffScrollTop} - {cfp.positionInBlock()}"
        #                      f" - {cfp.block().text()[cfp.positionInBlock():]}")
        return locator

    # ---------------------------------------------
    # Prefs

    def refreshPrefs(self):
        monoFont = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if settings.prefs.diff_font:
            monoFont.fromString(settings.prefs.diff_font)
        self.setFont(monoFont)

        currentDocument = self.document()
        if currentDocument:
            currentDocument.setDefaultFont(monoFont)

        tabWidth = settings.prefs.diff_tabSpaces
        self.setTabStopDistance(QFontMetricsF(monoFont).horizontalAdvance(' ' * tabWidth))
        self.refreshWordWrap()
        self.setCursorWidth(2)

    def refreshWordWrap(self):
        if settings.prefs.diff_wordWrap:
            wrapMode = QPlainTextEdit.LineWrapMode.WidgetWidth
        else:
            wrapMode = QPlainTextEdit.LineWrapMode.NoWrap
        self.setLineWrapMode(wrapMode)

    def toggleWordWrap(self):
        settings.prefs.diff_wordWrap = not settings.prefs.diff_wordWrap
        settings.prefs.write()
        self.refreshWordWrap()

    # ---------------------------------------------
    # Context menu

    def doContextMenu(self, globalPos: QPoint):
        # Don't show the context menu if we're empty
        if self.document().isEmpty():
            return

        # Get position of click in document
        clickedPosition = self.cursorForPosition(self.mapFromGlobal(globalPos)).position()

        cursor: QTextCursor = self.textCursor()
        hasSelection = cursor.hasSelection()

        # Find hunk at click position
        clickedHunkID = self.findHunkIDAt(clickedPosition)
        shortHunkHeader = ""
        if clickedHunkID >= 0:
            hunk: pygit2.DiffHunk = self.currentPatch.hunks[clickedHunkID]
            headerMatch = re.match(r"@@ ([^@]+) @@.*", hunk.header)
            shortHunkHeader = headerMatch.group(1) if headerMatch else f"#{clickedHunkID}"

        actions = []

        navContext = self.currentLocator.context

        if navContext == NavContext.COMMITTED:
            if hasSelection:
                actions = [
                    ActionDef(self.tr("Export Lines as Patch..."), self.exportSelection),
                    ActionDef(self.tr("Revert Lines..."), self.revertSelection),
                ]
            else:
                actions = [
                    ActionDef(self.tr("Export Hunk {0} as Patch...").format(shortHunkHeader), lambda: self.exportHunk(clickedHunkID)),
                    ActionDef(self.tr("Revert Hunk..."), lambda: self.revertHunk(clickedHunkID)),
                ]

        elif navContext == NavContext.UNTRACKED:
            if hasSelection:
                actions = [
                    ActionDef(self.tr("Export Lines as Patch..."), self.exportSelection),
                ]
            else:
                actions = [
                    ActionDef(self.tr("Export Hunk as Patch..."), lambda: self.exportHunk(clickedHunkID)),
                ]

        elif navContext == NavContext.UNSTAGED:
            if hasSelection:
                actions = [
                    ActionDef(
                        self.tr("Stage Lines"),
                        self.stageSelection,
                        shortcuts=GlobalShortcuts.stageHotkeys,
                    ),
                    ActionDef(
                        self.tr("Discard Lines"),
                        self.discardSelection,
                        QStyle.StandardPixmap.SP_TrashIcon,
                        shortcuts=GlobalShortcuts.discardHotkeys,
                    ),
                    ActionDef(
                        self.tr("Export Lines as Patch..."),
                        self.exportSelection
                    ),
                ]
            else:
                actions = [
                    ActionDef(
                        self.tr("Stage Hunk {0}").format(shortHunkHeader),
                        lambda: self.stageHunk(clickedHunkID),
                    ),
                    ActionDef(
                        self.tr("Discard Hunk"),
                        lambda: self.discardHunk(clickedHunkID),
                    ),
                    ActionDef(self.tr("Export Hunk as Patch..."), lambda: self.exportHunk(clickedHunkID)),
                ]

        elif navContext == NavContext.STAGED:
            if hasSelection:
                actions = [
                    ActionDef(
                        self.tr("Unstage Lines"),
                        self.unstageSelection,
                        shortcuts=GlobalShortcuts.discardHotkeys,
                    ),
                    ActionDef(
                        self.tr("Export Lines as Patch..."),
                        self.exportSelection,
                    ),
                ]
            else:
                actions = [
                    ActionDef(
                        self.tr("Unstage Hunk {0}").format(shortHunkHeader),
                        lambda: self.unstageHunk(clickedHunkID),
                    ),
                    ActionDef(
                        self.tr("Export Hunk as Patch..."),
                        lambda: self.exportHunk(clickedHunkID),
                    ),
                ]

        actions += [
            ActionDef.SEPARATOR,
            ActionDef(self.tr("&Word wrap"), self.toggleWordWrap, checkState=1 if settings.prefs.diff_wordWrap else -1),
        ]

        bottom: QMenu = self.createStandardContextMenu()
        menu = ActionDef.makeQMenu(self, actions, bottom)
        bottom.deleteLater()  # don't need this menu anymore
        menu.setObjectName("DiffViewContextMenu")
        menu.exec(globalPos)
        menu.deleteLater()

    # ---------------------------------------------
    # Patch

    def findLineDataIndexAt(self, cursorPosition: int, firstLineDataIndex: int = 0):
        if not self.lineData:
            return -1
        index = bisect_right(self.lineCursorStartCache, cursorPosition, firstLineDataIndex)
        return index - 1

    def findHunkIDAt(self, cursorPosition: int):
        clickLineDataIndex = self.findLineDataIndexAt(cursorPosition)
        try:
            return self.lineData[clickLineDataIndex].hunkPos.hunkID
        except IndexError:
            return -1

    def getSelectedLineExtents(self):
        cursor: QTextCursor = self.textCursor()
        posStart = cursor.selectionStart()
        posEnd = cursor.selectionEnd()

        # If line 1 is completely selected and the cursor has landed at the very beginning of line 2,
        # don't select line 2.
        if posEnd - posStart > 0:
            posEnd -= 1

        # Find indices of first and last LineData objects given the current selection
        biStart = self.findLineDataIndexAt(posStart)
        biEnd = self.findLineDataIndexAt(posEnd, biStart)

        return biStart, biEnd

    def isSelectionActionable(self):
        start, end = self.getSelectedLineExtents()
        if start < 0:
            return False
        for i in range(start, end+1):
            ld = self.lineData[i]
            if ld.diffLine and ld.diffLine.origin in "+-":
                return True
        return False

    def extractSelection(self, reverse=False) -> bytes:
        start, end = self.getSelectedLineExtents()
        return extractSubpatch(
            self.currentPatch,
            self.lineData[start].hunkPos,
            self.lineData[end].hunkPos,
            reverse)

    def extractHunk(self, hunkID: int, reverse=False) -> bytes:
        # Find indices of first and last LineData objects given the current hunk
        hunkFirstLineIndex = bisect_left(self.lineHunkIDCache, hunkID, 0)
        hunkLastLineIndex = bisect_left(self.lineHunkIDCache, hunkID+1, hunkFirstLineIndex) - 1

        return extractSubpatch(
            self.currentPatch,
            self.lineData[hunkFirstLineIndex].hunkPos,
            self.lineData[hunkLastLineIndex].hunkPos,
            reverse)

    def exportPatch(self, patchData: bytes, saveInto=""):
        if not patchData:
            QApplication.beep()
            return

        def dump(path: str):
            with open(path, "wb") as file:
                file.write(patchData)

        name = os.path.basename(self.currentPatch.delta.new_file.path) + "[partial].patch"

        if saveInto:
            savePath = os.path.join(saveInto, name)
            dump(savePath)
        else:
            qfd = PersistentFileDialog.saveFile(
                self, "SaveFile", self.tr("Export selected lines"), name)
            qfd.fileSelected.connect(dump)
            qfd.show()

    def fireRevert(self, patchData: bytes):
        self.revertPatch.emit(self.currentPatch, patchData)

    def fireApplyLines(self, purpose: PatchPurpose):
        purpose |= PatchPurpose.LINES
        reverse = not (purpose & PatchPurpose.STAGE)
        patchData = self.extractSelection(reverse)
        self.applyPatch.emit(self.currentPatch, patchData, purpose)

    def fireApplyHunk(self, hunkID: int, purpose: PatchPurpose):
        purpose |= PatchPurpose.HUNK
        reverse = not (purpose & PatchPurpose.STAGE)
        patchData = self.extractHunk(hunkID, reverse)
        self.applyPatch.emit(self.currentPatch, patchData, purpose)

    def stageSelection(self):
        self.fireApplyLines(PatchPurpose.STAGE)

    def unstageSelection(self):
        self.fireApplyLines(PatchPurpose.UNSTAGE)

    def discardSelection(self):
        self.fireApplyLines(PatchPurpose.DISCARD)

    def exportSelection(self, saveInto=""):
        patchData = self.extractSelection()
        self.exportPatch(patchData, saveInto)

    def revertSelection(self):
        patchData = self.extractSelection(reverse=True)
        self.fireRevert(patchData)

    def stageHunk(self, hunkID: int):
        self.fireApplyHunk(hunkID, PatchPurpose.STAGE)

    def unstageHunk(self, hunkID: int):
        self.fireApplyHunk(hunkID, PatchPurpose.UNSTAGE)

    def discardHunk(self, hunkID: int):
        self.fireApplyHunk(hunkID, PatchPurpose.DISCARD)

    def exportHunk(self, hunkID: int, saveInto=""):
        patchData = self.extractHunk(hunkID)
        self.exportPatch(patchData, saveInto)

    def revertHunk(self, hunkID: int):
        patchData = self.extractHunk(hunkID, reverse=True)
        self.fireRevert(patchData)

    # ---------------------------------------------
    # Gutter (inspired by https://doc.qt.io/qt-5/qtwidgets-widgets-codeeditor-example.html)

    def gutterWidth(self) -> int:
        paddingString = '0' * (2*self.gutterMaxDigits + 2)
        return self.fontMetrics().horizontalAdvance(paddingString)

    def resizeGutter(self):
        cr: QRect = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutterWidth(), cr.height()))

    def updateGutterWidth(self, newBlockCount: int):
        self.setViewportMargins(self.gutterWidth(), 0, 0, 0)

    def gutterPaintEvent(self, event: QPaintEvent):
        painter = QPainter(self.gutter)
        painter.setFont(self.font())

        # Set up colors
        palette = self.palette()
        themeBG = palette.color(QPalette.ColorRole.Base)  # standard theme background color
        themeFG = palette.color(QPalette.ColorRole.Text)  # standard theme foreground color
        if themeBG.value() > themeFG.value():
            gutterColor = themeBG.darker(105)  # light theme
        else:
            gutterColor = themeBG.lighter(140)  # dark theme
        lineColor = QColor(*themeFG.getRgb()[:3], 80)
        textColor = QColor(*themeFG.getRgb()[:3], 128)

        # Gather some metrics
        paintRect = event.rect()
        gutterRect = self.gutter.rect()
        fontHeight = self.fontMetrics().height()

        # Draw background
        painter.fillRect(paintRect, gutterColor)

        # Draw vertical separator line
        painter.fillRect(gutterRect.x() + gutterRect.width() - 1, paintRect.y(), 1, paintRect.height(), lineColor)

        block: QTextBlock = self.firstVisibleBlock()
        blockNumber = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        # Draw line numbers and hunk separator lines
        if settings.prefs.diff_colorblindFriendlyColors:
            noOldPlaceholder = "+"
            noNewPlaceholder = "-"
        else:
            noOldPlaceholder = "·"
            noNewPlaceholder = "·"

        painter.setPen(textColor)
        while block.isValid() and top <= paintRect.bottom():
            if blockNumber >= len(self.lineData):
                break

            ld = self.lineData[blockNumber]
            if block.isVisible() and bottom >= paintRect.top():
                if ld.diffLine:
                    # Draw line numbers
                    old = str(ld.diffLine.old_lineno) if ld.diffLine.old_lineno > 0 else noOldPlaceholder
                    new = str(ld.diffLine.new_lineno) if ld.diffLine.new_lineno > 0 else noNewPlaceholder

                    colW = (gutterRect.width() - 4) // 2
                    painter.drawText(0, top, colW, fontHeight, Qt.AlignmentFlag.AlignRight, old)
                    painter.drawText(colW, top, colW, fontHeight, Qt.AlignmentFlag.AlignRight, new)
                else:
                    # Draw hunk separator horizontal line
                    painter.fillRect(0, round((top+bottom)/2), gutterRect.width()-1, 1, lineColor)

            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            blockNumber += 1

        painter.end()

    def updateGutter(self, rect: QRect, dy: int):
        if dy != 0:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

        if rect.contains(self.viewport().rect()):
            self.updateGutterWidth(0)

    # ---------------------------------------------
    # Cursor/selection

    def getMaxPosition(self):
        lastBlock = self.document().lastBlock()
        return lastBlock.position() + max(0, lastBlock.length() - 1)

    def getAnchorHomeLinePosition(self):
        cursor: QTextCursor = self.textCursor()

        # Snap anchor to start of home line
        cursor.setPosition(cursor.anchor(), QTextCursor.MoveMode.MoveAnchor)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfLine, QTextCursor.MoveMode.MoveAnchor)

        return cursor.anchor()

    def getStartOfLineAt(self, point: QPoint):
        clickedCursor: QTextCursor = self.cursorForPosition(point)
        clickedCursor.movePosition(QTextCursor.MoveOperation.StartOfLine)
        return clickedCursor.position()

    def replaceCursor(self, cursor: QTextCursor):
        """Replace the cursor without moving the horizontal scroll bar"""
        with QScrollBackupContext(self.horizontalScrollBar()):
            self.setTextCursor(cursor)

    def selectWholeLineAt(self, point: QPoint):
        clickedPosition = self.getStartOfLineAt(point)

        cursor: QTextCursor = self.textCursor()
        cursor.setPosition(clickedPosition)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfLine, QTextCursor.MoveMode.KeepAnchor)

        self.replaceCursor(cursor)

    def selectWholeLinesTo(self, point: QPoint):
        homeLinePosition = self.getAnchorHomeLinePosition()
        clickedPosition = self.getStartOfLineAt(point)

        cursor: QTextCursor = self.textCursor()

        if homeLinePosition <= clickedPosition:
            # Move anchor to START of home line
            cursor.setPosition(homeLinePosition, QTextCursor.MoveMode.MoveAnchor)
            # Move cursor to END of clicked line
            cursor.setPosition(clickedPosition, QTextCursor.MoveMode.KeepAnchor)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        else:
            # Move anchor to END of home line
            cursor.setPosition(homeLinePosition, QTextCursor.MoveMode.MoveAnchor)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.MoveAnchor)
            # Move cursor to START of clicked line
            cursor.setPosition(clickedPosition, QTextCursor.MoveMode.KeepAnchor)
            cursor.movePosition(QTextCursor.MoveOperation.StartOfLine, QTextCursor.MoveMode.KeepAnchor)

        self.replaceCursor(cursor)

    def selectClumpOfLinesAt(self, clickPoint: QPoint = None, textCursorPosition: int = -1):
        assert bool(textCursorPosition >= 0) ^ bool(clickPoint)
        if textCursorPosition < 0:
            textCursorPosition = self.getStartOfLineAt(clickPoint)

        ldList = self.lineData
        i = self.findLineDataIndexAt(textCursorPosition)
        ld = ldList[i]

        if ld.hunkPos.hunkLineNum < 0:
            # Hunk header line, select whole hunk
            start = i
            end = i
            while end < len(ldList)-1 and ldList[end+1].hunkPos.hunkID == ld.hunkPos.hunkID:
                end += 1
        elif ld.clumpID < 0:
            # Context line
            QApplication.beep()
            return
        else:
            # Get clump boundaries
            start = i
            end = i
            while start > 0 and ldList[start-1].clumpID == ld.clumpID:
                start -= 1
            while end < len(ldList)-1 and ldList[end+1].clumpID == ld.clumpID:
                end += 1

        startPosition = ldList[start].cursorStart
        endPosition = min(self.getMaxPosition(), ldList[end].cursorEnd + 1)  # +1 to select empty lines

        cursor: QTextCursor = self.textCursor()
        cursor.setPosition(startPosition, QTextCursor.MoveMode.MoveAnchor)
        cursor.setPosition(endPosition, QTextCursor.MoveMode.KeepAnchor)
        self.replaceCursor(cursor)

    # ---------------------------------------------
    # Selection help

    def emitSelectionHelp(self):
        if self.currentLocator.context in [NavContext.COMMITTED, NavContext.EMPTY]:
            return

        if not self.isSelectionActionable():
            self.contextualHelp.emit("")
            return

        start, end = self.getSelectedLineExtents()
        numLines = end - start + 1

        if self.currentLocator.context == NavContext.UNSTAGED:
            help = self.tr("Hit {stagekey} to stage the current/selected line(s), or {discardkey} to discard it/them.",
                           "singular: 'current line', plural: 'selected lines'", numLines)
        elif self.currentLocator.context == NavContext.STAGED:
            help = self.tr("Hit {unstagekey} to unstage the current/selected line(s).",
                           "singular: 'current line', plural: 'selected lines'", numLines)
        else:
            return

        help = help.format(
            stagekey=QKeySequence(GlobalShortcuts.stageHotkeys[0]).toString(QKeySequence.SequenceFormat.NativeText),
            unstagekey=QKeySequence(GlobalShortcuts.discardHotkeys[0]).toString(QKeySequence.SequenceFormat.NativeText),
            discardkey=QKeySequence(GlobalShortcuts.discardHotkeys[0]).toString(QKeySequence.SequenceFormat.NativeText))

        self.contextualHelp.emit("💡 " + help)

    # ---------------------------------------------
    # Clipboard U+2029 fix
    # TODO: Do we still need this stuff with recent versions of Qt 6?

    def onCopyAvailable(self, yes: bool):
        if not WINDOWS and settings.prefs.debug_fixU2029InClipboard:
            if yes:
                QApplication.clipboard().changed.connect(self.fixU2029InClipboard)
            else:
                QApplication.clipboard().changed.disconnect(self.fixU2029InClipboard)

    def fixU2029InClipboard(self, mode: QClipboard.Mode):
        """
        Qt 6 replaces line breaks with U+2029 (PARAGRAPH SEPARATOR) when copying
        text from a QPlainTextEdit. Let's restore the line breaks.

        https://doc.qt.io/qt-6/qtextcursor.html#selectedText
        "If the selection obtained from an editor spans a line break, the text
        will contain a Unicode U+2029 paragraph separator character instead of
        a newline \n character. Use QString::replace() to replace these
        characters with newlines."
        """

        # The copied data probably didn't originate from the DiffView if it doesn't have focus
        if not self.hasFocus():
            return

        clipboard = QApplication.clipboard()

        if __debug__ and "\u2029" not in clipboard.text(mode):
            log.info(TAG, F"Scrubbing U+2029 would be useless in this buffer!")
            return

        # Even if we have focus, another process might have modified the clipboard in the background.
        # So, make sure our application owns the data in the clipboard.
        ownsData = ((mode == QClipboard.Mode.Clipboard and clipboard.ownsClipboard())
                    or (mode == QClipboard.Mode.Selection and clipboard.ownsSelection())
                    or (mode == QClipboard.Mode.FindBuffer and clipboard.ownsFindBuffer()))
        if not ownsData:
            return

        log.info(TAG, F"Scrubbing U+2029 characters from clipboard ({mode})")
        text = clipboard.text(mode).replace("\u2029", "\n")
        with QSignalBlockerContext(clipboard):
            clipboard.setText(text, mode)

    # ---------------------------------------------
    # Search

    def search(self, op: Literal["start", "next", "previous"]):
        self.searchBar.popUp(forceSelectAll=op == "start")

        if op == "start":
            return

        forward = op != "previous"

        message = self.searchBar.sanitizedSearchTerm
        if not message:
            QApplication.beep()
            return

        doc: QTextDocument = self.document()

        if forward:
            newCursor = doc.find(message, self.textCursor())
        else:
            newCursor = doc.find(message, self.textCursor(), QTextDocument.FindFlag.FindBackward)

        if newCursor:
            self.setTextCursor(newCursor)
            return

        def wrapAround():
            tc = self.textCursor()
            if forward:
                tc.movePosition(QTextCursor.MoveOperation.Start)
            else:
                tc.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(tc)
            self.search(op)

        prompt = [
            self.tr("End of diff reached.") if forward else self.tr("Top of diff reached."),
            self.tr("No more occurrences of “{0}” found.").format(escape(message))
        ]
        askConfirmation(self, self.tr("Find in Diff"), paragraphs(prompt), okButtonText=self.tr("Wrap Around"),
                        messageBoxIcon="information", callback=wrapAround)

