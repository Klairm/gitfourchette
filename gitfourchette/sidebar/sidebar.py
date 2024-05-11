from contextlib import suppress
import logging
from typing import Iterable, Callable

from gitfourchette import porcelain
from gitfourchette import settings
from gitfourchette.nav import NavLocator
from gitfourchette.tasks import *
from gitfourchette.globalshortcuts import GlobalShortcuts
from gitfourchette.porcelain import Oid, RefPrefix
from gitfourchette.qt import *
from gitfourchette.repostate import RepoState
from gitfourchette.sidebar.sidebardelegate import SidebarDelegate, SidebarClickZone
from gitfourchette.sidebar.sidebarmodel import (
    SidebarModel, SidebarNode, EItem,
    ROLE_REF, ROLE_ISHIDDEN,
)
from gitfourchette.toolbox import *
from gitfourchette.webhost import WebHost

logger = logging.getLogger(__name__)

INVALID_MOUSEPRESS = (-1, SidebarClickZone.Invalid)


class Sidebar(QTreeView):
    toggleHideRefPattern = Signal(str)
    toggleHideStash = Signal(Oid)
    toggleHideAllStashes = Signal()

    pushBranch = Signal(str)

    openSubmoduleRepo = Signal(str)
    openSubmoduleFolder = Signal(str)

    statusMessage = Signal(str)

    @property
    def sidebarModel(self) -> SidebarModel:
        model = self.model()
        assert isinstance(model, SidebarModel)
        return model

    def __init__(self, parent):
        super().__init__(parent)

        self.setObjectName("Sidebar")
        self.setMouseTracking(True)  # for eye icons
        self.setMinimumWidth(128)
        self.setIndentation(16)
        self.setHeaderHidden(True)
        self.setAnimated(True)
        self.setUniformRowHeights(True)  # large sidebars update twice as fast with this, but we can't have thinner spacers
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.customContextMenuRequested.connect(self.onCustomContextMenuRequested)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.setItemDelegate(SidebarDelegate(self))

        self.setModel(SidebarModel(self))

        if settings.DEVDEBUG and QAbstractItemModelTester is not None:
            self.modelTester = QAbstractItemModelTester(self.model())
            logger.warning("Sidebar model tester enabled. This will significantly slow down refreshes!")

        self.expanded.connect(self.onExpanded)
        self.collapsed.connect(self.onCollapsed)
        self.collapseCacheValid = False
        self.collapseCache = set()

        # When clicking on a row, information about the clicked row and "zone"
        # is kept until mouseReleaseEvent.
        self.mousePressCache = INVALID_MOUSEPRESS

        self.refreshPrefs()

    def drawBranches(self, painter, rect, index):
        """
        (overridden function)
        Prevent drawing the default "branches" and expand/collapse indicators.
        We draw the expand/collapse indicator ourselves in SidebarDelegate.
        """
        # Overriding this function has the same effect as adding this line in the QSS:
        # Sidebar::branch { image: none; border-image: none; }
        return

    def visualRect(self, index: QModelIndex) -> QRect:
        """
        Required so the theme can properly draw unindented rows.
        """

        vr = super().visualRect(index)

        if index.isValid():
            node = SidebarNode.fromIndex(index)
            SidebarDelegate.unindentRect(node.kind, vr, self.indentation())

        return vr

    def updateHiddenBranches(self, hiddenBranches: list[str]):
        self.model().updateHiddenBranches(hiddenBranches)

    def makeNodeMenu(self, node: SidebarNode, menu: QMenu = None, index: QModelIndex = None):
        if menu is None:
            menu = QMenu(self)
            menu.setObjectName("SidebarContextMenu")

        actions = []

        model = self.sidebarModel
        repo = model.repo
        item = node.kind
        data = node.data
        isHidden = model.isExplicitlyHidden(node)

        if item == EItem.UncommittedChanges:
            actions += [
                TaskBook.action(self, NewCommit, "&C"),
                TaskBook.action(self, AmendCommit, "&A"),
                ActionDef.SEPARATOR,
                TaskBook.action(self, NewStash, "&S"),
                TaskBook.action(self, ExportWorkdirAsPatch, "&X"),
            ]

        elif item == EItem.LocalBranchesHeader:
            actions += [
                TaskBook.action(self, NewBranchFromHead, self.tr("&New Branch...")),
            ]

        elif item == EItem.LocalBranch:
            refName = data
            prefix, branchName = RefPrefix.split(data)
            assert prefix == RefPrefix.HEADS
            branch = repo.branches.local[branchName]

            activeBranchName = repo.head_branch_shorthand
            isCurrentBranch = branch and branch.is_checked_out()
            hasUpstream = bool(branch.upstream)
            upstreamBranchName = "" if not hasUpstream else branch.upstream.shorthand

            thisBranchDisplay = lquoe(branchName)
            activeBranchDisplay = lquoe(activeBranchName)
            upstreamBranchDisplay = lquoe(upstreamBranchName)

            actions += [
                TaskBook.action(self,
                    SwitchBranch,
                    self.tr("&Switch to {0}").format(thisBranchDisplay),
                    taskArgs=(branchName, False),  # False: don't ask for confirmation
                    enabled=not isCurrentBranch,
                ),

                ActionDef.SEPARATOR,

                TaskBook.action(self,
                    MergeBranch,
                    self.tr("&Merge into {0}...").format(activeBranchDisplay),
                    taskArgs=refName,
                    enabled=not isCurrentBranch and activeBranchName,
                ),

                ActionDef.SEPARATOR,

                TaskBook.action(self,
                    FetchRemoteBranch,
                    self.tr("&Fetch {0}...").format(upstreamBranchDisplay) if hasUpstream else self.tr("Fetch..."),
                    taskArgs=branch.upstream.shorthand if hasUpstream else None,
                    enabled=hasUpstream,
                ),

                TaskBook.action(
                    self,
                    PullBranch,
                    self.tr("Pu&ll from {0}...").format(upstreamBranchDisplay) if hasUpstream else self.tr("Pull..."),
                    enabled=hasUpstream and isCurrentBranch,
                ),

                TaskBook.action(self,
                    FastForwardBranch,
                    self.tr("Fast-Forward to {0}...").format(upstreamBranchDisplay) if hasUpstream else self.tr("Fast-Forward..."),
                    taskArgs=branchName,
                    enabled=hasUpstream,
                ),

                ActionDef(
                    self.tr("&Push to {0}...").format(upstreamBranchDisplay) if hasUpstream else self.tr("Push..."),
                    lambda: self.pushBranch.emit(branchName),
                    "vcs-push",
                    shortcuts=GlobalShortcuts.pushBranch,
                    statusTip=self.tr("Upload your commits to the remote server")),

                ActionDef(self.tr("&Upstream Branch"), submenu=self.makeUpstreamSubmenu(repo, branchName, upstreamBranchName)),

                ActionDef.SEPARATOR,

                TaskBook.action(self, RenameBranch, self.tr("Re&name..."), taskArgs=branchName),

                TaskBook.action(self, DeleteBranch, self.tr("&Delete..."), taskArgs=branchName),

                ActionDef.SEPARATOR,

                TaskBook.action(self, NewBranchFromRef, self.tr("New &Branch from Here..."), taskArgs=refName),

                ActionDef.SEPARATOR,

                ActionDef(
                    self.tr("&Hide in Graph"),
                    lambda: self.wantHideNode(node),
                    checkState=[-1, 1][isHidden],
                    statusTip=self.tr("Hide this branch from the graph (effective if no other branches/tags point here)"),
                ),
            ]

        elif item == EItem.DetachedHead:
            actions += [TaskBook.action(self, NewBranchFromHead, self.tr("New &Branch from Here...")), ]

        elif item == EItem.RemoteBranch:
            activeBranchName = repo.head_branch_shorthand

            refName = data
            prefix, shorthand = RefPrefix.split(refName)
            assert prefix == RefPrefix.REMOTES
            thisBranchDisplay = lquoe(shorthand)
            activeBranchDisplay = lquoe(activeBranchName)

            remoteName, remoteBranchName = porcelain.split_remote_branch_shorthand(shorthand)
            remoteUrl = self.sidebarModel.repo.remotes[remoteName].url
            webUrl, webHost = WebHost.makeLink(remoteUrl, shorthand)
            webActions = []
            if webUrl:
                webActions = [
                    ActionDef(
                        self.tr("Visit Web Page on {0}...").format(escamp(webHost)),
                        lambda: QDesktopServices.openUrl(QUrl(webUrl)),
                        icon="internet-web-browser",
                    ),
                    ActionDef.SEPARATOR,
                ]

            actions += [
                TaskBook.action(self,
                    NewBranchFromRef,
                    self.tr("Start Local &Branch from Here..."),
                    taskArgs=refName,
                ),

                TaskBook.action(self, FetchRemoteBranch, self.tr("&Fetch New Commits..."), taskArgs=shorthand),

                ActionDef.SEPARATOR,

                TaskBook.action(self,
                    MergeBranch,
                    self.tr("&Merge into {0}...").format(activeBranchDisplay),
                    taskArgs=refName,
                ),

                ActionDef.SEPARATOR,

                TaskBook.action(self, RenameRemoteBranch, self.tr("Rename branch on remote..."), taskArgs=shorthand),

                TaskBook.action(self, DeleteRemoteBranch, self.tr("Delete branch on remote..."), taskArgs=shorthand),

                ActionDef.SEPARATOR,

                *webActions,

                ActionDef(self.tr("&Hide in Graph"),
                          lambda: self.wantHideNode(node),
                          checkState=[-1, 1][isHidden]),
            ]

        elif item == EItem.Remote:
            remoteUrl = model.repo.remotes[data].url
            webUrl, webHost = WebHost.makeLink(remoteUrl)

            webActions = []
            if webUrl:
                webActions = [
                    ActionDef(
                        self.tr("Visit Web Page on {0}...").format(escamp(webHost)),
                        lambda: QDesktopServices.openUrl(QUrl(webUrl)),
                        icon="internet-web-browser",
                    ),
                ]

            actions += [
                TaskBook.action(self, EditRemote, self.tr("&Edit Remote..."), taskArgs=data),

                TaskBook.action(self, FetchRemote, self.tr("&Fetch All Remote Branches..."), taskArgs=data),

                ActionDef.SEPARATOR,

                TaskBook.action(self, DeleteRemote, self.tr("&Remove Remote..."), taskArgs=data),

                ActionDef.SEPARATOR,

                *webActions,

                ActionDef(self.tr("Copy Remote &URL"),
                          lambda: self.copyToClipboard(remoteUrl)),

                ActionDef.SEPARATOR,

                ActionDef(self.tr("&Hide Remote in Graph"),
                          lambda: self.wantHideNode(node),
                          checkState=[-1, 1][isHidden]),
            ]

        elif item == EItem.RemotesHeader:
            actions += [
                TaskBook.action(self, NewRemote, "&A"),
            ]

        elif item == EItem.RefFolder:
            actions += [
                ActionDef(self.tr("&Hide Group in Graph"),
                          lambda: self.wantHideNode(node),
                          checkState=[-1, 1][isHidden]),
            ]

        elif item == EItem.StashesHeader:
            actions += [
                TaskBook.action(self, NewStash, "&S"),
                ActionDef.SEPARATOR,
                ActionDef(self.tr("&Hide All Stashes in Graph"),
                          lambda: self.wantHideNode(node),
                          checkState=[-1, 1][isHidden]),
            ]

        elif item == EItem.Stash:
            oid = Oid(hex=data)

            actions += [
                TaskBook.action(self, ApplyStash, self.tr("&Apply"), taskArgs=oid),

                TaskBook.action(self, ExportStashAsPatch, self.tr("E&xport As Patch..."), taskArgs=oid),

                ActionDef.SEPARATOR,

                TaskBook.action(self, DropStash, self.tr("&Delete"), taskArgs=oid),

                ActionDef.SEPARATOR,

                ActionDef(self.tr("&Hide in Graph"),
                          lambda: self.wantHideNode(node),
                          checkState=[-1, 1][isHidden],
                          statusTip=self.tr("Hide this stash from the graph")),
            ]

        elif item == EItem.TagsHeader:
            actions += [
                TaskBook.action(self, NewTag, self.tr("&New Tag on HEAD Commit...")),
            ]

        elif item == EItem.Tag:
            prefix, shorthand = RefPrefix.split(data)
            assert prefix == RefPrefix.TAGS

            actions += [
                TaskBook.action(self, DeleteTag, self.tr("&Delete Tag"), taskArgs=shorthand),
            ]

        elif item == EItem.Submodule:
            model = self.sidebarModel
            repo = model.repo

            actions += [
                ActionDef(self.tr("&Open Submodule in New Tab"),
                          lambda: self.openSubmoduleRepo.emit(data)),

                ActionDef(self.tr("Open Submodule &Folder"),
                          lambda: self.openSubmoduleFolder.emit(data)),

                ActionDef(self.tr("Copy &Path"),
                          lambda: self.copyToClipboard(repo.in_workdir(data))),

                ActionDef.SEPARATOR,

                TaskBook.action(self, UpdateSubmodule, taskArgs=[data]),
                TaskBook.action(self, UpdateSubmodule, name="Init && " + TaskBook.autoActionName(UpdateSubmodule), taskArgs=[data, True]),

                ActionDef.SEPARATOR,

                TaskBook.action(self, RemoveSubmodule, taskArgs=data),
            ]

        # --------------------

        ActionDef.addToQMenu(menu, *actions)

        return menu

    def onCustomContextMenuRequested(self, localPoint: QPoint):
        globalPoint = self.mapToGlobal(localPoint)
        index: QModelIndex = self.indexAt(localPoint)
        if index.isValid():
            node = SidebarNode.fromIndex(index)
            menu = self.makeNodeMenu(node, index=index)
            if menu.actions():
                menu.exec(globalPoint)
            menu.deleteLater()

    def refresh(self, repoState: RepoState):
        try:
            oldSelectedNode = SidebarNode.fromIndex(self.selectedIndexes()[0])
        except IndexError:
            oldSelectedNode = None

        self.sidebarModel.rebuild(repoState)
        self.restoreExpandedItems()

        # Restore selected node, if any
        if oldSelectedNode is not None:
            with suppress(StopIteration), QSignalBlockerContext(self, skipAlreadyBlocked=True):
                newNode = self.findNode(oldSelectedNode.isSimilarEnoughTo)
                restoreIndex = newNode.createIndex(self.sidebarModel)
                self.setCurrentIndex(restoreIndex)

    def refreshPrefs(self):
        self.setVerticalScrollMode(settings.prefs.listViewScrollMode)

    def wantSelectNode(self, node: SidebarNode):
        if self.signalsBlocked():  # Don't bother with the jump if our signals are blocked
            return

        if node is None:
            return

        assert isinstance(node, SidebarNode)

        item = node.kind

        if item == EItem.UncommittedChanges:
            locator = NavLocator.inWorkdir()
        elif item == EItem.UnbornHead:
            locator = NavLocator.inWorkdir()
        elif item == EItem.DetachedHead:
            locator = NavLocator.inRef("HEAD")
        elif item == EItem.LocalBranch:
            locator = NavLocator.inRef(node.data)
        elif item == EItem.RemoteBranch:
            locator = NavLocator.inRef(node.data)
        elif item == EItem.Tag:
            locator = NavLocator.inRef(node.data)
        elif item == EItem.Stash:
            locator = NavLocator.inCommit(Oid(hex=node.data))
        else:
            return None

        Jump.invoke(self, locator)

    def wantEnterNode(self, node: SidebarNode):
        if node is None:
            QApplication.beep()
            return
        assert isinstance(node, SidebarNode)

        item = node.kind

        if item == EItem.Spacer:
            pass

        elif item == EItem.LocalBranch:
            SwitchBranch.invoke(self, node.data.removeprefix(RefPrefix.HEADS), True)  # True: ask for confirmation

        elif item == EItem.Remote:
            EditRemote.invoke(self, node.data)

        elif item == EItem.RemotesHeader:
            NewRemote.invoke(self)

        elif item == EItem.LocalBranchesHeader:
            NewBranchFromHead.invoke(self)

        elif item == EItem.UncommittedChanges:
            NewCommit.invoke(self)

        elif item == EItem.Submodule:
            self.openSubmoduleRepo.emit(node.data)

        elif item == EItem.StashesHeader:
            NewStash.invoke(self)

        elif item == EItem.Stash:
            oid = Oid(hex=node.data)
            ApplyStash.invoke(self, oid)

        elif item == EItem.RemoteBranch:
            NewBranchFromRef.invoke(self, node.data)

        elif item == EItem.DetachedHead:
            NewBranchFromHead.invoke(self)

        elif item == EItem.TagsHeader:
            NewTag.invoke(self)

        else:
            QApplication.beep()

    def wantDeleteNode(self, node: SidebarNode):
        if node is None:
            QApplication.beep()
            return

        item = node.kind
        data = node.data

        if item == EItem.Spacer:
            pass

        elif item == EItem.LocalBranch:
            DeleteBranch.invoke(self, data.removeprefix(RefPrefix.HEADS))

        elif item == EItem.Remote:
            DeleteRemote.invoke(self, data)

        elif item == EItem.Stash:
            oid = Oid(hex=data)
            DropStash.invoke(self, oid)

        elif item == EItem.RemoteBranch:
            DeleteRemoteBranch.invoke(self, data)

        elif item == EItem.Tag:
            DeleteTag.invoke(self, data.removeprefix(RefPrefix.TAGS))

        else:
            QApplication.beep()

    def wantRenameNode(self, node: SidebarNode):
        if node is None:
            QApplication.beep()
            return

        item = node.kind
        data = node.data

        if item == EItem.Spacer:
            pass

        elif item == EItem.LocalBranch:
            prefix, name = RefPrefix.split(data)
            assert prefix == RefPrefix.HEADS
            RenameBranch.invoke(self, name)

        elif item == EItem.Remote:
            EditRemote.invoke(self, data)

        elif item == EItem.RemoteBranch:
            RenameRemoteBranch.invoke(self, data)

        else:
            QApplication.beep()

    def wantHideNode(self, node: SidebarNode):
        if node is None:
            return

        item = node.kind
        data = node.data

        if item == EItem.Spacer:
            pass

        elif item in [EItem.LocalBranch, EItem.RemoteBranch]:
            self.toggleHideRefPattern.emit(data)
            self.repaint()

        elif item == EItem.Remote:
            self.toggleHideRefPattern.emit(f"{RefPrefix.REMOTES}{data}/")
            self.repaint()

        elif item == EItem.Stash:
            oid = Oid(hex=data)
            self.toggleHideStash.emit(oid)
            self.repaint()

        elif item == EItem.StashesHeader:
            self.toggleHideAllStashes.emit()
            self.repaint()

        elif item == EItem.RefFolder:
            self.toggleHideRefPattern.emit(f"{data}/")
            self.repaint()

        else:
            QApplication.beep()

    def keyPressEvent(self, event: QKeyEvent):
        k = event.key()

        def getValidNode():
            try:
                index: QModelIndex = self.selectedIndexes()[0]
                return SidebarNode.fromIndex(index)
            except IndexError:
                return None

        if k in [Qt.Key.Key_Return, Qt.Key.Key_Enter]:
            self.wantEnterNode(getValidNode())

        elif k in [Qt.Key.Key_Delete]:
            self.wantDeleteNode(getValidNode())

        elif k in [Qt.Key.Key_F2]:
            self.wantRenameNode(getValidNode())

        else:
            super().keyPressEvent(event)

    def selectionChanged(self, selected: QItemSelection, deselected: QItemSelection):
        super().selectionChanged(selected, deselected)

        if selected.count() == 0:
            return

        current = selected.indexes()[0]
        if not current.isValid():
            return

        self.wantSelectNode(SidebarNode.fromIndex(current))

    def resolveClick(self, pos: QPoint) -> tuple[QModelIndex, SidebarNode, SidebarClickZone]:
        index = self.indexAt(pos)
        if index.isValid():
            rect = self.visualRect(index)
            node = SidebarNode.fromIndex(index)
            zone = SidebarDelegate.getClickZone(node, rect, pos.x())
        else:
            node = None
            zone = SidebarClickZone.Invalid
        return index, node, zone

    def mouseMoveEvent(self, event):
        """
        Bypass QTreeView's mouseMoveEvent to prevent the original branch
        expand indicator from taking over mouse input.
        """
        QAbstractItemView.mouseMoveEvent(self, event)

    def mousePressEvent(self, event: QMouseEvent):
        index, node, zone = self.resolveClick(event.pos())

        # Save click info for mouseReleaseEvent
        self.mousePressCache = (index.row(), zone)

        if zone == SidebarClickZone.Invalid:
            super().mousePressEvent(event)
            return

        if zone == SidebarClickZone.Select:
            self.setCurrentIndex(index)

        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        index, node, zone = self.resolveClick(event.pos())

        # Only perform special actions (hide, expand) if mouse was released
        # on same row/zone as mousePressEvent
        match = (index.row(), zone) == self.mousePressCache

        # Clear mouse press info on release
        self.mousePressCache = INVALID_MOUSEPRESS

        if not match or zone in [SidebarClickZone.Invalid, SidebarClickZone.Select]:
            super().mouseReleaseEvent(event)
            return
        elif zone == SidebarClickZone.Hide:
            self.wantHideNode(node)
            event.accept()
        elif zone == SidebarClickZone.Expand:
            # Toggle expanded state
            if node.mayHaveChildren():
                if self.isExpanded(index):
                    self.collapse(index)
                else:
                    self.expand(index)
            event.accept()
        else:
            logger.warning(f"Unknown click zone {zone}")

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        # NOT calling "super().mouseDoubleClickEvent(event)" on purpose.

        index, node, zone = self.resolveClick(event.pos())

        # Let user collapse/expand/hide a single node in quick succession
        # without triggering a double click
        if zone not in [SidebarClickZone.Invalid, SidebarClickZone.Select]:
            self.mousePressEvent(event)
            return

        if event.button() == Qt.MouseButton.LeftButton and node is not None:
            self.wantEnterNode(node)

    def walk(self):
        return self.sidebarModel.rootNode.walk()

    def findNode(self, predicate: Callable[[SidebarNode], bool]) -> SidebarNode:
        return next(node for node in self.walk() if predicate(node))

    def findNodesByKind(self, kind: EItem) -> Iterable[SidebarNode]:
        return (node for node in self.walk() if node.kind == kind)

    def findNodeByRef(self, ref: str) -> SidebarNode:
        return self.sidebarModel.nodesByRef[ref]

    def indexForRef(self, ref: str) -> QModelIndex | None:
        model = self.sidebarModel
        try:
            node = model.nodesByRef[ref]
            return node.createIndex(model)
        except KeyError:
            return None

    def selectNode(self, node: SidebarNode) -> QModelIndex:
        index = node.createIndex(self.sidebarModel)
        self.setCurrentIndex(index)
        return index

    def selectAnyRef(self, *refCandidates: str) -> QModelIndex | None:
        # Early out if any candidate ref is already selected
        with suppress(IndexError):
            index = self.selectedIndexes()[0]
            if index and index.data(ROLE_REF) in refCandidates:
                return index

        # If several refs point to the same commit, attempt to select
        # the checked-out branch first (or its upstream, if any)
        model = self.sidebarModel
        if model._checkedOut:
            favorRefs = [RefPrefix.HEADS + model._checkedOut,
                         RefPrefix.REMOTES + model._checkedOutUpstream]
            refCandidates = sorted(refCandidates, key=favorRefs.__contains__, reverse=True)

        # Find a visible index that matches any of the candidates
        for ref in refCandidates:
            index = self.indexForRef(ref)
            if index and self.isAncestryChainExpanded(index):  # Don't force-expand any collapsed indexes
                self.setCurrentIndex(index)
                return index

        # There are no indices that match the candidates, so select nothing
        self.clearSelection()
        return None

    def onExpanded(self, index: QModelIndex):
        node = SidebarNode.fromIndex(index)
        h = node.getCollapseHash()
        self.collapseCache.discard(h)

    def onCollapsed(self, index: QModelIndex):
        node = SidebarNode.fromIndex(index)
        h = node.getCollapseHash()
        self.collapseCache.add(h)

    @benchmark
    def restoreExpandedItems(self):
        # If we don't have a valid collapse cache (typically upon opening the repo), expand everything.
        # This can be pretty expensive, so cache collapsed items for next time.
        if not self.collapseCacheValid:
            self.expandAll()
            self.collapseCache.clear()
            self.collapseCacheValid = True
            return

        assert self.collapseCacheValid
        model = self.sidebarModel

        frontier = model.rootNode.children[:]
        while frontier:
            node = frontier.pop()

            if not node.mayHaveChildren():
                continue

            if node.wantForceExpand() or node.getCollapseHash() not in self.collapseCache:
                index = node.createIndex(model)
                self.expand(index)

            frontier.extend(node.children)

    def isAncestryChainExpanded(self, index: QModelIndex):
        # Assume everything is expanded if collapse cache is missing (see restoreExpandedItems).
        if not self.collapseCacheValid:
            return True

        # My collapsed state doesn't matter here - it only affects my children.
        # So start looking at my parent.
        node = SidebarNode.fromIndex(index)
        node = node.parent

        # Walk up parent chain until root index (row -1)
        while node.parent is not None:
            h = node.getCollapseHash()
            if h in self.collapseCache:
                return False
            node = node.parent

        return True

    def copyToClipboard(self, text: str):
        QApplication.clipboard().setText(text)
        self.statusMessage.emit(clipboardStatusMessage(text))

    def makeUpstreamSubmenu(self, repo, lbName, ubName) -> list:
        RADIO_GROUP = "UpstreamSelection"

        menu = [
            ActionDef(
                self.tr("Stop tracking upstream branch") if ubName else self.tr("Not tracking any upstream branch"),
                lambda: EditUpstreamBranch.invoke(self, lbName, ""),
                checkState=1 if not ubName else -1,
                radioGroup=RADIO_GROUP),
        ]

        for remoteName, remoteBranches in repo.listall_remote_branches(value_style="shorthand").items():
            if not remoteBranches:
                continue
            menu.append(None)  # separator
            for rbShorthand in remoteBranches:
                menu.append(ActionDef(
                    escamp(rbShorthand),
                    lambda rb=rbShorthand: EditUpstreamBranch.invoke(self, lbName, rb),
                    checkState=1 if ubName == rbShorthand else -1,
                    radioGroup=RADIO_GROUP))

        if len(menu) <= 1:
            if not repo.remotes:
                explainer = self.tr("No remotes.")
            else:
                explainer = self.tr("No remote branches found. Try fetching the remotes.")
            menu.append(None)  # separator
            menu.append(ActionDef(explainer, enabled=False))

        return menu
