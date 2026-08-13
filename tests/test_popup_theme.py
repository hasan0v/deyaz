import types
import unittest

from deyaz_app import DeYazWindow


class PopupThemeTests(unittest.TestCase):
    def test_global_popup_surfaces_have_explicit_colors(self):
        owner = types.SimpleNamespace()
        stylesheet = DeYazWindow.theme_stylesheet(owner, "light")
        self.assertIn("QMenu::item", stylesheet)
        self.assertIn("QMessageBox QLabel", stylesheet)
        self.assertIn("QToolTip", stylesheet)
        self.assertIn("color: #202321", stylesheet)

    def test_dark_popup_surfaces_use_light_text(self):
        owner = types.SimpleNamespace()
        stylesheet = DeYazWindow.theme_stylesheet(owner, "dark")
        self.assertIn("color: #F8F3E8", stylesheet)
        self.assertIn("background-color: #F8F3E8", stylesheet)
        self.assertIn("color: #202321", stylesheet)

    def test_primary_surface_tabs_expose_interaction_states(self):
        owner = types.SimpleNamespace()
        stylesheet = DeYazWindow.theme_stylesheet(owner, "light")
        for selector in (
            '#surfaceTab:hover:!checked', '#surfaceTab:checked',
            '#surfaceTab:pressed', '#surfaceTab:focus',
            '#surfaceTab[surface="meeting"]:checked',
        ):
            self.assertIn(selector, stylesheet)

    def test_theme_icon_button_is_center_aligned(self):
        owner = types.SimpleNamespace()
        stylesheet = DeYazWindow.theme_stylesheet(owner, "light")
        self.assertIn("text-align: center", stylesheet)
        self.assertIn("#appearanceSwitch:focus, #appearanceSwitch:pressed", stylesheet)


if __name__ == "__main__":
    unittest.main()
