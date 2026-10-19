import math
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CLOUDFLARE_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_D1_DATABASE_ID", "test-db")
os.environ.setdefault("CLOUDFLARE_API_TOKEN", "test-token")

from common.d1_client import MAX_BOUND_PARAMS, D1Client, D1ParamError


def _mock_response(json_body, status_code=200, ok=True):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = ok
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


def _ok_response(rows=None):
    return _mock_response(
        {"success": True, "result": [{"success": True, "results": rows or []}]}
    )


class TestD1Client(unittest.TestCase):
    def setUp(self):
        self.client = D1Client()

    @patch("common.d1_client.requests.Session.post")
    def test_execute_sql_success(self, mock_post):
        mock_post.return_value = _ok_response([{"code": "600519"}])
        rows = self.client.execute_sql("SELECT * FROM stocks WHERE code = ?", ["600519"])
        self.assertEqual(rows, [{"code": "600519"}])

        called_url = mock_post.call_args.args[0]
        self.assertIn("test-account", called_url)
        self.assertIn("test-db", called_url)
        called_json = mock_post.call_args.kwargs["json"]
        self.assertEqual(called_json["sql"], "SELECT * FROM stocks WHERE code = ?")
        self.assertEqual(called_json["params"], ["600519"])

    @patch("common.d1_client.time.sleep")
    @patch("common.d1_client.requests.Session.post")
    def test_execute_sql_retries_5xx_then_succeeds(self, mock_post, _sleep):
        mock_post.side_effect = [
            _mock_response({}, status_code=503, ok=False),
            _ok_response([{"ok": 1}]),
        ]
        rows = self.client.execute_sql("SELECT 1")
        self.assertEqual(rows, [{"ok": 1}])
        self.assertEqual(mock_post.call_count, 2)

    @patch("common.d1_client.time.sleep")
    @patch("common.d1_client.requests.Session.post")
    def test_execute_sql_retries_429_then_succeeds(self, mock_post, _sleep):
        mock_post.side_effect = [
            _mock_response({}, status_code=429, ok=False),
            _ok_response([]),
        ]
        self.assertEqual(self.client.execute_sql("SELECT 1"), [])
        self.assertEqual(mock_post.call_count, 2)

    @patch("common.d1_client.time.sleep")
    @patch("common.d1_client.requests.Session.post")
    def test_execute_sql_does_not_retry_4xx(self, mock_post, _sleep):
        mock_post.return_value = _mock_response({"error": "bad token"}, status_code=401, ok=False)
        with self.assertRaises(Exception):
            self.client.execute_sql("SELECT 1")
        self.assertEqual(mock_post.call_count, 1)

    @patch("common.d1_client.requests.Session.post")
    def test_upsert_21_columns_never_exceeds_100_params(self, mock_post):
        mock_post.return_value = _ok_response()

        columns = [f"c{i}" for i in range(21)]
        rows = [{c: f"{i}-{c}" for c in columns} for i in range(250)]
        written = self.client.upsert_rows("stocks", rows)

        self.assertEqual(written, 250)
        # 100 // 21 = 4 rows per statement -> ceil(250 / 4) = 63 requests.
        self.assertEqual(mock_post.call_count, 63)

        placeholder_row = "(" + ", ".join("?" for _ in columns) + ")"
        for call in mock_post.call_args_list:
            payload = call.kwargs["json"]
            self.assertLessEqual(len(payload["params"]), MAX_BOUND_PARAMS)
            rows_in_statement = payload["sql"].count(placeholder_row)
            self.assertLessEqual(rows_in_statement, 4)
            self.assertEqual(rows_in_statement, len(payload["params"]) // 21)

    @patch("common.d1_client.requests.Session.post")
    def test_upsert_8_columns_uses_12_rows_per_statement(self, mock_post):
        mock_post.return_value = _ok_response()
        columns = [f"c{i}" for i in range(8)]
        rows = [{c: 1 for c in columns} for _ in range(250)]
        self.client.upsert_rows("stock_prices", rows)

        # 100 // 8 = 12 rows -> ceil(250 / 12) = 21 requests.
        self.assertEqual(mock_post.call_count, 21)
        for call in mock_post.call_args_list:
            self.assertLessEqual(len(call.kwargs["json"]["params"]), MAX_BOUND_PARAMS)

    @patch("common.d1_client.requests.Session.post")
    def test_upsert_clamps_oversized_batch_size(self, mock_post):
        mock_post.return_value = _ok_response()
        columns = [f"c{i}" for i in range(21)]
        rows = [{c: 1 for c in columns} for _ in range(10)]
        self.client.upsert_rows("stocks", rows, batch_size=1000)

        # 10 rows at the clamped size of 4 -> 3 requests, never 1 x 1000 rows.
        self.assertEqual(mock_post.call_count, 3)
        for call in mock_post.call_args_list:
            self.assertLessEqual(len(call.kwargs["json"]["params"]), MAX_BOUND_PARAMS)

    @patch("common.d1_client.requests.Session.post")
    def test_upsert_rejects_nan_before_http(self, mock_post):
        rows = [{"code": "600519", "bonus_share_ratio": math.nan}]
        with self.assertRaises(D1ParamError) as ctx:
            self.client.upsert_rows("dividends", rows)
        self.assertIn("index 1", str(ctx.exception))
        self.assertIn("Non-finite", str(ctx.exception))
        mock_post.assert_not_called()

    @patch("common.d1_client.requests.Session.post")
    def test_upsert_rejects_infinity_before_http(self, mock_post):
        rows = [{"code": "600519", "close": math.inf}]
        with self.assertRaises(D1ParamError):
            self.client.upsert_rows("stock_prices", rows)
        mock_post.assert_not_called()

    @patch("common.d1_client.requests.Session.post")
    def test_execute_sql_rejects_more_than_100_params(self, mock_post):
        with self.assertRaises(D1ParamError) as ctx:
            self.client.execute_sql("SELECT 1 WHERE x IN (" + ", ".join("?" for _ in range(101)) + ")", [1] * 101)
        self.assertIn("101", str(ctx.exception))
        mock_post.assert_not_called()

    @patch("common.d1_client.requests.Session.post")
    def test_update_rows_builds_bounded_case_statements(self, mock_post):
        mock_post.return_value = _ok_response()
        rows = [{"code": f"{i:06d}", "last_price": float(i)} for i in range(500)]
        written = self.client.update_rows(
            "stocks",
            "code",
            rows,
            ["last_price"],
            static_values={"price_updated_at": "2026-10-19T07:10:00Z"},
        )

        self.assertEqual(written, 500)
        # (2 params per value column + 1 for IN) = 3/row; 98 // 3 = 32 rows.
        self.assertEqual(mock_post.call_count, 16)
        for call in mock_post.call_args_list:
            payload = call.kwargs["json"]
            self.assertLessEqual(len(payload["params"]), MAX_BOUND_PARAMS)
            self.assertIn("CASE code WHEN ? THEN ?", payload["sql"])
            self.assertIn("price_updated_at = ?", payload["sql"])

    @patch("common.d1_client.requests.Session.post")
    def test_update_rows_missing_key_raises(self, mock_post):
        with self.assertRaises(D1ParamError):
            self.client.update_rows("stocks", "code", [{"last_price": 1.0}], ["last_price"])
        mock_post.assert_not_called()

    def test_upsert_rows_empty_list_noop(self):
        self.assertEqual(self.client.upsert_rows("stocks", []), 0)


if __name__ == "__main__":
    unittest.main()
