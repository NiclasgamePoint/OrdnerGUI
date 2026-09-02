from io import BytesIO
import json
import unittest
import urllib.error
from unittest.mock import patch

from app.services.index_server_client import IndexServerClient, IndexServerError


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class IndexServerClientTests(unittest.TestCase):
    def test_status_actions_and_settings_use_authenticated_json_requests(self):
        responses = [
            _Response({"server": {"status": "online"}}),
            _Response({"accepted": True}),
            _Response({"settings": {"ocr_enabled": True}}),
            _Response({"settings": {"ocr_enabled": False}}),
        ]
        client = IndexServerClient("http://server:8765/", "secret")
        with patch("urllib.request.urlopen", side_effect=responses) as open_url:
            self.assertEqual(client.status()["server"]["status"], "online")
            self.assertTrue(client.action("run")["accepted"])
            self.assertTrue(client.settings()["ocr_enabled"])
            self.assertFalse(client.save_settings({"ocr_enabled": False})["ocr_enabled"])
        request = open_url.call_args_list[1].args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(json.loads(request.data)["action"], "run")

    def test_http_and_transport_errors_have_one_domain_exception(self):
        client = IndexServerClient("http://server")
        http_error = urllib.error.HTTPError(
            "http://server", 400, "bad", {}, BytesIO(b'{"error":"ungueltig"}')
        )
        with patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaisesRegex(IndexServerError, "ungueltig"):
                client.status()
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(IndexServerError, "offline"):
                client.status()


if __name__ == "__main__":
    unittest.main()
