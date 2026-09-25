"""规范 JSON 读写：键按字典序、分隔符后无空格、非 ASCII 不转义。"""

import json


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_line(text, context):
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        from .errors import InputError

        raise InputError(f"{context}: JSON 解析失败: {exc}") from exc
    return value


def read_jsonl(path):
    from .errors import InputError

    try:
        with open(path, "r", encoding="utf-8") as handle:
            items = []
            for lineno, line in enumerate(handle, 1):
                text = line.strip()
                if not text:
                    continue
                items.append(parse_line(text, f"{path}:{lineno}"))
            return items
    except OSError as exc:
        raise InputError(f"无法读取文件 {path}: {exc}") from exc


def write_jsonl(path, objects):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for obj in objects:
            handle.write(dumps(obj))
            handle.write("\n")
