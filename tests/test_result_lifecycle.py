import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton

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

    def test_clear_keeps_open_result_workspace_and_only_removes_copy(self):
        class Owner:
            def __init__(self):
                self.latest_result_text = "Generated result"
                self._dictation_result_open = True
                self.recent_preview = QLabel("Generated result")
                self.recent_time = QLabel("12:00")
                self.dictation_result_output = QPlainTextEdit("Generated result")
                self.copy_recent_button = QPushButton()
                self.copy_recent_button.setEnabled(True)
                self.layout_updates = 0

            def _update_dictation_result_layout(self):
                self.layout_updates += 1

        owner = Owner()
        DeYazWindow.clear_dictation_result(owner)
        self.assertEqual(owner.latest_result_text, "")
        self.assertEqual(owner.dictation_result_output.toPlainText(), "")
        self.assertTrue(owner._dictation_result_open)
        self.assertFalse(owner.copy_recent_button.isEnabled())
        self.assertEqual(owner.layout_updates, 1)


if __name__ == "__main__":
    unittest.main()
