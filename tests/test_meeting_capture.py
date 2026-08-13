"""Meeting capture helpers that do not require live audio hardware."""

from pathlib import Path
import tempfile
import unittest
import wave

import numpy as np

from meeting_capture import (
    MeetingCapture, SAMPLE_RATE, _write_wav, has_audible_signal,
    live_translation_prompt, meeting_document, merge_mono_wavs,
    merge_live_delta, normalize_live_transcript, timestamp,
)


class MeetingCaptureTests(unittest.TestCase):
    def test_timestamp_is_compact_and_monotonic(self):
        self.assertEqual(timestamp(0), "00:00")
        self.assertEqual(timestamp(65.9), "01:05")
        self.assertEqual(timestamp(3605), "60:05")

    def test_two_sources_merge_to_stereo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mic = root / "mic.wav"
            system = root / "system.wav"
            output = root / "meeting.wav"
            _write_wav(mic, np.full(800, 0.25, dtype=np.float32))
            _write_wav(system, np.full(400, -0.25, dtype=np.float32))
            merge_mono_wavs(mic, system, output)

            with wave.open(str(output), "rb") as merged:
                self.assertEqual(merged.getnchannels(), 2)
                self.assertEqual(merged.getframerate(), SAMPLE_RATE)
                self.assertEqual(merged.getnframes(), 800)
                data = np.frombuffer(merged.readframes(800), dtype="int16").reshape(-1, 2)
            self.assertGreater(data[0, 0], 0)
            self.assertLess(data[0, 1], 0)
            self.assertEqual(data[-1, 1], 0)

    def test_raw_transcript_is_saved_once_without_ai_notes(self):
        transcript = "[00:00] Sən: Salam"
        document = meeting_document("transcript", transcript, transcript)
        self.assertEqual(document.count(transcript), 1)
        self.assertNotIn("---", document)

    def test_meeting_notes_keep_the_source_transcript(self):
        document = meeting_document(
            "meeting_notes", "# Görüş\n\n## Qərarlar\n- Razılaşdırıldı",
            "[00:00] Sən: Salam",
        )
        self.assertIn("## Tam transkript", document)
        self.assertIn("[00:00] Sən: Salam", document)

    def test_live_translation_prompt_forbids_added_content(self):
        prompt = live_translation_prompt("az")
        self.assertIn("natural Azerbaijani", prompt)
        self.assertIn("Do not summarize", prompt)
        self.assertEqual(live_translation_prompt("original"), "")

    def test_quiet_laptop_microphone_is_not_discarded_as_silence(self):
        quiet_speech = np.full(4_000, 0.0005, dtype=np.float32)
        self.assertTrue(has_audible_signal("mic", quiet_speech))
        self.assertFalse(has_audible_signal("system", quiet_speech))

    def test_live_transcript_display_is_clean_without_rewriting_words(self):
        self.assertEqual(
            normalize_live_transcript("  salam,   bu testdir  "),
            "Salam, bu testdir.",
        )

    def test_new_meeting_resets_live_timestamps_to_zero(self):
        capture = MeetingCapture({})
        capture.live_segment_starts = {"mic": 22.4, "system": 18.1}
        capture._reset_live_timestamps()
        self.assertEqual(capture.live_segment_starts, {"mic": 0.0, "system": 0.0})

    def test_live_delta_supports_incremental_and_cumulative_events(self):
        self.assertEqual(merge_live_delta("Salam", ", dünya"), "Salam, dünya")
        self.assertEqual(merge_live_delta("Salam", "Salam, dünya"), "Salam, dünya")


if __name__ == "__main__":
    unittest.main()
