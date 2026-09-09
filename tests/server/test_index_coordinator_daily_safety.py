"""Deterministic daily scheduling checks; no wall-clock waits or source data."""

from datetime import datetime, timedelta, timezone

import pytest

from papagui_server.application import indexing
from papagui_server.domain.models import ServerSettings
from tests.server.test_index_coordinator import Catalog, Settings, _coordinator


@pytest.fixture
def fake_clock(monkeypatch):
    seconds = [0.0]
    base = datetime(2026, 9, 9, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = base + timedelta(seconds=seconds[0])
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(indexing, "datetime", Clock)
    monkeypatch.setattr(indexing.time, "monotonic", lambda: seconds[0])
    return seconds, base


class Wake:
    def __init__(self, seconds, coordinator):
        self.seconds = seconds
        self.coordinator = coordinator
        self.waits = []

    def wait(self, timeout):
        self.waits.append(timeout)
        if timeout is None:
            self.coordinator._stopped.set()
        else:
            self.seconds[0] += timeout

    def clear(self):
        pass


@pytest.mark.parametrize("age_hours,interval_hours,expected_hours", [
    (None, 48, 24), (25, 48, 24), (23, 48, 1), (0, 48, 24), (None, 2, 2),
])
def test_explicit_no_startup_run_defers_overdue_checks_but_keeps_persisted_deadline(
    tmp_path, fake_clock, monkeypatch, age_hours, interval_hours, expected_hours
):
    seconds, base = fake_clock
    catalog = Catalog()
    timestamp = (base - timedelta(hours=age_hours)).isoformat() if age_hours is not None else ""
    catalog.last_content_verification_at = lambda: timestamp
    coordinator = _coordinator(
        tmp_path, catalog=catalog,
        settings=Settings(ServerSettings(interval_seconds=interval_hours * 3600)),
    )
    coordinator._wake = Wake(seconds, coordinator)
    attempts = []

    def after():
        attempts.append(seconds[0])
        coordinator._stopped.set()

    class InlineThread:
        def __init__(self, *, target, kwargs, **_kwargs):
            self.target = target
            self.kwargs = kwargs

        def start(self):
            self.target(**self.kwargs)

    catalog.after = after
    monkeypatch.setattr(indexing.threading, "Thread", InlineThread)
    coordinator.start(run_on_start=False)
    assert attempts == [expected_hours * 3600]
    assert coordinator._wake.waits == [expected_hours * 3600]
    assert catalog.calls == [False]
    assert catalog.last_content_verification_at() == timestamp


@pytest.mark.parametrize("age_hours,expected_hours", [(0, 24), (23, 1), (25, 0)])
def test_restart_uses_persisted_daily_deadline_with_forty_eight_hour_interval(
    tmp_path, fake_clock, age_hours, expected_hours
):
    seconds, base = fake_clock
    catalog = Catalog()
    catalog.last_content_verification_at = lambda: (base - timedelta(hours=age_hours)).isoformat()
    coordinator = _coordinator(
        tmp_path,
        catalog=catalog,
        settings=Settings(ServerSettings(interval_seconds=48 * 3600)),
    )
    coordinator._next_due = 48 * 3600
    coordinator._wake = Wake(seconds, coordinator)
    catalog.after = coordinator._stopped.set
    coordinator._scheduler_loop()
    assert catalog.calls == [False]
    assert seconds[0] == expected_hours * 3600


def test_failed_overdue_verification_retries_after_capped_interval_without_advancing_timestamp(
    tmp_path, fake_clock
):
    seconds, base = fake_clock
    timestamp = (base - timedelta(hours=25)).isoformat()
    catalog = Catalog("error")
    catalog.last_content_verification_at = lambda: timestamp
    coordinator = _coordinator(
        tmp_path,
        catalog=catalog,
        settings=Settings(ServerSettings(interval_seconds=48 * 3600)),
    )
    coordinator._wake = Wake(seconds, coordinator)
    attempts = []

    def after():
        attempts.append(seconds[0])
        if len(attempts) == 2:
            coordinator._stopped.set()

    catalog.after = after
    coordinator._scheduler_loop()
    assert attempts == [0, 24 * 3600]
    assert coordinator._content_verification_due()
    assert catalog.last_content_verification_at() == timestamp


def test_disabled_automatic_runs_do_not_start_overdue_verification(tmp_path, fake_clock):
    seconds, _base = fake_clock
    catalog = Catalog()
    catalog.last_content_verification_at = lambda: ""
    coordinator = _coordinator(
        tmp_path,
        catalog=catalog,
        settings=Settings(ServerSettings(automatic_runs_enabled=False)),
    )
    wake = Wake(seconds, coordinator)
    coordinator._wake = wake
    coordinator._scheduler_loop()
    assert wake.waits == [None]
    assert catalog.calls == []


def test_disabled_daily_verification_keeps_the_normal_long_interval(tmp_path, fake_clock):
    seconds, _base = fake_clock
    catalog = Catalog()
    coordinator = _coordinator(
        tmp_path,
        catalog=catalog,
        settings=Settings(
            ServerSettings(interval_seconds=48 * 3600, daily_reconciliation_enabled=False)
        ),
    )
    coordinator._next_due = 48 * 3600
    coordinator._wake = Wake(seconds, coordinator)
    catalog.after = coordinator._stopped.set
    coordinator._scheduler_loop()
    assert catalog.calls == [False]
    assert seconds[0] == 48 * 3600
