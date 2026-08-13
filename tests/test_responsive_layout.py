import unittest

from deyaz_app import meeting_layout_mode_for_width


class ResponsiveMeetingLayoutTests(unittest.TestCase):
    def test_standard_small_window_uses_two_column_split(self):
        self.assertEqual(meeting_layout_mode_for_width(900), "split")

    def test_phone_width_stacks_panels(self):
        self.assertEqual(meeting_layout_mode_for_width(640), "stack")

    def test_wide_window_keeps_three_columns(self):
        self.assertEqual(meeting_layout_mode_for_width(1600), "wide")


if __name__ == "__main__":
    unittest.main()
