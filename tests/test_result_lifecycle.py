import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from deyaz_app import DeYazWindow


class ResultLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_history_does_not_restore_the_session_result(self):
        class Owner:
            def __init__(self):
                self.history = QLabel()
                self.recent_preview = QLabel()
                self.recent_time = QLabel()
                self.copy_recent_button = QPushButton()
                self.latest_result_text = ""

        rows = [{"ts": "2026-08-13 12:00", "text": "Old transcript"}]
        owner = Owner()
        with patch("deyaz_app.cfg.read_history", return_value=rows):
            DeYazWindow.refresh_history(owner)
        self.assertEqual(owner.latest_result_text, "")
        self.assertEqual(owner.recent_preview.text(), "")
        self.assertIn("Old transcript", owner.history.text())


if __name__ == "__main__":
    unittest.main()
