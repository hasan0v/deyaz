import base64
import unittest

import numpy as np

from realtime_transcription import (
    REALTIME_RATE, RealtimeTranscriptionStream, pcm24_base64, realtime_url,
    session_update,
)
import api


class RealtimeTranscriptionTests(unittest.TestCase):
    def test_api_base_uses_dedicated_transcription_websocket(self):
        self.assertEqual(
            realtime_url("https://api.openai.com/v1"),
            "wss://api.openai.com/v1/realtime?intent=transcription",
        )

    def test_48khz_float_audio_is_downsampled_to_24khz_pcm16(self):
        source = np.linspace(-0.5, 0.5, 48_000, dtype=np.float32)
        raw = base64.b64decode(pcm24_base64(source, 48_000))
        self.assertEqual(len(raw), REALTIME_RATE * 2)

    def test_session_is_transcription_with_manual_commit(self):
        event = session_update("gpt-live-transcribe", "DeYaz", "az")
        audio = event["session"]["audio"]["input"]
        self.assertEqual(event["session"]["type"], "transcription")
        self.assertEqual(audio["format"]["rate"], 24_000)
        self.assertEqual(audio["transcription"]["model"], "gpt-live-transcribe")
        self.assertEqual(audio["transcription"]["languages"], ["az"])
        self.assertNotIn("language", audio["transcription"])
        self.assertIsNone(audio["turn_detection"])

    def test_auto_language_sends_expected_multilingual_hints(self):
        transcription = session_update(
            "gpt-live-transcribe", "", "auto"
        )["session"]["audio"]["input"]["transcription"]
        self.assertEqual(transcription["languages"], ["az", "en", "tr", "ru"])
        self.assertEqual(transcription["delay"], "medium")

    def test_periodic_commit_only_sends_when_new_audio_exists(self):
        target = api.Target(
            "openai", "OpenAI", "key", "https://api.openai.com/v1",
            "gpt-live-transcribe",
        )
        stream = RealtimeTranscriptionStream(target)
        stream.opened.set()
        sent = []
        stream._send = lambda payload: sent.append(payload) or True
        stream.append(np.zeros(2400, dtype=np.float32), 24000)
        self.assertTrue(stream.commit())
        self.assertFalse(stream.commit())
        self.assertEqual(sent[-1]["type"], "input_audio_buffer.commit")


if __name__ == "__main__":
    unittest.main()
