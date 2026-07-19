import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CLOUDFLARE_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_D1_DATABASE_ID", "test-db")
os.environ.setdefault("CLOUDFLARE_API_TOKEN", "test-token")

from common.d1_client import D1Client


def _mock_response(json_body, status_code=200, ok=True):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = ok
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


class TestD1Client(unittest.TestCase):
    def setUp(self):
        self.client = D1Client()

    @patch("common.d1_client.requests.post")
    def test_execute_sql_success(self, mock_post):
        mock_post.return_value = _mock_response(
            {"success": True, "result": [{"success": True, "results": [{"code": "600519"}]}]}
        )
        rows = self.client.execute_sql("SELECT * FROM stocks WHERE code = ?", ["600519"])
        self.assertEqual(rows, [{"code": "600519"}])

        called_url = mock_post.call_args.args[0]
        self.assertIn("test-account", called_url)
        self.assertIn("test-db", called_url)
        called_json = mock_post.call_args.kwargs["json"]
        self.assertEqual(called_json["sql"], "SELECT * FROM stocks WHERE code = ?")
        self.assertEqual(called_json["params"], ["600519"])

    @patch("common.d1_client.requests.post")
    def test_execute_sql_raises_on_failure_response(self, mock_post):
        mock_post.return_value = _mock_response({}, status_code=500, ok=False)
        with self.assertRaises(Exception):
            self.client.execute_sql("SELECT 1")

    @patch("common.d1_client.requests.post")
    def test_upsert_rows_batches_correctly(self, mock_post):
        mock_post.return_value = _mock_response({"success": True, "result": [{"success": True, "results": []}]})

        rows = [{"code": f"{i:06d}", "name": f"stock{i}"} for i in range(250)]
        written = self.client.upsert_rows("stocks", rows, batch_size=100)

        self.assertEqual(written, 250)
        # 250 rows at batch_size=100 -> 3 requests (100, 100, 50)
        self.assertEqual(mock_post.call_count, 3)

        first_call_sql = mock_post.call_args_list[0].kwargs["json"]["sql"]
        self.assertIn("INSERT OR REPLACE INTO stocks", first_call_sql)
        self.assertEqual(first_call_sql.count("(?, ?)"), 100)

        first_call_params = mock_post.call_args_list[0].kwargs["json"]["params"]
        self.assertEqual(len(first_call_params), 200)  # 100 rows * 2 columns

    def test_upsert_rows_empty_list_noop(self):
        written = self.client.upsert_rows("stocks", [])
        self.assertEqual(written, 0)


if __name__ == "__main__":
    unittest.main()
