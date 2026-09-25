"""自包含 HTML 报告：内容全部来自引擎结果，不做二次匹配。

行数可能很大，报告按流式拼装：先是 rows 表（逐行写），totals 表放最后。
"""

from html import escape

from .jsonutil import dumps

_STYLE = """
body{margin:24px;background:#f6f7f9;color:#1f2328;font-family:-apple-system,"Segoe UI","Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei",sans-serif}
h1{font-size:20px;margin:0 0 16px}
h2{font-size:15px;margin:24px 0 8px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th,td{border:1px solid #d8dce1;padding:5px 9px;text-align:left;vertical-align:top}
th{background:#eef1f4}
tr.miss{background:#ffe3e3}
tr.miss td{border-color:#e8b4b4}
td.num,td.c{text-align:right;font-variant-numeric:tabular-nums}
pre{margin:0;white-space:pre-wrap;word-break:break-all;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
""".strip()


def html_head():
    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>回放报告</title>",
        f"<style>{_STYLE}</style>",
        "</head>",
        "<body>",
        "<h1>回放报告</h1>",
        "<h2>逐条结果</h2>",
        '<table id="rows">',
        "<thead><tr>"
        "<th>请求</th><th>命中</th><th>状态</th><th>耗时(ms)</th><th>候选</th><th>候选差异</th>"
        "</tr></thead>",
        "<tbody>",
        "",
    ])


def format_row(row):
    """row: {query, hit, status, ms, candidates}；candidates 为引擎结果对象列表或 None。"""
    query = escape(row["query"], quote=True)
    if row["hit"] is not None:
        hit = escape(row["hit"], quote=True)
        status = str(row["status"])
        candidates_attr = "-"
        css = ""
        detail = ""
        candidates_cell = "-"
    else:
        hit = "-"
        status = "-"
        candidates = row["candidates"]
        candidates_attr = escape(
            ";".join(candidate["id"] for candidate in candidates), quote=True
        )
        css = ' class="miss"'
        candidates_cell = escape("; ".join(candidate["id"] for candidate in candidates))
        detail = "<pre>" + escape(dumps(candidates)) + "</pre>"
    ms = row["ms"]
    return (
        f'<tr{css} data-query="{query}" data-hit="{hit}" data-status="{status}" '
        f'data-ms="{ms}" data-candidates="{candidates_attr}">'
        f"<td>{query}</td><td>{hit}</td><td>{status}</td>"
        f'<td class="num">{ms}</td><td>{candidates_cell}</td><td>{detail}</td></tr>\n'
    )


def totals_table(totals):
    parts = [
        "</tbody>",
        "</table>",
        "<h2>汇总</h2>",
        '<table id="totals">',
        "<thead><tr><th>结果</th><th>数量</th></tr></thead>",
        "<tbody>",
    ]
    for key in ("hit", "miss"):
        count = totals.get(key, 0)
        parts.append(
            f'<tr data-key="{key}" data-count="{count}">'
            f"<td>{key}</td><td>{count}</td></tr>"
        )
    parts += ["</tbody>", "</table>", "</body>", "</html>", ""]
    return "\n".join(parts)


def render_html(rows, totals):
    parts = [html_head()]
    parts.extend(format_row(row) for row in rows)
    parts.append(totals_table(totals))
    return "".join(parts)
