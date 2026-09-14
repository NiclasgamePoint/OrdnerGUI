"""Generation synchronization use case."""

from __future__ import annotations

from tempfile import TemporaryDirectory

from papagui_contracts.generations import GenerationComponentKind

from .models import InstalledGeneration, SyncResult
from .errors import GenerationNotReady
from .ports import GenerationGateway, GenerationStore


class SyncError(RuntimeError):
    """A remote generation could not be safely activated."""


class SyncCoordinator:
    """Download complete component sets and switch one atomic local pointer."""

    def __init__(self, gateway: GenerationGateway, store: GenerationStore):
        self._gateway = gateway
        self._store = store

    def sync(self) -> SyncResult:
        self._store.import_legacy_symlink()
        try:
            manifest = self._gateway.current_manifest()
        except GenerationNotReady:
            return SyncResult(
                current={
                    kind.value: generation
                    for kind in (GenerationComponentKind.INDEX, GenerationComponentKind.CUSTOMERS)
                    if (generation := self._store.current_generation(kind)) is not None
                },
                awaiting_generation=True,
            )
        except Exception as exc:
            if isinstance(exc, SyncError):
                raise
            raise SyncError(f"generation server unavailable: {exc}") from exc

        components = (
            (GenerationComponentKind.INDEX, manifest.index),
            (GenerationComponentKind.CUSTOMERS, manifest.customers),
        )
        components = tuple(
            (kind, component) for kind, component in components if component is not None
        )
        changed = [
            (kind, component)
            for kind, component in components
            if self._store.current_generation(kind) != component.generation
        ]
        if not changed:
            return SyncResult(
                current={kind.value: component.generation for kind, component in components}
            )

        installed: list[InstalledGeneration] = []
        try:
            self._store.root.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".download-", dir=self._store.root) as directory:
                from pathlib import Path

                staging = Path(directory)
                archives = []
                if manifest.legacy_combined:
                    # A v1 archive contains index and customer data together.
                    # Fetch it exactly once, then install the verified immutable
                    # payload for every component whose local generation differs.
                    kind, component = changed[0]
                    archive = staging / f"legacy-{component.generation}.zip"
                    self._gateway.download_component(kind, component, archive)
                    archives.extend(
                        (changed_kind, changed_component, archive)
                        for changed_kind, changed_component in changed
                    )
                else:
                    for kind, component in changed:
                        archive = staging / f"{kind.value}-{component.generation}.zip"
                        self._gateway.download_component(kind, component, archive)
                        archives.append((kind, component, archive))
                # Nothing is installed before every remote download has completed.
                for kind, component, archive in archives:
                    installed.append(
                        self._store.install_archive(
                            kind,
                            component,
                            archive,
                            legacy_combined=manifest.legacy_combined,
                        )
                    )
            current = self._store.activate(installed)
        except Exception as exc:
            self._store.discard(installed)
            if isinstance(exc, SyncError):
                raise
            raise SyncError(f"generation could not be activated: {exc}") from exc
        return SyncResult(
            changed_components=tuple(item.component for item in installed),
            current=current,
        )

    def has_local_data(self) -> bool:
        return any(
            self._store.active_component_path(kind) is not None
            for kind in (GenerationComponentKind.INDEX, GenerationComponentKind.CUSTOMERS)
        )
