"""请求/录制对象模型与校验。"""

from .errors import InputError

ALLOWLIST = ("accept", "content-type")


def normalize_header_value(value):
    """取第一个 ';' 前、去首尾空白、转小写。"""
    return value.split(";", 1)[0].strip().lower()


class Request:
    __slots__ = ("method", "path", "query", "headers", "body")

    def __init__(self, method, path, query, headers, body):
        self.method = method
        self.path = path
        self.query = query  # list[tuple[name, value]]
        self.headers = headers  # dict，头名已小写
        self.body = body

    @property
    def method_key(self):
        return self.method.upper()

    @property
    def query_key(self):
        return tuple(sorted(self.query))

    @property
    def allowlist_norm(self):
        """请求里出现的允许清单头：名字 -> 归一化值。"""
        return {
            name: normalize_header_value(self.headers[name])
            for name in ALLOWLIST
            if name in self.headers
        }


def _require(cond, context, message):
    if not cond:
        raise InputError(f"{context}: {message}")


def parse_request(obj, context):
    _require(isinstance(obj, dict), context, "request 必须是对象")
    for field in ("method", "path", "query", "headers", "body"):
        _require(field in obj, context, f"request 缺少字段 {field}")

    method = obj["method"]
    _require(isinstance(method, str) and method != "", context, "method 必须是非空字符串")

    path = obj["path"]
    _require(isinstance(path, str), context, "path 必须是字符串")

    query = obj["query"]
    _require(isinstance(query, list), context, "query 必须是数组")
    pairs = []
    for pair in query:
        _require(
            isinstance(pair, list)
            and len(pair) == 2
            and isinstance(pair[0], str)
            and isinstance(pair[1], str),
            context,
            "query 项必须是 [名, 值] 字符串二元组",
        )
        pairs.append((pair[0], pair[1]))

    headers = obj["headers"]
    _require(isinstance(headers, dict), context, "headers 必须是对象")
    norm_headers = {}
    for name, value in headers.items():
        _require(
            isinstance(name, str) and isinstance(value, str),
            context,
            "headers 的名与值必须是字符串",
        )
        norm_headers[name.lower()] = value

    body = obj["body"]
    _require(isinstance(body, str), context, "body 必须是字符串")

    return Request(method, path, pairs, norm_headers, body)


def parse_response(obj, context):
    _require(isinstance(obj, dict), context, "response 必须是对象")
    for field in ("status", "headers", "body"):
        _require(field in obj, context, f"response 缺少字段 {field}")

    status = obj["status"]
    _require(
        isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599,
        context,
        "status 必须是 100-599 的整数",
    )

    headers = obj["headers"]
    _require(isinstance(headers, dict), context, "response.headers 必须是对象")
    for name, value in headers.items():
        _require(
            isinstance(name, str) and isinstance(value, str),
            context,
            "response.headers 的名与值必须是字符串",
        )

    body = obj["body"]
    _require(isinstance(body, str), context, "response.body 必须是字符串")

    return {"status": status, "headers": dict(headers), "body": body}


def parse_id(obj, context):
    _require(isinstance(obj, dict), context, "行必须是对象")
    _require("id" in obj, context, "缺少字段 id")
    ident = obj["id"]
    _require(isinstance(ident, str) and ident != "", context, "id 必须是非空字符串")
    return ident


class Record:
    __slots__ = ("id", "order", "request", "response", "constraints", "hkey", "_canon")

    def __init__(self, ident, order, request, response):
        self.id = ident
        self.order = order
        self.request = request
        self.response = response
        self.constraints = request.allowlist_norm
        self.hkey = tuple(sorted(self.constraints.items()))
        self._canon = _CANON_UNSET

    @property
    def constraint_count(self):
        return len(self.constraints)


class _CanonUnset:
    pass


_CANON_UNSET = _CanonUnset()


def parse_record(obj, order, context):
    ident = parse_id(obj, context)
    _require("request" in obj, context, "缺少字段 request")
    _require("response" in obj, context, "缺少字段 response")
    request = parse_request(obj["request"], context)
    response = parse_response(obj["response"], context)
    return Record(ident, order, request, response)


def parse_query(obj, context):
    ident = parse_id(obj, context)
    _require("request" in obj, context, "缺少字段 request")
    request = parse_request(obj["request"], context)
    return ident, request
