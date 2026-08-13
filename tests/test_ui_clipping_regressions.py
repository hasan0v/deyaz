import inspect
import unittest

from deyaz_app import DeYazWindow


class UiClippingRegressionTests(unittest.TestCase):
    def test_dictation_combo_owns_its_drop_down_geometry(self):
        source = inspect.getsource(DeYazWindow.refresh_page_chrome)
        self.assertIn("QComboBox::drop-down", source)
        self.assertIn("subcontrol-position: center right", source)
        self.assertIn("background: transparent", source)

    def test_dictation_column_keeps_a_shadow_gutter(self):
        source = inspect.getsource(DeYazWindow._compose_template_pages)
        self.assertIn("self.dictation_left.setMinimumWidth(444)", source)
        self.assertIn("setContentsMargins(12, 0, 12, 8)", source)

    def test_media_seek_has_room_for_the_styled_handle(self):
        source = inspect.getsource(DeYazWindow._compose_template_pages)
        self.assertIn("self.file_seek.setFixedHeight(28)", source)


if __name__ == "__main__":
    unittest.main()
