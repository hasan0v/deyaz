import os
import secrets
import unittest

import credential_store


@unittest.skipUnless(os.name == "nt", "Windows Credential Manager test")
class CredentialStoreTests(unittest.TestCase):
    def test_round_trip_and_delete(self):
        target = f"DeYaz/Test/{secrets.token_hex(8)}"
        try:
            credential_store.set_secret(target, "sk-or-test-secret")
            self.assertEqual(
                credential_store.get_secret(target), "sk-or-test-secret"
            )
            credential_store.delete_secret(target)
            self.assertEqual(credential_store.get_secret(target), "")
        finally:
            credential_store.delete_secret(target)


if __name__ == "__main__":
    unittest.main()
