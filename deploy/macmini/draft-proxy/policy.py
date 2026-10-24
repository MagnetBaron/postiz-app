# Draft-only allowlist for the Mac mini gatekeeper.
#
# Python stdlib on purpose: the whole decision is in this file, with no
# packages to audit. proxy.py does I/O. This module does not.
#
# Tool ids are the MCP `id` values in libraries/nestjs-libraries/src/chat/tools/
# at upstream v2.25.2, not the TypeScript class names.
#
# Allowed tools/call names:
#   postsListTool, integrationList, groupList, integrationSchema,
#   mediaListTool, mediaStatusTool
#   schedulePostTool only when every "type" value is exactly "draft"
#
# Denied even when the source marks readOnlyHint true, because they exist
# to drive a write workflow (video, clipping, or upload):
#   generateVideoOptions, videoFunctionTool, videoStatusTool,
#   clippingStatusTool, uploadWidgetStatusTool
#
# Denied writes:
#   triggerTool, postSettingsTool, deletePostTool, generateImageTool,
#   generateVideoTool, clippingTool, clippingWidgetTicketTool,
#   uploadFromUrlTool, uploadWidgetTool, uploadWidgetTicketTool
#
# Publishing in this version is body.type of "schedule" or "now"
# (posts.service.ts createPost) and PUT status "schedule"
# (ChangePostStatusDto, changePostStatus). A date field is stored on a
# draft and does not queue while type stays "draft". settings.__type is
# the provider id, a different key from "type".

import json
import re

MAX_BODY_BYTES = 1024 * 1024

READ_ONLY_TOOLS = frozenset(
    {
        "postsListTool",
        "integrationList",
        "groupList",
        "integrationSchema",
        "mediaListTool",
        "mediaStatusTool",
    }
)

SCHEDULE_TOOL = "schedulePostTool"

_NOTIFICATION = re.compile(r"^notifications/[A-Za-z][A-Za-z0-9._-]{0,80}$")

_GET_EXACT = frozenset(
    {
        "/api/public/v1/posts",
        "/api/public/v1/is-connected",
        "/api/public/v1/media",
        "/api/public/v1/me",
        "/api/public/v1/groups",
        "/api/public/v1/integrations",
        "/api/public/v1/notifications",
    }
)

_GET_ONE = (
    re.compile(r"^/api/public/v1/posts/[^/]+/missing$"),
    re.compile(r"^/api/public/v1/media/[^/]+/status$"),
    re.compile(r"^/api/public/v1/integration-settings/[^/]+$"),
    re.compile(r"^/api/public/v1/find-slot/[^/]+$"),
    re.compile(r"^/api/public/v1/analytics/post/[^/]+$"),
    re.compile(r"^/api/public/v1/analytics/[^/]+$"),
)

# Query keys that select a publish path or carry a credential.
_QUERY_DENY = frozenset(
    {
        "type",
        "status",
        "republish",
        "authorization",
        "api_key",
        "apikey",
        "token",
        "key",
    }
)

POST_PATH = "/api/public/v1/posts"
MCP_PATH = "/api/mcp"


_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")
_SAFE_TYPE = re.compile(r"[A-Za-z0-9 ]{1,32}")


class PolicyError(Exception):
    def __init__(self, status, reason, tool="", type_label=""):
        super().__init__(reason)
        self.status = int(status)
        self.reason = str(reason)
        self.tool = tool
        self.type_label = type_label


class Decision(object):
    def __init__(self, tool, type_label, auth_style):
        self.tool = tool
        self.type_label = type_label
        self.auth_style = auth_style  # "bearer" or "raw"


def parse_json(raw):
    """Parse one JSON value. Duplicate keys are an error, not last-wins."""

    def pairs(items):
        seen = {}
        for key, value in items:
            if key in seen:
                raise PolicyError(400, "duplicate-key")
            seen[key] = value
        return seen

    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise PolicyError(400, "malformed-json")
    if text.startswith("\ufeff"):
        raise PolicyError(400, "malformed-json")
    try:
        value = json.loads(text, object_pairs_hook=pairs)
    except PolicyError:
        raise
    except ValueError:
        raise PolicyError(400, "malformed-json")
    return value


