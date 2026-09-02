from __future__ import annotations

from types import SimpleNamespace

from papagui_client.application.models import CatalogHit, CatalogRecord, SyncResult
from papagui_client.presentation.coordinators import (
    ClientPage,
    DataSession,
    DataSessionCoordinator,
    NavigationCoordinator,
    SearchCoordinator,
    SyncCoordinator,
)


class SearchService:
    def __init__(self, generation):
        self.generation = generation

    def search(self, query):
        return [
            CatalogHit(
                CatalogRecord("key", "source", "file.pdf", f"{self.generation}.pdf"),
                local_path=__import__("pathlib").PurePosixPath("/file.pdf"),
            )
        ]


class CustomerStore:
    def list_customer_views(self):
        return []

    def replay(self):
        from papagui_client.application.models import ReplayResult

        return ReplayResult()


def test_data_session_rebinds_all_readers_after_generation_sync():
    state = {"index": "one", "customers": "one"}
    built = []

    def factory(generations):
        built.append(dict(generations))
        return DataSession(SearchService(generations["index"]), CustomerStore(), dict(generations))

    sessions = DataSessionCoordinator(factory, lambda: dict(state))
    search = SearchCoordinator(sessions)
    assert search.search("anything")[0].record.filename == "one.pdf"

    class GenerationSync:
        def sync(self):
            state.update(index="two", customers="two")
            return SyncResult(("index", "customers"), dict(state))

        def has_local_data(self):
            return True

    sync = SyncCoordinator(GenerationSync(), sessions, 900)
    sync.sync()

    assert built == [
        {"index": "one", "customers": "one"},
        {"index": "two", "customers": "two"},
    ]
    assert search.refresh()[0].record.filename == "two.pdf"


def test_navigation_and_periodic_sync_are_explicit_coordinators():
    navigation = NavigationCoordinator()
    visited = []
    navigation.subscribe(visited.append)
    navigation.navigate(ClientPage.CUSTOMERS)
    assert navigation.current is ClientPage.CUSTOMERS
    assert visited == [ClientPage.CUSTOMERS]

    class Timer:
        def start(self, seconds, callback):
            self.seconds, self.callback, self.active = seconds, callback, True

        def stop(self):
            self.active = False

    timer = Timer()
    requested = []
    generation_sync = SimpleNamespace(sync=lambda: SyncResult(), has_local_data=lambda: False)
    sessions = SimpleNamespace(rebind=lambda **_kwargs: None)
    sync = SyncCoordinator(generation_sync, sessions, 1_800)
    sync.start(timer, lambda: requested.append("sync"))
    assert timer.seconds == 1_800
    assert requested == ["sync"]
    timer.callback()
    assert requested == ["sync", "sync"]
    sync.stop()
    assert not timer.active
