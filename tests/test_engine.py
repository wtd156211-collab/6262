"""引擎匹配语义单元测试。"""

import unittest

from replaydeck.engine import Engine, Hit, Miss
from replaydeck.model import parse_record, parse_query


def make_record(ident, order, method="GET", path="/a", query=None, headers=None,
                body="", status=200):
    obj = {
        "id": ident,
        "request": {
            "method": method,
            "path": path,
            "query": query or [],
            "headers": headers or {},
            "body": body,
        },
        "response": {"status": status, "headers": {}, "body": "ok"},
    }
    return parse_record(obj, order, "test")


def make_request(method="GET", path="/a", query=None, headers=None, body=""):
    obj = {
        "id": "q-1",
        "request": {
            "method": method,
            "path": path,
            "query": query or [],
            "headers": headers or {},
            "body": body,
        },
    }
    return parse_query(obj, "test")[1]


class TestDimensions(unittest.TestCase):
    def test_method_case_insensitive(self):
        eng = Engine([make_record("r1", 0, method="get")])
        self.assertIsInstance(eng.match(make_request(method="GET")), Hit)

    def test_path_case_sensitive(self):
        eng = Engine([make_record("r1", 0, path="/Abc")])
        self.assertIsInstance(eng.match(make_request(path="/abc")), Miss)
        self.assertIsInstance(eng.match(make_request(path="/Abc")), Hit)

    def test_path_no_normalization(self):
        eng = Engine([make_record("r1", 0, path="/a/")])
        self.assertIsInstance(eng.match(make_request(path="/a")), Miss)
        eng = Engine([make_record("r1", 0, path="/a/%2F")])
        self.assertIsInstance(eng.match(make_request(path="/a//")), Miss)

    def test_query_order_free(self):
        eng = Engine([make_record("r1", 0, query=[["a", "1"], ["b", "2"]])])
        hit = eng.match(make_request(query=[["b", "2"], ["a", "1"]]))
        self.assertIsInstance(hit, Hit)

    def test_query_multiplicity(self):
        eng = Engine([make_record("r1", 0, query=[["a", "1"], ["a", "1"]])])
        self.assertIsInstance(eng.match(make_request(query=[["a", "1"]])), Miss)
        self.assertIsInstance(
            eng.match(make_request(query=[["a", "1"], ["a", "1"]])), Hit)

    def test_query_no_decoding(self):
        eng = Engine([make_record("r1", 0, query=[["a", "%2F"]])])
        self.assertIsInstance(eng.match(make_request(query=[["a", "/"]])), Miss)
        eng = Engine([make_record("r1", 0, query=[["a", "+"]])])
        self.assertIsInstance(eng.match(make_request(query=[["a", " "]])), Miss)

    def test_query_name_case_sensitive(self):
        eng = Engine([make_record("r1", 0, query=[["Key", "1"]])])
        self.assertIsInstance(eng.match(make_request(query=[["key", "1"]])), Miss)

    def test_ignored_headers(self):
        eng = Engine([make_record("r1", 0, headers={"x-trace-id": "t1"})])
        hit = eng.match(make_request(headers={"x-trace-id": "t2", "user-agent": "x"}))
        self.assertIsInstance(hit, Hit)

    def test_header_normalization(self):
        eng = Engine([make_record("r1", 0, headers={"accept": "APPLICATION/JSON; charset=utf-8"})])
        hit = eng.match(make_request(headers={"accept": " application/json "}))
        self.assertIsInstance(hit, Hit)

    def test_header_constraint_must_be_present(self):
        eng = Engine([make_record("r1", 0, headers={"accept": "application/json"})])
        self.assertIsInstance(eng.match(make_request()), Miss)

    def test_empty_body_equal(self):
        eng = Engine([make_record("r1", 0)])
        self.assertIsInstance(eng.match(make_request()), Hit)


