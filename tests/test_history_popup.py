import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from deyaz_app import HistoryPopup


class HistoryPopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_history_drawer_can_resize_and_open(self):
        owner = QWidget()
        owner.resize(1100, 720)
        owner.show()
        popup = HistoryPopup()
        popup.refresh([
            {
                "ts": "2026-08-13 15:30",
                "mode": "dictation",
                "text": "History drawer regression test",
            }
        ])

        popup.show_as_drawer(owner)
        self.app.processEvents()

        self.assertTrue(popup.isVisible())
        self.assertGreaterEqual(popup.width(), 380)
        popup.close()
        owner.close()


if __name__ == "__main__":
    unittest.main()
