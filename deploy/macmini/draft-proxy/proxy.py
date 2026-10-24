#!/usr/bin/env python3
# Draft-only gatekeeper in front of Postiz.
#
# Python stdlib, no third-party packages. The allowlist lives in policy.py
# so it can be read on its own. This file loads secrets, buffers the body,
# and forwards only what policy.py already accepted.
#
# The organization API key is read from the macOS Keychain item
# (service postiz-macmini, account org-api-key) or from a mode-0600 file.
# It is never written to the log. Bots authenticate with their own tokens,
# stored as sha256 hashes.

import hashlib
import hmac
import http.client
import json
import logging
import logging.handlers
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import policy

FORWARD_REQUEST_HEADERS = (
    "accept",
    "content-type",
    "mcp-session-id",
    "mcp-protocol-version",
    "last-event-id",
    "mcp-method",
)

SKIP_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "set-cookie",
        "authorization",
        "cookie",
    }
)

_BOT_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_LOG = logging.getLogger("postiz.draft-proxy")


class ProxyError(Exception):
    def __init__(self, status, reason, tool="", type_label=""):
        super().__init__(reason)
        self.status = int(status)
        self.reason = str(reason)
        self.tool = tool
        self.type_label = type_label


def _state_dir():
    override = os.environ.get("POSTIZ_DRAFT_PROXY_STATE_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".postiz-draft-proxy")


def tokens_path():
    override = os.environ.get("POSTIZ_DRAFT_PROXY_TOKENS")
    if override:
        return override
    return os.path.join(_state_dir(), "tokens.json")


def _log_dir():
    override = os.environ.get("POSTIZ_DRAFT_PROXY_LOG_DIR")
    if override:
        return override
    return os.path.join(
        os.path.expanduser("~"), "Library", "Logs", "postiz-draft-proxy"
    )


def _require_mode(path, expected):
    mode = os.stat(path).st_mode & 0o777
    if mode != expected:
        raise ProxyError(503, "insecure-file-mode")


def _read_secret_file(path):
    _require_mode(path, 0o600)
    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read()
    if raw.endswith("\n"):
        raw = raw[:-1]
    if not raw or "\n" in raw or "\r" in raw:
        raise ProxyError(503, "org-key-unavailable")
    return raw


def _keychain_key():
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "postiz-macmini",
                "-a",
                "org-api-key",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout
    if raw.endswith("\n"):
        raw = raw[:-1]
    if not raw or "\n" in raw or "\r" in raw:
        raise ProxyError(503, "org-key-unavailable")
    return raw


def load_org_key():
    """Return the real Postiz org key. Do not log the return value."""
    override = os.environ.get("POSTIZ_ORG_API_KEY_FILE")
    if override:
        return _read_secret_file(override)
    default = os.path.join(_state_dir(), "org-api-key")
    if os.path.exists(default):
        return _read_secret_file(default)
    key = _keychain_key()
    if key:
        return key
    raise ProxyError(503, "org-key-unavailable")


def _load_token_records():
    path = tokens_path()
    if not os.path.exists(path):
        return []
    _require_mode(path, 0o600)
    parent = os.path.dirname(path)
    if os.path.isdir(parent):
        _require_mode(parent, 0o700)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError:
        raise ProxyError(503, "token-store-unavailable")
    bots = data.get("bots")
    if not isinstance(bots, list):
        raise ProxyError(503, "token-store-unavailable")
    return bots


def authenticate(header):
    if not header or not header.startswith("Bearer ") or header != header.strip():
        raise ProxyError(401, "missing-token")
    token = header[7:]
    if (
        not token
        or token != token.strip()
        or "\n" in token
        or "\r" in token
        or len(token) > 200
    ):
        raise ProxyError(401, "bad-token")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    found = ""
    for record in _load_token_records():
        stored = record.get("sha256")
        name = record.get("name")
        if (
            isinstance(stored, str)
            and len(stored) == len(digest)
            and hmac.compare_digest(digest, stored)
            and isinstance(name, str)
        ):
            found = name
    if not found:
        raise ProxyError(401, "bad-token")
    return found


def _write_tokens(records):
    path = tokens_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, 0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    blob = json.dumps({"bots": records}, indent=2) + "\n"
    tmp = path + ".tmp." + str(os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(blob)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)


def token_add(name):
    if not _BOT_NAME.match(name or ""):
        print("Bot name must match ^[a-z][a-z0-9-]{0,62}$.", file=sys.stderr)
        return 1
    try:
        records = _load_token_records()
    except ProxyError as exc:
        print(exc.reason, file=sys.stderr)
        return 1
    if any(item.get("name") == name for item in records):
        print("That bot already has a token. Revoke it before issuing another.", file=sys.stderr)
        return 1
    token = "pzx_" + secrets.token_hex(32)
    records.append(
        {
            "name": name,
            "sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    _write_tokens(records)
    # The token is shown once, on stdout, so the owner can hand it to that bot.
    # It is not written to the proxy log.
    print(token)
    print("Save this token now. It will not be shown again. Bot: " + name, file=sys.stderr)
    return 0


def token_list():
    try:
        records = _load_token_records()
    except ProxyError as exc:
        print(exc.reason, file=sys.stderr)
        return 1
    for record in records:
        name = record.get("name")
        created = record.get("created")
        if isinstance(name, str) and _BOT_NAME.match(name):
            when = created if isinstance(created, str) else ""
            print(name + "\t" + when)
    return 0


def token_revoke(name):
    if not _BOT_NAME.match(name or ""):
        print("Unknown bot name.", file=sys.stderr)
        return 1
    try:
        records = _load_token_records()
    except ProxyError as exc:
        print(exc.reason, file=sys.stderr)
        return 1
    kept = [item for item in records if item.get("name") != name]
    if len(kept) == len(records):
        print("No token for that bot.", file=sys.stderr)
        return 1
    _write_tokens(kept)
    print("Revoked " + name, file=sys.stderr)
    return 0


def _parse_host_port(raw, what):
    host, sep, port_text = raw.rpartition(":")
    if sep != ":" or host != "127.0.0.1":
        raise SystemExit(what + " must be 127.0.0.1:<port>")
    if not port_text.isdigit():
        raise SystemExit(what + " has a bad port")
    port = int(port_text)
    if port < 0 or port > 65535:
        raise SystemExit(what + " has a bad port")
    return host, port


def read_body(method, headers, rfile):
    """Buffer the whole body or fail. Never returns a partial body."""
    encodings = headers.get_all("Transfer-Encoding") or []
    lengths = headers.get_all("Content-Length") or []
    if encodings and lengths:
        raise ProxyError(400, "ambiguous-framing")
    if encodings:
        if len(encodings) != 1 or encodings[0].strip().lower() != "chunked":
            raise ProxyError(400, "bad-transfer-encoding")
        return _read_chunked(rfile, policy.MAX_BODY_BYTES)
    if not lengths:
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            raise ProxyError(411, "length-required")
        return b""
    if len(lengths) != 1:
        raise ProxyError(400, "ambiguous-framing")
    raw = lengths[0].strip()
    if not raw.isdigit() or (len(raw) > 1 and raw.startswith("0")):
        raise ProxyError(400, "bad-content-length")
    length = int(raw)
    if length > policy.MAX_BODY_BYTES:
        raise ProxyError(413, "body-too-large")
    buf = rfile.read(length)
    if len(buf) != length:
        raise ProxyError(400, "truncated-body")
    return buf


def _read_chunked(rfile, limit):
    total = 0
    out = bytearray()
    while True:
        line = rfile.readline(80)
        if not line or len(line) >= 80:
            raise ProxyError(400, "truncated-body")
        if line.endswith(b"\r\n"):
            line = line[:-2]
        elif line.endswith(b"\n"):
            line = line[:-1]
        else:
            raise ProxyError(400, "truncated-body")
        size_hex = line.split(b";", 1)[0]
        try:
            size = int(size_hex, 16)
        except ValueError:
            raise ProxyError(400, "truncated-body")
        if size < 0:
            raise ProxyError(400, "truncated-body")
        if size == 0:
            while True:
                trailer = rfile.readline(1024)
                if trailer in (b"\r\n", b"\n", b""):
                    break
                if len(trailer) >= 1024:
                    raise ProxyError(400, "truncated-body")
            return bytes(out)
        if total + size > limit:
            raise ProxyError(413, "body-too-large")
        chunk = rfile.read(size)
        if len(chunk) != size:
            raise ProxyError(400, "truncated-body")
        marker = rfile.read(2)
        if marker not in (b"\r\n", b"\n"):
            raise ProxyError(400, "truncated-body")
        out.extend(chunk)
        total += size


def configure_logging(directory):
    os.makedirs(directory, 0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    path = os.path.join(directory, "proxy.log")
    _LOG.handlers = []
    _LOG.setLevel(logging.INFO)
    _LOG.propagate = False
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1000000, backupCount=5
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOG.addHandler(handler)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def log_line(bot, method, path, tool, type_label, decision, reason):
    _LOG.info(
        json.dumps(
            {
                "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "bot": bot or "-",
                "method": method or "",
                "path": (path or "").split("?", 1)[0][:200],
                "tool": tool or "",
                "type": type_label or "",
                "decision": decision,
                "reason": reason or "",
            },
            ensure_ascii=True,
        )
    )


def _as_proxy_error(exc):
    if isinstance(exc, ProxyError):
        return exc
    if isinstance(exc, policy.PolicyError):
        return ProxyError(exc.status, exc.reason, exc.tool, exc.type_label)
    return ProxyError(500, "internal")


class DraftProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = ("127.0.0.1", 4007)

    def log_message(self, fmt, *args):
        return

    def handle_one_request(self):
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.close_connection = True
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            self._handle()
        except (TimeoutError, OSError):
            self.close_connection = True

    def _handle(self):
        bot = "-"
        tool = ""
        type_label = ""
        self._responded = False
        try:
            self.connection.settimeout(30)
            auths = self.headers.get_all("Authorization") or []
            if len(auths) != 1:
                raise ProxyError(401, "missing-token")
            bot = authenticate(auths[0])
            types = self.headers.get_all("Content-Type") or []
            if len(types) > 1:
                raise ProxyError(415, "bad-content-type")
            body = read_body(self.command, self.headers, self.rfile)
            decision = policy.route(
                self.command,
                self.path,
                types[0] if types else None,
                body,
            )
            tool = decision.tool
            type_label = decision.type_label
            key = load_org_key()
            self.connection.settimeout(None)
            self._forward(decision, body, key)
            log_line(bot, self.command, self.path, tool, type_label, "allow", "ok")
        except Exception as exc:
            err = _as_proxy_error(exc)
            tool = tool or err.tool
            type_label = type_label or err.type_label
            if self._responded:
                self.close_connection = True
                log_line(
                    bot,
                    self.command,
                    getattr(self, "path", ""),
                    tool,
                    type_label,
                    "reject",
                    err.reason,
                )
                return
            self._reject(err.status, err.reason, bot, tool, type_label)

    def _reject(self, status, reason, bot, tool, type_label):
        self.close_connection = True
        log_line(bot, self.command, getattr(self, "path", ""), tool, type_label, "reject", reason)
        payload = json.dumps({"error": reason}).encode("ascii")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            return

    def _forward(self, decision, body, key):
        host, port = self.upstream
        conn = http.client.HTTPConnection(host, port, timeout=None)
        headers = {}
        for name in FORWARD_REQUEST_HEADERS:
            value = self.headers.get(name)
            if value and "\n" not in value and "\r" not in value:
                headers[name] = value
        if decision.auth_style == "bearer":
            headers["Authorization"] = "Bearer " + key
        else:
            headers["Authorization"] = key
        if body:
            headers["Content-Length"] = str(len(body))
            headers.setdefault("Content-Type", "application/json")
        try:
            conn.request(self.command, self.path, body=body if body else None, headers=headers)
            resp = conn.getresponse()
        except OSError:
            conn.close()
            raise ProxyError(502, "upstream-unavailable")
        self._responded = True
        self.send_response(resp.status)
        for name, value in resp.getheaders():
            if name.lower() in SKIP_RESPONSE_HEADERS:
                continue
            if "\n" in value or "\r" in value:
                continue
            self.send_header(name, value)
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            self.wfile.write(chunk)
        conn.close()


def listen(bind, upstream, log_directory):
    configure_logging(log_directory)
    handler = type(
        "BoundDraftProxyHandler",
        (DraftProxyHandler,),
        {"upstream": upstream},
    )
    httpd = ThreadingHTTPServer(bind, handler)
    httpd.daemon_threads = True
    return httpd


def serve():
    bind = _parse_host_port(
        os.environ.get("POSTIZ_DRAFT_PROXY_BIND", "127.0.0.1:4017"), "bind"
    )
    upstream = _parse_host_port(
        os.environ.get("POSTIZ_DRAFT_PROXY_UPSTREAM", "127.0.0.1:4007"), "upstream"
    )
    httpd = listen(bind, upstream, _log_dir())
    print(
        "draft-proxy listening on http://%s:%s" % (bind[0], httpd.server_address[1]),
        file=sys.stderr,
    )
    httpd.serve_forever()


def main(argv):
    if len(argv) >= 2 and argv[1] == "serve":
        serve()
        return 0
    if len(argv) >= 4 and argv[1] == "token" and argv[2] == "add":
        return token_add(argv[3])
    if len(argv) >= 3 and argv[1] == "token" and argv[2] == "list":
        return token_list()
    if len(argv) >= 4 and argv[1] == "token" and argv[2] == "revoke":
        return token_revoke(argv[3])
    print(
        "Usage: proxy.py serve | token add NAME | token list | token revoke NAME",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
