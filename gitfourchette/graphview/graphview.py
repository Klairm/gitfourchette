# -----------------------------------------------------------------------------
# Copyright (C) 2024 Iliyas Jorio.
# This file is part of GitFourchette, distributed under the GNU GPL v3.
# For full terms, see the included LICENSE file.
# -----------------------------------------------------------------------------

from contextlib import suppress

from gitfourchette import settings
from gitfourchette.forms.searchbar import SearchBar
from gitfourchette.globalshortcuts import GlobalShortcuts
from gitfourchette.graph import MockCommit
from gitfourchette.graphview.commitlogdelegate import CommitLogDelegate
from gitfourchette.graphview.commitlogfilter import CommitLogFilter
from gitfourchette.graphview.commitlogmodel import CommitLogModel, SpecialRow
from gitfourchette.nav import NavLocator, NavContext
from gitfourchette.porcelain import *
from gitfourchette.qt import *
from gitfourchette.repomodel import UC_FAKEID
from gitfourchette.tasks import *
from gitfourchette.toolbox import *


class GraphView(QListView):
    linkActivated = Signal(str)
    statusMessage = Signal(str)

    clModel: CommitLogModel
    clFilter: CommitLogFilter

    class SelectCommitError(KeyError):
        def __init__(self, oid: Oid, foundButHidden: bool, likelyTruncated: bool = False):
            super().__init__()
            self.oid = oid
            self.foundButHidden = foundButHidden
            self.likelyTruncated = likelyTruncated

        def __str__(self):
            if self.foundButHidden:
                m = translate("GraphView", "This commit isn’t shown in the graph because it’s part of a hidden branch.")
            elif self.likelyTruncated:
                m = translate("GraphView", "This commit isn’t shown in the graph because it isn’t part of the truncated commit history.")
            else:
                m = translate("GraphView", "This commit isn’t shown in the graph.")
            return m

    def __init__(self, parent):
        super().__init__(parent)

        self.clModel = CommitLogModel(self)
        self.clFilter = CommitLogFilter(self)
        self.clFilter.setSourceModel(self.clModel)

        self.setModel(self.clFilter)

        # Massive perf boost when displaying/updating huge commit logs
        self.setUniformItemSizes(True)

        self.repoWidget = parent
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # prevents double-clicking to edit row text
        self.setItemDelegate(CommitLogDelegate(parent, parent=self))

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.onContextMenuRequested)

        self.searchBar = SearchBar(self, self.tr("Find a commit by hash, message or authorFind commit"))
        self.searchBar.detectHashes = True
        self.searchBar.setUpItemViewBuddy()
        self.searchBar.hide()

        self.refreshPrefs(invalidateMetrics=False)

    @property
    def repoModel(self):
        return self.repoWidget.repoModel

    def makeContextMenu(self) -> QMenu:
        kind = self.currentRowKind
        oid = self.currentCommitId
        repoModel = self.repoModel

        mergeActions = []

        if kind == SpecialRow.UncommittedChanges:
            actions = [
                TaskBook.action(self, NewCommit, accel="C"),
                TaskBook.action(self, AmendCommit, accel="A"),
                ActionDef.SEPARATOR,
                TaskBook.action(self, NewStash, accel="S"),
                TaskBook.action(self, ExportWorkdirAsPatch, accel="X"),
            ]

        elif kind == SpecialRow.EndOfShallowHistory:
            return None

        elif kind == SpecialRow.TruncatedHistory:
            expandSome = makeInternalLink("expandlog")
            expandAll = makeInternalLink("expandlog", n=str(0))
            changePref = makeInternalLink("prefs", "maxCommits")
            actions = [
                ActionDef(self.tr("Load up to {0} commits").format(QLocale().toString(repoModel.nextTruncationThreshold)),
                          lambda: self.linkActivated.emit(expandSome)),
                ActionDef(self.tr("Load full commit history"),
                          lambda: self.linkActivated.emit(expandAll)),
                ActionDef(self.tr("Change threshold setting"),
                          lambda: self.linkActivated.emit(changePref)),
            ]

        elif kind == SpecialRow.Commit:
            # Merge actions
            if repoModel.homeBranch:
                with suppress(KeyError, StopIteration):
                    refsHere = repoModel.refsAt[oid]
                    target = next(ref for ref in refsHere if ref.startswith((RefPrefix.HEADS, RefPrefix.REMOTES)))
                    mergeCaption = self.tr("&Merge into {0}...").format(lquo(repoModel.homeBranch))
                    mergeActions = [
                        TaskBook.action(self, MergeBranch, name=mergeCaption, taskArgs=(target,)),
                    ]

            checkoutAction = TaskBook.action(self, CheckoutCommit, self.tr("&Check Out..."), taskArgs=oid)
            checkoutAction.shortcuts = makeMultiShortcut(QKeySequence("Return"))

            actions = [
                *mergeActions,
                ActionDef.SEPARATOR,
                TaskBook.action(self, NewBranchFromCommit, self.tr("New &Branch Here..."), taskArgs=oid),
                TaskBook.action(self, NewTag, self.tr("&Tag This Commit..."), taskArgs=oid),
                ActionDef.SEPARATOR,
                checkoutAction,
                TaskBook.action(self, ResetHead, self.tr("&Reset HEAD to Here..."), taskArgs=oid),
                ActionDef.SEPARATOR,
                TaskBook.action(self, CherrypickCommit, self.tr("Cherry &Pick..."), taskArgs=oid),
                TaskBook.action(self, RevertCommit, self.tr("Re&vert..."), taskArgs=oid),
                TaskBook.action(self, ExportCommitAsPatch, self.tr("E&xport As Patch..."), taskArgs=oid),
                ActionDef.SEPARATOR,
                ActionDef(self.tr("Copy Commit &Hash"), self.copyCommitHashToClipboard, shortcuts=GlobalShortcuts.copy),
                ActionDef(self.tr("Get &Info..."), self.getInfoOnCurrentCommit, "SP_MessageBoxInformation", shortcuts=QKeySequence("Space")),
            ]

        menu = ActionDef.makeQMenu(self, actions)
        menu.setObjectName("GraphViewCM")

        return menu

    def onContextMenuRequested(self, point: QPoint):
        menu = self.makeContextMenu()
        if menu is not None:
            globalPoint = self.mapToGlobal(point)
            menu.exec(globalPoint)
            menu.deleteLater()

    def clear(self):
        self.clModel.clear()
        self.onSetCurrent()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        currentIndex = self.currentIndex()
        if not currentIndex.isValid() or event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        event.accept()
        rowKind = currentIndex.data(CommitLogModel.Role.SpecialRow)
        if rowKind == SpecialRow.UncommittedChanges:
            NewCommit.invoke(self)
        elif rowKind == SpecialRow.TruncatedHistory:
            self.linkActivated.emit(makeInternalLink("expandlog"))
        elif rowKind == SpecialRow.Commit:
            oid = self.currentCommitId
            CheckoutCommit.invoke(self, oid)

    def keyPressEvent(self, event: QKeyEvent):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copyCommitHashToClipboard()
            return

        k = event.key()
        oid = self.currentCommitId
        isValidCommit = oid and oid != UC_FAKEID

        if k in GlobalShortcuts.getCommitInfoHotkeys:
            if isValidCommit:
                self.getInfoOnCurrentCommit()
            else:
                QApplication.beep()

        elif k in GlobalShortcuts.checkoutCommitFromGraphHotkeys:
            if isValidCommit:
                CheckoutCommit.invoke(self, oid)
            else:
                NewCommit.invoke(self)

        elif k == Qt.Key.Key_Escape:
            if self.searchBar.isVisible():  # close search bar if it doesn't have focus
                self.searchBar.hide()
            else:
                QApplication.beep()

        else:
            super().keyPressEvent(event)

    @property
    def currentRowKind(self) -> SpecialRow:
        currentIndex = self.currentIndex()
        if not currentIndex.isValid():
            return SpecialRow.Invalid
        return currentIndex.data(CommitLogModel.Role.SpecialRow)

    @property
    def currentCommitId(self) -> Oid | None:
        currentIndex = self.currentIndex()
        if not currentIndex.isValid():
            return
        if SpecialRow.Commit != currentIndex.data(CommitLogModel.Role.SpecialRow):
            return
        oid = currentIndex.data(CommitLogModel.Role.Oid)
        return oid

    def getInfoOnCurrentCommit(self):
        oid = self.currentCommitId
        if not oid:
            return
        withDebugInfo = QGuiApplication.keyboardModifiers() & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)
        GetCommitInfo.invoke(self, oid, withDebugInfo)

    def copyCommitHashToClipboard(self):
        oid = self.currentCommitId
        if not oid:  # uncommitted changes
            return
        text = str(oid)
        QApplication.clipboard().setText(text)
        self.statusMessage.emit(clipboardStatusMessage(text))

    def selectionChanged(self, selected: QItemSelection, deselected: QItemSelection):
        # do standard callback, such as scrolling the viewport if reaching the edges, etc.
        super().selectionChanged(selected, deselected)

        if selected.count() == 0:
            self.onSetCurrent(None)
        else:
            self.onSetCurrent(selected.indexes()[0])

    def onSetCurrent(self, current: QModelIndex = None):
        if self.signalsBlocked():  # Don't bother with the jump if our signals are blocked
            return

        if current is None or not current.isValid():
            locator = NavLocator(NavContext.EMPTY)
        else:
            special = current.data(CommitLogModel.Role.SpecialRow)
            if special == SpecialRow.UncommittedChanges:
                locator = NavLocator(NavContext.WORKDIR)
            elif special == SpecialRow.Commit:
                oid = current.data(CommitLogModel.Role.Oid)
                locator = NavLocator(NavContext.COMMITTED, commit=oid)
            else:
                locator = NavLocator(NavContext.SPECIAL, path=str(special))
        Jump.invoke(self, locator)

    def selectRowForLocator(self, locator: NavLocator, force=False):
        filterIndex = self.getFilterIndexForLocator(locator)
        if force or filterIndex.row() != self.currentIndex().row():
            self.scrollTo(filterIndex, QAbstractItemView.ScrollHint.EnsureVisible)
            self.setCurrentIndex(filterIndex)
        return filterIndex

    def getFilterIndexForLocator(self, locator: NavLocator):
        if locator.context == NavContext.COMMITTED:
            index = self.getFilterIndexForCommit(locator.commit)
            assert index.data(CommitLogModel.Role.SpecialRow) == SpecialRow.Commit
        elif locator.context.isWorkdir():
            index = self.clFilter.index(0, 0)
            assert index.data(CommitLogModel.Role.SpecialRow) == SpecialRow.UncommittedChanges
        elif locator.context == NavContext.SPECIAL:
            if self.clModel._extraRow == SpecialRow.Invalid:
                raise ValueError("no special row")
            index = self.clFilter.index(self.clFilter.rowCount()-1, 0)
            assert locator.path == str(index.data(CommitLogModel.Role.SpecialRow))
        else:
            raise NotImplementedError(f"unsupported locator context {locator.context}")
        return index

    def getFilterIndexForCommit(self, oid: Oid) -> QModelIndex | None:
        try:
            rawIndex = self.repoModel.graph.getCommitRow(oid)
        except KeyError as exc:
            raise GraphView.SelectCommitError(oid, foundButHidden=False, likelyTruncated=self.repoModel.truncatedHistory) from exc

        newSourceIndex = self.clModel.index(rawIndex, 0)
        newFilterIndex = self.clFilter.mapFromSource(newSourceIndex)

        if not newFilterIndex.isValid():
            raise GraphView.SelectCommitError(oid, foundButHidden=True)

        return newFilterIndex

    def scrollToRowForLocator(self, locator: NavLocator, scrollHint=QAbstractItemView.ScrollHint.EnsureVisible):
        with suppress(GraphView.SelectCommitError):
            filterIndex = self.getFilterIndexForLocator(locator)
            self.scrollTo(filterIndex, scrollHint)

    def repaintCommit(self, oid: Oid):
        with suppress(GraphView.SelectCommitError):
            filterIndex = self.getFilterIndexForCommit(oid)
            self.update(filterIndex)

    def refreshPrefs(self, invalidateMetrics=True):
        self.setVerticalScrollMode(settings.prefs.listViewScrollMode)
        self.setAlternatingRowColors(settings.prefs.alternatingRowColors)

        # Force redraw to reflect changes in row height, flattening, date format, etc.
        if invalidateMetrics:
            self.itemDelegate().invalidateMetrics()
            self.model().layoutChanged.emit()

    # -------------------------------------------------------------------------
    # Find text in commit message or hash

    def searchRange(self, searchRange: range) -> QModelIndex | None:
        model = self.model()  # to filter out hidden rows, don't use self.clModel directly

        term = self.searchBar.searchTerm
        likelyHash = self.searchBar.searchTermLooksLikeHash
        assert term
        assert term == term.lower(), "search term should have been sanitized"

        for i in searchRange:
            index = model.index(i, 0)
            commit = model.data(index, CommitLogModel.Role.Commit)
            if commit is None or type(commit) is MockCommit:
                continue
            if likelyHash and str(commit.id).startswith(term):
                return index
            if term in commit.message.lower():
                return index
            if term in abbreviatePerson(commit.author, settings.prefs.authorDisplayStyle).lower():
                return index

        return None
