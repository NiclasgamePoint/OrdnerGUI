from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from papagui_client.application.paths import PlatformFamily
from papagui_client.config import ClientSettings
from papagui_client.gui.widgets import AppButton
from papagui_client.presentation.settings import (
    ClientSettingsPresenter,
    SourceMappingViewModel,
)

class OnboardingDialog(QDialog):
    """First-run client connection in the original v0.4.1 dialog design."""

    def __init__(
        self,
        current: ClientSettings,
        *,
        sources=None,
        presenter: ClientSettingsPresenter | None = None,
        platform: PlatformFamily | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("OnboardingDialog")
        self.setWindowTitle("PapaGUI einrichten")
        self.setModal(True)
        self.setMinimumWidth(600)
        self.resize(660, 480)

        self._current = current
        self._presenter = presenter or ClientSettingsPresenter()
        self._model = self._presenter.present(current, sources)
        self._platform = platform or PlatformFamily.current()
        self._selected_mapping_index = 0 if self._model.mappings else None
        self._accepted_settings: ClientSettings | None = None
        initial_mapping = (
            self._model.mappings[0]
            if self._model.mappings
            else SourceMappingViewModel("primary")
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("Willkommen bei PapaGUI")
        title.setObjectName("PopupTitle")
        layout.addWidget(title)

        description = QLabel(
            "Verbinden Sie diesen Client mit dem zentralen PapaGUI-Server und "
            "wählen Sie den lokalen Pfad zur dort indizierten Datenquelle aus. "
            "Das kann ein eingebundenes Netzlaufwerk oder eine NAS-Freigabe sein."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        connection = QFormLayout()
        connection.setHorizontalSpacing(16)
        connection.setVerticalSpacing(10)
        self.server_url_input = QLineEdit(self._model.server_url)
        self.server_url_input.setObjectName("OnboardingServerUrl")
        self.server_url_input.setPlaceholderText("http://papagui-server:8765")
        self.server_url_input.setAccessibleName("Server-URL")
        connection.addRow("Server-URL", self.server_url_input)

        self.api_token_input = QLineEdit(self._model.api_token)
        self.api_token_input.setObjectName("OnboardingApiToken")
        self.api_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_token_input.setPlaceholderText("Client-Token")
        self.api_token_input.setAccessibleName("Client-Token")
        connection.addRow("Client-Token", self.api_token_input)

        self.source_id_input = QLineEdit(initial_mapping.source_id)
        self.source_id_input.setObjectName("OnboardingSourceId")
        self.source_id_input.setPlaceholderText("primary")
        self.source_id_input.setAccessibleName("source_id")
        connection.addRow("source_id", self.source_id_input)
        layout.addLayout(connection)

        nas_hint = QLabel(self._path_hint())
        nas_hint.setObjectName("PopupCaption")
        nas_hint.setWordWrap(True)
        layout.addWidget(nas_hint)

        path_row = QHBoxLayout()
        self.path_input = QLineEdit(self._mapping_path(initial_mapping))
        self.path_input.setObjectName("OnboardingLocalPath")
        self.path_input.setPlaceholderText(self._path_placeholder())
        self.path_input.setAccessibleName(self._path_label())
        path_row.addWidget(self.path_input, 1)
        self.browse_button = AppButton("Durchsuchen", AppButton.SECONDARY)
        self.browse_button.clicked.connect(self.choose_path)
        path_row.addWidget(self.browse_button)
        layout.addLayout(path_row)

        self.error_label = QLabel("")
        self.error_label.setObjectName("SettingsError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        info = QLabel(
            "Der Index wird ausschließlich vom Server erzeugt. Dieser Client speichert "
            "nur geprüfte Generationen und verwendet den lokalen Pfad zum Öffnen der "
            "Originaldateien."
        )
        info.setObjectName("PopupCaption")
        info.setWordWrap(True)
        layout.addWidget(info)

        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_button = AppButton("Später", AppButton.SECONDARY)
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.finish_button = AppButton("Einrichtung abschließen")
        self.finish_button.setDefault(True)
        self.finish_button.clicked.connect(self.accept_settings)
        actions.addWidget(self.finish_button)
        layout.addLayout(actions)

        for editor in (
            self.server_url_input,
            self.api_token_input,
            self.source_id_input,
            self.path_input,
        ):
            editor.textChanged.connect(self._clear_error)
        self._apply_environment_overrides()

    def _apply_environment_overrides(self) -> None:
        overrides = self._model.environment_overrides
        for field, widgets in (
            ("server_url", (self.server_url_input,)),
            ("api_token", (self.api_token_input,)),
            (
                "source_mappings",
                (self.source_id_input, self.path_input, self.browse_button),
            ),
        ):
            if field not in overrides:
                continue
            for widget in widgets:
                widget.setEnabled(False)
                widget.setToolTip(
                    "Dieser Wert wird durch eine Umgebungsvariable überschrieben."
                )

    def _mapping_path(self, mapping: SourceMappingViewModel) -> str:
        return {
            PlatformFamily.WINDOWS: mapping.windows,
            PlatformFamily.MACOS: mapping.macos,
            PlatformFamily.LINUX: mapping.linux,
        }[self._platform]

    def _path_label(self) -> str:
        return {
            PlatformFamily.WINDOWS: "Lokaler Windows-Pfad",
            PlatformFamily.MACOS: "Lokaler macOS-Pfad",
            PlatformFamily.LINUX: "Lokaler Linux-Pfad",
        }[self._platform]

    def _path_placeholder(self) -> str:
        return {
            PlatformFamily.WINDOWS: r"Z:\Daten oder \\server\freigabe",
            PlatformFamily.MACOS: "/Volumes/Daten",
            PlatformFamily.LINUX: "/mnt/daten",
        }[self._platform]

    def _path_hint(self) -> str:
        return (
            f"{self._path_label()}: Verbinden Sie die Freigabe zuerst im "
            "Betriebssystem. Der Eintrag muss zur source_id des Server-Containers passen."
        )

    def _clear_error(self, _text: str = "") -> None:
        self.error_label.clear()

    def choose_path(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Datenquelle auswählen",
            self.path_input.text().strip(),
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self.path_input.setText(selected)

    def _local_mapping(self, path: str) -> SourceMappingViewModel:
        existing = (
            self._model.mappings[self._selected_mapping_index]
            if self._selected_mapping_index is not None
            else SourceMappingViewModel("")
        )
        values = {
            "windows": existing.windows,
            "macos": existing.macos,
            "linux": existing.linux,
        }
        values[self._platform.value] = path
        return SourceMappingViewModel(
            self.source_id_input.text(),
            windows=values["windows"],
            macos=values["macos"],
            linux=values["linux"],
        )

    def _mappings(
        self, local_mapping: SourceMappingViewModel
    ) -> tuple[SourceMappingViewModel, ...]:
        mappings = list(self._model.mappings)
        if self._selected_mapping_index is None:
            mappings.append(local_mapping)
        else:
            mappings[self._selected_mapping_index] = local_mapping
        return tuple(mappings)

    def _build_settings(self) -> ClientSettings:
        raw_path = self.path_input.text().strip()
        if not raw_path:
            raise ValueError("Bitte wählen Sie einen Datenordner aus.")
        path = Path(raw_path).expanduser()
        if not path.exists() or not path.is_dir():
            raise ValueError(
                "Der Ordner ist nicht erreichbar. Bitte prüfen Sie die "
                "NAS-/Netzwerkverbindung."
            )
        resolved_path = str(path.resolve())
        interval_value, interval_unit = self._presenter.interval_fields(
            self._current.sync_interval_seconds
        )
        return self._presenter.build(
            current=self._current,
            server_url=self.server_url_input.text(),
            api_token=self.api_token_input.text(),
            mappings=self._mappings(self._local_mapping(resolved_path)),
            interval_value=interval_value,
            interval_unit=interval_unit,
            theme=self._current.theme.value,
        )

    def settings(self) -> ClientSettings:
        return self._accepted_settings or self._build_settings()

    def accept_settings(self) -> None:
        try:
            self._accepted_settings = self._build_settings()
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        super().accept()