def _walk(node, visit):
    if isinstance(node, dict):
        for key, value in node.items():
            visit(key, value)
            _walk(value, visit)
    elif isinstance(node, list):
        for item in node:
            _walk(item, visit)


def _reject_publish_keys(node, require_draft_type):
    """Reject status, republish, and any type other than the exact string draft.

    require_draft_type is for schedulePostTool and REST create, where a
    missing or non-draft type publishes or is ambiguous. Read-only tools
    pass False and still reject a type/status/republish key, because those
    keys are not in their schemas.
    """

    def visit(key, value):
        if key == "status":
            raise PolicyError(403, "status-not-allowed")
        if key == "republish":
            raise PolicyError(403, "republish-not-allowed")
        if key == "type":
            if value != "draft":
                raise PolicyError(403, "type-not-draft")
            if not require_draft_type:
                raise PolicyError(403, "type-not-allowed")

    _walk(node, visit)


def _check_schedule(arguments):
    if not isinstance(arguments, dict):
        raise PolicyError(403, "arguments-not-object")
    posts = arguments.get("socialPost")
    if not isinstance(posts, list) or not posts:
        raise PolicyError(403, "social-post-missing")
    _reject_publish_keys(arguments, True)
    for post in posts:
        if not isinstance(post, dict) or post.get("type") != "draft":
            raise PolicyError(403, "type-not-draft")
        # MCP settings are {key, value} pairs, reduced onto the post settings
        # object. A pair whose key is type or status is not the publish
        # switch (that is socialPost.type), and it is not part of the schema.
        settings = post.get("settings")
        if settings is None:
            continue
        if not isinstance(settings, list):
            raise PolicyError(403, "settings-not-array")
        for setting in settings:
            if not isinstance(setting, dict):
                raise PolicyError(403, "settings-not-array")
            if setting.get("key") in ("type", "status", "republish"):
                raise PolicyError(403, "status-not-allowed")
    return Decision(SCHEDULE_TOOL, "draft", "bearer")


def _check_read_tool(name, arguments):
    if name not in READ_ONLY_TOOLS:
        raise PolicyError(403, "unknown-tool")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise PolicyError(403, "arguments-not-object")
    _reject_publish_keys(arguments, False)
    return Decision(name, "", "bearer")


def _check_tool_call(message):
    params = message.get("params")
    if not isinstance(params, dict):
        raise PolicyError(403, "params-not-object")
    extra = set(params.keys()) - {"name", "arguments"}
    if extra:
        raise PolicyError(403, "unexpected-param")
    name = params.get("name")
    if not isinstance(name, str) or not _TOOL_NAME.match(name):
        raise PolicyError(403, "unknown-tool")
    arguments = params.get("arguments")
    try:
        if name == SCHEDULE_TOOL:
            return _check_schedule(arguments)
        return _check_read_tool(name, arguments)
    except PolicyError as exc:
        if not exc.tool:
            exc.tool = name
        if name == SCHEDULE_TOOL and not exc.type_label:
            exc.type_label = _type_hint(arguments)
        raise


def _check_rpc_message(message):
    if not isinstance(message, dict):
        raise PolicyError(400, "malformed-json")
    if message.get("jsonrpc") != "2.0":
        raise PolicyError(403, "unknown-method")
    method = message.get("method")
    if not isinstance(method, str):
        raise PolicyError(403, "unknown-method")
    if method == "initialize" or method == "tools/list":
        return Decision("", "", "bearer")
    if method == "tools/call":
        return _check_tool_call(message)
    if _NOTIFICATION.match(method):
        return Decision("", "", "bearer")
    raise PolicyError(403, "unknown-method")


def _type_hint(arguments):
    """A short type string for the log. Never a post body."""
    if not isinstance(arguments, dict):
        return ""
    posts = arguments.get("socialPost")
    if not isinstance(posts, list):
        return ""
    for post in posts:
        if isinstance(post, dict):
            value = post.get("type")
            if isinstance(value, str) and _SAFE_TYPE.fullmatch(value):
                return value
    return ""


