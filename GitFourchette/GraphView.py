from PySide2.QtCore import *
from PySide2.QtGui import *
from PySide2.QtWidgets import *
import git
import html

from GraphDelegate import GraphDelegate
import settings


class GraphView(QListView):
    uncommittedChangesClicked = Signal()
    emptyClicked = Signal()
    commitClicked = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.repoWidget = parent
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)  # sinon on peut double-cliquer pour éditer les lignes...
        self.setItemDelegate(GraphDelegate())

    def _replaceModel(self, model):
        if self.model():
            self.model().deleteLater()  # avoid memory leak
        self.setModel(model)

    def fill(self, orderedCommitMetadata):
        model = QStandardItemModel(self)  # creating a model from scratch seems faster than clearing an existing one
        model.appendRow(QStandardItem("Uncommitted Changes"))
        model.insertRows(1, len(orderedCommitMetadata))
        for i, meta in enumerate(orderedCommitMetadata):
            model.setData(model.index(1 + i, 0), meta, Qt.DisplayRole)
        self._replaceModel(model)
        self.onSetCurrent()

    def patchFill(self, trimStartRows, orderedCommitMetadata):
        model = self.model()
        model.removeRows(1, trimStartRows)
        model.insertRows(1, len(orderedCommitMetadata))
        for i, meta in enumerate(orderedCommitMetadata):
            model.setData(model.index(1 + i, 0), meta, Qt.DisplayRole)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if not self.currentIndex().isValid():
            return

        repo: git.Repo = self.repoWidget.state.repo
        commit: git.Commit = repo.commit(self.currentIndex().data().hexsha)

        msg = commit.message.strip()
        msg = html.escape(msg)
        msg = msg.replace('\n', '<br>')  # Qt 5.15 does support 'pre-wrap',
        # but it inserts too many blank lines when there are 2 consecutive line breaks.

        authorMarkup = F"{html.escape(commit.author.name)} &lt;{html.escape(commit.author.email)}&gt;" + \
            F"<br>{html.escape(commit.authored_datetime.strftime(settings.prefs.longTimeFormat))}"

        if (commit.author.email == commit.committer.email
                and commit.author.name == commit.committer.name
                and commit.authored_datetime == commit.committed_datetime):
            committerMarkup = F"<i>(same as author)</i>"
        else:
            committerMarkup = F"{html.escape(commit.committer.name)} &lt;{html.escape(commit.committer.email)}&gt;" + \
                F"<br>{html.escape(commit.committed_datetime.strftime(settings.prefs.longTimeFormat))}"

        markup = F"""<p style='font-size: large'>{msg}</p>
            <br>
            <table>
            <tr><td><b>SHA </b></td><td>{commit.hexsha}</td></tr>
            <tr><td><b>Author </b></td><td>{authorMarkup}</td></tr>
            <tr><td><b>Committer </b></td><td>{committerMarkup}</td></tr>
            </table>"""
        QMessageBox.information(self, F"Commit info {commit.hexsha[:7]}", markup)

    def selectionChanged(self, selected: QItemSelection, deselected: QItemSelection):
        # do standard callback, such as scrolling the viewport if reaching the edges, etc.
        super().selectionChanged(selected, deselected)

        if len(selected.indexes()) == 0:
            self.onSetCurrent(None)
        else:
            self.onSetCurrent(selected.indexes()[0])

    def onSetCurrent(self, current=None):
        if current is None or not current.isValid():
            self.emptyClicked.emit()
        elif current.row() == 0:  # uncommitted changes
            self.uncommittedChangesClicked.emit()
        else:
            self.commitClicked.emit(current.data().hexsha)
