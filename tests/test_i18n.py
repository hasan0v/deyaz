"""Regression tests for DeYaz's runtime UI localization."""

import ast
import inspect
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QLabel, QTabWidget, QVBoxLayout, QWidget,
)

import i18n
from deyaz_app import (
    ContextAddDialog, ContextManagerDialog, DeYazWindow, FILE_TRANSCRIPTION_CHOICES,
    MEETING_LIVE_TRANSCRIPTION_CHOICES, MEETING_TEXT_CHOICES,
    OPENAI_CLEANUP_CHOICES, OPENAI_TRANSCRIPTION_CHOICES,
    OPENROUTER_CLEANUP_CHOICES, OPENROUTER_TRANSCRIPTION_CHOICES,
    localize_widget_tree,
)


class LocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        i18n.set_language("az")

    def test_all_supported_languages_translate_primary_copy(self):
        expected = {
            "az": "Səsini fikrə çevir.",
            "tr": "Sesini fikre dönüştür.",
            "en": "Turn your voice into ideas.",
            "ru": "Превратите голос в идеи.",
        }
        for code, text in expected.items():
            with self.subTest(code=code):
                i18n.set_language(code)
                self.assertEqual(i18n.t("Səsini fikrə çevir."), text)

    def test_runtime_widget_and_tab_retranslation_keeps_source(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        label = QLabel("Ayarlar yadda saxlanıldı")
        tabs = QTabWidget()
        tabs.addTab(QWidget(), "History")
        layout.addWidget(label)
        layout.addWidget(tabs)

        i18n.set_language("en")
        localize_widget_tree(root)
        self.assertEqual(label.text(), "Settings saved")
        self.assertEqual(tabs.tabText(0), "History")

        i18n.set_language("ru")
        localize_widget_tree(root)
        self.assertEqual(label.text(), "Настройки сохранены")
        self.assertEqual(tabs.tabText(0), "История")

    def test_russian_is_a_resolvable_interface_language(self):
        self.assertEqual(i18n.resolve("ru"), "ru")

    def test_window_titles_and_combo_tooltips_are_retranslated(self):
        root = QWidget()
        root.setWindowTitle("Kontekst")
        combo = QComboBox(root)
        combo.addItem("Başla")
        combo.setItemData(0, "Oynat", Qt.ItemDataRole.ToolTipRole)

        i18n.set_language("ru")
        localize_widget_tree(root)
        self.assertEqual(root.windowTitle(), "Контекст")
        self.assertEqual(combo.itemText(0), "Начать")
        self.assertEqual(
            combo.itemData(0, Qt.ItemDataRole.ToolTipRole), "Воспроизвести"
        )

    def test_every_runtime_catalog_has_all_service_messages(self):
        for source in i18n.AZ:
            with self.subTest(source=source):
                self.assertIn(source, i18n.EN)
                self.assertIn(source, i18n.TR)
                self.assertIn(source, i18n.RU)

    def test_model_badges_and_descriptions_are_localized(self):
        collections = (
            OPENROUTER_TRANSCRIPTION_CHOICES, OPENROUTER_CLEANUP_CHOICES,
            OPENAI_TRANSCRIPTION_CHOICES, FILE_TRANSCRIPTION_CHOICES,
            OPENAI_CLEANUP_CHOICES, MEETING_LIVE_TRANSCRIPTION_CHOICES,
            MEETING_TEXT_CHOICES,
        )
        for choices in collections:
            for row in choices:
                for source in (row[0], row[-1]):
                    with self.subTest(source=source):
                        self.assertIn(source, i18n.UI)

    def test_all_static_user_facing_literals_have_catalog_entries(self):
        source_path = Path(__file__).resolve().parents[1] / "deyaz_app.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        sinks = {
            "QLabel", "QPushButton", "QToolButton", "QCheckBox", "QRadioButton",
            "setText", "setWindowTitle", "setToolTip",
            "setPlaceholderText", "addAction", "addItem", "addItems",
            "addRow", "add_settings_page",
            "information", "warning",
            "critical", "question", "getOpenFileName", "getExistingDirectory",
            "setInformativeText",
        }
        non_language_copy = {
            "", "DeYaz", "OpenRouter", "OpenAI", "API", "₵", "●", "↗", "✕", "00:00",
        }
        missing = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else ""
            )
            if name not in sinks:
                continue
            if name in {"addItem", "addItems", "addRow"}:
                arguments = node.args[:1]
            elif name == "add_settings_page":
                arguments = node.args[1:2]
            else:
                arguments = node.args
            for argument in arguments:
                if name == "addItems" and isinstance(argument, (ast.List, ast.Tuple)):
                    values = [
                        child.value for child in argument.elts
                        if isinstance(child, ast.Constant) and isinstance(child.value, str)
                    ]
                elif isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    values = [argument.value]
                else:
                    values = []
                for value in values:
                    if value.strip() and value not in non_language_copy and value not in i18n.UI:
                        missing.append(f"line {node.lineno}: {value!r}")
        self.assertEqual(missing, [], "Missing UI translations:\n" + "\n".join(missing))

    def test_reported_settings_and_meeting_labels_translate(self):
        sources = (
            "Qısa yol", "Mətn modeli", "Fayl transkripti", "Görüş qeydi",
            "Görüş xülasəsi", "Tam transkript", "Əsas məqamlar",
            "Ətraflı icmal", "Tapşırıqlar", "Xüsusi fokus",
            "Nəticəni təmizlə",
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertIn(source, i18n.UI)
                self.assertNotEqual(i18n.UI[source][0], "")
                self.assertNotEqual(i18n.UI[source][1], "")
                self.assertNotEqual(i18n.UI[source][2], "")

    def test_main_context_button_opens_manager_not_add_chooser(self):
        source = inspect.getsource(DeYazWindow._compose_template_pages)
        self.assertIn(
            "self.context_button.clicked.connect(self.open_context_manager)", source
        )
        self.assertNotIn(
            "self.context_button.clicked.connect(self.open_context_add_dialog)", source
        )
        manager_source = inspect.getsource(ContextManagerDialog.open_add)
        self.assertIn("ContextAddDialog(self.owner, self).exec()", manager_source)

    def test_context_surfaces_open_in_the_selected_language(self):
        class Owner(QWidget):
            def __init__(self):
                super().__init__()
                self.conf = {"context_items": []}

            def set_context_item_enabled(self, *_args):
                pass

        owner = Owner()
        i18n.set_language("en")
        manager = ContextManagerDialog(owner)
        chooser = ContextAddDialog(owner)
        self.assertEqual(manager.windowTitle(), "Context")
        self.assertEqual(chooser.windowTitle(), "Add context")
        button_texts = {
            button.text() for button in chooser.findChildren(QWidget)
            if hasattr(button, "text")
        }
        self.assertIn("Paste text", button_texts)
        self.assertIn("Upload file", button_texts)


if __name__ == "__main__":
    unittest.main()
