import inspect
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from deyaz_app import CrtVideoOverlay, DeYazWindow, SubtitleVideoWidget


class UiClippingRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dictation_combo_owns_its_drop_down_geometry(self):
        source = inspect.getsource(DeYazWindow.refresh_page_chrome)
        self.assertIn("QComboBox::drop-down", source)
        self.assertIn("subcontrol-position: center right", source)
        self.assertIn("background: transparent", source)

    def test_dictation_column_keeps_a_shadow_gutter(self):
        source = inspect.getsource(DeYazWindow._compose_template_pages)
        self.assertIn("self.dictation_left.setMinimumWidth(468)", source)
        self.assertIn("setContentsMargins(24, 0, 24, 18)", source)
        self.assertIn("self.record.shadow = self.hero_shadow", source)
        self.assertIn("self.record.shadow_animation.setTargetObject", source)

    def test_dictation_microphone_has_only_the_combo_item_icon(self):
        source = inspect.getsource(DeYazWindow._compose_template_pages)
        self.assertNotIn("dictation_audio_icon", source)
        self.assertIn("self._load_dictation_microphones", source)

    def test_media_seek_has_room_for_the_styled_handle(self):
        source = inspect.getsource(DeYazWindow._compose_template_pages)
        self.assertIn("self.file_seek.setFixedHeight(28)", source)

    def test_video_crt_overlay_tracks_the_video_surface(self):
        video = SubtitleVideoWidget()
        video.resize(640, 360)
        video.show()
        self.app.processEvents()
        self.assertIsInstance(video.crt_overlay, CrtVideoOverlay)
        self.assertEqual(video.crt_overlay.geometry(), video.rect())
        video.set_playing(True)
        self.assertTrue(video.crt_overlay.timer.isActive())
        video.set_playing(False)
        self.assertFalse(video.crt_overlay.timer.isActive())
        video.close()

    def test_dark_media_controls_use_theme_colours(self):
        source = inspect.getsource(DeYazWindow.refresh_page_chrome)
        self.assertIn("QLabel#mediaTime {{ color: {c['text']}", source)
        self.assertIn("QPushButton#mediaSkip {{ background-color: {c['surface2']}; color: {c['text']}", source)


if __name__ == "__main__":
    unittest.main()
