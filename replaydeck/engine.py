"""回放引擎：按五个维度（方法、路径、查询、头、体）匹配录制。"""

import json
from collections import Counter
from heapq import nsmallest

from .model import ALLOWLIST, _CANON_UNSET, normalize_header_value

_MAX_CONSTRAINTS = len(ALLOWLIST)


def _reject_constant(value):
    raise ValueError(f"非法 JSON 常量: {value}")


def _canon_node(node):
    if node is None:
        return (0,)
    if node is True:
        return (1, True)
    if node is False:
        return (1, False)
    if isinstance(node, (int, float)):
        return (2, node)
    if isinstance(node, str):
        return (3, node)
    if isinstance(node, list):
        return (4, tuple(_canon_node(item) for item in node))
    if isinstance(node, dict):
        return (5, frozenset((key, _canon_node(value)) for key, value in node.items()))
    raise ValueError("不支持的 JSON 节点")


def canon_json_body(body):
    """解析为规范化结构；解析失败返回 None（调用方退化为字节模式）。"""
    try:
        node = json.loads(body, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None
    return _canon_node(node)


class Hit:
    __slots__ = ("record",)

    def __init__(self, record):
        self.record = record


class Candidate:
    __slots__ = ("record", "score", "diff")

    def __init__(self, record, score, diff):
        self.record = record
        self.score = score
        self.diff = diff


class Miss:
    __slots__ = ("candidates",)

    def __init__(self, candidates):
        self.candidates = candidates


class _QueryContext:
    __slots__ = ("request", "allowlist_norm", "json_mode", "hkeys", "hkey_set", "_canon")

    def __init__(self, request):
        self.request = request
        self.allowlist_norm = request.allowlist_norm
        content_type = self.allowlist_norm.get("content-type", "")
        self.json_mode = "json" in content_type
        keys = [()]
        if "accept" in self.allowlist_norm:
            keys.append((("accept", self.allowlist_norm["accept"]),))
        if "content-type" in self.allowlist_norm:
            keys.append((("content-type", self.allowlist_norm["content-type"]),))
        if len(keys) == 3:
            keys.append(tuple(sorted(self.allowlist_norm.items())))
        self.hkeys = keys
        self.hkey_set = frozenset(keys)
        self._canon = _CANON_UNSET

    def body_canon(self):
        if self._canon is _CANON_UNSET:
            self._canon = canon_json_body(self.request.body)
        return self._canon


class Engine:
    def __init__(self, records):
        self.records = list(records)
        self._methods = []
        self._paths = []
        self._qkeys = []
        self._hkeys = []
        self._bodies = []
        self._ccounts = []
        self._canons = []
        self._by_method = {}
        self._by_path = {}
        self._by_query = {}
        self._by_hkey = {}
        self._by_body_byte = {}
        self._by_body_json = None
        self._hit_index = {}
        for record in self.records:
            rid = record.order
            request = record.request
            self._methods.append(request.method_key)
            self._paths.append(request.path)
            self._qkeys.append(request.query_key)
            self._hkeys.append(record.hkey)
            self._bodies.append(request.body)
            self._ccounts.append(record.constraint_count)
            self._canons.append(_CANON_UNSET)
            self._by_method.setdefault(request.method_key, []).append(rid)
            self._by_path.setdefault(request.path, []).append(rid)
            self._by_query.setdefault(request.query_key, []).append(rid)
            self._by_hkey.setdefault(record.hkey, []).append(rid)
            self._by_body_byte.setdefault(request.body, []).append(rid)
            hit_key = (request.method_key, request.path, request.query_key, record.hkey)
            self._hit_index.setdefault(hit_key, []).append(rid)
        self._by_spec = sorted(
            range(len(self.records)),
            key=lambda rid: (-self._ccounts[rid], rid),
        )
        self._marked = bytearray(len(self.records))

    def _record_canon(self, rid):
        canon = self._canons[rid]
        if canon is _CANON_UNSET:
            canon = canon_json_body(self._bodies[rid])
            self._canons[rid] = canon
        return canon

    def _body_equal(self, rid, ctx, qcanon):
        if ctx.json_mode and qcanon is not None:
            canon = self._record_canon(rid)
            if canon is not None:
                return canon == qcanon
        return self._bodies[rid] == ctx.request.body

    def match(self, request):
        """命中返回 Hit，未命中返回 Miss（含最多 3 条最接近候选）。"""
        ctx = _QueryContext(request)
        hit = self._find_hit(ctx)
        if hit is not None:
            return Hit(hit)
        return Miss(self._find_candidates(ctx))

    def _find_hit(self, ctx):
        request = ctx.request
        qcanon = ctx.body_canon() if ctx.json_mode else None
        best = None
        best_count = -1
        max_count = min(_MAX_CONSTRAINTS, len(ctx.allowlist_norm))
        for hkey in sorted(ctx.hkeys, key=len, reverse=True):
            key = (request.method_key, request.path, request.query_key, hkey)
            for rid in self._hit_index.get(key, ()):
                count = self._ccounts[rid]
                if count <= best_count:
                    continue
                if not self._body_equal(rid, ctx, qcanon):
                    continue
                best = self.records[rid]
                best_count = count
            if best_count >= max_count:
                break
        return best

    def _body_index_json(self):
        if self._by_body_json is None:
            self._by_body_json = {}
            for record in self.records:
                canon = self._record_canon(record.order)
                if canon is not None:
                    self._by_body_json.setdefault(canon, []).append(record.order)
        return self._by_body_json

    def _body_bucket(self, ctx, qcanon):
        if ctx.json_mode and qcanon is not None:
            return self._body_index_json().get(qcanon, ())
        return self._by_body_byte.get(ctx.request.body, ())

    def _find_candidates(self, ctx):
        request = ctx.request
        method = request.method_key
        path = request.path
        qkey = request.query_key
        hkey_set = ctx.hkey_set
        qcanon = ctx.body_canon() if ctx.json_mode else None

        dim_buckets = {
            1: self._by_method.get(method, ()),
            2: self._by_path.get(path, ()),
            3: self._by_query.get(qkey, ()),
            5: self._body_bucket(ctx, qcanon),
        }
        hkey_bucket = []
        for hkey in ctx.hkeys:
            hkey_bucket.extend(self._by_hkey.get(hkey, ()))
        dim_buckets[4] = hkey_bucket
        # 空桶的维度等同于已扫描：所有记录在该维度都不匹配。
        scanned_dims = {dim for dim, bucket in dim_buckets.items() if not bucket}
        path_scanned = not dim_buckets[2]
        buckets = sorted(
            ((dim, bucket) for dim, bucket in dim_buckets.items() if bucket),
            key=lambda bucket: len(bucket[1]),
        )

        methods = self._methods
        paths = self._paths
        qkeys = self._qkeys
        hkeys = self._hkeys
        bodies = self._bodies
        ccounts = self._ccounts
        json_mode = ctx.json_mode
        qbody = request.body

        marked = self._marked
        touched = []
        scored = []
        canons = self._canons

        best = []
        certified = False
        for dim, bucket in buckets:
            for rid in bucket:
                if marked[rid]:
                    continue
                marked[rid] = 1
                touched.append(rid)
                score = 0
                if methods[rid] != method:
                    score += 1
                path_eq = paths[rid] == path
                if not path_eq:
                    score += 1
                if qkeys[rid] != qkey:
                    score += 1
                if hkeys[rid] not in hkey_set:
                    score += 1
                if json_mode and qcanon is not None:
                    canon = canons[rid]
                    if canon is _CANON_UNSET:
                        canon = canon_json_body(bodies[rid])
                        canons[rid] = canon
                    if canon is not None:
                        if canon != qcanon:
                            score += 1
                    elif bodies[rid] != qbody:
                        score += 1
                elif bodies[rid] != qbody:
                    score += 1
                scored.append((0 if path_eq else 1, score, -ccounts[rid], rid))
            scanned_dims.add(dim)
            if dim == 2:
                path_scanned = True
            if len(scored) >= 3:
                best = nsmallest(3, scored)
                optimistic = (
                    0 if not path_scanned else 1,
                    max(len(scanned_dims), 1),
                    -_MAX_CONSTRAINTS,
                    0,
                )
                if best[2] < optimistic:
                    certified = True
                    break
        if not certified:
            best = nsmallest(3, scored)
            if len(best) < 3:
                have = {key[3] for key in best}
                for rid in self._by_spec:
                    if len(best) >= 3:
                        break
                    if rid not in have:
                        best.append((1, 5, -ccounts[rid], rid))
                        have.add(rid)
                best.sort()

        for rid in touched:
            marked[rid] = 0

        best = [key[3] for key in best[:3]]
        return [self._make_candidate(self.records[rid], ctx) for rid in best[:3]]

    def _make_candidate(self, record, ctx):
        request = ctx.request
        diff = {}

        if record.request.method_key != request.method_key:
            diff["method"] = {"record": record.request.method, "request": request.method}

        if record.request.path != request.path:
            diff["path"] = {"record": record.request.path, "request": request.path}

        if record.request.query_key != request.query_key:
            record_counter = Counter(record.request.query)
            request_counter = Counter(request.query)
            diff["query"] = {
                "only_record": [list(pair) for pair in sorted((record_counter - request_counter).elements())],
                "only_request": [list(pair) for pair in sorted((request_counter - record_counter).elements())],
            }

        header_diff = {}
        for name in ALLOWLIST:
            if name not in record.request.headers:
                continue
            record_raw = record.request.headers[name]
            record_norm = normalize_header_value(record_raw)
            if name in request.headers:
                request_raw = request.headers[name]
                request_norm = normalize_header_value(request_raw)
            else:
                request_raw = None
                request_norm = None
            if record_norm != request_norm:
                header_diff[name] = {"record": record_raw, "request": request_raw}
        if header_diff:
            diff["headers"] = header_diff

        qcanon = ctx.body_canon() if ctx.json_mode else None
        if not self._body_equal(record.order, ctx, qcanon):
            diff["body"] = {"record": record.request.body, "request": request.body}

        return Candidate(record, len(diff), diff)


def result_object(query_id, outcome):
    """把引擎结果转成 4.3 的结果对象。"""
    if isinstance(outcome, Hit):
        return {
            "hit": outcome.record.id,
            "query": query_id,
            "status": outcome.record.response["status"],
        }
    return {
        "candidates": [
            {
                "constraints": candidate.record.constraint_count,
                "diff": candidate.diff,
                "id": candidate.record.id,
                "score": candidate.score,
            }
            for candidate in outcome.candidates
        ],
        "hit": None,
        "query": query_id,
    }
