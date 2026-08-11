import unittest
from unittest.mock import patch

import api
from dikte_windows import Transcription
from project_context import CONTEXT_RULES, ContextSnapshot
from work_modes import mode, project_context_policy, uses_project_context


class FakeConfig:
    def __init__(self):
        self.values = {
            "language": "az",
            "transcribe_prompt": "",
            "work_mode": "prompt_engineer",
            "cleanup_enabled": True,
            "cleanup_model": "test-model",
            "cleanup_reasoning": "",
            "openrouter_base_url": "https://example.invalid",
        }

    def __getitem__(self, key):
        return self.values[key]

    def transcribe_target(self):
        return api.Target("openai", "OpenAI", "test", "https://example.invalid", "test")

    def openrouter_key(self):
        return "test"

    def cleanup_prompt(self):
        return "cleanup"


class PromptEngineerSafetyTests(unittest.TestCase):
    def test_prompt_engineer_requires_verified_project_context(self):
        self.assertEqual(project_context_policy("prompt_engineer"), "verified")
        self.assertFalse(uses_project_context("prompt_engineer"))
        verified = ContextSnapshot(
            text="requirements.txt: PyQt6", label="Dikte",
            project_root="C:/Dikte", confidence="high",
        )
        self.assertTrue(uses_project_context("prompt_engineer", verified))

    def test_prompt_contains_no_guessing_contract(self):
        prompt = mode("prompt_engineer")["prompt"]
        self.assertIn("only sources of concrete facts", prompt)
        self.assertIn("current stack", prompt)
        self.assertIn("do not fill it in yourself", prompt)
        self.assertIn("Use every relevant detail the user actually says", prompt)

    @patch("dikte_windows.cfg.append_history")
    @patch("dikte_windows.api.transcribe", return_value="mövcud layihənin UI-nı yaxşılaşdır")
    def test_pipeline_does_not_send_unverified_stack_to_prompt_engineer(
        self, _transcribe, _history
    ):
        captured = {}

        def fake_cleanup(text, key, model, system_prompt, reasoning, base_url,
                         context=""):
            captured["system_prompt"] = system_prompt
            captured["context"] = context
            return text

        detected = ContextSnapshot(
            text="Framework: React\nRuntime: Node.js",
            label="example",
            project_root="C:/example",
            confidence="medium",
        )
        pipeline = Transcription(FakeConfig())
        with patch("dikte_windows.api.cleanup", side_effect=fake_cleanup):
            pipeline._work("missing-test-audio.wav", 1.0, detected)

        self.assertEqual(captured["context"], "")
        self.assertNotIn(CONTEXT_RULES, captured["system_prompt"])
        self.assertNotIn("Framework: React", captured["system_prompt"])

    @patch("dikte_windows.cfg.append_history")
    @patch("dikte_windows.api.transcribe", return_value="mövcud app-i yaxşılaşdır")
    def test_pipeline_sends_verified_project_files_to_prompt_engineer(
        self, _transcribe, _history
    ):
        captured = {}

        def fake_cleanup(text, key, model, system_prompt, reasoning, base_url,
                         context=""):
            captured["context"] = context
            return text

        detected = ContextSnapshot(
            text="README.md excerpt: Native PyQt6 Windows app",
            label="Dikte",
            project_root="C:/Dikte",
            confidence="high",
            evidence="pwsh child working directory",
        )
        pipeline = Transcription(FakeConfig())
        with patch("dikte_windows.api.cleanup", side_effect=fake_cleanup):
            pipeline._work("missing-test-audio.wav", 1.0, detected)

        self.assertIn("Native PyQt6 Windows app", captured["context"])


if __name__ == "__main__":
    unittest.main()
