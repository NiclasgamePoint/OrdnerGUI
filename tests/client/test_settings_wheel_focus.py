"""Real Qt wheel delivery must scroll settings without silently editing them."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QComboBox, QLineEdit, QScrollArea, QSpinBox, QStyle,
    QStyleOptionSlider, QVBoxLayout, QWidget,
)

from papagui_client.gui.settings import ClientSettingsDialog
from papagui_client.gui.tray import ServerTrayWindow
from papagui_client.gui.widgets.click_activated_inputs import (
    ClickActivatedComboBox,
    ClickActivatedSlider,
    ClickActivatedSpinBox,
)
from tests.client.test_client_settings import _complete_server_settings, configured
from tests.client.test_gui_lifecycle_edges import make_container


def wheel(target, delta=-120):
    window = target.window()
    position = target.mapTo(window, target.rect().center())
    QTest.wheelEvent(window.windowHandle(), QPointF(position), QPoint(0, delta))


@pytest.fixture(params=("spin", "spin-editor", "combo", "slider"))
def editor(request, qtbot, qapp):
    scroll = QScrollArea()
    qtbot.addWidget(scroll)
    scroll.setWidgetResizable(True)
    scroll.resize(420, 220)
    content = QWidget()
    content.setMinimumHeight(900)
    layout = QVBoxLayout(content)
    if request.param == "combo":
        field = ClickActivatedComboBox()
        field.addItems(("One", "Two", "Three", "Four"))
        field.setCurrentIndex(1)
        value = field.currentIndex
    else:
        field = (
            ClickActivatedSlider(Qt.Orientation.Horizontal)
            if request.param == "slider" else ClickActivatedSpinBox()
        )
        field.setRange(0, 100)
        field.setValue(50)
        value = field.value
    layout.addWidget(field)
    other = QLineEdit()
    layout.addWidget(other)
    layout.addStretch()
    scroll.setWidget(content)
    scroll.show()
    scroll.activateWindow()
    other.setFocus()
    qapp.processEvents()
    target = field.lineEdit() if request.param == "spin-editor" else field
    return SimpleNamespace(scroll=scroll, field=field, target=target, other=other, value=value)


def click_editor(editor, qtbot):
    qtbot.mouseClick(editor.target, Qt.MouseButton.LeftButton)
    if isinstance(editor.field, QComboBox):
        # Confirm the current entry in the actual popup, returning focus to the combo.
        QApplication.processEvents()
        view = editor.field.view()
        qtbot.keyClick(view, Qt.Key.Key_Return)
        QApplication.processEvents()
        assert not editor.field.view().isVisible()
        # The offscreen macOS backend does not reactivate a parent window after
        # a popup closes. Supply that window-manager event, without setting the
        # field's focus or enabling its wheel guard from the test.
        editor.field.window().activateWindow()
    qtbot.waitUntil(editor.field.hasFocus)


def test_hover_wheel_scrolls_page_without_changing_value(editor):
    before = editor.value()
    wheel(editor.target)
    assert editor.value() == before
    assert editor.scroll.verticalScrollBar().value() > 0
    assert editor.other.hasFocus()


@pytest.mark.parametrize("reason", (Qt.FocusReason.OtherFocusReason, Qt.FocusReason.TabFocusReason))
def test_focus_without_click_does_not_enable_wheel(editor, reason):
    editor.field.setFocus(reason)
    assert editor.field.hasFocus()
    before = editor.value()
    wheel(editor.target)
    assert editor.value() == before
    assert editor.scroll.verticalScrollBar().value() > 0


def test_explicit_click_enables_wheel_editing(editor, qtbot):
    click_editor(editor, qtbot)
    before = editor.value()
    wheel(editor.target)
    assert editor.value() != before
    assert editor.scroll.verticalScrollBar().value() == 0


@pytest.mark.parametrize("reset", ("focus", "hide"))
def test_focus_or_page_change_requires_another_click(editor, qtbot, qapp, reset):
    click_editor(editor, qtbot)
    if reset == "focus":
        qtbot.mouseClick(editor.other, Qt.MouseButton.LeftButton)
    else:
        editor.scroll.widget().hide()
        editor.scroll.widget().show()
        qapp.processEvents()
    editor.field.setFocus(Qt.FocusReason.TabFocusReason)
    assert editor.field.hasFocus()
    before = editor.value()
    wheel(editor.target)
    assert editor.value() == before
    assert editor.scroll.verticalScrollBar().value() > 0


def test_keyboard_editing_still_works_without_click(editor, qtbot):
    editor.field.setFocus(Qt.FocusReason.TabFocusReason)
    before = editor.value()
    qtbot.keyClick(editor.target, Qt.Key.Key_Up)
    assert editor.value() != before


@pytest.mark.parametrize("page, name", (
    (1, "sync_page.interval_value"),
    (1, "sync_page.interval_unit"),
    (5, "sync_page.theme"),
    (5, "accent_combo"),
    (5, "contrast_slider"),
    (5, "font_size_slider"),
))
def test_client_settings_require_click_and_reset_on_page_change(page, name, qtbot, qapp, tmp_path):
    dialog = ClientSettingsDialog(configured(tmp_path))
    qtbot.addWidget(dialog)
    field = dialog
    for component in name.split("."):
        field = getattr(field, component)
    dialog.nav_list.setCurrentRow(page)
    dialog.show()
    dialog.activateWindow()
    qapp.processEvents()
    before = (dialog.settings(), dialog.appearance_settings())
    for focus_target in (dialog.nav_list, field):
        focus_target.setFocus(Qt.FocusReason.TabFocusReason)
        for delta in (120, -120):
            wheel(field, delta)
            assert (dialog.settings(), dialog.appearance_settings()) == before

    click_editor(SimpleNamespace(field=field, target=field), qtbot)
    before = (dialog.settings(), dialog.appearance_settings())
    if isinstance(field, QComboBox):
        delta = -120 if field.currentIndex() < field.count() - 1 else 120
    else:
        delta = 120 if field.value() < field.maximum() else -120
    wheel(field, delta)
    assert (dialog.settings(), dialog.appearance_settings()) != before

    dialog.nav_list.setCurrentRow(0)
    dialog.nav_list.setCurrentRow(page)
    field.setFocus(Qt.FocusReason.TabFocusReason)
    before = (dialog.settings(), dialog.appearance_settings())
    for delta in (120, -120):
        wheel(field, delta)
        assert (dialog.settings(), dialog.appearance_settings()) == before


def test_appearance_slider_can_still_be_dragged(qtbot, qapp, tmp_path):
    dialog = ClientSettingsDialog(configured(tmp_path))
    qtbot.addWidget(dialog)
    dialog.nav_list.setCurrentRow(5)
    dialog.show()
    dialog.activateWindow()
    qapp.processEvents()
    slider = dialog.contrast_slider
    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    handle = slider.style().subControlRect(
        QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, slider,
    )
    before = slider.value()
    target = QPoint(slider.width() - 15, handle.center().y())
    qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=handle.center())
    qtbot.mouseMove(slider, pos=target)
    qtbot.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=target)
    assert slider.value() > before
    assert dialog.appearance_settings().contrast == slider.value()


def test_all_index_settings_keep_values_and_saved_state_when_scrolling(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ServerTrayWindow, "refresh_status", lambda self: None)
    window = ServerTrayWindow(make_container(tmp_path))
    qtbot.addWidget(window)
    try:
        window._timer.stop()
        window._apply_settings({"settings": _complete_server_settings()})
        window.tabs.setCurrentIndex(1)
        window.show()
        window.activateWindow()
        qapp.processEvents()
        scroll = window.tabs.widget(1)
        before = window._collect_settings()
        fields = scroll.findChildren(QSpinBox) + scroll.findChildren(QComboBox)
        assert len(fields) >= 20
        for field in fields:
            scroll.ensureWidgetVisible(field)
            qapp.processEvents()
            wheel(field, 120)
            wheel(field, -120)
            assert window._collect_settings() == before
            assert not window._settings_dirty
            assert not window.settings_save_button.isEnabled()

        # Recognition administration belongs to the same server window.
        monkeypatch.setattr(window.recognition_admin_page, "reload", lambda: None)
        window.tabs.setCurrentIndex(2)
        qapp.processEvents()
        combo = window.recognition_admin_page.kind_combo
        combo.setEnabled(True)
        window.recognition_admin_page.scroll.ensureWidgetVisible(combo)
        before_kind = combo.currentData()
        wheel(combo, -120)
        assert combo.currentData() == before_kind
    finally:
        window.shutdown()
        window.close()