class TestBody(unittest.TestCase):
    JSON = {"content-type": "application/json"}

    def test_json_key_order_and_numbers(self):
        eng = Engine([make_record("r1", 0, headers=self.JSON,
                                  body='{"a":2,"b":[1,2]}')])
        req = make_request(headers=self.JSON, body='{"b":[1.0,2e0],"a":2.0}')
        self.assertIsInstance(eng.match(req), Hit)

    def test_json_bool_not_number(self):
        eng = Engine([make_record("r1", 0, headers=self.JSON, body='{"a":1}')])
        req = make_request(headers=self.JSON, body='{"a":true}')
        self.assertIsInstance(eng.match(req), Miss)

    def test_json_array_order_sensitive(self):
        eng = Engine([make_record("r1", 0, headers=self.JSON, body='[1,2]')])
        req = make_request(headers=self.JSON, body='[2,1]')
        self.assertIsInstance(eng.match(req), Miss)

    def test_json_string_case_sensitive(self):
        eng = Engine([make_record("r1", 0, headers=self.JSON, body='{"a":"X"}')])
        req = make_request(headers=self.JSON, body='{"a":"x"}')
        self.assertIsInstance(eng.match(req), Miss)

    def test_bad_body_falls_back_to_bytes(self):
        eng = Engine([make_record("r1", 0, headers=self.JSON, body='{"a": 1,}')])
        req = make_request(headers=self.JSON, body='{"a":1,}')
        miss = eng.match(req)
        self.assertIsInstance(miss, Miss)  # 字节不同（空格）→ 不命中
        req2 = make_request(headers=self.JSON, body='{"a": 1,}')
        self.assertIsInstance(eng.match(req2), Hit)

    def test_mode_from_request_content_type(self):
        # 请求无 content-type → 字节模式，JSON 等价不适用
        eng = Engine([make_record("r1", 0, body='{"a":2}')])
        req = make_request(body='{"a":2.0}')
        self.assertIsInstance(eng.match(req), Miss)

    def test_vendor_json_content_type(self):
        eng = Engine([make_record("r1", 0, body='{"a":2}')])
        req = make_request(
            headers={"content-type": "application/vnd.api+json"}, body='{"a":2.0}')
        self.assertIsInstance(eng.match(req), Hit)


class TestHitSelection(unittest.TestCase):
    def test_more_constraints_win(self):
        r1 = make_record("r1", 0, headers={"content-type": "text/plain"})
        r2 = make_record("r2", 1, headers={"accept": "application/json",
                                           "content-type": "text/plain"})
        eng = Engine([r1, r2])
        req = make_request(headers={"accept": "application/json",
                                    "content-type": "text/plain"})
        outcome = eng.match(req)
        self.assertEqual(outcome.record.id, "r2")

    def test_earlier_record_wins_tie(self):
        r1 = make_record("r1", 0, headers={"accept": "application/json"})
        r2 = make_record("r2", 1, headers={"accept": "application/json"})
        eng = Engine([r1, r2])
        outcome = eng.match(make_request(headers={"accept": "application/json"}))
        self.assertEqual(outcome.record.id, "r1")


class TestMiss(unittest.TestCase):
    def test_no_fallback_response(self):
        eng = Engine([make_record("r1", 0, path="/x")])
        outcome = eng.match(make_request(path="/y"))
        self.assertIsInstance(outcome, Miss)
        self.assertFalse(hasattr(outcome, "record"))

    def test_candidates_limit_and_order(self):
        records = [make_record(f"r{i}", i, path="/p", query=[["a", str(i)]])
                   for i in range(5)]
        eng = Engine(records)
        outcome = eng.match(make_request(path="/p", query=[["a", "x"]]))
        self.assertEqual(len(outcome.candidates), 3)
        self.assertEqual([c.record.id for c in outcome.candidates], ["r0", "r1", "r2"])
        self.assertEqual([c.score for c in outcome.candidates], [1, 1, 1])

    def test_path_equal_ranks_first(self):
        r1 = make_record("r1", 0, path="/a", method="POST")
        r2 = make_record("r2", 1, path="/b")
        eng = Engine([r1, r2])
        outcome = eng.match(make_request(path="/a"))
        # r1 只差方法（score 1），r2 差路径（score 1）→ 路径相等者优先
        self.assertEqual(outcome.candidates[0].record.id, "r1")

    def test_diff_formats(self):
        rec = make_record("r1", 0, method="POST", path="/a",
                          query=[["a", "1"], ["a", "1"]],
                          headers={"accept": "application/json"}, body="B1")
        eng = Engine([rec])
        req = make_request(method="get", path="/b", query=[["a", "2"]],
                           headers={}, body="B2")
        outcome = eng.match(req)
        diff = outcome.candidates[0].diff
        self.assertEqual(diff["method"], {"record": "POST", "request": "get"})
        self.assertEqual(diff["path"], {"record": "/a", "request": "/b"})
        self.assertEqual(diff["query"], {"only_record": [["a", "1"], ["a", "1"]],
                                         "only_request": [["a", "2"]]})
        self.assertEqual(diff["headers"], {"accept": {"record": "application/json",
                                                      "request": None}})
        self.assertEqual(diff["body"], {"record": "B1", "request": "B2"})
        self.assertEqual(outcome.candidates[0].score, 5)

    def test_header_diff_raw_values(self):
        rec = make_record("r1", 0, headers={"content-type": "text/plain; charset=utf-8"})
        eng = Engine([rec])
        req = make_request(headers={"content-type": "application/json"})
        outcome = eng.match(req)
        diff = outcome.candidates[0].diff
        self.assertEqual(diff["headers"]["content-type"],
                         {"record": "text/plain; charset=utf-8",
                          "request": "application/json"})


if __name__ == "__main__":
    unittest.main()
