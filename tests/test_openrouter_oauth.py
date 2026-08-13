import unittest
import urllib.parse

import openrouter_oauth
import deyaz_app


class OpenRouterOAuthTests(unittest.TestCase):
    def test_auth_errors_are_distinguished_from_credit_errors(self):
        is_auth_error = deyaz_app.DeYazWindow.is_openrouter_auth_error
        self.assertTrue(is_auth_error("OpenRouter API açarını qəbul etmədi (HTTP 401)."))
        self.assertTrue(is_auth_error("HTTP 401: User not found."))
        self.assertFalse(is_auth_error("OpenRouter says the account is out of credit (HTTP 402)."))

    def test_upstream_provider_401_is_not_reported_as_bad_user_key(self):
        message = "The selected model provider cannot currently process this OpenRouter request (HTTP 401)."
        self.assertTrue(deyaz_app.DeYazWindow.is_model_provider_error(message))
        self.assertFalse(deyaz_app.DeYazWindow.is_openrouter_auth_error(message))

    def test_pkce_pair_uses_s256_compatible_values(self):
        verifier, challenge = openrouter_oauth.create_pkce_pair()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertEqual(len(challenge), 43)
        self.assertNotIn("=", challenge)

    def test_authorization_url_contains_callback_and_pkce(self):
        callback = "http://localhost:51515/callback?state=test-state"
        url = openrouter_oauth.build_authorization_url(callback, "challenge")
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "openrouter.ai")
        self.assertEqual(params["callback_url"], [callback])
        self.assertEqual(params["code_challenge"], ["challenge"])
        self.assertEqual(params["code_challenge_method"], ["S256"])


if __name__ == "__main__":
    unittest.main()
