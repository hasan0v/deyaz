import inspect
import unittest

import i18n
from deyaz_app import DeYazWindow


class HeaderSocialLinkTests(unittest.TestCase):
    def test_header_uses_verified_profile_and_repository_links(self):
        source = inspect.getsource(DeYazWindow._build_ui)
        self.assertIn("by Ali Hasanov", source)
        self.assertIn("https://github.com/hasan0v/deyaz", source)
        self.assertIn("https://www.linkedin.com/in/ali-hasanov", source)
        self.assertIn('qta.icon("fa6b.github"', source)
        self.assertIn('qta.icon("fa6b.linkedin-in"', source)
        self.assertIn("self.github_star", source)

    def test_creator_credit_hides_only_at_non_roomy_widths(self):
        source = inspect.getsource(DeYazWindow._apply_shell_responsiveness)
        self.assertIn('self.creator_credit.setVisible(density == "roomy")', source)

    def test_linkedin_call_to_action_is_first_person(self):
        self.assertEqual(
            i18n.UI["LinkedIn-də məni izləyin"][0], "Follow me on LinkedIn"
        )
        source = inspect.getsource(DeYazWindow._build_ui)
        self.assertIn("LinkedIn-də məni izləyin", source)
        self.assertNotIn("LinkedIn-də Ali Hasanov-u izləyin", source)


if __name__ == "__main__":
    unittest.main()
