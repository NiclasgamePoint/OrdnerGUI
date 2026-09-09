"""Geometry regressions using fabricated values only, never customer snapshots."""

from collections.abc import Callable

import pytest
from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import QApplication, QFrame, QWidget

from papagui_contracts import Customer, SourcePath
from papagui_contracts.recognition import CustomerSuggestion
from papagui_client.gui.customer_suggestions import CustomerSuggestionsDialog
from papagui_client.gui.theme import build_palette, build_stylesheet
from papagui_client.gui.widgets.buttons import AppButton


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=("light", "dark"))
def themed_application(application, request):
    previous_palette = application.palette()
    previous_stylesheet = application.styleSheet()
    # Apply the production palette/styles without reading persisted user settings.
    application.setPalette(build_palette(request.param, "#2db89d"))
    application.setStyleSheet(build_stylesheet(request.param, "#2db89d"))
    yield application
    application.setStyleSheet(previous_stylesheet)
    application.setPalette(previous_palette)
    application.processEvents()


def _settle(application):
    # Showing, wrapping and changing visibility each schedule another layout pass.
    for _ in range(8):
        application.processEvents()


def _synthetic_suggestions():
    evidence = tuple(
        {
            "source": SourcePath(
                "synthetic-layout",
                "synthetic-archive/"
                + "/".join(f"synthetic-directory-{index}-" + "x" * 80 for index in range(8))
                + f"/synthetic-document-{number}-" + "y" * 100 + ".pdf",
            ).to_dict(),
            "page": number + 1,
            "excerpt": (
                "Fabricated evidence for a layout regression. " * 12
                + "synthetic-unbroken-token-" + "z" * 220
            ),
        }
        for number in range(7)
    )
    fields = (
        ("email", "synthetic." + "unbroken" * 24 + "@example.test"),
        ("phone", "+49 000 123456789"),
        ("company", "Synthetic organization " + "unbroken" * 35),
    )
    return tuple(
        CustomerSuggestion(
            id=index + 1,
            customer_id=101,
            field_name=fields[index % 3][0],
            value=fields[index % 3][1],
            source=SourcePath.from_dict(evidence[0]["source"]),
            fingerprint=f"synthetic-layout-{index}",
            quality="strong",
            reasons=("customer-identity-in-block", "explicit-customer-role"),
            evidence=evidence,
            evidence_count=len(evidence),
        )
        for index in range(9)
    )


@pytest.fixture
def open_dialog(themed_application) -> Callable:
    dialogs = []

    def create(size):
        dialog = CustomerSuggestionsDialog(
            _synthetic_suggestions(),
            4,
            customer=Customer(id=101, display_name="Synthetic layout customer"),
        )
        dialogs.append(dialog)
        dialog.show()
        _settle(themed_application)
        # Test explicit window sizes independently of the offscreen desktop size.
        dialog.resize(*size)
        _settle(themed_application)
        assert dialog.size() == QSize(*size)
        return dialog

    yield create
    for dialog in dialogs:
        dialog.close()
        dialog.deleteLater()
    _settle(themed_application)


def _cards(dialog):
    return dialog.scroll.widget().findChildren(QFrame, "CustomerSuggestionCard")


def _rect_in(widget, ancestor):
    return QRect(widget.mapTo(ancestor, QPoint(0, 0)), widget.size())


def _assert_no_horizontal_overflow(dialog):
    scroll = dialog.scroll
    content = scroll.widget()
    viewport = scroll.viewport()
    assert scroll.horizontalScrollBar().maximum() == 0
    assert not scroll.horizontalScrollBar().isVisible()
    assert content.width() <= viewport.width()
    # Hiding the scrollbar alone must not conceal oversized/clipped descendants.
    for widget in content.findChildren(QWidget):
        if widget.isVisibleTo(content):
            rect = _rect_in(widget, viewport)
            assert rect.left() >= 0, (widget.objectName(), rect)
            assert rect.right() < viewport.width(), (widget.objectName(), rect)


def _assert_first_decision_visible(dialog):
    first = _cards(dialog)[0]
    viewport = dialog.scroll.viewport()
    for name in ("ReviewAcceptButton", "ReviewRejectButton"):
        button = first.findChild(AppButton, name)
        assert button is not None
        assert button.isVisibleTo(viewport) and button.isEnabled()
        assert viewport.rect().contains(_rect_in(button, viewport)), name


@pytest.mark.parametrize("size", ((760, 560), (1050, 760)))
def test_compact_cards_keep_first_decision_visible(open_dialog, size):
    dialog = open_dialog(size)
    cards = _cards(dialog)
    assert len(cards) == 9
    assert dialog.scroll.verticalScrollBar().maximum() > 0
    for card in cards:
        toggle = card.findChild(AppButton, "ReviewEvidenceToggle")
        panel = card.findChild(QWidget, "ReviewEvidencePanel")
        assert toggle is not None and toggle.isCheckable() and not toggle.isChecked()
        assert panel is not None and panel.isHidden()
    _assert_no_horizontal_overflow(dialog)
    _assert_first_decision_visible(dialog)
    # Primary window controls remain reachable independently of the card scroll.
    for button in (dialog.refresh_button, dialog.rescan_button, dialog.extract_button):
        assert dialog.rect().contains(_rect_in(button, dialog))


@pytest.mark.parametrize("size", ((760, 560), (1050, 760)))
def test_expanded_long_evidence_fits_and_retains_source(open_dialog, themed_application, size):
    dialog = open_dialog(size)
    card = _cards(dialog)[0]
    toggle = card.findChild(AppButton, "ReviewEvidenceToggle")
    panel = card.findChild(QWidget, "ReviewEvidencePanel")
    assert toggle is not None and panel is not None
    toggle.click()
    _settle(themed_application)
    assert toggle.isChecked() and panel.isVisibleTo(card)
    _assert_no_horizontal_overflow(dialog)
    _assert_first_decision_visible(dialog)
    sources = panel.findChildren(AppButton, "ReviewSourceButton")
    assert sources and sources[0].isVisibleTo(panel)
    expected_source = _synthetic_suggestions()[0].source
    assert expected_source.relative_path in sources[0].toolTip()
    opened = []
    dialog.sourceRequested.connect(opened.append)
    sources[0].click()
    assert opened == [expected_source]
    toggle.click()
    _settle(themed_application)
    assert panel.isHidden()
    _assert_no_horizontal_overflow(dialog)
    _assert_first_decision_visible(dialog)


def test_expanded_evidence_reflows_after_window_resize(open_dialog, themed_application):
    dialog = open_dialog((1050, 760))
    first = _cards(dialog)[0]
    toggle = first.findChild(AppButton, "ReviewEvidenceToggle")
    assert toggle is not None
    toggle.click()
    _settle(themed_application)
    for size in ((760, 560), (1050, 760)):
        dialog.resize(*size)
        _settle(themed_application)
        assert dialog.size() == QSize(*size)
        assert toggle.isChecked()
        _assert_no_horizontal_overflow(dialog)
        _assert_first_decision_visible(dialog)
