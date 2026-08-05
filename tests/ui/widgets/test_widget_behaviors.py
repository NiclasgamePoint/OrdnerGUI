from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QIcon, QMouseEvent, QPixmap
from PySide6.QtWidgets import QSpacerItem

from app.core.search_models import SearchSort
from app.core.statistics import ApplicationStatistics
from app.gui.widgets.app_header import AppHeader
from app.gui.widgets.buttons import AppButton, BusyIndicator, CountBadgeButton
from app.gui.widgets.document_result_row import DocumentResultRow
from app.gui.widgets.index_status_bar import IndexStatusBar
from app.gui.widgets.result_row import ResultRow
from app.gui.widgets.search_filter_popup import SearchFilterPopup
from app.gui.widgets.statistics_widget import StatisticsWidget
from tests.base.qt_test_case import QtTestCase


class WidgetBehaviorTests(QtTestCase):
    def test_header_query_history_document_mode_and_filter_badge(self):
        header = AppHeader(["first"])
        header.set_query("  query  ")
        self.assertEqual(header.query(), "query")
        header.set_history(["second"])
        self.assertEqual(header.history_model.stringList(), ["second"])
        header.set_document_search_enabled(True)
        self.assertIn("Dokumentinhalte", header.search_input.placeholderText())
        header.set_filter_count(2)
        self.assertEqual(header.filter_button.text(), "Filter (2)")
        self.assertTrue(header.filter_button.property("filtersActive"))
        header.set_filter_count(0)
        self.assertEqual(header.filter_button.text(), "Filter")
        pixmap = QPixmap(2, 2)
        pixmap.fill(Qt.black)
        with patch("app.gui.widgets.app_header.QIcon.fromTheme", return_value=QIcon(pixmap)):
            icon_header = AppHeader()
        self.assertFalse(icon_header.settings_button.icon().isNull())

    def test_buttons_busy_badge_and_indicator_lifecycle(self):
        button = AppButton("Action", minimum_width=100)
        button.set_role(AppButton.DANGER)
        self.assertEqual(button.property("buttonRole"), AppButton.DANGER)
        button.set_busy(True)
        button.set_busy(True)
        self.assertFalse(button.isEnabled())
        button.set_busy(False)
        self.assertTrue(button.isEnabled())
        button.setEnabled(False)
        button.set_busy(True)
        button.set_busy(False)
        self.assertFalse(button.isEnabled())

        badge = CountBadgeButton("Items")
        badge.resize(120, 40)
        badge.set_count(-1)
        self.assertEqual(badge.count(), 0)
        badge.set_count(3)
        self.assertEqual(badge.count(), 3)
        self.assertTrue(badge.property("hasBadge"))

        indicator = BusyIndicator()
        indicator.start()
        self.assertTrue(indicator.is_running())
        first = indicator.text()
        indicator._advance()
        self.assertNotEqual(indicator.text(), first)
        indicator.stop()
        self.assertFalse(indicator.is_running())

    def test_document_and_result_rows_emit_actions(self):
        file_path = str(Path("/root/file.txt"))
        root_path = str(Path("/root"))
        document = DocumentResultRow("file.txt", "", file_path)
        files, paths = [], []
        document.openFileRequested.connect(files.append)
        document.openPathRequested.connect(paths.append)
        document._open_file()
        document._open_folder()
        self.assertEqual(files, [file_path])
        self.assertEqual(paths, [root_path])
        self.assertEqual(document.path, file_path)
        empty = DocumentResultRow("empty", "", "")
        empty.openFileRequested.connect(files.append)
        empty._open_file()
        empty._open_folder()

        left_event = Mock()
        left_event.button.return_value = Qt.LeftButton
        left_event.position.return_value.toPoint.return_value = document.rect().center()
        document.mouseReleaseEvent(left_event)
        self.assertEqual(files, ["/root/file.txt", "/root/file.txt"])
        left_event.accept.assert_called_once_with()

        right_release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(0, 0),
            Qt.RightButton,
            Qt.RightButton,
            Qt.NoModifier,
        )
        document.mouseReleaseEvent(right_release)

        result = ResultRow("Title", "", {"id": 1}, "/root")
        activated, opened = [], []
        result.activated.connect(activated.append)
        result.openPathRequested.connect(opened.append)
        self.assertEqual(result.payload, {"id": 1})
        self.assertEqual(result.path, "/root")
        result._open_path()
        self.assertEqual(opened, ["/root"])

        event = Mock()
        event.button.return_value = Qt.LeftButton
        event.position.return_value.toPoint.return_value = result.rect().center()
        result.mouseReleaseEvent(event)
        self.assertEqual(activated, [{"id": 1}])
        event.accept.assert_called_once_with()
        result.mouseReleaseEvent(right_release)

        empty_result = ResultRow("Empty", "", "payload")
        empty_result._open_path()

    def test_result_context_menu_routes_each_action(self):
        result = ResultRow("Title", "Subtitle", "payload", "/root")
        activated, opened = [], []
        result.activated.connect(activated.append)
        result.openPathRequested.connect(opened.append)
        event = Mock(spec=QContextMenuEvent)
        event.globalPos.return_value = QPoint()
        for selected_index in range(3):
            menu = Mock()
            actions = [Mock(), Mock(), Mock()]
            menu.addAction.side_effect = actions
            menu.exec.return_value = actions[selected_index]
            with patch("app.gui.widgets.result_row.QMenu", return_value=menu):
                result.contextMenuEvent(event)
        self.assertEqual(activated, ["payload"])
        self.assertEqual(opened, ["/root"])
        empty = ResultRow("Empty", "", "payload")
        for selected_index in (1, 2):
            menu = Mock()
            actions = [Mock(), Mock(), Mock()]
            menu.addAction.side_effect = actions
            menu.exec.return_value = actions[selected_index]
            with patch("app.gui.widgets.result_row.QMenu", return_value=menu):
                empty.contextMenuEvent(event)

    def test_status_bar_text_busy_and_click(self):
        status = IndexStatusBar()
        texts, busy, details = [], [], []
        status.textChanged.connect(texts.append)
        status.busyChanged.connect(busy.append)
        status.detailsRequested.connect(lambda: details.append(True))
        status.set_text("Working")
        status.set_busy(True)
        self.assertEqual(status.progress_bar.maximum(), 0)
        status.set_busy(False)
        self.assertEqual(status.progress_bar.maximum(), 100)
        event = Mock(spec=QMouseEvent)
        event.button.return_value = Qt.LeftButton
        status.mouseReleaseEvent(event)
        self.assertEqual((texts, busy, details), (["Working"], [True, False], [True]))
        status.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(0, 0),
            Qt.RightButton,
            Qt.RightButton,
            Qt.NoModifier,
        ))

    def test_filter_popup_counts_and_clears_only_when_changed(self):
        popup = SearchFilterPopup()
        for combo in popup.combos:
            combo.addItem("All", "")
            combo.addItem("Value", "value")
            combo.setCurrentIndex(1)
        popup.sort_combo.setCurrentIndex(popup.sort_combo.findData(SearchSort.DATE))
        popup.include_subfolders_checkbox.setChecked(True)
        self.assertEqual(popup.active_filter_count(), 5)
        changes = []
        popup.filtersChanged.connect(lambda: changes.append(True))
        popup.clear_filters()
        self.assertEqual(popup.active_filter_count(), 0)
        self.assertEqual(len(changes), 1)
        popup.clear_filters()
        self.assertEqual(len(changes), 1)

    def test_statistics_widget_formats_full_compact_loading_and_errors(self):
        statistics = ApplicationStatistics(
            customer_count=1, project_count=2, contact_count=3, service_count=4,
            file_count=5, total_file_size=2 * 1024**3, content_count=6,
            pending_recognition_count=7, last_indexed_at="2026-08-05T12:30:00Z",
            last_index_duration_seconds=1.25,
        )
        widget = StatisticsWidget()
        widget.set_statistics(statistics)
        self.assertEqual(widget._value_labels["total_file_size"].text(), "2.00 GB")
        widget.set_loading()
        self.assertEqual(widget._value_labels, {})
        widget.set_error("bad")
        self.assertIn("bad", widget.status_label.text())
        compact = StatisticsWidget(compact=True)
        compact.set_statistics(statistics)
        self.assertEqual(set(compact._value_labels), {"customer_count", "project_count"})
        self.assertEqual(StatisticsWidget._format_size(2 * 1024**2), "2.00 MB")
        self.assertEqual(StatisticsWidget._format_size(2048), "2.0 KB")
        self.assertEqual(StatisticsWidget._format_size(12), "12 B")
        self.assertEqual(StatisticsWidget._format_timestamp(""), "Noch nicht ausgeführt")
        self.assertEqual(StatisticsWidget._format_timestamp("invalid"), "invalid")
        widget.grid.addItem(QSpacerItem(1, 1), 0, 0)
        widget._clear()
