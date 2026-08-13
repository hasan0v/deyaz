import unittest

from work_modes import (
    WORK_MODES, all_modes, mode, normalise_custom_mode, set_custom_modes,
)


class CustomWorkModeTests(unittest.TestCase):
    def tearDown(self):
        set_custom_modes([])

    def test_custom_mode_is_available_to_transcription_pipeline(self):
        set_custom_modes([{
            "id": "daily_report",
            "name": "Daily Report",
            "short": "Report",
            "color": "#12ABEF",
            "prompt": "Turn the transcript into a factual daily report.",
            "project_context": "verified",
        }])
        self.assertIn("daily_report", all_modes())
        self.assertEqual(mode("daily_report")["short"], "Report")
        self.assertEqual(mode("daily_report")["project_context"], "verified")

    def test_invalid_custom_values_are_normalised(self):
        item = normalise_custom_mode({
            "id": "Daily Mode",
            "name": "My Mode",
            "color": "not-a-colour",
            "prompt": "Keep only supplied facts.",
            "project_context": "anything",
        })
        self.assertEqual(item["id"], "daily_mode")
        self.assertEqual(item["color"], "#7C8CFF")
        self.assertFalse(item["project_context"])

    def test_built_in_modes_cannot_be_overwritten(self):
        item = normalise_custom_mode({
            "id": "dictation",
            "name": "Custom Dictation",
            "prompt": "Custom prompt",
        })
        self.assertTrue(item["id"].startswith("custom_"))
        set_custom_modes([item])
        self.assertEqual(mode("dictation"), WORK_MODES["dictation"])


if __name__ == "__main__":
    unittest.main()
