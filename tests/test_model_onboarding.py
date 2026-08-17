import unittest

import config as cfg
import deyaz_app


class ModelOnboardingTests(unittest.TestCase):
    def test_recommendation_ids_are_unique(self):
        transcription = [choice[2] for choice in deyaz_app.TRANSCRIPTION_CHOICES]
        cleanup = [choice[2] for choice in deyaz_app.CLEANUP_CHOICES]
        self.assertEqual(len(transcription), len(set(transcription)))
        self.assertEqual(len(cleanup), len(set(cleanup)))

    def test_free_cleanup_option_is_explicit(self):
        free = [choice for choice in deyaz_app.CLEANUP_CHOICES if choice[2] == "openrouter/free"]
        self.assertEqual(len(free), 1)
        self.assertEqual(free[0][0], "PULSUZ")

    def test_recommendations_do_not_offer_unroutable_deepgram_model(self):
        transcription = [choice[2] for choice in deyaz_app.TRANSCRIPTION_CHOICES]
        self.assertNotIn("deepgram/nova-3", transcription)
        self.assertIn("mistralai/voxtral-mini-transcribe", transcription)
        self.assertIn("microsoft/mai-transcribe-1.5", transcription)

    def test_openrouter_recommendations_only_offer_verified_routes(self):
        transcription = [choice[2] for choice in deyaz_app.TRANSCRIPTION_CHOICES]
        self.assertIn("mistralai/voxtral-mini-transcribe", transcription)
        self.assertIn("qwen/qwen3-asr-0.6b", transcription)
        self.assertNotIn("openai/gpt-transcribe", transcription)
        self.assertNotIn("openai/gpt-4o-transcribe", transcription)
        self.assertNotIn("openai/gpt-4o-mini-transcribe", transcription)

    def test_provider_catalogs_are_distinct_and_luna_replaces_gpt5_mini(self):
        openrouter_audio = {
            choice[2] for choice in deyaz_app.OPENROUTER_TRANSCRIPTION_CHOICES
        }
        openai_audio = {
            choice[2] for choice in deyaz_app.OPENAI_TRANSCRIPTION_CHOICES
        }
        self.assertNotEqual(openrouter_audio, openai_audio)
        openrouter_text = {
            choice[2] for choice in deyaz_app.OPENROUTER_CLEANUP_CHOICES
        }
        openai_text = {
            choice[2] for choice in deyaz_app.OPENAI_CLEANUP_CHOICES
        }
        self.assertIn("openai/gpt-5.6-luna", openrouter_text)
        self.assertIn("gpt-5.6-luna", openai_text)
        self.assertNotIn("openai/gpt-5-mini", openrouter_text)

    def test_openai_catalog_uses_supported_transcribe_models(self):
        openai_audio = {
            choice[2] for choice in deyaz_app.OPENAI_TRANSCRIPTION_CHOICES
        }
        self.assertIn("gpt-4o-transcribe", openai_audio)
        self.assertIn("gpt-4o-mini-transcribe", openai_audio)
        self.assertIn("gpt-transcribe", openai_audio)
        self.assertNotIn("whisper-1", openai_audio)

    def test_meeting_catalog_separates_smart_and_true_live(self):
        meeting_audio = {
            choice[3] for choice in deyaz_app.MEETING_LIVE_TRANSCRIPTION_CHOICES
        }
        self.assertEqual(
            meeting_audio, {"gpt-transcribe", "gpt-live-transcribe"}
        )
        self.assertNotIn("whisper-1", meeting_audio)

    def test_file_catalog_keeps_provider_and_model_together(self):
        values = {
            f"{choice[2]}|{choice[3]}"
            for choice in deyaz_app.FILE_TRANSCRIPTION_CHOICES
        }
        self.assertIn("openai|gpt-transcribe", values)
        self.assertIn("openrouter|mistralai/voxtral-mini-transcribe", values)
        self.assertNotIn("openrouter|openai/gpt-transcribe", values)

    def test_file_target_is_independent_from_dictation_provider(self):
        conf = cfg.Config.__new__(cfg.Config)
        conf.data = dict(cfg.DEFAULTS)
        conf.data["transcribe_provider"] = "openrouter"
        conf.data["file_transcribe_provider"] = "openai"
        conf.data["file_transcribe_model"] = "gpt-transcribe"
        target = conf.file_transcribe_target()
        self.assertEqual(target.provider, "openai")
        self.assertEqual(target.model, "gpt-transcribe")

    def test_openai_cleanup_target_removes_openrouter_namespace(self):
        conf = cfg.Config.__new__(cfg.Config)
        conf.data = dict(cfg.DEFAULTS)
        conf.data["cleanup_provider"] = "openai"
        conf.data["openai_cleanup_model"] = "openai/gpt-5.6-luna"
        target = conf.cleanup_target()
        self.assertEqual(target.provider, "openai")
        self.assertEqual(target.model, "gpt-5.6-luna")


if __name__ == "__main__":
    unittest.main()
