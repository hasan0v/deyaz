import os
from pathlib import Path
import unittest

from project_context import _detect_active_project, _find_project_root, _project_summary


class ActiveProjectDetectionTests(unittest.TestCase):
    def test_current_process_tree_detects_repository_root(self):
        expected = Path(__file__).resolve().parents[1]
        root, cwd, confidence, evidence = _detect_active_project(
            os.getpid(), expected.name
        )
        self.assertEqual(root, expected)
        self.assertTrue(cwd)
        self.assertEqual(confidence, "high")
        self.assertIn("working directory", evidence)

    def test_project_summary_reads_readme_and_manifest(self):
        root = Path(__file__).resolve().parents[1]
        summary = _project_summary(root)
        self.assertIn("README.md excerpt:", summary)
        self.assertIn("requirements.txt excerpt:", summary)

    def test_user_profile_is_never_treated_as_project_root(self):
        self.assertIsNone(_find_project_root(Path.home()))


if __name__ == "__main__":
    unittest.main()
