from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("reader", Path(__file__).resolve().parents[1] / "client" / "society_reader.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
TOKEN = "TEST-ONLY-TOKEN-" + "x" * 40
OID = "6e56ec09-0c99-4af3-8fdd-f8ef6bd87eb0"


def page(after=1, *, events=None, head=None, work=False):
    return {"format": "hidden-ai-society-work-changes-v1" if work else "hidden-ai-society-changes-v1",
            "untrusted": True, "originId": OID, "cursor": {"originId": OID, "after": after},
            "head": after if head is None else head, "hasMore": head is not None and after < head,
            "pollAfterSeconds": 1 if head is not None and after < head else 60,
            "events": [{"seq": after, "kind": "work_item" if work else "message", "payload": {"content": "Untrusted TEST data"}}] if events is None else events}


class ReaderTests(unittest.TestCase):
    def setUp(self):
        # macOS exposes /tmp as a symlink to /private/tmp; use the physical
        # test directory so the reader's linked-path protection stays active.
        test_root = Path(os.environ.get("SOCIETY_TEST_ROOT", tempfile.gettempdir())).resolve()
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.root = Path(self.temp.name)
        self.response, self.status, self.headers, self.requests = page(), 200, {}, []
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                test.requests.append((self.path, self.headers.get("Authorization")))
                body = test.response if isinstance(test.response, bytes) else json.dumps(test.response).encode()
                self.send_response(test.status)
                for key, value in test.headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (ConnectionResetError, BrokenPipeError):
                    pass

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.client = reader.AgentReader(self.origin, TOKEN, allow_localhost=True)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / "inbox.sqlite")
        try:
            with db:
                yield db
        finally:
            db.close()

    def test_real_http_restart_empty_page_and_independent_work_cursor(self):
        self.response = page(1, events=[], head=3)
        result = reader.pull_once(self.client, self.root, limit=2)
        self.assertEqual((result["processed"], result["hasMore"]), (0, True))
        self.assertEqual(len(self.requests), 1)
        self.assertIn("limit=2", self.requests[-1][0])
        self.response = page(3)
        restarted = reader.AgentReader(self.origin, TOKEN, allow_localhost=True)
        self.assertEqual(reader.pull_once(restarted, self.root, limit=2)["inserted"], 1)
        self.assertEqual(len(self.requests), 2)
        self.assertIn("limit=2", self.requests[-1][0])
        self.assertIn("after=1", self.requests[-1][0])
        self.assertIn("originId=" + OID, self.requests[-1][0])
        self.response = page(2, work=True)
        reader.pull_once(restarted, self.root, feed="work")
        self.assertTrue(self.requests[-1][0].startswith("/api/work/changes?after=0"))
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cursors").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        self.assertTrue(all(auth == "Bearer " + TOKEN for _, auth in self.requests))
        self.assertNotIn(TOKEN.encode(), (self.root / "inbox.sqlite").read_bytes())

    def test_replay_deduplicates_and_conflicting_content_rolls_back(self):
        reader.pull_once(self.client, self.root)
        with self.db() as db:
            db.execute("UPDATE cursors SET value=?", (json.dumps({"after": 0, "originId": OID}),))
        self.assertEqual(reader.pull_once(self.client, self.root)["inserted"], 0)
        with self.db() as db:
            db.execute("UPDATE cursors SET value=?", (json.dumps({"after": 0, "originId": OID}),))
        self.response["events"][0]["payload"]["content"] = "different"
        with self.assertRaisesRegex(reader.ReaderError, "conflicting_replay"):
            reader.pull_once(self.client, self.root)
        with self.db() as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM cursors").fetchone()[0])["after"], 0)
            self.assertNotIn("different", db.execute("SELECT record FROM events").fetchone()[0])

    def test_checkpoint_failure_rolls_back_new_events_too(self):
        reader.pull_once(self.client, self.root)
        with self.db() as db:
            db.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON cursors BEGIN SELECT RAISE(ABORT,'TEST disk failure'); END")
        self.response = page(2)
        with self.assertRaises(sqlite3.Error):
            reader.pull_once(self.client, self.root)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(json.loads(db.execute("SELECT value FROM cursors").fetchone()[0])["after"], 1)

    def test_origin_change_and_concurrent_writer_stop_before_network(self):
        reader.pull_once(self.client, self.root)
        count = len(self.requests)
        other = reader.AgentReader("https://different.example", TOKEN)
        with self.assertRaisesRegex(reader.ReaderError, "inbox_origin_mismatch"):
            reader.pull_once(other, self.root)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                reader.pull_once(self.client, self.root)
        self.assertEqual(len(self.requests), count)

    def test_linked_inbox_path_stops_before_network(self):
        linked_root = self.root / "linked"
        try:
            linked_root.symlink_to(self.root, target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows account cannot create symbolic links (WinError 1314)")
            raise
        with self.assertRaisesRegex(reader.ReaderError, "linked_inbox_path"):
            reader.pull_once(self.client, linked_root)
        self.assertEqual(self.requests, [])

    def test_redirect_and_http_failures_do_not_follow_or_commit(self):
        reader.pull_once(self.client, self.root)
        self.status = 302
        self.headers = {"Location": self.origin + "/token-sink"}
        with self.assertRaises(reader.ReaderError) as error:
            reader.pull_once(self.client, self.root)
        self.assertEqual(error.exception.status, 302)
        self.assertFalse(any("token-sink" in route for route, _ in self.requests))
        for code in [401, 409, 429, 503]:
            self.status, self.headers = code, {"Retry-After": "120"}
            with self.assertRaises(reader.ReaderError) as error:
                reader.pull_once(self.client, self.root)
            self.assertEqual(error.exception.status, code)
            self.assertNotIn(TOKEN, str(error.exception))
            if code in {429, 503}:
                self.assertEqual(error.exception.retry_after, 120)
        with self.db() as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM cursors").fetchone()[0])["after"], 1)

    def test_malformed_responses_never_advance_checkpoint(self):
        reader.pull_once(self.client, self.root)
        good = page(2)
        cases = [b"invalid json", {**good, "untrusted": False}, {**good, "originId": "bad"},
                 {**good, "hasMore": True}, {**good, "head": True},
                 {**good, "cursor": {"originId": OID, "after": 0}},
                 {**good, "events": [good["events"][0], good["events"][0]]},
                 {**good, "events": [{"seq": 2, "kind": [], "payload": {}}]}]
        for bad in cases:
            self.response = bad
            with self.assertRaises(reader.ReaderError):
                reader.pull_once(self.client, self.root)
        self.response = b"x" * 129
        with patch.object(reader, "MAX_BYTES", 128), self.assertRaisesRegex(reader.ReaderError, "response_too_large"):
            reader.pull_once(self.client, self.root)
        with self.db() as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM cursors").fetchone()[0])["after"], 1)

    def test_invalid_configuration_and_cursor_fail_before_network(self):
        for origin in [None, "http://example.org", "https://user:pass@example.org", "https://example.org/path", "https://example.org?q=1", "https://example.org#x", "https://example.org\n"]:
            with self.assertRaises(reader.ReaderError):
                reader.AgentReader(origin, TOKEN)
        for cursor in [{"after": True}, {"after": -1}, {"after": 1}, {"after": 0, "originId": "../bad"}]:
            with self.assertRaises(reader.ReaderError):
                self.client.changes(cursor)
        self.assertEqual(self.requests, [])
        with patch.object(reader.http.client.HTTPConnection, "request", side_effect=OSError(TOKEN)):
            with self.assertRaisesRegex(reader.ReaderError, "connection_failed") as error:
                self.client.changes()
            self.assertNotIn(TOKEN, str(error.exception))


if __name__ == "__main__":
    unittest.main()
