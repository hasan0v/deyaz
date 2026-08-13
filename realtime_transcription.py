"""Native OpenAI Realtime transcription transport for Meeting Notes."""

import base64
import json
import threading
from urllib.parse import urlsplit, urlunsplit

import numpy as np
import websocket


REALTIME_RATE = 24_000
AUTO_TRANSCRIPTION_LANGUAGES = ("az", "en", "tr", "ru")


def realtime_url(base_url):
    """Convert an OpenAI HTTPS API base into a transcription WebSocket URL."""
    parsed = urlsplit(base_url.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/") + "/realtime"
    return urlunsplit((scheme, parsed.netloc, path, "intent=transcription", ""))


def pcm24_base64(data, source_rate=48_000):
    """Return mono PCM16/24 kHz audio expected by the Realtime API."""
    mono = np.asarray(data, dtype=np.float32).reshape(-1)
    if source_rate != REALTIME_RATE:
        # DeYaz captures at 48 kHz. Linear interpolation also keeps this helper
        # correct if a future device uses a different native sample rate.
        output_size = max(1, round(len(mono) * REALTIME_RATE / source_rate))
        old_points = np.linspace(0.0, 1.0, len(mono), endpoint=False)
        new_points = np.linspace(0.0, 1.0, output_size, endpoint=False)
        mono = np.interp(new_points, old_points, mono).astype(np.float32)
    pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype("<i2", copy=False)
    return base64.b64encode(pcm.tobytes()).decode("ascii")


def session_update(model, prompt="", language=""):
    transcription = {"model": model}
    if prompt:
        transcription["prompt"] = prompt
    if model == "gpt-live-transcribe":
        # The live model benefits from explicit expected languages when a
        # meeting mixes Azerbaijani with English technical names. "Auto" still
        # allows detection; this list only narrows likely languages.
        transcription["languages"] = (
            list(AUTO_TRANSCRIPTION_LANGUAGES)
            if not language or language == "auto"
            else [language]
        )
        transcription["delay"] = "medium"
    elif language and language != "auto":
        transcription["language"] = language
    return {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": REALTIME_RATE},
                    "transcription": transcription,
                    # GPT Live Transcribe emits deltas while audio arrives. The
                    # current model rejects server VAD, so DeYaz commits the
                    # final buffered turn explicitly when the meeting stops.
                    "turn_detection": None,
                }
            },
        },
    }


class RealtimeTranscriptionStream:
    """One authenticated Realtime transcription session for one audio source."""

    def __init__(self, target, language="", prompt="", on_completed=None,
                 on_delta=None, on_error=None):
        self.target = target
        self.language = language
        self.prompt = prompt
        self.on_completed = on_completed or (lambda text: None)
        self.on_delta = on_delta or (lambda text: None)
        self.on_error = on_error or (lambda message: None)
        self.opened = threading.Event()
        self.closed = threading.Event()
        self.send_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.audio_state_lock = threading.Lock()
        self.pending_audio = []
        self.uncommitted_audio = False
        self.app = None
        self.thread = None

    def start(self):
        if not self.target.api_key:
            raise RuntimeError("OpenAI API key boşdur.")
        headers = ["Authorization: Bearer " + self.target.api_key]
        self.app = websocket.WebSocketApp(
            realtime_url(self.target.base_url),
            header=headers,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.thread = threading.Thread(
            target=self.app.run_forever, name="deyaz-realtime-stt", daemon=True
        )
        self.thread.start()

    def _send(self, payload):
        if self.app is None or not self.opened.is_set():
            return False
        try:
            with self.send_lock:
                self.app.send(json.dumps(payload))
            return True
        except Exception as exc:
            self.on_error(str(exc))
            return False

    def _on_open(self, _app):
        self.opened.set()
        self._send(session_update(
            self.target.model, prompt=self.prompt, language=self.language
        ))
        with self.pending_lock:
            pending = self.pending_audio
            self.pending_audio = []
        for audio in pending:
            self._send({"type": "input_audio_buffer.append", "audio": audio})

    def _on_message(self, _app, message):
        try:
            event = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return
        event_type = event.get("type", "")
        if event_type == "conversation.item.input_audio_transcription.delta":
            delta = str(event.get("delta") or "")
            if delta:
                self.on_delta(delta)
        elif event_type == "conversation.item.input_audio_transcription.completed":
            transcript = str(event.get("transcript") or "").strip()
            if transcript:
                self.on_completed(transcript)
        elif event_type == "error":
            error = event.get("error") or {}
            self.on_error(str(error.get("message") or event))

    def _on_error(self, _app, error):
        if not self.closed.is_set():
            self.on_error(str(error))

    def _on_close(self, _app, _code, _message):
        self.closed.set()

    def append(self, data, source_rate=48_000):
        audio = pcm24_base64(data, source_rate)
        with self.audio_state_lock:
            self.uncommitted_audio = True
        if not self.opened.is_set():
            with self.pending_lock:
                # Each DeYaz capture block is about half a second. Retain at
                # most four seconds while the authenticated socket connects.
                self.pending_audio = (self.pending_audio + [audio])[-8:]
            return True
        return self._send({
            "type": "input_audio_buffer.append",
            "audio": audio,
        })

    def commit(self):
        """Finalize only when new audio exists; the socket stays live afterwards."""
        with self.audio_state_lock:
            if not self.uncommitted_audio or not self.opened.is_set():
                return False
            sent = self._send({"type": "input_audio_buffer.commit"})
            if sent:
                self.uncommitted_audio = False
            return sent

    def finish(self, timeout=3.0):
        if self.opened.is_set():
            self.commit()
            self.closed.wait(timeout)
        if self.app is not None:
            self.app.close()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
