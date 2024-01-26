import filecmp
import logging
import os
import shutil
import tempfile

from gitfourchette import tempdir
from gitfourchette.porcelain import *
from gitfourchette.qt import *
from gitfourchette.toolbox import *
from gitfourchette.exttools import openInMergeTool, PREFKEY_MERGETOOL


logger = logging.getLogger(__name__)


class UnmergedConflict(QObject):
    mergeFailed = Signal(int)
    mergeComplete = Signal()

    process: QProcess | None

    def __init__(self, parent: QWidget, repo: Repo, conflict: DiffConflict):
        super().__init__(parent)

        self.conflict = conflict
        self.repo = repo

        # Keep mergeDir around so the temp dir doesn't vanish
        self.mergeDir = tempfile.TemporaryDirectory(dir=tempdir.getSessionTemporaryDirectory(), prefix="merge-", ignore_cleanup_errors=True)
        mergeDirPath = self.mergeDir.name

        self.ancestorPath = dumpTempBlob(repo, mergeDirPath, conflict.ancestor, "ANCESTOR")
        self.oursPath = dumpTempBlob(repo, mergeDirPath, conflict.ours, "OURS")
        self.theirsPath = dumpTempBlob(repo, mergeDirPath, conflict.theirs, "THEIRS")
        self.scratchPath = os.path.join(mergeDirPath, "[MERGED]" + os.path.basename(conflict.ours.path))

        # Make sure the output path exists so the FSW can begin watching it
        shutil.copyfile(repo.in_workdir(self.conflict.ours.path), self.scratchPath)

        self.process = None

        self.mergeFailed.connect(lambda: self.deleteLater())

    def startProcess(self):
        self.process = openInMergeTool(self.parent(), self.ancestorPath, self.oursPath, self.theirsPath, self.scratchPath)
        if self.process:
            self.process.errorOccurred.connect(lambda: self.mergeFailed.emit(-1))
            self.process.finished.connect(self.onMergeProcessFinished)

    def onMergeProcessFinished(self, exitCode: int, exitStatus: QProcess.ExitStatus):
        logger.info(f"Merge tool exited with code {exitCode}, {exitStatus}")

        if exitCode != 0 or exitStatus == QProcess.ExitStatus.CrashExit:
            logger.warning(f"Process returned {exitCode}")
            self.mergeFailed.emit(exitCode)
            return

        # If output file still contains original contents,
        # the merge tool probably hasn't done anything
        if filecmp.cmp(self.scratchPath, self.repo.in_workdir(self.conflict.ours.path)):
            self.mergeFailed.emit(exitCode)
            return

        message = paragraphs(
            self.tr("It looks like you’ve resolved the merge conflict in {0}."),
            self.tr("Do you want to keep this resolution?")
        ).format(bquo(self.conflict.ours.path))

        qmb = asyncMessageBox(self.parent(), 'information', self.tr("Merge conflict resolved"), message,
                              QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        qmb.button(QMessageBox.StandardButton.Ok).setText(self.tr("Confirm resolution"))
        qmb.button(QMessageBox.StandardButton.Cancel).setText(self.tr("Discard resolution"))
        qmb.accepted.connect(self.mergeComplete)
        qmb.rejected.connect(lambda: self.mergeFailed.emit(-1))
        qmb.show()
