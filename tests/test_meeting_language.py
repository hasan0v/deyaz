import unittest

import config


class MeetingLanguageTests(unittest.TestCase):
    def make_config(self, output_language):
        conf = config.Config.__new__(config.Config)
        conf.data = dict(config.DEFAULTS)
        conf.data["meeting_live_output_language"] = output_language
        conf.data["meeting_prompt"] = "Prepare accurate meeting notes."
        return conf

    def test_original_language_explicitly_blocks_english_translation(self):
        prompt = self.make_config("original").meeting_prompt("meeting_notes")
        self.assertIn("transcript's original language", prompt)
        self.assertIn("Do not translate it into English", prompt)

    def test_selected_azerbaijani_is_required_in_final_notes(self):
        prompt = self.make_config("az").meeting_prompt("key_points")
        self.assertIn("only in natural Azerbaijani", prompt)


if __name__ == "__main__":
    unittest.main()
