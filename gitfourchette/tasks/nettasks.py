"""
Remote access tasks.
"""

from gitfourchette import porcelain
from gitfourchette import util
from gitfourchette.qt import *
from gitfourchette.tasks.repotask import RepoTask, TaskEffects
from gitfourchette.widgets.brandeddialog import showTextInputDialog
from gitfourchette.widgets.remotelinkprogressdialog import RemoteLinkProgressDialog
from html import escape
import contextlib


class _BaseNetTask(RepoTask):
    remoteLinkDialog: RemoteLinkProgressDialog | None

    def __init__(self, parent):
        super().__init__(parent)
        self.remoteLinkDialog = None

    def effects(self) -> TaskEffects:
        return TaskEffects.Remotes

    def _showRemoteLinkDialog(self):
        assert not self.remoteLinkDialog
        assert util.onAppThread()
        self.remoteLinkDialog = RemoteLinkProgressDialog(self.parentWidget())

    def _closeRemoteLinkDialog(self):
        assert util.onAppThread()
        if self.remoteLinkDialog:
            self.remoteLinkDialog.close()
            self.remoteLinkDialog.deleteLater()
            self.remoteLinkDialog = None

    def onError(self, exc):
        self._closeRemoteLinkDialog()
        super().onError(exc)

    @property
    def remoteLink(self):
        return self.remoteLinkDialog.remoteLink


class DeleteRemoteBranch(_BaseNetTask):
    def name(self):
        return translate("Operation", "Delete branch on remote")

    def flow(self, remoteBranchShorthand: str):
        assert not remoteBranchShorthand.startswith(porcelain.REMOTES_PREFIX)

        remoteName, _ = porcelain.splitRemoteBranchShorthand(remoteBranchShorthand)

        text = util.paragraphs(
            self.tr("Really delete branch <b>“{0}”</b> "
                    "from the remote repository?").format(escape(remoteBranchShorthand)),
            self.tr("The remote branch will disappear for all users of remote “{0}”.").format(escape(remoteName))
            + " " + translate("Global", "This cannot be undone!"))
        verb = self.tr("Delete on remote")
        yield from self._flowConfirm(text=text, verb=verb, buttonIcon=QStyle.StandardPixmap.SP_DialogDiscardButton)

        self._showRemoteLinkDialog()

        yield from self._flowBeginWorkerThread()
        self.remoteLink.discoverKeyFiles(self.repo.remotes[remoteName])
        porcelain.deleteRemoteBranch(self.repo, remoteBranchShorthand, self.remoteLink)

        yield from self._flowExitWorkerThread()
        self._closeRemoteLinkDialog()


class RenameRemoteBranch(_BaseNetTask):
    def name(self):
        return translate("Operation", "Rename branch on remote")

    def flow(self, remoteBranchName: str):
        assert not remoteBranchName.startswith(porcelain.REMOTES_PREFIX)
        remoteName, branchName = porcelain.splitRemoteBranchShorthand(remoteBranchName)
        newBranchName = branchName  # naked name, NOT prefixed with the name of the remote

        reservedNames = porcelain.getRemoteBranchNames(self.repo).get(remoteName, [])
        with contextlib.suppress(ValueError):
            reservedNames.remove(branchName)
        nameTaken = self.tr("This name is already taken by another branch on this remote.")

        dlg = showTextInputDialog(
            self.parentWidget(),
            self.tr("Rename remote branch “{0}”").format(escape(remoteBranchName)),
            self.tr("Enter new name:"),
            newBranchName,
            okButtonText=self.tr("Rename on remote"),
            validate=lambda name: util.validateRefName(name, reservedNames, nameTaken),
            deleteOnClose=False)

        yield from self._flowDialog(dlg)
        dlg.deleteLater()

        # Naked name, NOT prefixed with the name of the remote
        newBranchName = dlg.lineEdit.text()

        self._showRemoteLinkDialog()

        yield from self._flowBeginWorkerThread()
        self.remoteLink.discoverKeyFiles(self.repo.remotes[remoteName])
        porcelain.renameRemoteBranch(self.repo, remoteBranchName, newBranchName, self.remoteLink)

        yield from self._flowExitWorkerThread()
        self._closeRemoteLinkDialog()


class FetchRemote(_BaseNetTask):
    def name(self):
        return translate("Operation", "Fetch remote")

    def flow(self, remoteName: str):
        self._showRemoteLinkDialog()

        yield from self._flowBeginWorkerThread()
        self.remoteLink.discoverKeyFiles(self.repo.remotes[remoteName])
        porcelain.fetchRemote(self.repo, remoteName, self.remoteLink)

        yield from self._flowExitWorkerThread()
        self._closeRemoteLinkDialog()


class FetchRemoteBranch(_BaseNetTask):
    def name(self):
        return translate("Operation", "Fetch remote branch")

    def flow(self, remoteBranchName: str):
        self._showRemoteLinkDialog()

        yield from self._flowBeginWorkerThread()

        remoteName, _ = porcelain.splitRemoteBranchShorthand(remoteBranchName)
        self.remoteLink.discoverKeyFiles(self.repo.remotes[remoteName])
        porcelain.fetchRemoteBranch(self.repo, remoteBranchName, self.remoteLink)

        yield from self._flowExitWorkerThread()
        self._closeRemoteLinkDialog()
