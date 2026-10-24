# Exercise the draft proxy against a mock Postiz. No network except localhost.

import contextlib
import hashlib
import http.client
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import policy
import proxy

ORG_KEY = "org-key-REAL-value"
TOKEN = "pzx_" + ("ab" * 32)
SENTINEL = "UNIQUE_CONTENT_SENTINEL"


class UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _take(self):
        lengths = self.headers.get_all("Content-Length") or []
        length = int(lengths[0]) if lengths else 0
        body = self.rfile.read(length) if length else b""
        self.server.seen.append(
            {
                "method": self.command,
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "cookie": self.headers.get("Cookie"),
                "body": body,
            }
        )
        payload = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _take
    do_POST = _take
    do_PUT = _take
    do_DELETE = _take


def mcp(tool, arguments, method="tools/call"):
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {"name": tool, "arguments": arguments},
    }
    return json.dumps(message).encode("utf-8")


def draft_arguments(type_value="draft"):
    return {
        "socialPost": [
            {
                "integrationId": "chan",
                "isPremium": False,
                "date": "2026-10-26T15:00:00.000Z",
                "shortLink": False,
                "type": type_value,
                "postsAndComments": [
                    {"content": "<p>" + SENTINEL + "</p>", "attachments": []}
                ],
                "settings": [],
            }
        ]
    }


def rest_body(type_value="draft"):
    return json.dumps(
        {
            "type": type_value,
            "date": "2026-10-26T15:00:00.000Z",
            "shortLink": False,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": "chan"},
                    "value": [{"content": "<p>" + SENTINEL + "</p>", "image": []}],
                    "settings": {"__type": "facebook"},
                }
            ],
        }
    ).encode("utf-8")


class DraftProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        cls.state = os.path.join(root, "state")
        os.makedirs(cls.state, 0o700)
        cls.log_dir = os.path.join(root, "logs")
        key_path = os.path.join(root, "org-api-key")
        with open(key_path, "w", encoding="utf-8") as handle:
            handle.write(ORG_KEY + "\n")
        os.chmod(key_path, 0o600)
        os.environ["POSTIZ_ORG_API_KEY_FILE"] = key_path
        os.environ["POSTIZ_DRAFT_PROXY_STATE_DIR"] = cls.state
        os.environ["POSTIZ_DRAFT_PROXY_LOG_DIR"] = cls.log_dir
        os.environ["POSTIZ_DRAFT_PROXY_TOKENS"] = os.path.join(cls.state, "tokens.json")
        proxy._write_tokens(
            [
                {
                    "name": "writer",
                    "sha256": hashlib.sha256(TOKEN.encode("utf-8")).hexdigest(),
                    "created": "2026-10-24T00:00:00Z",
                }
            ]
        )
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        cls.upstream.seen = []
        cls.upstream.daemon_threads = True
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.proxy_httpd = proxy.listen(
            ("127.0.0.1", 0),
            ("127.0.0.1", cls.upstream.server_address[1]),
            cls.log_dir,
        )
        threading.Thread(target=cls.proxy_httpd.serve_forever, daemon=True).start()
        cls.port = cls.proxy_httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.proxy_httpd.shutdown()
        cls.upstream.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        self.upstream.seen = []

    def request(self, method, path, body=None, token=TOKEN, extra=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            headers["Content-Type"] = "application/json"
        if extra:
            headers.update(extra)
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        status = resp.status
        conn.close()
        return status, payload

    def log_text(self):
        path = os.path.join(self.log_dir, "proxy.log")
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def assert_rejected(self, status, payload, reason_part):
        self.assertGreaterEqual(status, 400)
        self.assertLess(status, 500)
        body = json.loads(payload.decode("utf-8"))
        self.assertIn(reason_part, body["error"])
        self.assertEqual(self.upstream.seen, [])
        text = self.log_text()
        self.assertNotIn(ORG_KEY, text)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(SENTINEL, text)
        last = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(last["decision"], "reject")
        self.assertEqual(last["bot"], "writer" if status != 401 else last["bot"])

    def test_mcp_schedule_blocked(self):
        status, payload = self.request(
            "POST", "/api/mcp", mcp("schedulePostTool", draft_arguments("schedule"))
        )
        self.assert_rejected(status, payload, "type-not-draft")

    def test_mcp_now_blocked(self):
        status, payload = self.request(
            "POST", "/api/mcp", mcp("schedulePostTool", draft_arguments("now"))
        )
        self.assert_rejected(status, payload, "type-not-draft")

    def test_rest_schedule_blocked(self):
        status, payload = self.request("POST", "/api/public/v1/posts", rest_body("schedule"))
        self.assert_rejected(status, payload, "type-not-draft")

    def test_rest_now_blocked(self):
        status, payload = self.request("POST", "/api/public/v1/posts", rest_body("now"))
        self.assert_rejected(status, payload, "type-not-draft")

    def test_case_and_whitespace_type(self):
        for value in ("Draft", " draft", "draft ", "DRAFT", "now "):
            status, payload = self.request(
                "POST", "/api/mcp", mcp("schedulePostTool", draft_arguments(value))
            )
            self.assert_rejected(status, payload, "type-not-draft")
            status, payload = self.request(
                "POST", "/api/public/v1/posts", rest_body(value)
            )
            self.assert_rejected(status, payload, "type-not-draft")

    def test_duplicate_key_rejected(self):
        raw = (
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"schedulePostTool",'
            b'"arguments":{"socialPost":[{"type":"draft","type":"now","integrationId":"chan"}]}}}'
        )
        status, payload = self.request("POST", "/api/mcp", raw)
        self.assert_rejected(status, payload, "duplicate-key")
        raw_rest = b'{"type":"draft","type":"schedule","shortLink":false,"posts":[]}'
        status, payload = self.request("POST", "/api/public/v1/posts", raw_rest)
        self.assert_rejected(status, payload, "duplicate-key")

    def test_batch_smuggle_rejected(self):
        batch = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "postsListTool", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "schedulePostTool",
                    "arguments": draft_arguments("now"),
                },
            },
        ]
        status, payload = self.request(
            "POST", "/api/mcp", json.dumps(batch).encode("utf-8")
        )
        self.assert_rejected(status, payload, "type-not-draft")
        last = json.loads(self.log_text().strip().splitlines()[-1])
        self.assertTrue(last["reason"].startswith("batch-"))

    def test_unknown_tool_delete_put_and_comments(self):
        status, payload = self.request(
            "POST",
            "/api/mcp",
            mcp("deletePostTool", {"id": "post-1"}),
        )
        self.assert_rejected(status, payload, "unknown-tool")
        status, payload = self.request(
            "PUT",
            "/api/public/v1/posts/post-1/status",
            b'{"status":"schedule"}',
        )
        self.assert_rejected(status, payload, "method-not-allowed")
        status, payload = self.request("DELETE", "/api/public/v1/posts/post-1", b"")
        self.assert_rejected(status, payload, "method-not-allowed")
        status, payload = self.request(
            "POST",
            "/api/public/posts/post-1/comments",
            b'{"comment":"hi"}',
        )
        self.assert_rejected(status, payload, "unknown-path")
        status, payload = self.request(
            "POST", "/api/public/v1/upload", b'{"file":"x"}'
        )
        self.assert_rejected(status, payload, "unknown-path")

    def test_bad_token_and_malformed_json(self):
        status, payload = self.request(
            "POST", "/api/mcp", mcp("postsListTool", {}), token="pzx_nope"
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.upstream.seen, [])
        self.assertNotIn(ORG_KEY, self.log_text())
        status, payload = self.request("POST", "/api/mcp", b"{")
        self.assert_rejected(status, payload, "malformed-json")
        status, payload = self.request(
            "POST",
            "/api/mcp",
            mcp("postsListTool", {}),
            extra={"Content-Type": "text/plain"},
        )
        self.assert_rejected(status, payload, "bad-content-type")

    def test_oversized_body(self):
        blob = b"{" + (b"a" * policy.MAX_BODY_BYTES)
        status, payload = self.request("POST", "/api/mcp", blob)
        self.assertEqual(status, 413)
        self.assertEqual(self.upstream.seen, [])
        body = json.loads(payload.decode("utf-8"))
        self.assertEqual(body["error"], "body-too-large")

    def test_truncated_chunked_body(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        head = (
            "POST /api/mcp HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Authorization: Bearer " + TOKEN + "\r\n"
            "Content-Type: application/json\r\n"
            "Transfer-Encoding: chunked\r\n"
            "\r\n"
            "5\r\nhello\r\n"
        )
        sock.sendall(head.encode("ascii"))
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            piece = sock.recv(4096)
            if not piece:
                break
            data += piece
        sock.close()
        self.assertIn(b"400", data.split(b"\r\n", 1)[0])
        self.assertEqual(self.upstream.seen, [])

    def test_draft_and_list_forward_with_injected_key(self):
        raw = mcp("schedulePostTool", draft_arguments("draft"))
        status, payload = self.request("POST", "/api/mcp", raw, extra={"Cookie": "session=owner"})
        self.assertEqual(status, 200)
        self.assertEqual(payload, b'{"ok":true}')
        seen = self.upstream.seen[-1]
        self.assertEqual(seen["auth"], "Bearer " + ORG_KEY)
        self.assertNotIn(TOKEN, seen["auth"])
        self.assertIsNone(seen["cookie"])
        self.assertEqual(seen["body"], raw)
        last = json.loads(self.log_text().strip().splitlines()[-1])
        self.assertEqual(last["decision"], "allow")
        self.assertEqual(last["bot"], "writer")
        self.assertEqual(last["tool"], "schedulePostTool")
        self.assertEqual(last["type"], "draft")
        self.assertNotIn(SENTINEL, self.log_text())
        self.assertNotIn(ORG_KEY, self.log_text())

        raw_rest = rest_body("draft")
        status, payload = self.request("POST", "/api/public/v1/posts", raw_rest)
        self.assertEqual(status, 200)
        seen = self.upstream.seen[-1]
        self.assertEqual(seen["auth"], ORG_KEY)
        self.assertFalse(seen["auth"].startswith("Bearer "))
        self.assertEqual(seen["body"], raw_rest)

        status, payload = self.request(
            "POST",
            "/api/mcp",
            mcp(
                "postsListTool",
                {"startDate": "2026-10-01T00:00:00.000Z", "endDate": "2026-11-01T00:00:00.000Z"},
            ),
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream.seen[-1]["auth"], "Bearer " + ORG_KEY)

        status, payload = self.request(
            "GET",
            "/api/public/v1/posts?startDate=2026-10-01T00:00:00.000Z&endDate=2026-11-01T00:00:00.000Z",
        )
        self.assertEqual(status, 200)
        seen = self.upstream.seen[-1]
        self.assertEqual(seen["method"], "GET")
        self.assertEqual(seen["auth"], ORG_KEY)
        self.assertEqual(seen["body"], b"")

        status, payload = self.request(
            "POST",
            "/api/mcp",
            json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            ).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream.seen[-1]["auth"], "Bearer " + ORG_KEY)

    def test_nested_type_and_status_rejected(self):
        body = rest_body("draft")
        parsed = json.loads(body.decode("utf-8"))
        parsed["posts"][0]["type"] = "now"
        status, payload = self.request(
            "POST", "/api/public/v1/posts", json.dumps(parsed).encode("utf-8")
        )
        self.assert_rejected(status, payload, "type-not-draft")
        arguments = draft_arguments("draft")
        arguments["socialPost"][0]["settings"] = [{"key": "status", "value": "schedule"}]
        status, payload = self.request(
            "POST", "/api/mcp", mcp("schedulePostTool", arguments)
        )
        self.assert_rejected(status, payload, "status-not-allowed")

    def test_chunked_draft_is_buffered_then_forwarded(self):
        raw = mcp("schedulePostTool", draft_arguments("draft"))
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        head = (
            "POST /api/mcp HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Authorization: Bearer " + TOKEN + "\r\n"
            "Content-Type: application/json\r\n"
            "Transfer-Encoding: chunked\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        sock.sendall(head.encode("ascii"))
        sock.sendall(("%x\r\n" % len(raw)).encode("ascii") + raw + b"\r\n")
        sock.sendall(b"0\r\n\r\n")
        data = b""
        while True:
            piece = sock.recv(4096)
            if not piece:
                break
            data += piece
        sock.close()
        self.assertIn(b"200", data.split(b"\r\n", 1)[0])
        self.assertEqual(len(self.upstream.seen), 1)
        self.assertEqual(self.upstream.seen[0]["body"], raw)
        self.assertEqual(self.upstream.seen[0]["auth"], "Bearer " + ORG_KEY)

    def test_token_cli(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = proxy.token_add("artist")
        self.assertEqual(code, 0)
        issued = stdout.getvalue().strip()
        self.assertTrue(issued.startswith("pzx_"))
        with open(proxy.tokens_path(), "r", encoding="utf-8") as handle:
            stored = handle.read()
        self.assertNotIn(issued, stored)
        listed = io.StringIO()
        with contextlib.redirect_stdout(listed):
            self.assertEqual(proxy.token_list(), 0)
        self.assertIn("artist", listed.getvalue())
        self.assertNotIn(issued, listed.getvalue())
        self.assertEqual(proxy.token_revoke("artist"), 0)
        listed = io.StringIO()
        with contextlib.redirect_stdout(listed):
            proxy.token_list()
        self.assertNotIn("artist", listed.getvalue())
        self.assertIn("writer", listed.getvalue())


if __name__ == "__main__":
    unittest.main()
