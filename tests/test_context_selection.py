import inspect
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QCheckBox, QRadioButton, QWidget

from deyaz_app import ContextManagerDialog, DeYazWindow


class MemoryConfig(dict):
    def save(self):
        self["save_count"] = self.get("save_count", 0) + 1


class ContextSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_owner(self):
        owner = QWidget()
        owner.conf = MemoryConfig({
            "context_enabled": True,
            "context_project_dir": "C:/one",
            "context_items": [
                {"label": "One", "kind": "project", "path": "C:/one", "enabled": True},
                {"label": "Two", "kind": "project", "path": "C:/two", "enabled": False},
                {"label": "Text", "kind": "text", "text": "alpha", "enabled": True},
                {"label": "File", "kind": "file", "text": "beta", "enabled": True},
            ],
        })
        owner._context_items = lambda: DeYazWindow._context_items(owner)
        owner._save_context_items = lambda items: DeYazWindow._save_context_items(owner, items)
        owner.set_context_item_enabled = (
            lambda index, enabled: DeYazWindow.set_context_item_enabled(
                owner, index, enabled
            )
        )
        return owner

    def test_projects_are_radio_buttons_and_references_are_checkboxes(self):
        dialog = ContextManagerDialog(self.make_owner())
        self.assertEqual(len(dialog.findChildren(QRadioButton)), 2)
        self.assertEqual(len(dialog.findChildren(QCheckBox)), 2)

    def test_selecting_project_disables_every_other_project(self):
        owner = self.make_owner()
        DeYazWindow.set_context_item_enabled(owner, 1, True)
        items = owner.conf["context_items"]
        self.assertFalse(items[0]["enabled"])
        self.assertTrue(items[1]["enabled"])
        self.assertTrue(items[2]["enabled"])
        self.assertTrue(items[3]["enabled"])
        self.assertEqual(owner.conf["context_project_dir"], "C:/two")

    def test_file_focus_is_always_editable(self):
        source = inspect.getsource(DeYazWindow.update_file_option_state)
        self.assertIn("self.file_summary_focus.setEnabled(True)", source)


if __name__ == "__main__":
    unittest.main()
