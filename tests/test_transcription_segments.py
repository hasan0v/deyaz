import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import api


TARGET = api.Target("openai", "OpenAI", "test", "https://example.invalid", "gpt-transcribe")


class TranscriptionSegmentTests(unittest.TestCase):
    def make_wav(self, seconds=12):
        handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        handle.close()
        path = Path(handle.name)
        with wave.open(str(path), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(16000)
            target.writeframes(b"\0\0" * 16000 * seconds)
        self.addCleanup(path.unlink, missing_ok=True)
        return str(path)

    @patch("api._transcribe_request")
    def test_uses_native_verbose_segments(self, request):
        target = TARGET._replace(model="native-segment-stt")
        request.return_value = {
            "text": "Salam. Davam edirik.",
            "segments": [
                {"start": 0, "end": 2.5, "text": " Salam. "},
                {"start": 2.5, "end": 5, "text": "Davam edirik."},
            ],
        }
        self.assertEqual(
            api.transcribe_segments(target, self.make_wav()),
            [(0.0, 2.5, "Salam."), (2.5, 5.0, "Davam edirik.")],
        )
        fields = request.call_args.args
        self.assertEqual(fields[4], "verbose_json")
        self.assertEqual(request.call_args.kwargs["granularity"], "segment")

    @patch("api._transcribe_request")
    def test_json_only_fallback_keeps_model_and_covers_duration(self, request):
        target = TARGET._replace(model="gpt-4o-transcribe")
        request.return_value = {
            "text": (
                "Birinci fikir burada bitir. İkinci fikir də kifayət qədər uzundur. "
                "Üçüncü fikir videonun sonuna qədər görünməlidir."
            )
        }
        segments = api.transcribe_segments(target, self.make_wav(15))
        self.assertGreater(len(segments), 1)
        self.assertAlmostEqual(segments[0][0], 0.0)
        self.assertAlmostEqual(segments[-1][1], 15.0)
        self.assertTrue(all(a[1] <= b[0] + 0.001 for a, b in zip(segments, segments[1:])))
        self.assertEqual(request.call_args.args[0].model, "gpt-4o-transcribe")
        self.assertEqual(request.call_args.args[4], "json")

    @patch("api._transcribe_request")
    def test_gpt_transcribe_uses_supported_json_without_verbose_probe(self, request):
        request.return_value = {"text": "Birinci cümlə. İkinci cümlə."}
        segments = api.transcribe_segments(TARGET, self.make_wav(8))
        self.assertTrue(segments)
        self.assertEqual(len(request.call_args_list), 1)
        self.assertEqual(request.call_args.args[4], "json")

    @patch("api._request")
    def test_gpt_transcribe_id_reaches_http_unchanged(self, request):
        request.return_value = {"text": "Salam"}
        api._transcribe_request(TARGET, self.make_wav(1), "az", "", "json")
        body = request.call_args.args[1]
        self.assertIn(b'\r\n\r\ngpt-transcribe\r\n', body)

    @patch("api._transcribe_request")
    def test_verbose_rejection_retries_same_model_as_json(self, request):
        target = TARGET._replace(model="native-segment-stt")
        request.side_effect = [
            api.ApiError("unsupported response format", 400),
            {"text": "Birinci cümlə. İkinci cümlə."},
        ]
        segments = api.transcribe_segments(target, self.make_wav(8))
        self.assertTrue(segments)
        self.assertEqual([call.args[0].model for call in request.call_args_list], [
            "native-segment-stt", "native-segment-stt",
        ])

    @patch("api._transcribe_request")
    def test_empty_transcript_is_retried_once(self, request):
        request.side_effect = [
            {"text": "", "usage": {"seconds": 12}},
            {"text": "Salam, bu testdir."},
        ]
        self.assertEqual(
            api.transcribe(TARGET, self.make_wav()),
            "Salam, bu testdir.",
        )
        self.assertEqual(len(request.call_args_list), 2)


if __name__ == "__main__":
    unittest.main()
