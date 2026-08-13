import unittest
from unittest import mock

import api


class OpenRouterAccountTests(unittest.TestCase):
    @mock.patch("api._get_json")
    def test_real_balance_is_total_credits_minus_total_usage(self, get_json):
        get_json.side_effect = [
            {"data": {"is_free_tier": False, "limit_remaining": None}},
            {"data": {"total_credits": 5, "total_usage": 2.75}},
        ]
        info = api.openrouter_account_info("test-key")
        self.assertEqual(info["account_balance"], 2.25)

    def test_upstream_402_is_not_described_as_empty_openrouter_balance(self):
        error = api.explain(api.ApiError("HTTP 402: Provider returned 402", 402),
                            "OpenRouter")
        self.assertEqual(error.status, 402)
        self.assertIn("model provider", str(error).lower())
        self.assertNotIn("out of credit", str(error).lower())


if __name__ == "__main__":
    unittest.main()
