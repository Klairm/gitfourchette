from allqt import *
from datetime import datetime
from graphpaint import paintGraphFrame
from repostate import CommitMetadata, RepoState
from util import messageSummary
import colors
import settings


class GraphDelegate(QStyledItemDelegate):
    def __init__(self, repoWidget, parent=None):
        super().__init__(parent)
        self.repoWidget = repoWidget
        self.hashCharWidth = 0

    @property
    def state(self) -> RepoState:
        return self.repoWidget.state

    def paint(self, painter, option, index):
        hasFocus = option.state & QStyle.State_HasFocus
        isSelected = option.state & QStyle.State_Selected

        # Draw selection background _underneath_ the style's default graphics.
        # This is a workaround for the "windowsvista" style, which does not draw a solid color background for
        # selected items -- instead, it draws a very slight alpha overlay on _top_ of the item.
        # The problem is that its palette still returns white for foreground text, so the result would be unreadable
        # if we didn't draw a strong solid-color background. Most other styles draw their own background as a solid
        # color, so this rect is probably not visible outside of "windowsvista".
        if hasFocus and isSelected:
            painter.fillRect(option.rect, option.palette.color(QPalette.ColorRole.Highlight))

        outlineColor = option.palette.color(QPalette.ColorRole.Base)

        super().paint(painter, option, index)

        XMargin = 4
        ColW_Author = 16
        ColW_Hash = settings.prefs.shortHashChars + 1
        ColW_Date = 20

        painter.save()

        palette: QPalette = option.palette
        colorGroup = QPalette.ColorGroup.Normal if hasFocus else QPalette.ColorGroup.Inactive

        if isSelected:
            #if option.state & QStyle.State_HasFocus:
            #    painter.fillRect(option.rect, palette.color(pcg, QPalette.ColorRole.Highlight))
            painter.setPen(palette.color(colorGroup, QPalette.ColorRole.HighlightedText))

        rect = QRect(option.rect)
        rect.setLeft(rect.left() + XMargin)
        rect.setRight(rect.right() - XMargin)

        # Get metrics of '0' before setting a custom font,
        # so that alignments are consistent in all commits regardless of bold or italic.
        if self.hashCharWidth == 0:
            self.hashCharWidth = max(painter.fontMetrics().horizontalAdvance(c) for c in "0123456789abcdef")

        if index.row() > 0:
            meta: CommitMetadata = index.data()
            summaryText, contd = messageSummary(meta.body)
            hashText = meta.hexsha[:settings.prefs.shortHashChars]
            authorText = meta.authorEmail.split('@')[0]
            dateText = datetime.fromtimestamp(meta.authorTimestamp).strftime(settings.prefs.shortTimeFormat)
            if meta.bold:
                painter.setFont(settings.boldFont)
        else:
            meta: CommitMetadata = None
            summaryText = "Uncommitted Changes"
            hashText = "·" * settings.prefs.shortHashChars
            authorText = ""
            dateText = ""
            painter.setFont(settings.alternateFont)

        # Get metrics now so the message gets elided according to the custom font style
        # that may have been just set for this commit.
        metrics = painter.fontMetrics()

        # ------ Hash
        rect.setWidth(ColW_Hash * self.hashCharWidth)
        charRect = QRect(rect.left(), rect.top(), self.hashCharWidth, rect.height())
        painter.save()
        painter.setPen(palette.color(colorGroup, QPalette.ColorRole.PlaceholderText))
        for hashChar in hashText:
            painter.drawText(charRect, Qt.AlignCenter, hashChar)
            charRect.translate(self.hashCharWidth, 0)
        painter.restore()

        # ------ Graph
        rect.setLeft(rect.right())
        if meta is not None:
            paintGraphFrame(self.state, meta, painter, rect, outlineColor)

        # ------ Refs
        if meta is not None and meta.hexsha in self.state.refsByCommit:
            for refName, isTag in self.state.refsByCommit[meta.hexsha]:
                refColor = Qt.darkYellow if isTag else Qt.darkMagenta
                painter.save()
                painter.setFont(settings.smallFont)
                painter.setPen(refColor)
                rect.setLeft(rect.right())
                label = F"[{refName}] "
                rect.setWidth(settings.smallFontMetrics.horizontalAdvance(label) + 1)
                painter.drawText(rect, Qt.AlignVCenter, label)
                painter.restore()

        def elide(text):
            return metrics.elidedText(text, Qt.ElideRight, rect.width())

        # ------ message
        if meta and not meta.hasLocal:
            painter.setPen(QColor(Qt.gray))
        rect.setLeft(rect.right())
        rect.setRight(option.rect.right() - (ColW_Author + ColW_Date) * self.hashCharWidth - XMargin)
        painter.drawText(rect, Qt.AlignVCenter, elide(summaryText))

        # ------ Author
        rect.setLeft(rect.right())
        rect.setWidth(ColW_Author * self.hashCharWidth)
        painter.drawText(rect, Qt.AlignVCenter, elide(authorText))

        # ------ Date
        rect.setLeft(rect.right())
        rect.setWidth(ColW_Date * self.hashCharWidth)
        painter.drawText(rect, Qt.AlignVCenter, elide(dateText))

        # ------ Debug (show redrawn rows from last refresh)
        if settings.prefs.debug_showDirtyCommitsAfterRefresh and meta and meta.debugPrefix:
            rect = QRect(option.rect)
            rect.setLeft(rect.left() + XMargin + (ColW_Hash-3) * self.hashCharWidth)
            rect.setRight(rect.left() + 3*self.hashCharWidth)
            painter.fillRect(rect, colors.rainbow[meta.batchID % len(colors.rainbow)])
            painter.drawText(rect, Qt.AlignVCenter, "-"+meta.debugPrefix)

        # ----------------
        painter.restore()
        pass  # QStyledItemDelegate.paint(self, painter, option, index)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        r = super().sizeHint(option, index)
        r.setHeight(r.height() * settings.prefs.graph_rowHeightPercent / 100.0)
        return r
