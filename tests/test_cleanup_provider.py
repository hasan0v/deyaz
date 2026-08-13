import json
import unittest
from unittest import mock

import api


class CleanupProviderTests(unittest.TestCase):
    @mock.patch("api._request")
    def test_direct_openai_gpt56_omits_unsupported_temperature(self, request):
        request.return_value = {"choices": [{"message": {"content": "Fixed."}}]}
        api.cleanup(
            "fixed", "key", "gpt-5.6-luna", "Fix text.",
            base_url=api.OPENAI_URL, provider="openai", service="OpenAI",
        )
        payload = json.loads(request.call_args.args[1].decode("utf-8"))
        self.assertNotIn("temperature", payload)

    @mock.patch("api._request")
    def test_openrouter_cleanup_keeps_deterministic_temperature(self, request):
        request.return_value = {"choices": [{"message": {"content": "Fixed."}}]}
        api.cleanup("fixed", "key", "openai/gpt-5.6-luna", "Fix text.")
        payload = json.loads(request.call_args.args[1].decode("utf-8"))
        self.assertEqual(payload["temperature"], 0)


if __name__ == "__main__":
    unittest.main()
