import unittest
from unittest.mock import patch

import filetranscribe
import api


class WaveformTests(unittest.TestCase):
    def test_gpt_transcribe_has_music_vocal_fallback(self):
        target = api.Target(
            "openai", "OpenAI", "key", "https://api.openai.com/v1",
            "gpt-transcribe",
        )
        fallback = filetranscribe.empty_transcript_fallback(target)
        self.assertEqual(fallback.model, "gpt-4o-transcribe")
        self.assertIsNone(filetranscribe.empty_transcript_fallback(
            target._replace(model="gpt-4o-transcribe")
        ))

    @patch("filetranscribe.subprocess.run")
    def test_extracts_normalized_real_audio_peaks(self, run):
        samples = [0, 1000, -2000, 4000] * 24
        run.return_value.returncode = 0
        run.return_value.stdout = b"".join(
            int(value).to_bytes(2, "little", signed=True) for value in samples
        )
        peaks = filetranscribe.extract_waveform_peaks("audio.mp3", count=12)
        self.assertEqual(len(peaks), 12)
        self.assertTrue(all(0.05 <= value <= 1.0 for value in peaks))
        self.assertTrue(run.called)


if __name__ == "__main__":
    unittest.main()
