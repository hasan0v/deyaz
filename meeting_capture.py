"""Meeting capture with live chunks and platform-aware audio sources."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import wave

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

import api
import config as cfg
from realtime_transcription import RealtimeTranscriptionStream


SAMPLE_RATE = 48_000
READ_FRAMES = SAMPLE_RATE // 2
CHUNK_SECONDS = 7
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_SECONDS
LIVE_COMMIT_SECONDS = 5
LIVE_COMMIT_FRAMES = SAMPLE_RATE * LIVE_COMMIT_SECONDS
MIN_CHUNK_FRAMES = SAMPLE_RATE
SILENCE_RMS = 0.0015
MIC_SILENCE_RMS = 0.0002

OUTPUT_LANGUAGES = {
    "az": "natural Azerbaijani",
    "en": "natural English",
    "tr": "natural Turkish",
    "ru": "natural Russian",
}


def timestamp(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def meeting_document(result_type, notes, transcript):
    """Build saved Markdown without duplicating an unchanged transcript."""
    if result_type == "transcript":
        return f"# Tam transkript\n\n{transcript}\n"
    return f"{notes.rstrip()}\n\n---\n\n## Tam transkript\n\n{transcript}\n"


def live_translation_prompt(language):
    target = OUTPUT_LANGUAGES.get(language)
    if not target:
        return ""
    return (
        f"Translate the transcript faithfully into {target}. Preserve names, numbers, "
        "technical terms, intent and tone. Do not summarize, explain, answer, censor, "
        "or add information. Return only the translated speech."
    )


def _mono_float(data):
    array = np.asarray(data, dtype=np.float32)
    if array.ndim == 1:
        return array
    return array.mean(axis=1, dtype=np.float32)


def _write_wav(path, data, samplerate=SAMPLE_RATE):
    pcm = (np.clip(data, -1, 1) * 32767).astype("int16", copy=False)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(samplerate)
        wav.writeframes(pcm.tobytes())


def has_audible_signal(source, data):
    """Keep quiet laptop microphones while still suppressing loopback noise."""
    if not len(data):
        return False
    threshold = MIC_SILENCE_RMS if source == "mic" else SILENCE_RMS
    rms = float(np.sqrt(np.mean(np.square(data))))
    return rms >= threshold


def normalize_live_transcript(text):
    """Fix display-level spacing/casing without rewriting spoken words."""
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return ""
    value = re.sub(r"(?<=[.!?…])(?=[^\s])", " ", value)
    value = value[0].upper() + value[1:]
    if value[-1] not in ".!?…":
        value += "."
    return value


def merge_live_delta(current, delta):
    """Accept both incremental and cumulative Realtime delta event styles."""
    current = str(current or "")
    delta = str(delta or "")
    if not delta:
        return current
    if delta.startswith(current):
        return delta
    if current.endswith(delta):
        return current
    return current + delta


def merge_mono_wavs(left_path, right_path, output_path):
    """Merge microphone/system mono tracks into one aligned stereo WAV."""
    block_frames = 65_536
    with wave.open(str(left_path), "rb") as left, wave.open(
        str(right_path), "rb"
    ) as right, wave.open(str(output_path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        while True:
            left_data = left.readframes(block_frames)
            right_data = right.readframes(block_frames)
            if not left_data and not right_data:
                break
            l_array = np.frombuffer(left_data, dtype="int16")
            r_array = np.frombuffer(right_data, dtype="int16")
            count = max(len(l_array), len(r_array))
            stereo = np.zeros((count, 2), dtype="int16")
            stereo[:len(l_array), 0] = l_array
            stereo[:len(r_array), 1] = r_array
            out.writeframes(stereo.tobytes())


class MeetingCapture(QObject):
    """Capture two Windows audio sources and emit near-live transcript pieces."""

    segment = pyqtSignal(dict)
    partial = pyqtSignal(dict)
    level = pyqtSignal(str, float)
    status = pyqtSignal(str)
    finished = pyqtSignal(str, str, str, float)
    failed = pyqtSignal(str)

    def __init__(self, conf):
        super().__init__()
        self.conf = conf
        self.active = False
        self.stop_event = threading.Event()
        self.capture_threads = []
        self.executor = None
        self.segments = []
        self.segment_lock = threading.Lock()
        self.started_at = 0.0
        self.base = ""
        self.keep_audio = False
        self.track_paths = {}
        self.devices = {}
        self.live_streams = {}
        self.live_partial_texts = {"mic": "", "system": ""}
        self._reset_live_timestamps()
        self.live_partial_texts = {"mic": "", "system": ""}

    def _reset_live_timestamps(self):
        """Start every meeting's microphone and system transcript at zero."""
        self.live_segment_starts = {"mic": 0.0, "system": 0.0}

    def available_sources(self):
        """Return current source names without keeping stale hardware handles."""
        import soundcard as sc

        mic = self._resolve_microphone(sc)
        system = self._resolve_loopback(sc) if os.name == "nt" else None
        return mic.name, system.name if system is not None else ""

    def _resolve_microphone(self, sc):
        target = str(self.conf.get("meeting_mic_target", "")).strip()
        return sc.get_microphone(target) if target else sc.default_microphone()

    def _resolve_loopback(self, sc):
        target = str(self.conf.get("meeting_system_target", "")).strip()
        if target:
            return sc.get_microphone(target, include_loopback=True)
        speaker = sc.default_speaker()
        return sc.get_microphone(speaker.id, include_loopback=True)

    def start(self):
        if self.active:
            return False
        try:
            import soundcard as sc
            mic = self._resolve_microphone(sc)
            system = self._resolve_loopback(sc) if os.name == "nt" else None
            if mic is None:
                raise RuntimeError("Mikrofon tapılmadı.")
        except Exception as exc:
            self.failed.emit(f"Meeting audio başlatılmadı: {exc}")
            return False

        self.stop_event.clear()
        self.segments = []
        # A MeetingCapture object is reused for every meeting. These offsets
        # must not leak from the previous session into the next one's first
        # transcript line.
        self._reset_live_timestamps()
        self.capture_threads = []
        self.started_at = time.monotonic()
        self.base = time.strftime("meeting-%Y%m%d-%H%M%S")
        self.keep_audio = bool(self.conf.get("meeting_keep_audio", False))
        self.devices = {"mic": mic}
        if system is not None:
            self.devices["system"] = system
        self.executor = ThreadPoolExecutor(
            max_workers=len(self.devices), thread_name_prefix="deyaz-live"
        )
        self.active = True
        cfg.MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

        target = self.conf.meeting_transcribe_target()
        if target.model == "gpt-live-transcribe":
            if target.provider != "openai":
                self.failed.emit("GPT Live Transcribe yalnız birbaşa OpenAI ilə işləyir.")
                self.active = False
                self.executor.shutdown(wait=False, cancel_futures=True)
                self.executor = None
                return False
            language = self.conf.get("meeting_language", "") or self.conf["language"]
            for source in self.devices:
                stream = RealtimeTranscriptionStream(
                    target, language=language, prompt=self.conf.meeting_hint(),
                    on_completed=lambda text, source=source:
                        self._accept_live_text(source, text),
                    on_delta=lambda delta, source=source:
                        self._accept_live_delta(source, delta),
                    on_error=lambda message, source=source:
                        self.status.emit(
                            f"{self._speaker(source)} canlı bağlantı xətası: {message}"
                        ),
                )
                self.live_streams[source] = stream
                stream.start()

        for source in self.devices:
            thread = threading.Thread(
                target=self._capture_source, args=(source,), daemon=True
            )
            self.capture_threads.append(thread)
            thread.start()
        if os.name != "nt":
            self.status.emit("Canlı transkript başladı · mikrofon")
        else:
            self.status.emit(
                "True Live transkript başladı" if self.live_streams
                else "Smart Live transkript başladı"
            )
        return True

    def stop(self):
        if not self.active:
            return
        self.stop_event.set()
        self.status.emit("Son hissələr hazırlanır…")
        threading.Thread(target=self._finish, daemon=True).start()

    def _capture_source(self, source):
        device = self.devices[source]
        pending = []
        pending_frames = 0
        live_frames = 0
        chunk_start = max(0.0, time.monotonic() - self.started_at)
        track = None
        try:
            if self.keep_audio:
                fd, raw_path = tempfile.mkstemp(
                    prefix=f"{self.base}-{source}-", suffix=".wav"
                )
                os.close(fd)
                self.track_paths[source] = Path(raw_path)
                track = wave.open(raw_path, "wb")
                track.setnchannels(1)
                track.setsampwidth(2)
                track.setframerate(SAMPLE_RATE)

            with device.recorder(samplerate=SAMPLE_RATE, blocksize=2048) as recorder:
                while not self.stop_event.is_set():
                    data = _mono_float(recorder.record(numframes=READ_FRAMES))
                    if not len(data):
                        continue
                    self.level.emit(source, min(1.0, float(np.max(np.abs(data))) * 2.5))
                    if track is not None:
                        pcm = (np.clip(data, -1, 1) * 32767).astype("int16", copy=False)
                        track.writeframesraw(pcm.tobytes())
                    if source in self.live_streams:
                        self.live_streams[source].append(data, SAMPLE_RATE)
                        live_frames += len(data)
                        if live_frames >= LIVE_COMMIT_FRAMES:
                            self.live_streams[source].commit()
                            live_frames = 0
                        continue
                    pending.append(data)
                    pending_frames += len(data)
                    if pending_frames >= CHUNK_FRAMES:
                        chunk = np.concatenate(pending)
                        end = max(chunk_start, time.monotonic() - self.started_at)
                        self._submit_chunk(source, chunk, chunk_start, end)
                        pending, pending_frames = [], 0
                        chunk_start = end
        except Exception as exc:
            self.status.emit(f"{self._speaker(source)} audio xətası: {exc}")
        finally:
            if source not in self.live_streams and pending_frames >= MIN_CHUNK_FRAMES:
                chunk = np.concatenate(pending)
                self._submit_chunk(
                    source, chunk, chunk_start,
                    max(chunk_start, time.monotonic() - self.started_at),
                )
            if track is not None:
                track.close()

    def _submit_chunk(self, source, data, started, ended):
        if not has_audible_signal(source, data):
            return
        if self.executor is not None:
            self.executor.submit(
                self._transcribe_chunk, source, data.copy(), started, ended
            )

    def _transcribe_chunk(self, source, data, started, ended):
        fd, path = tempfile.mkstemp(prefix="deyaz-meeting-live-", suffix=".wav")
        os.close(fd)
        try:
            _write_wav(path, data)
            language = self.conf.get("meeting_language", "") or self.conf["language"]
            text = api.transcribe(
                self.conf.meeting_transcribe_target(), path, language=language,
                prompt=self.conf.meeting_hint(), timeout=180,
            ).strip()
            if not text:
                return
            self._store_transcript(source, text, started, ended)
        except Exception as exc:
            self.status.emit(f"Canlı transkript xətası: {exc}")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _accept_live_text(self, source, text):
        """Turn a native Realtime completed event into a meeting segment."""
        ended = max(0.0, time.monotonic() - self.started_at)
        with self.segment_lock:
            started = self.live_segment_starts.get(source, 0.0)
            self.live_segment_starts[source] = ended
            self.live_partial_texts[source] = ""
        if self.executor is not None:
            self.executor.submit(self._store_transcript, source, text, started, ended)

    def _accept_live_delta(self, source, delta):
        """Publish an in-progress Realtime sentence without persisting it yet."""
        with self.segment_lock:
            combined = merge_live_delta(self.live_partial_texts.get(source, ""), delta)
            self.live_partial_texts[source] = combined
            started = self.live_segment_starts.get(source, 0.0)
        shown = " ".join(combined.split()).strip()
        if shown:
            self.partial.emit({
                "source": source,
                "speaker": self._speaker(source),
                "start": float(started),
                "text": shown,
            })

    def _store_transcript(self, source, text, started, ended):
        """Translate when requested, persist the segment and update the UI."""
        try:
            original_text = normalize_live_transcript(text)
            if not original_text:
                return
            text = original_text
            output_language = self.conf.get(
                "meeting_live_output_language", "original"
            )
            translation_prompt = live_translation_prompt(output_language)
            if translation_prompt:
                try:
                    target = self.conf.meeting_text_target()
                    text = api.cleanup(
                        text, target.api_key, target.model, translation_prompt,
                        "", target.base_url, provider=target.provider,
                        service=target.service, timeout=120,
                    ).strip()
                except Exception as exc:
                    self.status.emit(
                        f"Canlı tərcümə alınmadı; orijinal mətn göstərilir: {exc}"
                    )
                    text = original_text
            item = {
                "source": source,
                "speaker": self._speaker(source),
                "start": float(started),
                "end": float(ended),
                "text": text,
                "original_text": original_text,
            }
            with self.segment_lock:
                self.segments.append(item)
            self.segment.emit(item)
        except Exception as exc:
            self.status.emit(f"Canlı transkript xətası: {exc}")

    def _speaker(self, source):
        mine, theirs = self.conf.speaker_names()
        return mine if source == "mic" else theirs

    def _finish(self):
        for thread in self.capture_threads:
            thread.join(timeout=4)
        for stream in self.live_streams.values():
            stream.finish(timeout=2.5)
        self.live_streams = {}
        if self.executor is not None:
            self.executor.shutdown(wait=True, cancel_futures=False)
            self.executor = None
        duration = max(0.0, time.monotonic() - self.started_at)
        self.active = False

        ordered = sorted(self.segments, key=lambda item: (item["start"], item["source"]))
        transcript = "\n".join(
            f"[{timestamp(item['start'])}] {item['speaker']}: {item['text']}"
            for item in ordered
        )
        if not transcript:
            self._cleanup_tracks()
            self.failed.emit("Görüşdə transkripsiya ediləcək danışıq aşkarlanmadı.")
            return

        result_type = self.conf.get("meeting_result_type", "meeting_notes")
        notes = transcript
        if result_type != "transcript" and self.conf.get("meeting_cleanup", True):
            status_by_type = {
                "key_points": "Görüşün əsas məqamları hazırlanır…",
                "detailed_summary": "Görüşün ətraflı icmalı hazırlanır…",
                "action_items": "Görüş tapşırıqları hazırlanır…",
            }
            self.status.emit(status_by_type.get(
                result_type, "Görüş xülasəsi və tapşırıqlar hazırlanır…"
            ))
            try:
                target = self.conf.meeting_text_target()
                notes = api.cleanup(
                    transcript, target.api_key, target.model,
                    self.conf.meeting_prompt(result_type),
                    self.conf.get("meeting_reasoning", ""),
                    target.base_url, provider=target.provider, service=target.service,
                    timeout=240,
                )
            except Exception as exc:
                self.status.emit(f"Xülasə yaradılmadı; canlı transkript saxlandı: {exc}")

        markdown_path, audio_path = cfg.meeting_paths(self.base)
        document = meeting_document(result_type, notes, transcript)
        markdown_path.write_text(document, encoding="utf-8")
        saved_audio = ""
        if self.keep_audio and all(
            source in self.track_paths for source in ("mic", "system")
        ):
            try:
                merge_mono_wavs(
                    self.track_paths["mic"], self.track_paths["system"], audio_path
                )
                saved_audio = str(audio_path)
            except Exception as exc:
                self.status.emit(f"Audio faylı saxlanmadı: {exc}")
        self._cleanup_tracks()
        cfg.save_meeting({
            "base": self.base,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": round(duration, 1),
            "status": "complete",
            "notes": str(markdown_path),
            "audio": saved_audio,
            "segments": len(ordered),
            "result_type": result_type,
        })
        self.finished.emit(transcript, notes, str(markdown_path), duration)

    def _cleanup_tracks(self):
        for path in self.track_paths.values():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self.track_paths = {}
