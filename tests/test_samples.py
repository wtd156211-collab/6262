"""基于 samples/ 的端到端测试（只读 samples/）。"""

import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from html.parser import HTMLParser

from replaydeck.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDINGS = os.path.join(ROOT, "samples", "recordings")
QUERIES = os.path.join(ROOT, "samples", "queries")
EXPECTED = os.path.join(ROOT, "samples", "expected")


def expected_text():
    parts = []
    for name in sorted(os.listdir(EXPECTED)):
        if name.endswith(".jsonl"):
            with open(os.path.join(EXPECTED, name), encoding="utf-8") as handle:
                parts.append(handle.read())
    return "".join(parts)


def run_replay(extra=None):
    argv = ["replay", "--recordings", RECORDINGS, "--queries", QUERIES]
    argv.extend(extra or [])
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(argv)
    return code, stdout.getvalue()


class TestReplaySamples(unittest.TestCase):
    def test_replay_matches_expected_byte_for_byte(self):
        code, out = run_replay()
        self.assertEqual(code, 0)
        self.assertEqual(out, expected_text())

    def test_out_file_identical_to_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "results.jsonl")
            code, out = run_replay(["--out", out_path])
            self.assertEqual(code, 0)
            with open(out_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), out)

    def test_no_fallback_on_miss(self):
        code, out = run_replay()
        self.assertEqual(code, 0)
        for line in out.splitlines():
            obj = json.loads(line)
            if obj["hit"] is None:
                self.assertIn("candidates", obj)
                self.assertLessEqual(len(obj["candidates"]), 3)
                self.assertNotIn("status", obj)


class TestRecordSamples(unittest.TestCase):
    def test_record_is_deterministic(self):
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            self.assertEqual(main(["record", "--in", RECORDINGS, "--out", t1]), 0)
            self.assertEqual(main(["record", "--in", RECORDINGS, "--out", t2]), 0)
            for name in sorted(os.listdir(t1)):
                with open(os.path.join(t1, name), "rb") as h1:
                    d1 = h1.read()
                with open(os.path.join(t2, name), "rb") as h2:
                    self.assertEqual(d1, h2.read(), name)

    def test_record_rewrite_is_fixed_point(self):
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            self.assertEqual(main(["record", "--in", RECORDINGS, "--out", t1]), 0)
            self.assertEqual(main(["record", "--in", t1, "--out", t2]), 0)
            self.assertEqual(sorted(os.listdir(t1)), sorted(os.listdir(t2)))
            for name in sorted(os.listdir(t1)):
                with open(os.path.join(t1, name), "rb") as h1:
                    self.assertEqual(h1.read(), open(os.path.join(t2, name), "rb").read())

    def test_record_ids_and_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["record", "--in", RECORDINGS, "--out", tmp]), 0)
            names = sorted(os.listdir(tmp))
            self.assertEqual(names, ["part-01.jsonl", "part-02.jsonl", "part-03.jsonl"])
            total = 0
            for file_seq, name in enumerate(names, 1):
                with open(os.path.join(tmp, name), encoding="utf-8") as handle:
                    lines = handle.read().splitlines()
                self.assertLessEqual(len(lines), 1000)
                for line_seq, line in enumerate(lines, 1):
                    obj = json.loads(line)
                    self.assertEqual(obj["id"], f"rec-{file_seq:02d}-{line_seq:04d}")
                    total += 1
            self.assertEqual(total, 3000)


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = {}
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._current = dict(attrs).get("id")
            self.tables.setdefault(self._current, [])
        elif tag == "tr" and self._current:
            self.tables[self._current].append(dict(attrs))


class TestReportSamples(unittest.TestCase):
    def _generate(self, directory):
        path = os.path.join(directory, "replay.html")
        code = main(["report", "--recordings", RECORDINGS, "--queries", QUERIES,
                     "--html", path])
        self.assertEqual(code, 0)
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_report_structure_and_consistency(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = self._generate(tmp)
        self.assertNotIn("<script", html)
        self.assertNotRegex(html, r'(src|href)\s*=')
        parser = _TableParser()
        parser.feed(html)
        rows = [r for r in parser.tables["rows"] if "data-query" in r]
        totals = {r["data-key"]: int(r["data-count"])
                  for r in parser.tables["totals"] if "data-key" in r}

        code, out = run_replay()
        result_lines = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(len(rows), len(result_lines))
        hits = misses = 0
        for row, result in zip(rows, result_lines):
            self.assertEqual(row["data-query"], result["query"])
            self.assertRegex(row["data-ms"], r"^\d+$")
            if result["hit"] is None:
                misses += 1
                self.assertEqual(row["data-hit"], "-")
                self.assertEqual(row["data-status"], "-")
                self.assertEqual(row["class"], "miss")
                expected_ids = ";".join(c["id"] for c in result["candidates"])
                self.assertEqual(row["data-candidates"], expected_ids)
            else:
                hits += 1
                self.assertEqual(row["data-hit"], result["hit"])
                self.assertEqual(row["data-status"], str(result["status"]))
                self.assertEqual(row["data-candidates"], "-")
        self.assertEqual(totals, {"hit": hits, "miss": misses})

    def test_report_deterministic_modulo_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self._generate(tmp)
            second = self._generate(tmp)
        mask = lambda text: re.sub(
            r'(<td class="num">)\d+(</td>)', r"\1-\2",
            re.sub(r'data-ms="\d+"', 'data-ms="-"', text))
        self.assertEqual(mask(first), mask(second))


if __name__ == "__main__":
    unittest.main()