def decide_mcp(body):
    """Return a Decision or raise. A batch is all-or-nothing."""
    value = parse_json(body)
    if isinstance(value, list):
        if not value:
            raise PolicyError(403, "empty-batch")
        decisions = []
        for item in value:
            try:
                decisions.append(_check_rpc_message(item))
            except PolicyError as exc:
                if not exc.reason.startswith("batch"):
                    exc.reason = "batch-" + exc.reason
                raise
        names = [item.tool for item in decisions if item.tool]
        tool = ",".join(names)
        if len(tool) > 120:
            tool = "batch"
        type_label = ""
        labels = [item.type_label for item in decisions if item.type_label]
        if labels and all(label == "draft" for label in labels):
            type_label = "draft"
        return Decision(tool, type_label, "bearer")
    if isinstance(value, dict):
        return _check_rpc_message(value)
    raise PolicyError(400, "malformed-json")


def _path_only(target):
    if not target or target.startswith(("http://", "https://")):
        raise PolicyError(400, "bad-path")
    path = target.split("?", 1)[0]
    if path != _percent_decode_reject(path):
        raise PolicyError(400, "bad-path")
    if "\\" in path or "//" in path or ".." in path or not path.startswith("/"):
        raise PolicyError(400, "bad-path")
    return path


def _percent_decode_reject(path):
    """Return path when it contains no percent-encoding. Encoding is rejected."""
    if "%" in path:
        raise PolicyError(400, "bad-path")
    return path


def _query_ok(query):
    if not query:
        return
    # parse_qsl keeps duplicates. Any denied key, in any case, fails the request.
    from urllib.parse import parse_qsl

    for key, _value in parse_qsl(query, keep_blank_values=True):
        if key.lower() in _QUERY_DENY:
            raise PolicyError(403, "query-not-allowed")


def decide_rest(method, target, content_type, body):
    path = _path_only(target)
    query = ""
    if "?" in target:
        query = target.split("?", 1)[1]
    _query_ok(query)
    if method == "GET":
        if body:
            raise PolicyError(400, "unexpected-body")
        if path in _GET_EXACT or any(pattern.match(path) for pattern in _GET_ONE):
            return Decision("", "", "raw")
        raise PolicyError(404, "unknown-path")
    if method == "POST":
        if path != POST_PATH:
            raise PolicyError(404, "unknown-path")
        if not _json_type(content_type):
            raise PolicyError(415, "bad-content-type")
        if not body:
            raise PolicyError(400, "malformed-json")
        value = parse_json(body)
        if not isinstance(value, dict):
            raise PolicyError(400, "malformed-json")
        if value.get("type") != "draft":
            raise PolicyError(403, "type-not-draft")
        _reject_publish_keys(value, True)
        return Decision("", "draft", "raw")
    raise PolicyError(405, "method-not-allowed")


def decide_mcp_request(method, target, content_type, body):
    path = _path_only(target)
    if "?" in target:
        raise PolicyError(403, "query-not-allowed")
    if path != MCP_PATH:
        raise PolicyError(404, "unknown-path")
    if method == "GET":
        if body:
            raise PolicyError(400, "unexpected-body")
        return Decision("", "", "bearer")
    if method != "POST":
        raise PolicyError(405, "method-not-allowed")
    if not _json_type(content_type):
        raise PolicyError(415, "bad-content-type")
    if not body:
        raise PolicyError(400, "malformed-json")
    return decide_mcp(body)


def _json_type(value):
    if not value:
        return False
    media = value.split(";", 1)[0].strip().lower()
    return media == "application/json"


def route(method, target, content_type, body):
    """Dispatch one fully buffered request. Never called with a live stream."""
    bare = _path_only(target)
    if bare == MCP_PATH or bare.startswith("/api/mcp/"):
        return decide_mcp_request(method, target, content_type, body)
    if bare == "/api/public" or bare.startswith("/api/public/"):
        return decide_rest(method, target, content_type, body)
    raise PolicyError(404, "unknown-path")
