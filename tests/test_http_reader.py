import json
import os
import tempfile
import unittest
from unittest.mock import patch

from timetree_mcp.http_reader import HttpResponse, ReadOnlyViolationError, TimeTreeHttpReader


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.cleared = False

    def request(self, method, url, body=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers or {}})
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)

    def clear(self):
        self.cleared = True


class FakeCredentialStore:
    def __init__(self, credentials=(None, None)):
        self.credentials = credentials

    def read(self):
        return self.credentials

    def status(self):
        return {"available": True, "email_configured": bool(self.credentials[0]), "password_configured": bool(self.credentials[1])}


class HttpReaderTests(unittest.TestCase):
    def test_login_uses_put_credentials_without_exposing_password(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
            ]
        )
        reader = TimeTreeHttpReader(
            email="me@example.com",
            password="secret-value",
            transport=transport,
        )

        result = reader.login()

        self.assertTrue(result["authenticated"])
        self.assertTrue(result["read_only"])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["method"], "PUT")
        self.assertTrue(transport.calls[0]["url"].endswith("/api/v1/auth/email/signin"))
        self.assertEqual(transport.calls[0]["body"]["uid"], "me@example.com")
        self.assertEqual(transport.calls[0]["body"]["password"], "secret-value")
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertEqual(reader.status()["authenticated"], True)

    def test_missing_credentials_fail_without_network_call(self):
        transport = FakeTransport([])
        reader = TimeTreeHttpReader(transport=transport, credential_store=FakeCredentialStore())

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TIMETREE_EMAIL.*TIMETREE_PASSWORD"):
                reader.login()

        self.assertEqual(transport.calls, [])

    def test_password_file_can_supply_secret_without_putting_it_in_mcp_arguments(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
            ]
        )
        reader = TimeTreeHttpReader(transport=transport)
        with tempfile.NamedTemporaryFile("w", delete=True) as secret_file:
            secret_file.write("file-secret\n")
            secret_file.flush()
            with patch.dict(
                os.environ,
                {"TIMETREE_EMAIL": "me@example.com", "TIMETREE_PASSWORD_FILE": secret_file.name},
                clear=True,
            ):
                reader.login()

        self.assertEqual(transport.calls[0]["body"]["password"], "file-secret")

    def test_write_operations_are_unsupported_and_logout_clears_session(self):
        transport = FakeTransport([HttpResponse(200, {}, {})])
        reader = TimeTreeHttpReader(email="me@example.com", password="secret", transport=transport)
        reader.login()

        for operation in (
            "create_event", "update_event", "delete_event", "create_memo", "update_memo", "delete_memo",
            "add_event_comment", "update_event_comment", "delete_event_comment", "update_calendar_labels",
        ):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "Unsupported"):
                reader.read(operation, {"calendar_id": "cal-1", "event_uuid": "uuid-1", "payload": {"title": "Test"}})
        reader.logout()

        self.assertEqual([call["method"] for call in transport.calls], ["PUT"])
        self.assertTrue(transport.cleared)
        self.assertFalse(reader.status()["authenticated"])

    def test_request_layer_blocks_every_non_read_method(self):
        transport = FakeTransport([])
        reader = TimeTreeHttpReader(email="me@example.com", password="secret", transport=transport)
        for method in ("POST", "PUT", "PATCH", "DELETE", "delete"):
            with self.subTest(method=method), self.assertRaises(ReadOnlyViolationError):
                reader._request_raw(method, f"{reader.api_base}/calendar/1/event", {"title": "x"})
        with self.assertRaises(ReadOnlyViolationError):
            reader._request_raw("PUT", f"{reader.api_base}/calendar/1/labels", {})
        with self.assertRaises(ReadOnlyViolationError):
            reader._request_raw("GET", f"{reader.api_base}/calendars", {"smuggled": True})
        self.assertEqual(transport.calls, [])

    def test_password_file_readable_by_others_is_rejected(self):
        reader = TimeTreeHttpReader(transport=FakeTransport([]), credential_store=FakeCredentialStore())
        with tempfile.NamedTemporaryFile("w", delete=True) as secret_file:
            secret_file.write("file-secret")
            secret_file.flush()
            os.chmod(secret_file.name, 0o644)
            with patch.dict(
                os.environ,
                {"TIMETREE_EMAIL": "me@example.com", "TIMETREE_PASSWORD_FILE": secret_file.name},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "chmod 600"):
                    reader.login()

    def test_direct_reader_lists_events_without_browser_calls(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
                HttpResponse(200, {}, {"calendars": [{"id": 1, "alias_code": "a", "name": "Personal"}]}),
                HttpResponse(200, {}, {"events": [{"id": "event-1", "uuid": "uuid-1", "calendar_id": 1, "title": "Lunch", "start_at": 1787619600000, "end_at": 1787623200000}], "chunk": False, "since": 0}),
            ]
        )
        reader = TimeTreeHttpReader(email="me@example.com", password="secret", transport=transport)

        result = reader.read(
            "list_events",
            {"start_date": "2026-08-25", "end_date": "2026-08-26", "calendar_id": "1"},
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["calendar_name"], "Personal")
        self.assertEqual([call["method"] for call in transport.calls], ["PUT", "GET", "GET"])

    def test_login_falls_back_to_keychain_store_and_exposes_presence_only_status(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
            ]
        )
        store = FakeCredentialStore(("keychain@example.com", "keychain-secret"))
        reader = TimeTreeHttpReader(transport=transport, credential_store=store)

        result = reader.read("login", {})
        status = reader.read("keychain_status", {})

        self.assertTrue(result["authenticated"])
        self.assertEqual(transport.calls[0]["body"]["uid"], "keychain@example.com")
        self.assertEqual(transport.calls[0]["body"]["password"], "keychain-secret")
        self.assertNotIn("keychain-secret", json.dumps(status))
        self.assertTrue(status["password_configured"])

    def test_list_events_skips_unavailable_calendars_and_reports_them(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
                HttpResponse(200, {}, {"calendars": [{"id": 1, "name": "Old"}, {"id": 2, "name": "Readable"}]}),
                HttpResponse(400, {}, {"error": {"code": -425, "message": "failed to api request"}}),
                HttpResponse(200, {}, {"events": [{"id": "event-1", "uuid": "uuid-1", "calendar_id": 2, "title": "Lunch", "start_at": 1787619600000, "end_at": 1787623200000}], "chunk": False, "since": 0}),
            ]
        )
        reader = TimeTreeHttpReader(email="me@example.com", password="secret", transport=transport)

        result = reader.read("list_events", {"start_date": "2026-08-25", "end_date": "2026-08-26"})

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["calendar_id"], "2")
        self.assertEqual(result["skipped_calendars"][0]["calendar_id"], "1")
        self.assertEqual(result["skipped_calendars"][0]["error_code"], -425)
        self.assertFalse(result["sync_complete"])


    def test_sync_follows_decreasing_cursor_until_terminal_page(self):
        transport = FakeTransport([
            HttpResponse(200, {}, {"events": [{"id": "a"}], "chunk": True, "since": 200}),
            HttpResponse(200, {}, {"events": [{"id": "b"}], "chunk": True, "since": 100}),
            HttpResponse(200, {}, {"events": [{"id": "c"}], "chunk": False, "since": 50}),
        ])
        reader = TimeTreeHttpReader(transport=transport)
        events, issue = reader._sync_raw_events("1")
        self.assertEqual([e["id"] for e in events], ["a", "b", "c"])
        self.assertIsNone(issue)
        self.assertTrue(transport.calls[2]["url"].endswith("since=100"))

    def test_sync_reports_repeated_cursor_without_looping(self):
        transport = FakeTransport([
            HttpResponse(200, {}, {"events": [{"id": "a"}], "chunk": True, "since": 200}),
            HttpResponse(200, {}, {"events": [{"id": "b"}], "chunk": True, "since": 100}),
            HttpResponse(200, {}, {"events": [{"id": "c"}], "chunk": True, "since": 200}),
        ])
        reader = TimeTreeHttpReader(transport=transport)
        events, issue = reader._sync_raw_events("1")
        self.assertEqual(len(events), 3)
        self.assertEqual(issue["reason"], "repeated_cursor")
        self.assertEqual(len(transport.calls), 3)

    def test_sync_reports_invalid_cursor_and_page_limit(self):
        for cursor in (None, True, 1.5, "100", float("nan"), float("inf")):
            with self.subTest(cursor=cursor):
                reader = TimeTreeHttpReader(transport=FakeTransport([
                    HttpResponse(200, {}, {"events": [], "chunk": True, "since": cursor}),
                ]))
                _, issue = reader._sync_raw_events("1")
                self.assertEqual(issue["reason"], "invalid_cursor")
        reader = TimeTreeHttpReader(transport=FakeTransport([
            HttpResponse(200, {}, {"events": [], "chunk": True, "since": i + 1}) for i in range(25)
        ]))
        _, issue = reader._sync_raw_events("1")
        self.assertEqual(issue["reason"], "page_limit")

    def test_sync_preserves_partial_events_on_upstream_425(self):
        reader = TimeTreeHttpReader(transport=FakeTransport([
            HttpResponse(200, {}, {"events": [{"id": "a"}], "chunk": True, "since": 100}),
            HttpResponse(400, {}, {"error": {"code": -425, "message": "failed to api request"}}),
        ]))
        events, issue = reader._sync_raw_events("1")
        self.assertEqual(events, [{"id": "a"}])
        self.assertEqual(issue["error_code"], -425)

    def test_list_events_reports_partial_sync_and_retains_events(self):
        transport = FakeTransport([
            HttpResponse(200, {}, {"calendars": [{"id": 1, "name": "Readable"}]}),
            HttpResponse(200, {}, {"events": [{"id": "a", "calendar_id": 1, "start_at": "2026-08-25T12:00:00Z", "end_at": "2026-08-25T13:00:00Z"}], "chunk": True, "since": 0}),
        ])
        reader = TimeTreeHttpReader(transport=transport)
        reader._authenticated = True
        result = reader.read("list_events", {"start_date": "2026-08-25", "end_date": "2026-08-26"})
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["sync_complete"])
        self.assertEqual(result["incomplete_calendars"][0]["reason"], "repeated_cursor")


    def test_calendar_listing_filters_deactivated_flat_and_nested_records(self):
        calendars = [
            {"id": 1, "name": "Missing"},
            {"id": 2, "attributes": {"name": "Null", "deactivated_at": None}},
            {"id": 3, "name": "Zero", "deactivated_at": 0},
            {"id": 4, "name": "Inactive", "deactivated_at": 1700000000000},
            {"id": 5, "attributes": {"name": "Inactive nested", "deactivated_at": 1700000000000}},
        ]
        reader = TimeTreeHttpReader(transport=FakeTransport([HttpResponse(200, {}, {"calendars": calendars})]))
        reader._authenticated = True
        self.assertEqual([c["id"] for c in reader.read("list_calendars", {})["calendars"]], ["1", "2", "3"])

    def test_invalid_calendar_deactivation_value_is_not_silently_filtered(self):
        for value in (True, "1700000000000", "", [], float("nan"), float("inf")):
            with self.subTest(value=value):
                reader = TimeTreeHttpReader(transport=FakeTransport([HttpResponse(200, {}, {"calendars": [{"id": 1, "deactivated_at": value}]})]))
                with self.assertRaisesRegex(ValueError, "deactivated_at"):
                    reader._get_calendars()

    def test_default_event_sync_never_requests_deactivated_calendar(self):
        transport = FakeTransport([
            HttpResponse(200, {}, {"calendars": [{"id": 1, "deactivated_at": 123}, {"id": 2, "name": "Active", "deactivated_at": None}]}),
            HttpResponse(200, {}, {"events": [], "chunk": False}),
        ])
        reader = TimeTreeHttpReader(transport=transport);reader._authenticated = True
        result = reader.read("list_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"})
        self.assertTrue(result["sync_complete"])
        self.assertTrue(transport.calls[1]["url"].endswith("/calendar/2/events/sync?since=0"))

    def test_explicit_deactivated_calendar_is_rejected_before_sync(self):
        transport = FakeTransport([HttpResponse(200, {}, {"calendars": [{"id": 1, "deactivated_at": 123}]})])
        reader = TimeTreeHttpReader(transport=transport);reader._authenticated = True
        with self.assertRaisesRegex(ValueError, "Unknown TimeTree calendar_id"):
            reader.read("sync_events", {"calendar_id": "1"})
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
