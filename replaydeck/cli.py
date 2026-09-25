"""命令行入口：record / replay / report。"""

import argparse
import os
import sys
import time

from .engine import Engine, Hit, result_object
from .errors import InputError
from .jsonutil import dumps, read_jsonl, write_jsonl
from .model import parse_query, parse_record
from .report import format_row, html_head, totals_table

DEFAULT_SHARD_SIZE = 1000
_CHUNK = 4096


def _jsonl_files(path):
    if not os.path.exists(path):
        raise InputError(f"路径不存在: {path}")
    if os.path.isfile(path):
        return [path]
    if not os.path.isdir(path):
        raise InputError(f"不是文件或目录: {path}")
    files = [
        os.path.join(path, name)
        for name in sorted(os.listdir(path))
        if name.endswith(".jsonl")
    ]
    if not files:
        raise InputError(f"目录下没有 .jsonl 文件: {path}")
    return files


def _load_records(paths):
    records = []
    seen = set()
    for path in paths:
        for obj in read_jsonl(path):
            record = parse_record(obj, len(records), path)
            if record.id in seen:
                raise InputError(f"{path}: id 重复: {record.id}")
            seen.add(record.id)
            records.append(record)
    return records


def _iter_queries(paths):
    for path in paths:
        for obj in read_jsonl(path):
            yield parse_query(obj, path)


def _validate_queries(paths):
    """第一遍：只做校验（解析、字段、id 重复），对象即弃，控制内存。"""
    seen = set()
    for ident, _request in _iter_queries(paths):
        if ident in seen:
            raise InputError(f"查询 id 重复: {ident}")
        seen.add(ident)


def _cmd_record(args):
    files = _jsonl_files(args.in_path)
    items = []
    for path in files:
        for obj in read_jsonl(path):
            context = path
            if not isinstance(obj, dict):
                raise InputError(f"{context}: 行必须是对象")
            if "id" not in obj:
                raise InputError(f"{context}: 缺少字段 id")
            record = parse_record(obj, len(items), context)
            items.append(record)

    shard_size = args.shard_size
    shards = {}
    for index, record in enumerate(items):
        file_seq = index // shard_size + 1
        line_seq = index % shard_size + 1
        out_obj = {
            "id": f"rec-{file_seq:02d}-{line_seq:04d}",
            "request": {
                "method": record.request.method,
                "path": record.request.path,
                "query": [[name, value] for name, value in record.request.query],
                "headers": dict(record.request.headers),
                "body": record.request.body,
            },
            "response": record.response,
        }
        shards.setdefault(file_seq, []).append(out_obj)

    os.makedirs(args.out, exist_ok=True)
    for file_seq, objects in shards.items():
        write_jsonl(os.path.join(args.out, f"part-{file_seq:02d}.jsonl"), objects)
    return 0


def _cmd_replay(args):
    recordings = _jsonl_files(args.recordings)
    queries = _jsonl_files(args.queries)
    records = _load_records(recordings)
    _validate_queries(queries)
    engine = Engine(records)

    out_file = None
    if args.out:
        out_file = open(args.out, "w", encoding="utf-8", newline="\n")
    try:
        chunk = []
        for ident, request in _iter_queries(queries):
            chunk.append(dumps(result_object(ident, engine.match(request))))
            if len(chunk) >= _CHUNK:
                text = "\n".join(chunk) + "\n"
                sys.stdout.write(text)
                if out_file:
                    out_file.write(text)
                chunk.clear()
        if chunk:
            text = "\n".join(chunk) + "\n"
            sys.stdout.write(text)
            if out_file:
                out_file.write(text)
    finally:
        if out_file:
            out_file.close()
    return 0


def _cmd_report(args):
    recordings = _jsonl_files(args.recordings)
    queries = _jsonl_files(args.queries)
    records = _load_records(recordings)
    _validate_queries(queries)
    engine = Engine(records)

    totals = {"hit": 0, "miss": 0}
    with open(args.html, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(html_head())
        for ident, request in _iter_queries(queries):
            start = time.perf_counter()
            outcome = engine.match(request)
            ms = int(round((time.perf_counter() - start) * 1000))
            if isinstance(outcome, Hit):
                totals["hit"] += 1
                row = {"query": ident, "hit": outcome.record.id,
                       "status": outcome.record.response["status"], "ms": ms,
                       "candidates": None}
            else:
                totals["miss"] += 1
                row = {"query": ident, "hit": None, "status": None, "ms": ms,
                       "candidates": result_object(ident, outcome)["candidates"]}
            handle.write(format_row(row))
        handle.write(totals_table(totals))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="replaydeck", description="请求录制与回放"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_record = sub.add_parser("record", help="把请求/响应配对写成录制文件")
    p_record.add_argument("--in", dest="in_path", required=True, help="录制文件或目录")
    p_record.add_argument("--out", required=True, help="输出目录")
    p_record.add_argument("--shard-size", type=int, default=DEFAULT_SHARD_SIZE,
                          help=f"分片大小（默认 {DEFAULT_SHARD_SIZE}）")
    p_record.set_defaults(func=_cmd_record)

    p_replay = sub.add_parser("replay", help="按请求回放录制")
    p_replay.add_argument("--recordings", required=True)
    p_replay.add_argument("--queries", required=True)
    p_replay.add_argument("--out", default=None)
    p_replay.set_defaults(func=_cmd_replay)

    p_report = sub.add_parser("report", help="回放并生成 HTML 报告")
    p_report.add_argument("--recordings", required=True)
    p_report.add_argument("--queries", required=True)
    p_report.add_argument("--html", required=True)
    p_report.set_defaults(func=_cmd_report)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "record" and args.shard_size < 1:
        parser.error("--shard-size 必须 >= 1")
    try:
        return args.func(args)
    except InputError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
