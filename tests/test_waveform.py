import unittest
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import filetranscribe
import api


class WaveformTests(unittest.TestCase):
    def test_ffmpeg_helpers_are_hidden_on_windows(self):
        kwargs = filetranscribe.hidden_subprocess_kwargs()
        if os.name == "nt":
            self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertTrue(
                kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
            )
        else:
            self.assertEqual(kwargs, {})

    def test_only_transient_provider_failures_are_retried(self):
        self.assertTrue(filetranscribe.retryable_chunk_error(
            api.ApiError("invalid model ID", 400)
        ))
        self.assertTrue(filetranscribe.retryable_chunk_error(
            api.ApiError("rate limited", 429)
        ))
        self.assertTrue(filetranscribe.retryable_chunk_error(
            api.ApiError("gateway", 503)
        ))
        self.assertFalse(filetranscribe.retryable_chunk_error(
            api.ApiError("bad API key", 401)
        ))

    @patch("filetranscribe.CHUNK_RETRY_DELAYS", (0, 0))
    @patch("filetranscribe.api.transcribe")
    def test_chunk_retry_keeps_exact_selected_model(self, transcribe):
        transcribe.side_effect = [
            api.ApiError("HTTP 400: invalid model ID", 400),
            "Salam",
        ]
        target = api.Target(
            "openai", "OpenAI", "key", "https://api.openai.com/v1",
            "gpt-transcribe",
        )
        worker = filetranscribe.FileTranscriber({"transcribe_prompt": ""})
        result = worker._transcribe_chunk(
            target, "chunk.wav", "az", False, 5, 9
        )
        self.assertEqual(result, "Salam")
        self.assertEqual([call.args[0].model for call in transcribe.call_args_list], [
            "gpt-transcribe", "gpt-transcribe",
        ])

    @patch("filetranscribe.CHUNK_RETRY_DELAYS", (0, 0))
    @patch("filetranscribe.api.transcribe")
    def test_silent_chunk_is_skipped_after_retries(self, transcribe):
        transcribe.side_effect = api.EmptyTranscriptError("empty")
        target = api.Target(
            "openai", "OpenAI", "key", "https://api.openai.com/v1",
            "gpt-transcribe",
        )
        worker = filetranscribe.FileTranscriber({"transcribe_prompt": ""})
        result = worker._transcribe_chunk(
            target, "silent-tail.wav", "az", False, 9, 9
        )
        self.assertEqual(result, "")
        self.assertEqual(transcribe.call_count, 3)

    @patch("filetranscribe.CHUNK_RETRY_DELAYS", (0, 0))
    @patch("filetranscribe.api.transcribe_segments")
    def test_silent_timestamped_chunk_returns_empty_segment_list(self, transcribe):
        transcribe.side_effect = api.EmptyTranscriptError("empty")
        target = api.Target(
            "openai", "OpenAI", "key", "https://api.openai.com/v1",
            "gpt-transcribe",
        )
        worker = filetranscribe.FileTranscriber({"transcribe_prompt": ""})
        result = worker._transcribe_chunk(
            target, "silent-tail.wav", "az", True, 9, 9
        )
        self.assertEqual(result, [])
        self.assertEqual(transcribe.call_count, 3)

    def test_recovery_checkpoint_keeps_latest_and_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("filetranscribe._recovery_root", return_value=Path(directory)):
                saved = filetranscribe.save_recovery_transcript("raw transcript", "job1")
            self.assertEqual(Path(saved).read_text(encoding="utf-8"), "raw transcript")
            self.assertEqual(
                (Path(directory) / "latest-transcript.txt").read_text(encoding="utf-8"),
                "raw transcript",
            )

    @patch("filetranscribe.save_recovery_transcript", return_value="recovery.txt")
    @patch("filetranscribe.split_wav", return_value=[("chunk.wav", 0)])
    @patch("filetranscribe._to_wav", return_value="prepared.wav")
    @patch("filetranscribe.shutil.which", return_value="ffmpeg")
    def test_postprocess_error_returns_raw_transcript(
            self, _which, _to_wav, _split, _save):
        class FakeConfig(dict):
            def file_transcribe_target(self):
                return api.Target("openai", "OpenAI", "key", "url", "gpt-transcribe")

        conf = FakeConfig(language="az", transcribe_prompt="")
        worker = filetranscribe.FileTranscriber(conf)
        worker._transcribe_chunk = lambda *args, **kwargs: "raw transcript"
        worker._cleanup = lambda *args, **kwargs: (_ for _ in ()).throw(
            api.ApiError("invalid model ID", 400)
        )
        completed = []
        worker.finished.connect(lambda text, segments, warning: completed.append(
            (text, segments, warning)
        ))
        worker._work("recording.wav", False, True)
        self.assertEqual(completed[0][0], "raw transcript")
        self.assertIn("invalid model ID", completed[0][2])

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
