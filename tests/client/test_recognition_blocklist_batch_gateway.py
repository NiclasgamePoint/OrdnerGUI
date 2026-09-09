"""Batch HTTP gateway coverage using only synthetic in-memory responses."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import Mock

import pytest

from papagui_client.adapters.http_api import (
    ApiRejectedError,
    ApiUnavailableError,
    HttpServerControlGateway,
)


def _entry():
    return {
        "id": 7,
        "kind": "email",
        "value": "synthetic@example.test",
        "reason": "Synthetic test entry",
        "normalized_value": "synthetic@example.test",
        "created_at": "2030-01-01T12:00:00Z",
    }


@pytest.mark.parametrize("publication_pending", [True, False, None])
def test_blocklist_state_restores_publication_status_and_supports_older_servers(
    monkeypatch, publication_pending
):
    requests = []
    response = {"entries": [_entry()]}
    if publication_pending is not None:
        response["publication_pending"] = publication_pending

    def urlopen(request, timeout):
        requests.append(request)
        assert timeout == 5
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    gateway = HttpServerControlGateway("http://synthetic-server.test", "synthetic-token")

    assert gateway.recognition_blocklist_state() == {
        "entries": [_entry()], "publication_pending": publication_pending is True,
    }
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].selector == "/v2/admin/recognition/blocklist"
    assert requests[0].get_header("Authorization") == "Bearer synthetic-token"


@pytest.mark.parametrize("response", [
    {}, {"entries": None}, {"entries": {}}, {"entries": [None]},
    {"entries": [], "publication_pending": None},
    {"entries": [], "publication_pending": 1},
    {"entries": [], "publication_pending": "false"},
])
def test_blocklist_state_rejects_invalid_status_or_entries(monkeypatch, response):
    monkeypatch.setattr("urllib.request.urlopen", Mock(
        return_value=io.BytesIO(json.dumps(response).encode()),
    ))
    gateway = HttpServerControlGateway("http://synthetic-server.test")

    with pytest.raises(ApiUnavailableError, match="invalid recognition blocklist state"):
        gateway.recognition_blocklist_state()


@pytest.mark.parametrize("changed", [True, False])
@pytest.mark.parametrize("published", [True, False])
def test_batch_uses_one_authenticated_request_and_returns_complete_entries(
    monkeypatch, changed, published
):
    requests = []
    response = {"entries": [_entry()], "changed": changed, "published": published}
    additions = (
        {"kind": "email", "value": "synthetic@example.test", "reason": "Synthetic test entry"},
        {"kind": "company", "value": "Synthetic Example Company", "reason": ""},
    )

    def urlopen(request, timeout):
        requests.append(request)
        assert timeout == 60
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    gateway = HttpServerControlGateway(
        "http://synthetic-server.test/", "synthetic-token", timeout_seconds=8
    )
    result = gateway.apply_recognition_blocklist_changes(iter(additions), iter((2, 3)))

    assert result == response
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.full_url == "http://synthetic-server.test/v2/admin/recognition/blocklist/batch"
    assert request.get_header("Authorization") == "Bearer synthetic-token"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Accept") == "application/json"
    assert json.loads(request.data) == {"additions": list(additions), "deletions": [2, 3]}


def test_batch_accepts_empty_authoritative_list_and_copies_entries():
    gateway = HttpServerControlGateway("http://synthetic-server.test")
    gateway._transport = Mock(timeout_seconds=5)
    response = {"entries": [_entry()], "changed": False, "published": True}
    gateway._transport.json.return_value = response

    result = gateway.apply_recognition_blocklist_changes([], [])

    assert result == response
    assert result["entries"] is not response["entries"]
    assert result["entries"][0] is not response["entries"][0]
    gateway._transport.json.assert_called_once_with(
        "POST", "/v2/admin/recognition/blocklist/batch", {"additions": [], "deletions": []},
        timeout_seconds=60,
    )
    gateway._transport.json.return_value = {"entries": [], "changed": True, "published": True}
    assert gateway.apply_recognition_blocklist_changes([], [7]) == {
        "entries": [], "changed": True, "published": True
    }


@pytest.mark.parametrize("timeout_seconds", [5, 60, 90])
def test_batch_allows_snapshot_publication_time_without_changing_poll_timeout(
    monkeypatch, timeout_seconds
):
    calls = []

    def urlopen(request, timeout):
        calls.append((request.selector, timeout))
        if request.selector.endswith("/batch"):
            response = {"entries": [], "changed": False, "published": True}
        else:
            response = {"job": None}
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    gateway = HttpServerControlGateway(
        "http://synthetic-server.test", timeout_seconds=timeout_seconds
    )

    assert gateway.recognition_rebuild() is None
    assert gateway.apply_recognition_blocklist_changes([], []) == {
        "entries": [], "changed": False, "published": True
    }
    assert gateway.recognition_rebuild() is None

    assert calls == [
        ("/v2/admin/recognition/rebuild", timeout_seconds),
        ("/v2/admin/recognition/blocklist/batch", max(timeout_seconds, 60)),
        ("/v2/admin/recognition/rebuild", timeout_seconds),
    ]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"entries": [], "published": True},
        {"changed": True, "published": True},
        {"entries": None, "changed": True, "published": True},
        {"entries": {}, "changed": True, "published": True},
        {"entries": [], "changed": None, "published": True},
        {"entries": [], "changed": 1, "published": True},
        {"entries": [], "changed": "false", "published": True},
        {"entries": [], "changed": True},
        {"entries": [], "changed": True, "published": None},
        {"entries": [], "changed": True, "published": 1},
        {"entries": [], "changed": True, "published": "false"},
        {"entries": [None], "changed": True, "published": True},
        {"entries": [[]], "changed": True, "published": True},
        {"entries": [{}], "changed": True, "published": True},
        *(
            {"entries": [{**_entry(), field: value}], "changed": True, "published": True}
            for field, value in (
                ("id", None), ("id", True), ("id", "7"), ("id", 0), ("id", -1),
                ("kind", None), ("kind", " "), ("value", []), ("value", ""),
                ("reason", {}),
            )
        ),
    ],
)
def test_batch_rejects_incomplete_or_invalid_success_responses(monkeypatch, response):
    urlopen = Mock(return_value=io.BytesIO(json.dumps(response).encode()))
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    gateway = HttpServerControlGateway("http://synthetic-server.test")

    with pytest.raises(ApiUnavailableError, match="invalid recognition blocklist batch"):
        gateway.apply_recognition_blocklist_changes([], [7])

    assert urlopen.call_count == 1


@pytest.mark.parametrize("body", [b"not json", b"[]", b"null", b"\xff", b""])
def test_batch_rejects_invalid_json_or_non_object_response(monkeypatch, body):
    urlopen = Mock(return_value=io.BytesIO(body))
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    gateway = HttpServerControlGateway("http://synthetic-server.test")

    with pytest.raises(ApiUnavailableError):
        gateway.apply_recognition_blocklist_changes([], [7])

    assert urlopen.call_count == 1


@pytest.mark.parametrize("status", [404, 405])
@pytest.mark.parametrize("body", [b'{"detail": "Not Found"}', b"[]", b"old server"])
def test_batch_requires_server_update_without_fallback_on_unsupported_route(monkeypatch, status, body):
    urlopen = Mock(side_effect=urllib.error.HTTPError(
        "http://synthetic-server.test/v2/admin/recognition/blocklist/batch",
        status, "Unsupported endpoint", {}, io.BytesIO(body),
    ))
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    gateway = HttpServerControlGateway("http://synthetic-server.test")

    with pytest.raises(ApiRejectedError, match="Serverupdate erforderlich") as failure:
        gateway.apply_recognition_blocklist_changes(
            [{"kind": "email", "value": "synthetic@example.test", "reason": ""}], [7]
        )

    assert failure.value.status == status
    assert failure.value.payload["error"]["code"] == "recognition_blocklist_batch_unsupported"
    assert failure.value.payload["error"]["message"] == str(failure.value)
    assert urlopen.call_count == 1


@pytest.mark.parametrize("status", [400, 401, 403, 409, 422, 429, 500, 503])
def test_batch_preserves_other_http_rejections_without_retry(monkeypatch, status):
    payload = {"error": {"code": "synthetic_rejection", "message": "Synthetic rejection"}}
    urlopen = Mock(side_effect=urllib.error.HTTPError(
        "http://synthetic-server.test/v2/admin/recognition/blocklist/batch",
        status, "Synthetic rejection", {}, io.BytesIO(json.dumps(payload).encode()),
    ))
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    gateway = HttpServerControlGateway("http://synthetic-server.test")

    with pytest.raises(ApiRejectedError) as failure:
        gateway.apply_recognition_blocklist_changes([], [7])

    assert failure.value.status == status
    assert failure.value.payload == payload
    assert urlopen.call_count == 1


@pytest.mark.parametrize("failure", [urllib.error.URLError("synthetic offline"), OSError("synthetic offline")])
def test_batch_offline_errors_remain_unavailable_without_retry(monkeypatch, failure):
    urlopen = Mock(side_effect=failure)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    gateway = HttpServerControlGateway("http://synthetic-server.test")

    with pytest.raises(ApiUnavailableError, match="synthetic offline"):
        gateway.apply_recognition_blocklist_changes([], [7])

    assert urlopen.call_count == 1
