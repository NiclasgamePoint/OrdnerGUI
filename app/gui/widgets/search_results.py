from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAbstractTextDocumentLayout, QPainter, QPalette, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.widgets.buttons import AppButton


class HighlightDelegate(QStyledItemDelegate):
    """Draw list item text with every search token highlighted."""

    QUERY_ROLE = Qt.UserRole + 20

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        text = str(index.data(Qt.DisplayRole) or "")
        query = str(index.data(self.QUERY_ROLE) or "").strip()
        if not query:
            super().paint(painter, option, index)
            return

        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        style = styled.widget.style() if styled.widget else None
        if style is None:
            super().paint(painter, option, index)
            return
        styled.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, styled, painter, styled.widget)

        escaped = html.escape(text)
        for token in sorted(set(re.findall(r"\w+", query)), key=len, reverse=True):
            escaped = re.sub(
                re.escape(html.escape(token)),
                lambda match: f"<b>{match.group(0)}</b>",
                escaped,
                flags=re.IGNORECASE,
            )
        document = QTextDocument()
        color = styled.palette.color(
            QPalette.HighlightedText if option.state & QStyle.State_Selected else QPalette.Text
        ).name()
        document.setDefaultStyleSheet(f"body {{ color: {color}; }}")
        document.setHtml(escaped)
        document.setTextWidth(max(1, styled.rect.width() - 14))
        painter.save()
        painter.translate(styled.rect.left() + 7, styled.rect.top() + 3)
        context = QAbstractTextDocumentLayout.PaintContext()
        document.documentLayout().draw(painter, context)
        painter.restore()


class SearchResultSection(QWidget):
    pageRequested = Signal(int)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.base_title = title
        self.current_page = 1
        self.page_count = 1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.heading = QLabel(title)
        self.heading.setObjectName("SearchGroupTitle")
        layout.addWidget(self.heading)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("ResultList")
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.setItemDelegate(HighlightDelegate(self.list_widget))
        layout.addWidget(self.list_widget, 1)

        paging = QHBoxLayout()
        paging.setContentsMargins(0, 0, 0, 0)
        self.previous_button = AppButton("‹", AppButton.SECONDARY, minimum_width=30)
        self.next_button = AppButton("›", AppButton.SECONDARY, minimum_width=30)
        self.page_label = QLabel("Seite 1 / 1")
        self.page_label.setObjectName("StatCaption")
        self.previous_button.clicked.connect(lambda: self.pageRequested.emit(self.current_page - 1))
        self.next_button.clicked.connect(lambda: self.pageRequested.emit(self.current_page + 1))
        paging.addWidget(self.previous_button)
        paging.addWidget(self.page_label)
        paging.addWidget(self.next_button)
        paging.addStretch()
        layout.addLayout(paging)
        self.set_page(1, 1, 0)

    def set_page(self, page: int, page_count: int, total: int):
        self.current_page = max(1, page)
        self.page_count = max(1, page_count)
        self.heading.setText(f"{self.base_title} ({total})")
        self.page_label.setText(f"Seite {self.current_page} / {self.page_count}")
        self.previous_button.setEnabled(self.current_page > 1)
        self.next_button.setEnabled(self.current_page < self.page_count)
        self.page_label.setVisible(total > 0)
        self.previous_button.setVisible(self.page_count > 1)
        self.next_button.setVisible(self.page_count > 1)

    def reset_title(self):
        self.heading.setText(self.base_title)
        self.set_page(1, 1, 0)
