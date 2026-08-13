import unittest

from deyaz_app import (
    compose_meeting_live_text, format_media_time, format_srt_clock,
    subtitle_at_position,
)


class MediaPlayerHelperTests(unittest.TestCase):
    def test_formats_short_and_long_media_time(self):
        self.assertEqual(format_media_time(0), "00:00")
        self.assertEqual(format_media_time(65_000), "01:05")
        self.assertEqual(format_media_time(3_661_000), "1:01:01")

    def test_formats_srt_monitor_clock(self):
        self.assertEqual(format_srt_clock(17_042), "00:00:17,042")

    def test_live_meeting_preview_has_cursor_until_segment_is_final(self):
        text = compose_meeting_live_text(
            [{"source": "mic", "speaker": "Sən", "start": 0, "text": "Salam."}],
            {"system": {
                "source": "system", "speaker": "Görüş səsi", "start": 5,
                "text": "Davam edir",
            }},
        )
        self.assertIn("[00:00] Sən: Salam.", text)
        self.assertIn("[00:05] Görüş səsi: Davam edir  ▍", text)

    def test_finds_caption_at_player_position(self):
        segments = [(0, 2.5, "Birinci"), (2.5, 5, "İkinci")]
        self.assertEqual(subtitle_at_position(segments, 1_000), "Birinci")
        self.assertEqual(subtitle_at_position(segments, 3_000), "İkinci")
        self.assertEqual(subtitle_at_position(segments, 6_000), "")

    def test_segment_without_end_time_gets_short_preview_window(self):
        self.assertEqual(subtitle_at_position([(10, 0, "Mətn")], 12_000), "Mətn")
        self.assertEqual(subtitle_at_position([(10, 0, "Mətn")], 15_000), "")


if __name__ == "__main__":
    unittest.main()
