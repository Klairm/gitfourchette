from gitfourchette import settings
from gitfourchette.filelists.filelist import FileList
from gitfourchette.globalshortcuts import GlobalShortcuts
from gitfourchette.nav import NavContext
from gitfourchette.porcelain import *
from gitfourchette.qt import *
from gitfourchette.toolbox import *
from gitfourchette.tasks import *


class StagedFiles(FileList):
    def __init__(self, parent):
        super().__init__(parent, NavContext.STAGED)

        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def createContextMenuActions(self, patches: list[Patch]) -> list[ActionDef]:
        n = len(patches)

        return [
            ActionDef(
                self.tr("&Unstage %n File(s)", "", n),
                self.unstage,
                icon="list-remove",  # QStyle.StandardPixmap.SP_ArrowUp,
                shortcuts=makeMultiShortcut(GlobalShortcuts.discardHotkeys),
            ),

            ActionDef(
                self.tr("Stas&h Changes..."),
                self.wantPartialStash,
                shortcuts=TaskBook.shortcuts.get(NewStash, []),
                icon="vcs-stash",
            ),

            self.revertModeActionDef(n, self.unstageModeChange),

            ActionDef.SEPARATOR,

            ActionDef(
                self.tr("Compare in {0}").format(settings.getDiffToolName()),
                self.wantOpenInDiffTool,
                icon="vcs-diff",
            ),

            ActionDef(
                self.tr("E&xport Diff(s) As Patch...", "", n),
                self.savePatchAs,
            ),

            ActionDef.SEPARATOR,

            ActionDef(
                self.tr("&Edit in {0}").format(settings.getExternalEditorName()),
                self.openWorkdirFile,
                icon=QStyle.StandardPixmap.SP_FileIcon,
            ),

            ActionDef(
                self.tr("Edit &HEAD Version(s) in {0}", "", n).format(settings.getExternalEditorName()),
                self.openHeadRevision,
            ),

            ActionDef.SEPARATOR,

            ActionDef(
                self.tr("Open &Folder(s)", "", n),
                self.showInFolder,
                icon=QStyle.StandardPixmap.SP_DirIcon,
            ),

            ActionDef(
                self.tr("&Copy Path(s)", "", n),
                self.copyPaths,
                shortcuts=GlobalShortcuts.copy
            ),

            self.pathDisplayStyleSubmenu(),
        ] + super().createContextMenuActions(n)

    def keyPressEvent(self, event: QKeyEvent):
        k = event.key()
        if k in GlobalShortcuts.stageHotkeys + GlobalShortcuts.discardHotkeys:
            self.unstage()
        else:
            super().keyPressEvent(event)

    def unstage(self):
        patches = list(self.selectedPatches())
        UnstageFiles.invoke(self, patches)

    def unstageModeChange(self):
        patches = list(self.selectedPatches())
        UnstageModeChanges.invoke(self, patches)
