"""命令行行为与错误处理。"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from replaydeck.cli import main

REC_LINE = json.dumps({
    "id": "rec-01-0001",
    "request": {"method": "GET", "path": "/a", "query": [],
                "headers": {"accept": "application/json"}, "body": ""},
    "response": {"status": 200, "headers": {}, "body": "ok"},
}, sort_keys=True, separators=(",", ":"))
QUERY_LINE = json.dumps({
    "id": "q-00001",
    "request": {"method": "GET", "path": "/a", "query": [],
                "headers": {"accept": "application/json"}, "body": ""},
}, sort_keys=True, separators=(",", ":"))


def write(directory, name, text):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
        handle.write(text)


class TestExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.recs = os.path.join(self.root, "recs")
        self.queries = os.path.join(self.root, "queries")
        write(self.recs, "part-01.jsonl", REC_LINE + "\n")
        write(self.queries, "q-01.jsonl", QUERY_LINE + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def replay_args(self, **kw):
        args = ["replay", "--recordings", kw.get("recs", self.recs),
                "--queries", kw.get("queries", self.queries)]
        if kw.get("out"):
            args += ["--out", kw["out"]]
        return args

    def test_success(self):
        code, out, _ = self.run_cli(self.replay_args())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hit"], "rec-01-0001")

    def test_missing_path_is_input_error(self):
        code, _, _ = self.run_cli(self.replay_args(recs=os.path.join(self.root, "nope")))
        self.assertEqual(code, 1)

    def test_parse_failure_is_input_error(self):
        write(self.queries, "q-02.jsonl", "{bad json}\n")
        code, out, _ = self.run_cli(self.replay_args())
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

    def test_duplicate_query_id(self):
        write(self.queries, "q-02.jsonl", QUERY_LINE + "\n")
        code, out, _ = self.run_cli(self.replay_args())
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

    def test_duplicate_record_id(self):
        write(self.recs, "part-02.jsonl", REC_LINE + "\n")
        code, out, _ = self.run_cli(self.replay_args())
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

    def test_status_out_of_range(self):
        bad = json.loads(REC_LINE)
        bad["id"] = "rec-01-0002"
        bad["response"]["status"] = 99
        write(self.recs, "part-02.jsonl", json.dumps(bad) + "\n")
        code, _, _ = self.run_cli(self.replay_args())
        self.assertEqual(code, 1)

    def test_status_bool_rejected(self):
        bad = json.loads(REC_LINE)
        bad["response"]["status"] = True
        write(self.recs, "part-02.jsonl", json.dumps(bad) + "\n")
        code, _, _ = self.run_cli(self.replay_args())
        self.assertEqual(code, 1)

    def test_no_output_written_on_error(self):
        out_path = os.path.join(self.root, "results.jsonl")
        write(self.queries, "q-02.jsonl", "{bad}\n")
        code, _, _ = self.run_cli(self.replay_args(out=out_path))
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(out_path))

    def test_usage_error_is_2(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["replay", "--recordings", self.recs])
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_command_is_2(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["frobnicate"])
        self.assertEqual(ctx.exception.code, 2)


class TestRecordCommand(unittest.TestCase):
    def test_shard_size_and_id_derivation(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            lines = []
            for i in range(5):
                obj = json.loads(REC_LINE)
                obj["id"] = f"old-{i}"
                obj["request"]["path"] = f"/p{i}"
                lines.append(json.dumps(obj))
            write(src, "in.jsonl", "\n".join(lines) + "\n")
            out = os.path.join(tmp, "out")
            code = main(["record", "--in", src, "--out", out, "--shard-size", "2"])
            self.assertEqual(code, 0)
            self.assertEqual(sorted(os.listdir(out)),
                             ["part-01.jsonl", "part-02.jsonl", "part-03.jsonl"])
            ids = []
            for name in sorted(os.listdir(out)):
                with open(os.path.join(out, name), encoding="utf-8") as handle:
                    ids.extend(json.loads(line)["id"] for line in handle)
            self.assertEqual(ids, ["rec-01-0001", "rec-01-0002",
                                   "rec-02-0001", "rec-02-0002", "rec-03-0001"])

    def test_record_single_file_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.jsonl")
            write(tmp, "in.jsonl", REC_LINE + "\n")
            out = os.path.join(tmp, "out")
            self.assertEqual(main(["record", "--in", src, "--out", out]), 0)
            with open(os.path.join(out, "part-01.jsonl"), encoding="utf-8") as handle:
                obj = json.loads(handle.read())
            self.assertEqual(obj["id"], "rec-01-0001")

    def test_record_no_timestamps(self):
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            src = os.path.join(t1, "in.jsonl")
            write(t1, "in.jsonl", REC_LINE + "\n")
            o1 = os.path.join(t1, "o1")
            o2 = os.path.join(t2, "o2")
            main(["record", "--in", src, "--out", o1])
            main(["record", "--in", src, "--out", o2])
            with open(os.path.join(o1, "part-01.jsonl"), "rb") as h1:
                with open(os.path.join(o2, "part-01.jsonl"), "rb") as h2:
                    self.assertEqual(h1.read(), h2.read())


if __name__ == "__main__":
    unittest.main()
