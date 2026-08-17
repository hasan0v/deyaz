"""DeYaz desktop: record, transcribe, clean up and paste."""

import ctypes
import html
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

if os.name == "nt":
    from ctypes import wintypes
else:
    wintypes = None

import sounddevice as sd
import qtawesome as qta
from PyQt6.QtCore import (
    QEasingCurve, QObject, QPointF, QPropertyAnimation, QRect, QRectF, QSize,
    QTimer, Qt, QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QDesktopServices, QFont, QIcon, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QStackedWidget, QSystemTrayIcon, QTabWidget,
    QToolButton, QVBoxLayout, QWidget,
)

import api
from audio_devices import (
    audio_choice_signature, choice_index, resolve_sounddevice_selector,
    soundcard_microphone_choices, sounddevice_input_choices,
)
import config as cfg
import credential_store
import diagnostics
import filetranscribe
import i18n
import openrouter_oauth
from meeting_capture import MeetingCapture, timestamp as meeting_timestamp
from work_modes import (
    WORK_MODES, all_modes, mode as get_work_mode, project_context_policy,
    normalise_custom_mode, set_custom_modes, uses_project_context,
)
from project_context import CONTEXT_RULES, ContextSnapshot, capture_context


RATE = 16000
HOTKEY_ID = 0xD17E
MOD_ALT, MOD_CONTROL = 0x0001, 0x0002
MOD_SHIFT, MOD_NOREPEAT = 0x0004, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
INSTANCE_NAME = "deyaz-desktop"
APP_USER_MODEL_ID = "DeYaz.Desktop.3"
ASSET_DIR = Path(__file__).resolve().parent / "assets"
ICON_PATH = ASSET_DIR / (
    "deyaz.icns" if sys.platform == "darwin" else
    "deyaz.ico" if os.name == "nt" else
    "deyaz-logo.png"
)
LOGO_PATH = ASSET_DIR / "deyaz-logo.png"


def format_media_time(milliseconds):
    seconds = max(0, int(milliseconds or 0) // 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (
        f"{hours}:{minutes:02d}:{seconds:02d}"
        if hours else f"{minutes:02d}:{seconds:02d}"
    )


def format_srt_clock(milliseconds):
    """SRT-style clock used by the audio monitor visualizer."""
    milliseconds = max(0, int(milliseconds or 0))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def meeting_layout_mode_for_width(width):
    """Responsive Meeting layout: stack only when two columns cannot fit."""
    width = max(0, int(width or 0))
    return "stack" if width < 700 else "split" if width < 1180 else "wide"


def responsive_density_for_width(width):
    """Shared density tier for the shell, pages and modal surfaces."""
    width = max(0, int(width or 0))
    return "narrow" if width < 640 else "compact" if width < 960 else "roomy"


def responsive_content_width(window_width):
    """Approximate the real page width after adaptive shell gutters."""
    window_width = max(0, int(window_width or 0))
    density = responsive_density_for_width(window_width)
    gutter = 24 if density == "narrow" else 36 if density == "compact" else 52
    return max(320, min(1240, window_width - gutter))


def subtitle_at_position(segments, milliseconds):
    """Return the timed segment visible at the current player position."""
    second = max(0.0, float(milliseconds or 0) / 1000.0)
    for segment in segments or []:
        if not isinstance(segment, (tuple, list)) or len(segment) < 3:
            continue
        start, end, text = segment[:3]
        try:
            start = float(start or 0)
            end = float(end or 0)
        except (TypeError, ValueError):
            continue
        if end <= start:
            end = start + 4.0
        if start <= second < end:
            return str(text or "").strip()
    return ""


def compose_meeting_live_text(finalized, partials):
    """Combine stable segments and current speech previews in timestamp order."""
    entries = []
    for item in finalized or []:
        entries.append((
            float(item.get("start", 0) or 0), str(item.get("source", "")),
            f"[{meeting_timestamp(item.get('start', 0))}] "
            f"{item.get('speaker', '')}: {item.get('text', '')}",
        ))
    for item in (partials or {}).values():
        text = str(item.get("text", "") or "").strip()
        if text:
            entries.append((
                float(item.get("start", 0) or 0), str(item.get("source", "")),
                f"[{meeting_timestamp(item.get('start', 0))}] "
                f"{item.get('speaker', '')}: {text}  ▍",
            ))
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    return "\n".join(entry[2] for entry in entries)


class CrtVideoOverlay(QWidget):
    """Lightweight CRT glass drawn above video without intercepting input."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fileVideoOverlay")
        self.phase = 0.0
        self.playing = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._animate)

    def set_playing(self, playing):
        self.playing = bool(playing)
        if self.playing:
            self.timer.start()
        else:
            self.timer.stop()
        self.update()

    def _animate(self):
        self.phase = (self.phase + 0.018) % 1.0
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if bounds.width() <= 0 or bounds.height() <= 0:
            return

        # Rounded glass keeps the video inside the television-shaped screen.
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 14, 14)
        painter.setClipPath(clip)

        # Fine phosphor scanlines and a very subtle RGB mask.
        painter.setPen(QPen(QColor(4, 12, 10, 44), 1))
        for y in range(2, self.height(), 4):
            painter.drawLine(0, y, self.width(), y)
        for x, colour in ((0, QColor(255, 80, 90, 9)),
                          (1, QColor(80, 255, 170, 8)),
                          (2, QColor(80, 150, 255, 8))):
            painter.setPen(QPen(colour, 1))
            for column in range(x, self.width(), 6):
                painter.drawLine(column, 0, column, self.height())

        # Moving refresh band is restrained enough to avoid hiding captions.
        sweep_y = bounds.top() + bounds.height() * self.phase
        sweep = QLinearGradient(0, sweep_y - 16, 0, sweep_y + 16)
        sweep.setColorAt(0.0, QColor(145, 255, 205, 0))
        sweep.setColorAt(0.5, QColor(145, 255, 205, 22 if self.playing else 10))
        sweep.setColorAt(1.0, QColor(145, 255, 205, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(sweep)
        painter.drawRect(QRectF(0, sweep_y - 16, self.width(), 32))

        # Curved-screen vignette and inner glass highlight.
        vignette = QRadialGradient(bounds.center(), bounds.width() * 0.68)
        vignette.setColorAt(0.0, QColor(0, 0, 0, 0))
        vignette.setColorAt(0.72, QColor(0, 0, 0, 12))
        vignette.setColorAt(1.0, QColor(0, 0, 0, 112))
        painter.setBrush(vignette)
        painter.drawRoundedRect(bounds, 14, 14)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(214, 255, 235, 52), 1.2))
        painter.drawRoundedRect(bounds.adjusted(2, 2, -2, -2), 12, 12)


class SubtitleVideoWidget(QVideoWidget):
    """Video surface with CRT glass and captions kept above the effect."""

    def __init__(self, parent=None, objectName=""):
        super().__init__(parent)
        if objectName:
            self.setObjectName(objectName)
        self.crt_overlay = CrtVideoOverlay(self)
        self.subtitle_label = QLabel(self, objectName="fileSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.subtitle_label.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.crt_overlay.setGeometry(self.rect())
        margin = max(16, min(28, self.width() // 16))
        caption_height = max(54, min(86, self.height() // 3))
        self.subtitle_label.setGeometry(
            margin,
            max(margin, self.height() - caption_height - margin),
            max(1, self.width() - (margin * 2)),
            caption_height,
        )
        self.crt_overlay.raise_()
        self.subtitle_label.raise_()

    def set_playing(self, playing):
        self.crt_overlay.set_playing(playing)


class AudioWaveformWidget(QWidget):
    """Animated subtitle-monitor visualizer driven by the file's real waveform."""

    peaks_ready = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fileWaveform")
        self.setMinimumHeight(210)
        self.peaks = [0.08] * 72
        self.position = 0
        self.duration = 0
        self.playing = False
        self.phase = 0.0
        self.theme = "light"
        self.cues = []
        self._generation = 0
        self.peaks_ready.connect(self._apply_peaks)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._animate)

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def set_media(self, path):
        self._generation += 1
        generation = self._generation
        self.peaks = [0.08] * 72
        self.position = 0
        self.duration = 0
        self.cues = []
        self.update()

        def extract():
            try:
                peaks = filetranscribe.extract_waveform_peaks(path, 72)
            except (OSError, ValueError):
                peaks = []
            self.peaks_ready.emit((generation, peaks))

        threading.Thread(
            target=extract, name="deyaz-waveform", daemon=True
        ).start()

    def _apply_peaks(self, payload):
        generation, peaks = payload
        if generation == self._generation and peaks:
            self.peaks = list(peaks)
            self.update()

    def set_position(self, position, duration=None):
        self.position = max(0, int(position or 0))
        if duration is not None:
            self.duration = max(0, int(duration or 0))
        self.update()

    def set_cues(self, cues):
        self.cues = [tuple(cue[:3]) for cue in (cues or []) if len(cue) >= 3]
        self.update()

    def set_playing(self, playing):
        self.playing = bool(playing)
        if self.playing:
            self.timer.start()
        else:
            self.timer.stop()
        self.update()

    def _animate(self):
        self.phase = (self.phase + 0.18) % (math.pi * 2)
        self.update()

    def _wave_path(self, bounds):
        count = max(1, len(self.peaks))
        middle = bounds.center().y()
        points_top = []
        points_bottom = []
        for index, peak in enumerate(self.peaks):
            x = bounds.left() + index * bounds.width() / max(1, count - 1)
            motion = (
                math.sin(self.phase + index * 0.31) * 0.055
                if self.playing else 0.0
            )
            amplitude = max(3.0, min(1.0, float(peak) + motion) * bounds.height() * 0.43)
            points_top.append(QPointF(x, middle - amplitude))
            points_bottom.append(QPointF(x, middle + amplitude))

        path = QPainterPath(points_top[0])
        for index in range(1, len(points_top)):
            previous = points_top[index - 1]
            current = points_top[index]
            midpoint = QPointF(
                (previous.x() + current.x()) / 2,
                (previous.y() + current.y()) / 2,
            )
            path.quadTo(previous, midpoint)
        path.lineTo(points_top[-1])
        for index in range(len(points_bottom) - 1, 0, -1):
            previous = points_bottom[index]
            current = points_bottom[index - 1]
            midpoint = QPointF(
                (previous.x() + current.x()) / 2,
                (previous.y() + current.y()) / 2,
            )
            path.quadTo(previous, midpoint)
        path.lineTo(points_bottom[0])
        path.closeSubpath()
        return path

    @staticmethod
    def _draw_timecode(painter, x, y, value, colour):
        """Draw a compact seven-segment clock without relying on bundled fonts."""
        digit_segments = {
            "0": "ab cdef".replace(" ", ""), "1": "bc", "2": "abdeg",
            "3": "abcdg", "4": "bcfg", "5": "acdfg", "6": "acdefg",
            "7": "abc", "8": "abcdefg", "9": "abcdfg",
        }
        lines = {
            "a": ((1, 0), (5, 0)), "b": ((6, 1), (6, 5)),
            "c": ((6, 7), (6, 11)), "d": ((1, 12), (5, 12)),
            "e": ((0, 7), (0, 11)), "f": ((0, 1), (0, 5)),
            "g": ((1, 6), (5, 6)),
        }
        painter.setPen(QPen(
            colour, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap
        ))
        cursor = float(x)
        for character in value:
            if character.isdigit():
                for segment in digit_segments[character]:
                    start, end = lines[segment]
                    painter.drawLine(
                        QPointF(cursor + start[0], y + start[1]),
                        QPointF(cursor + end[0], y + end[1]),
                    )
                cursor += 9
            elif character == ":":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(colour)
                painter.drawEllipse(QPointF(cursor + 1, y + 4), 1, 1)
                painter.drawEllipse(QPointF(cursor + 1, y + 9), 1, 1)
                painter.setPen(QPen(
                    colour, 1.5, Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                ))
                cursor += 5
            else:  # SRT millisecond comma
                painter.drawPoint(QPointF(cursor + 1, y + 11))
                painter.drawLine(QPointF(cursor + 1, y + 11), QPointF(cursor, y + 14))
                cursor += 5

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        monitor = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        if monitor.width() <= 0 or monitor.height() <= 0:
            return

        dark = self.theme == "dark"
        screen = QColor("#101817" if dark else "#17201E")
        ink = QColor("#EAF8EB")
        quiet = QColor("#75A98A")
        accent = QColor("#FF8FA1")
        electric = QColor("#8BE8C2")
        progress = min(1.0, self.position / self.duration) if self.duration else 0.0

        # Recessed CRT/subtitle monitor instead of the previous flat green box.
        painter.setPen(QPen(QColor("#292C2A"), 3))
        painter.setBrush(screen)
        painter.drawRoundedRect(monitor, 20, 20)
        inner = monitor.adjusted(12, 12, -12, -12)

        # Soft phosphor wash and moving scanline.
        wash = QRadialGradient(inner.center(), inner.width() * 0.58)
        wash.setColorAt(0, QColor(78, 225, 175, 28))
        wash.setColorAt(1, QColor(78, 225, 175, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(wash)
        painter.drawRoundedRect(inner, 14, 14)
        painter.setPen(QPen(QColor(155, 240, 205, 13), 1))
        for y in range(int(inner.top()) + 4, int(inner.bottom()), 6):
            painter.drawLine(int(inner.left()), y, int(inner.right()), y)
        if self.playing:
            scan_y = inner.top() + ((self.phase / (math.pi * 2)) * inner.height())
            painter.setPen(QPen(QColor(139, 232, 194, 55), 2))
            painter.drawLine(int(inner.left()), int(scan_y), int(inner.right()), int(scan_y))

        # SRT-era timecode and subtitle-track badge.
        self._draw_timecode(
            painter, inner.left() + 6, inner.top() + 6,
            format_srt_clock(self.position), ink,
        )
        badge = QRectF(inner.right() - 42, inner.top() + 2, 38, 21)
        painter.setBrush(QColor(139, 232, 194, 24))
        painter.setPen(QPen(QColor(139, 232, 194, 120), 1))
        painter.drawRoundedRect(badge, 5, 5)
        painter.setPen(QPen(
            electric, 1.5, Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
        ))
        for index, width in enumerate((20, 15, 18)):
            y = badge.top() + 6 + index * 4
            painter.drawLine(
                QPointF(badge.center().x() - width / 2, y),
                QPointF(badge.center().x() + width / 2, y),
            )

        wave_bounds = inner.adjusted(7, 31, -7, -45)
        path = self._wave_path(wave_bounds)
        painter.setPen(QPen(QColor(117, 169, 138, 130), 1.2))
        painter.setBrush(QColor(117, 169, 138, 48))
        painter.drawPath(path)

        # Played section is a luminous pink/green signal, clipped at playhead.
        if self.duration:
            painter.save()
            painter.setClipRect(QRectF(
                wave_bounds.left(), wave_bounds.top(),
                wave_bounds.width() * progress, wave_bounds.height(),
            ))
            signal = QLinearGradient(
                wave_bounds.left(), 0, wave_bounds.right(), 0
            )
            signal.setColorAt(0, electric)
            signal.setColorAt(0.72, accent)
            signal.setColorAt(1, QColor("#FFD2A6"))
            painter.setPen(QPen(accent, 1.7))
            painter.setBrush(signal)
            painter.drawPath(path)
            painter.restore()

            cursor_x = wave_bounds.left() + wave_bounds.width() * progress
            halo = QColor(accent)
            halo.setAlpha(42 + (22 if self.playing else 0))
            painter.setPen(QPen(halo, 7))
            painter.drawLine(
                int(cursor_x), int(wave_bounds.top()),
                int(cursor_x), int(wave_bounds.bottom()),
            )
            painter.setPen(QPen(QColor("#FFE7EC"), 1.5))
            painter.drawLine(
                int(cursor_x), int(wave_bounds.top()),
                int(cursor_x), int(wave_bounds.bottom()),
            )

        # Actual subtitle cues become a small SRT timeline after transcription.
        rail = QRectF(inner.left() + 5, inner.bottom() - 24, inner.width() - 10, 13)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(234, 248, 235, 22))
        painter.drawRoundedRect(rail, 4, 4)
        if self.duration and self.cues:
            for start, end, _text in self.cues:
                try:
                    left = max(0.0, float(start) * 1000 / self.duration)
                    right = min(1.0, float(end) * 1000 / self.duration)
                except (TypeError, ValueError):
                    continue
                if right <= left:
                    continue
                cue_rect = QRectF(
                    rail.left() + rail.width() * left,
                    rail.top() + 2,
                    max(2.0, rail.width() * (right - left) - 1),
                    rail.height() - 4,
                )
                painter.setBrush(accent if left <= progress < right else quiet)
                painter.drawRoundedRect(cue_rect, 2, 2)
        else:
            painter.setPen(QPen(QColor(139, 232, 194, 70), 1))
            for tick in range(1, 10):
                x = rail.left() + rail.width() * tick / 10
                painter.drawLine(int(x), int(rail.top() + 3), int(x), int(rail.bottom() - 3))
        painter.end()


class OpenRouterOAuth(QObject):
    """Run browser OAuth without blocking the Qt event loop."""

    connected = pyqtSignal(str)
    failed = pyqtSignal(str)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.connected.emit(openrouter_oauth.authorize())
        except Exception as exc:
            self.failed.emit(str(exc))


class OpenRouterAccountStatus(QObject):
    """Fetch account/key budget without blocking the main UI."""

    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.busy = False

    def refresh(self, key):
        if self.busy or not key:
            return
        self.busy = True
        threading.Thread(target=self._run, args=(key,), daemon=True).start()

    def _run(self, key):
        try:
            self.finished.emit(api.openrouter_account_info(key))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.busy = False


OPENROUTER_TRANSCRIPTION_CHOICES = (
    ("TÖVSİYƏ", "Voxtral Mini Transcribe", "mistralai/voxtral-mini-transcribe",
     "OpenAI açarı olmadan OpenRouter krediti ilə işləyən balanslı seçim."),
    ("BALANSLI", "MAI-Transcribe 1.5", "microsoft/mai-transcribe-1.5",
     "Avtomatik dil tanıma və durğu işarələri ilə OpenRouter seçimi."),
    ("ƏN UCUZ", "Qwen3 ASR 0.6B", "qwen/qwen3-asr-0.6b",
     "OpenRouter üzərindən işləyən qənaətli çoxdilli model."),
)

OPENROUTER_CLEANUP_CHOICES = (
    ("PULSUZ", "OpenRouter Free", "openrouter/free",
     "Kredit olmadan işləyir; gündəlik pulsuz limit tətbiq olunur."),
    ("ƏN UCUZ", "Gemini Flash Lite", "google/gemini-3.5-flash-lite",
     "Tərcümə və yüksək həcmli mətn üçün ən qənaətli seçim."),
    ("BALANSLI", "Gemini 3.6 Flash", "google/gemini-3.6-flash",
     "Sürət, uzun kontekst və nəticə keyfiyyəti balansı."),
    ("SƏMƏRƏLİ", "GPT-5.6 Luna", "openai/gpt-5.6-luna",
     "Güclü instruction following və yüksək həcm üçün qənaətli GPT seçimi."),
    ("ƏN KEYFİYYƏTLİ", "GPT-5.6 Sol", "openai/gpt-5.6-sol",
     "Mürəkkəb work mode, xülasə və prompt refinement üçün."),
)

OPENAI_TRANSCRIPTION_CHOICES = (
    ("ƏN UCUZ", "GPT-4o Mini Transcribe", "gpt-4o-mini-transcribe",
     "OpenAI ilə sürətli və qənaətli gündəlik diktə."),
    ("BALANSLI", "GPT-4o Transcribe", "gpt-4o-transcribe",
     "Əvvəlki nəsil yüksək dəqiqlikli transkripsiya modeli."),
    ("ƏN KEYFİYYƏTLİ", "GPT Transcribe", "gpt-transcribe",
     "Yeni yüksək dəqiqlikli fayl və Realtime transkripsiya modeli."),
)

FILE_TRANSCRIPTION_CHOICES = (
    ("ƏN KEYFİYYƏTLİ", "OpenAI · GPT Transcribe", "openai", "gpt-transcribe",
     "Birbaşa OpenAI açarı ilə yeni yüksək dəqiqlikli model."),
    ("BALANSLI", "OpenAI · GPT-4o Transcribe", "openai", "gpt-4o-transcribe",
     "Birbaşa OpenAI açarı ilə əvvəlki nəsil yüksək dəqiqlik."),
    ("ƏN UCUZ", "OpenAI · GPT-4o Mini Transcribe", "openai",
     "gpt-4o-mini-transcribe", "Birbaşa OpenAI ilə qənaətli gündəlik seçim."),
    ("TÖVSİYƏ", "OpenRouter · Voxtral Mini", "openrouter",
     "mistralai/voxtral-mini-transcribe", "OpenAI açarı olmadan işləyən balanslı seçim."),
    ("BALANSLI", "OpenRouter · MAI-Transcribe 1.5", "openrouter",
     "microsoft/mai-transcribe-1.5", "Avtomatik dil tanıma və durğu işarələri."),
    ("ƏN UCUZ", "OpenRouter · Qwen3 ASR 0.6B", "openrouter",
     "qwen/qwen3-asr-0.6b", "OpenAI açarı olmadan işləyən qənaətli seçim."),
)

OPENAI_CLEANUP_CHOICES = (
    ("ƏN UCUZ", "GPT-5.6 Luna", "gpt-5.6-luna",
     "Yüksək həcmli işlər üçün yeni, qənaətli mətn modeli."),
    ("BALANSLI", "GPT-5.6 Terra", "gpt-5.6-terra",
     "Keyfiyyət və xərc arasında balanslı mətn redaktəsi."),
    ("ƏN KEYFİYYƏTLİ", "GPT-5.6 Sol", "gpt-5.6-sol",
     "Mürəkkəb iş modları və yüksək keyfiyyətli nəticə üçün."),
)

# Meeting Notes has an independent pipeline: an economical near-live chunk
# mode and a native low-latency Realtime WebSocket mode.
MEETING_LIVE_TRANSCRIPTION_CHOICES = (
    ("ƏN KEYFİYYƏTLİ", "GPT Transcribe · Smart Live", "openai",
     "gpt-transcribe",
     "Yeni yüksək dəqiqlikli model danışığı qısa hissələrlə transkripsiya edir."),
    ("ƏN SÜRƏTLİ", "GPT Live Transcribe · True Live", "openai",
     "gpt-live-transcribe",
     "Realtime WebSocket ilə danışıq bitdikcə dərhal canlı mətn verir."),
)

MEETING_TEXT_CHOICES = (
    ("ƏN UCUZ", "Gemini 3.5 Flash-Lite", "openrouter",
     "google/gemini-3.5-flash-lite",
     "Tərcümə və yüksək həcmli sadə mətn emalı üçün."),
    ("BALANSLI", "GPT-5.6 Terra", "openai",
     "gpt-5.6-terra",
     "Güclü instruction following ilə xərc və nəticə keyfiyyəti balansı."),
    ("ƏN KEYFİYYƏTLİ", "GPT-5.6 Sol", "openai",
     "gpt-5.6-sol",
     "Mürəkkəb xülasə, qərar və tapşırıq çıxarılması üçün."),
)

# Backwards-compatible aliases used by tests and older integrations.
TRANSCRIPTION_CHOICES = OPENROUTER_TRANSCRIPTION_CHOICES
CLEANUP_CHOICES = OPENROUTER_CLEANUP_CHOICES


def model_tag_tone(label):
    return {
        "PULSUZ": "free",
        "ƏN UCUZ": "cheap",
        "BALANSLI": "balanced",
        "ƏN PERFORMANSLI": "speed",
        "ƏN SÜRƏTLİ": "speed",
        "YENİ · BALANSLI": "balanced",
        "TÖVSİYƏ": "speed",
        "ƏN KEYFİYYƏTLİ": "quality",
    }.get(label, "balanced")


class ModelChoiceCard(QWidget):
    def __init__(self, badge, name, description):
        super().__init__()
        self.setObjectName("modelChoiceCard")
        # QListWidget owns selection.  The visual card must not swallow clicks
        # that should reach the list item underneath it.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(4)
        model_name = QLabel(name, objectName="modelName")
        model_description = QLabel(i18n.t(description), objectName="modelDescription")
        model_description.setWordWrap(True)
        tag_row = QHBoxLayout()
        tag = QLabel(i18n.t(badge), objectName="modelTag")
        tag.setProperty("tone", model_tag_tone(badge))
        tag_row.addWidget(tag)
        tag_row.addStretch()
        layout.addWidget(model_name)
        layout.addWidget(model_description)
        layout.addLayout(tag_row)


class ModelOnboardingDialog(QDialog):
    """Focused first-run model choice with plain-language trade-offs."""

    def __init__(self, parent, available, transcribe_model, cleanup_model,
                 transcription_choices=OPENROUTER_TRANSCRIPTION_CHOICES,
                 cleanup_choices=OPENROUTER_CLEANUP_CHOICES,
                 provider_name="OpenRouter"):
        super().__init__(parent)
        self.setObjectName("modelDialog")
        self.setWindowTitle("DeYaz model seçimi")
        self.setModal(True)
        self.resize(980, 720)
        self.setMinimumSize(620, 600)
        root = QVBoxLayout(self)
        self.model_root_layout = root
        root.setContentsMargins(28, 26, 28, 24)
        root.setSpacing(18)
        eyebrow = QLabel("İLK QURAŞDIRMA", objectName="dialogEyebrow")
        title = QLabel("DeYaz necə işləsin?", objectName="dialogTitle")
        intro = QLabel(
            i18n.t(
                "{provider} üçün iki modeli ayrıca seçin: biri səsi mətnə "
                "çevirir, digəri nəticəni təmizləyir.",
                provider=provider_name,
            ),
            objectName="dialogCopy",
        )
        intro.setWordWrap(True)
        root.addWidget(eyebrow)
        root.addWidget(title)
        root.addWidget(intro)
        self.model_switcher = QStackedWidget()
        self.model_wide_page = QWidget()
        columns = QHBoxLayout(self.model_wide_page)
        self.model_columns_layout = columns
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(14)
        self.transcription_list = self._choice_column(
            columns, "1  TRANSKRİPSİYA", "Səsi mətnə çevirən model",
            transcription_choices, available, transcribe_model,
        )
        self.cleanup_list = self._choice_column(
            columns, "2  MƏTNİ TƏMİZLƏMƏ", "Durğu, təkrar və iş modları",
            cleanup_choices, available, cleanup_model,
        )
        self.transcription_frame = self.transcription_list.parentWidget()
        self.cleanup_frame = self.cleanup_list.parentWidget()
        self.model_tabs = QTabWidget(objectName="modelResponsiveTabs")
        self.model_switcher.addWidget(self.model_wide_page)
        self.model_switcher.addWidget(self.model_tabs)
        root.addWidget(self.model_switcher, 1)
        self._reflow_model_columns(self.width())
        note = QLabel(
            ("Pulsuz seçim yalnız mətn təmizləməyə aiddir. Audio "
             "transkripsiyası üçün OpenRouter krediti lazımdır.")
            if provider_name == "OpenRouter" else
            "Bu seçimlər birbaşa OpenAI açarı və OpenAI balansı ilə işləyir.",
            objectName="dialogNote",
        )
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Seçimi yadda saxla")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Ləğv et")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primaryAction")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        localize_widget_tree(self)

    def _choice_column(self, parent_layout, heading, subheading, choices,
                       available, current):
        frame = QFrame(objectName="modelColumn")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(7)
        label = QLabel(heading, objectName="modelHeading")
        sub = QLabel(subheading, objectName="modelSubheading")
        listing = QListWidget(objectName="modelChoices")
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for badge, name, model_id, description in choices:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, model_id)
            item.setToolTip(model_id)
            item.setSizeHint(QSize(260, 90))
            card = ModelChoiceCard(badge, name, description)
            if available and model_id not in available:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                card.setEnabled(False)
            listing.addItem(item)
            listing.setItemWidget(item, card)
            if model_id == current:
                listing.setCurrentItem(item)
        if listing.currentRow() < 0:
            for index in range(listing.count()):
                if listing.item(index).flags() & Qt.ItemFlag.ItemIsEnabled:
                    listing.setCurrentRow(index)
                    break
        layout.addWidget(label)
        layout.addWidget(sub)
        layout.addWidget(listing, 1)
        parent_layout.addWidget(frame, 1)
        return listing

    def _reflow_model_columns(self, width):
        compact = width < 760
        if getattr(self, "_compact_model_columns", None) == compact:
            return
        self._compact_model_columns = compact
        frames = (
            (self.transcription_frame, "1  TRANSKRİPSİYA"),
            (self.cleanup_frame, "2  MƏTNİ TƏMİZLƏMƏ"),
        )
        if compact:
            for frame, title in frames:
                self.model_columns_layout.removeWidget(frame)
                if self.model_tabs.indexOf(frame) < 0:
                    self.model_tabs.addTab(frame, title)
            self.model_switcher.setCurrentWidget(self.model_tabs)
        else:
            for frame, _title in frames:
                index = self.model_tabs.indexOf(frame)
                if index >= 0:
                    self.model_tabs.removeTab(index)
                self.model_columns_layout.addWidget(frame, 1)
            self.model_switcher.setCurrentWidget(self.model_wide_page)
        localize_widget_tree(self.model_switcher)

    def resizeEvent(self, event):
        compact = event.size().width() < 720
        self._reflow_model_columns(event.size().width())
        self.model_root_layout.setContentsMargins(
            16 if compact else 28, 18 if compact else 26,
            16 if compact else 28, 18 if compact else 24,
        )
        self.model_root_layout.setSpacing(12 if compact else 18)
        self.model_columns_layout.setSpacing(9 if compact else 14)
        super().resizeEvent(event)

    def selected_models(self):
        transcription = self.transcription_list.currentItem()
        cleanup = self.cleanup_list.currentItem()
        return (
            transcription.data(Qt.ItemDataRole.UserRole) if transcription else "",
            cleanup.data(Qt.ItemDataRole.UserRole) if cleanup else "",
        )


class ContextTextDialog(QDialog):
    """Small second step used by the context add chooser."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("contextTextDialog")
        self.setWindowTitle("Mətni yapışdır")
        self.setModal(True)
        self.resize(620, 390)
        self.setMinimumSize(460, 340)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(QLabel("Mətni yapışdır", objectName="contextDialogTitle"))
        self.editor = QPlainTextEdit(objectName="contextTextEditor")
        self.editor.setMinimumHeight(230)
        root.addWidget(self.editor, 1)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("Ləğv et")
        actions.button(QDialogButtonBox.StandardButton.Save).setText("Əlavə et")
        actions.button(QDialogButtonBox.StandardButton.Save).setObjectName(
            "contextSaveAction"
        )
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        root.addWidget(actions)
        localize_widget_tree(self)


class ContextAddDialog(QDialog):
    """Reference-matched chooser; never jumps straight to a folder picker."""

    def __init__(self, owner, parent=None):
        super().__init__(parent or owner)
        self.owner = owner
        self.setObjectName("contextAddDialog")
        self.setWindowTitle("Kontekst əlavə et")
        self.setModal(True)
        self.resize(760, 430)
        self.setMinimumSize(500, 390)
        root = QVBoxLayout(self)
        root.setContentsMargins(42, 30, 42, 36)
        root.setSpacing(26)
        title = QLabel("Kontekst əlavə et", objectName="contextDialogTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)
        self.choices_layout = QGridLayout()
        self.choices_layout.setHorizontalSpacing(20)
        self.choices_layout.setVerticalSpacing(18)
        self.project_action = QPushButton(
            "Proyekt\nəlavə et", objectName="contextProjectAction"
        )
        self.project_action.setIcon(line_icon("folder", "#202321", 50))
        self.project_action.setIconSize(QSize(72, 72))
        self.project_action.clicked.connect(self.add_project)
        self.paste_action = QPushButton(
            "Mətni yapışdır", objectName="contextPasteAction"
        )
        self.paste_action.setIcon(line_icon("clipboard", "#202321", 42))
        self.paste_action.setIconSize(QSize(62, 62))
        self.paste_action.clicked.connect(self.add_text)
        self.upload_action = QPushButton(
            "Fayl yüklə", objectName="contextFileAction"
        )
        self.upload_action.setIcon(line_icon("upload", "#202321", 42))
        self.upload_action.setIconSize(QSize(62, 62))
        self.upload_action.clicked.connect(self.add_file)
        root.addLayout(self.choices_layout, 1)
        self._reflow_choices(self.width())
        localize_widget_tree(self)

    def _reflow_choices(self, width):
        compact = width < 650
        if getattr(self, "_compact_choices", None) == compact:
            return
        self._compact_choices = compact
        for button in (self.project_action, self.paste_action, self.upload_action):
            self.choices_layout.removeWidget(button)
        if compact:
            self.project_action.setMinimumSize(0, 110)
            self.project_action.setMaximumHeight(120)
            self.paste_action.setMinimumHeight(88)
            self.upload_action.setMinimumHeight(88)
            self.choices_layout.addWidget(self.project_action, 0, 0)
            self.choices_layout.addWidget(self.paste_action, 1, 0)
            self.choices_layout.addWidget(self.upload_action, 2, 0)
            self.choices_layout.setColumnStretch(0, 1)
            self.choices_layout.setColumnStretch(1, 0)
            self.setMinimumHeight(520)
        else:
            self.project_action.setMinimumSize(210, 220)
            self.project_action.setMaximumHeight(16777215)
            self.paste_action.setMinimumHeight(104)
            self.upload_action.setMinimumHeight(92)
            self.choices_layout.addWidget(self.project_action, 0, 0, 2, 1)
            self.choices_layout.addWidget(self.paste_action, 0, 1)
            self.choices_layout.addWidget(self.upload_action, 1, 1)
            self.choices_layout.setColumnStretch(0, 2)
            self.choices_layout.setColumnStretch(1, 5)
            self.setMinimumHeight(390)
        self.choices_layout.invalidate()

    def resizeEvent(self, event):
        self._reflow_choices(event.size().width())
        super().resizeEvent(event)

    def add_project(self):
        if self.owner.add_project_context():
            self.accept()

    def add_text(self):
        dialog = ContextTextDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            text = dialog.editor.toPlainText().strip()
            if text:
                self.owner.add_manual_context("Mətn", text, "text")
                self.accept()

    def add_file(self):
        if self.owner.add_context_file():
            self.accept()


class ContextManagerDialog(QDialog):
    """Manage enabled project, pasted and file context in one surface."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setObjectName("contextManagerDialog")
        self.setWindowTitle("Kontekst")
        self.setModal(True)
        self.resize(1000, 660)
        self.setMinimumSize(520, 560)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(28, 24, 28, 28)
        self.root.setSpacing(20)
        header = QFrame(objectName="contextManagerHeader")
        head = QHBoxLayout(header)
        head.setContentsMargins(20, 14, 14, 14)
        head.setSpacing(14)
        title = QLabel("Kontekst", objectName="contextManagerTitle")
        self.add_button = QPushButton(objectName="contextPlusAction")
        self.add_button.setIcon(line_icon("plus", "#202321", 28))
        self.add_button.setIconSize(QSize(26, 26))
        self.add_button.setFixedSize(54, 50)
        self.add_button.clicked.connect(self.open_add)
        head.addWidget(title)
        head.addStretch()
        head.addWidget(self.add_button)
        self.root.addWidget(header)
        self.body = QWidget()
        self.body_layout = QGridLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setHorizontalSpacing(20)
        self.root.addWidget(self.body, 1)
        self.refresh()
        localize_widget_tree(self)

    def open_add(self):
        ContextAddDialog(self.owner, self).exec()
        self.refresh()

    @staticmethod
    def _choice_copy(label, preview, compact_path=False):
        preview = " ".join((preview or "").split())
        if not preview:
            return label
        if compact_path:
            normalized = preview.replace("\\", "/").rstrip("/")
            parts = [part for part in normalized.split("/") if part]
            preview = "/".join(parts[-2:]) if parts else normalized
            if len(preview) > 38:
                preview = "…" + preview[-37:]
        elif len(preview) > 72:
            preview = preview[:69].rstrip() + "…"
        return f"{label}\n{preview}"

    def _toggle_project(self, index, checked, source):
        if getattr(self, "_syncing_project_choices", False):
            return
        self._syncing_project_choices = True
        try:
            if checked:
                for button in self.project_buttons:
                    if button is source:
                        continue
                    button.blockSignals(True)
                    button.setChecked(False)
                    button.blockSignals(False)
            self.owner.set_context_item_enabled(index, checked)
        finally:
            self._syncing_project_choices = False

    def _sync_choice_icon(self, button, icon_name, checked):
        dark = getattr(self.owner, "theme", "light") == "dark"
        color = "#202321" if checked or not dark else "#F8F3E8"
        button.setIcon(line_icon(icon_name, color, 20))

    def _panel(self, title, icon_name, object_name):
        panel = QFrame(objectName=object_name)
        panel.setMinimumWidth(0)
        panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        panel_l = QVBoxLayout(panel)
        panel_l.setContentsMargins(18, 16, 18, 18)
        panel_l.setSpacing(12)
        panel_head = QHBoxLayout()
        panel_head.setSpacing(10)
        icon = QLabel(objectName="contextColumnIcon")
        icon.setPixmap(line_icon(icon_name, "#202321", 20).pixmap(24, 24))
        icon.setFixedSize(32, 32)
        panel_head.addWidget(icon)
        panel_head.addWidget(QLabel(title, objectName="contextColumnTitle"))
        panel_head.addStretch()
        panel_l.addLayout(panel_head)

        scroll = QScrollArea(objectName="contextListScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget(objectName="contextListContent")
        content_l = QVBoxLayout(content)
        content_l.setContentsMargins(1, 1, 1, 1)
        content_l.setSpacing(10)
        scroll.setWidget(content)
        panel_l.addWidget(scroll, 1)
        return panel, content_l

    def refresh(self):
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        projects, project_l = self._panel(
            "Proyekt", "folder", "contextProjectPanel"
        )
        references, reference_l = self._panel(
            "Mətn və fayllar", "clipboard", "contextReferencePanel"
        )
        items = (
            self.owner._context_items()
            if hasattr(self.owner, "_context_items")
            else list(self.owner.conf.get("context_items", []) or [])
        )
        self.project_buttons = []
        self.reference_buttons = []
        has_project = False
        has_reference = False
        for index, item in enumerate(items):
            preview = (item.get("text") or item.get("path") or "").strip()
            label = (item.get("label") or "Kontekst").strip()
            if item.get("kind") == "project":
                has_project = True
                choice = QPushButton(
                    self._choice_copy(label, preview, compact_path=True),
                    objectName="contextProjectChoice",
                )
                choice.setCheckable(True)
                choice.setMinimumWidth(0)
                choice.setSizePolicy(
                    QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
                )
                choice.setChecked(bool(item.get("enabled", False)))
                choice.setIconSize(QSize(24, 24))
                self._sync_choice_icon(choice, "folder", choice.isChecked())
                choice.setCursor(Qt.CursorShape.PointingHandCursor)
                choice.setToolTip(preview)
                choice.toggled.connect(
                    lambda checked, button=choice:
                    self._sync_choice_icon(button, "folder", checked)
                )
                choice.toggled.connect(
                    lambda checked, row=index, button=choice:
                    self._toggle_project(row, checked, button)
                )
                self.project_buttons.append(choice)
                project_l.addWidget(choice)
                continue
            has_reference = True
            choice = QPushButton(
                self._choice_copy(label, preview), objectName="contextEntry"
            )
            choice.setCheckable(True)
            choice.setMinimumWidth(0)
            choice.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
            )
            choice.setChecked(bool(item.get("enabled", True)))
            icon_name = "file" if item.get("kind") == "file" else "clipboard"
            choice.setIconSize(QSize(24, 24))
            self._sync_choice_icon(choice, icon_name, choice.isChecked())
            choice.setCursor(Qt.CursorShape.PointingHandCursor)
            choice.setToolTip(preview)
            choice.toggled.connect(
                lambda checked, button=choice, name=icon_name:
                self._sync_choice_icon(button, name, checked)
            )
            choice.toggled.connect(
                lambda checked, row=index: self.owner.set_context_item_enabled(
                    row, checked
                )
            )
            self.reference_buttons.append(choice)
            reference_l.addWidget(choice)
        if not has_project:
            project_l.addWidget(QLabel(
                "Proyekt əlavə edilməyib", objectName="contextEmpty"
            ))
        if not has_reference:
            reference_l.addWidget(QLabel(
                "Mətn və ya fayl əlavə edilməyib", objectName="contextEmpty"
            ))
        project_l.addStretch()
        reference_l.addStretch()
        self.project_panel = projects
        self.reference_panel = references
        self._reflow_panels(self.width())
        localize_widget_tree(self)

    def _reflow_panels(self, width):
        compact = width < 720
        if getattr(self, "_compact_panels", None) == compact:
            return
        self._compact_panels = compact
        for panel in (self.project_panel, self.reference_panel):
            self.body_layout.removeWidget(panel)
        for index in range(2):
            self.body_layout.setColumnStretch(index, 0)
            self.body_layout.setRowStretch(index, 0)
        if compact:
            self.project_panel.setMinimumHeight(190)
            self.reference_panel.setMinimumHeight(250)
            self.body_layout.addWidget(self.project_panel, 0, 0)
            self.body_layout.addWidget(self.reference_panel, 1, 0)
            self.body_layout.setColumnStretch(0, 1)
            self.body_layout.setRowStretch(0, 2)
            self.body_layout.setRowStretch(1, 3)
        else:
            self.project_panel.setMinimumHeight(0)
            self.reference_panel.setMinimumHeight(0)
            self.body_layout.addWidget(self.project_panel, 0, 0)
            self.body_layout.addWidget(self.reference_panel, 0, 1)
            self.body_layout.setColumnStretch(0, 3)
            self.body_layout.setColumnStretch(1, 5)
        self.body_layout.invalidate()

    def resizeEvent(self, event):
        if hasattr(self, "project_panel"):
            self._reflow_panels(event.size().width())
        super().resizeEvent(event)


class CreditDialog(QDialog):
    def __init__(self, parent, provider, message):
        super().__init__(parent)
        self.provider = provider
        self.setObjectName("creditDialog")
        self.setWindowTitle("Kredit tələb olunur")
        self.setModal(True)
        self.setFixedWidth(480)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 26, 28, 24)
        root.setSpacing(13)
        icon = QLabel("₵", objectName="creditIcon", alignment=Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(46, 46)
        title = QLabel(
            i18n.t("{provider} krediti kifayət etmir", provider=provider),
            objectName="dialogTitle",
        )
        copy = QLabel(
            "Transkripsiyanı davam etdirmək üçün hesabınıza kredit əlavə edin. "
            "DeYaz açarı saxlamır və ödənişi özü qəbul etmir.",
            objectName="dialogCopy",
        )
        copy.setWordWrap(True)
        detail = QLabel(message, objectName="dialogNote")
        detail.setWordWrap(True)
        root.addWidget(icon, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addWidget(title)
        root.addWidget(copy)
        root.addWidget(detail)
        actions = QHBoxLayout()
        later = QPushButton("Sonra")
        models = QPushButton("Model seçimini dəyiş")
        add_credit = QPushButton("Kredit əlavə et", objectName="primaryAction")
        later.clicked.connect(self.reject)
        models.clicked.connect(lambda: self.done(2))
        add_credit.clicked.connect(self.open_credits)
        actions.addWidget(later)
        actions.addStretch()
        actions.addWidget(models)
        actions.addWidget(add_credit)
        root.addLayout(actions)
        localize_widget_tree(self)

    def open_credits(self):
        url = (
            "https://openrouter.ai/settings/credits"
            if self.provider == "OpenRouter"
            else "https://platform.openai.com/settings/organization/billing/overview"
        )
        QDesktopServices.openUrl(QUrl(url))
        self.accept()


class AudioRecorder(QObject):
    level = pyqtSignal(float)
    finished = pyqtSignal(str, float)
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.stream = None
        self.samples = []
        self.started = 0.0
        self.device = None

    @property
    def active(self):
        return self.stream is not None

    def start(self):
        if self.active:
            return
        self.samples = []
        try:
            self.stream = sd.InputStream(
                device=resolve_sounddevice_selector(self.device),
                samplerate=RATE, channels=1,
                dtype="int16", blocksize=1024,
                callback=self._audio,
            )
            self.stream.start()
            self.started = time.monotonic()
        except Exception as exc:
            self.stream = None
            self.failed.emit(i18n.t(
                "Mikrofon başlatıla bilmədi: {error}", error=exc
            ))

    def _audio(self, indata, frames, _time, status):
        if status:
            return
        data = indata.copy()
        self.samples.append(data)
        peak = float(abs(data).max()) / 32768.0
        self.level.emit(min(1.0, peak))

    def stop(self):
        if not self.stream:
            return
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        self.stream = None
        duration = time.monotonic() - self.started
        if duration < 0.3 or not self.samples:
            self.failed.emit(i18n.t(
                "Yazı çox qısadır — ən azı 0.3 saniyə danış."
            ))
            return
        try:
            import numpy as np
            data = np.concatenate(self.samples, axis=0).astype("int16", copy=False)
            fd, path = tempfile.mkstemp(prefix="deyaz-recording-", suffix=".wav")
            with os.fdopen(fd, "wb") as raw, wave.open(raw, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(RATE)
                wav.writeframes(data.tobytes())
            self.finished.emit(path, duration)
        except Exception as exc:
            self.failed.emit(i18n.t(
                "Səs faylı hazırlana bilmədi: {error}", error=exc
            ))
        finally:
            self.samples = []


class Transcription(QObject):
    stage = pyqtSignal(str)
    finished = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, conf):
        super().__init__()
        self.conf = conf
        self.thread = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def run(self, path, duration, context=None):
        self.thread = threading.Thread(
            target=self._work, args=(path, duration, context), daemon=True
        )
        self.thread.start()

    def _work(self, path, duration, context=None):
        started = time.monotonic()
        try:
            self.stage.emit("Mətnə çevrilir…")
            raw = api.transcribe(
                self.conf.transcribe_target(), path,
                language=self.conf["language"],
                prompt=self.conf["transcribe_prompt"],
            )
            text = raw
            work_mode = get_work_mode(self.conf["work_mode"])
            mode_active = self.conf["work_mode"] != "dictation"
            mode_context = (
                context
                if uses_project_context(self.conf["work_mode"], context)
                else None
            )
            if self.conf["cleanup_enabled"] or mode_active:
                self.stage.emit(
                    f"{work_mode['name']} hazırlanır…"
                    if mode_active else "Mətn təmizlənir…"
                )
                system_prompt = (
                    work_mode["prompt"] + (CONTEXT_RULES if mode_context else "")
                    if mode_active else self.conf.cleanup_prompt()
                )
                cleanup_target = self.conf.cleanup_target()
                text = api.cleanup(
                    raw, cleanup_target.api_key, cleanup_target.model,
                    system_prompt, self.conf["cleanup_reasoning"],
                    cleanup_target.base_url,
                    context=(
                        mode_context.text if (mode_active and mode_context) else ""
                    ),
                    provider=cleanup_target.provider,
                    service=cleanup_target.service,
                )
            cfg.append_history({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": round(duration, 1),
                "elapsed": round(time.monotonic() - started, 1),
                "model": self.conf.transcribe_target().model,
                "raw": raw, "text": text, "mode": self.conf["work_mode"],
                "context": context.label if context else "",
                "project_root": context.project_root if context else "",
            })
            self.finished.emit(raw, text)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


class GlobalHotkey(QObject):
    pressed = pyqtSignal()
    registration_changed = pyqtSignal(bool, str)

    def __init__(self, shortcut="Ctrl+Alt+R"):
        super().__init__()
        self.thread = None
        self.thread_id = None
        self.shortcut = shortcut
        self.registered = False
        self.error = ""
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.registration_method = ""
        self.listener = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return self.registered
        self.ready.clear()
        self.stop_event.clear()
        self.registered = False
        self.error = ""
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()
        self.ready.wait(timeout=1.5)
        return self.registered

    @staticmethod
    def parse_shortcut(shortcut):
        parts = [part.strip().upper() for part in shortcut.split("+") if part.strip()]
        modifiers = MOD_NOREPEAT
        if "CTRL" in parts or "CONTROL" in parts:
            modifiers |= MOD_CONTROL
        if "ALT" in parts:
            modifiers |= MOD_ALT
        if "SHIFT" in parts:
            modifiers |= MOD_SHIFT
        key_name = parts[-1] if parts else "R"
        virtual_key = 0x20 if key_name == "SPACE" else ord(key_name[:1])
        return modifiers, virtual_key

    def _listen(self):
        if os.name != "nt":
            self._listen_portable()
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.thread_id = kernel32.GetCurrentThreadId()
        modifiers, virtual_key = self.parse_shortcut(self.shortcut)
        if user32.RegisterHotKey(None, HOTKEY_ID, modifiers, virtual_key):
            self.registered = True
            self.registration_method = "register-hotkey"
        else:
            register_error = ctypes.get_last_error()
            # Another application may already own this shortcut. Polling key
            # state is deliberately used instead of a low-level keyboard hook:
            # it observes the shortcut but can never consume normal typing.
            self.error = f"Windows qeydiyyat xətası {register_error}; təhlükəsiz fallback aktivdir."
            self.registered = True
            self.registration_method = "key-state-poll"
        self.ready.set()
        self.registration_changed.emit(
            True, f"{self.shortcut} · {self.registration_method}"
        )
        if self.registration_method == "key-state-poll":
            self._poll_shortcut(user32, modifiers, virtual_key)
            self.registered = False
            return

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.pressed.emit()
        user32.UnregisterHotKey(None, HOTKEY_ID)
        self.registered = False

    def _listen_portable(self):
        try:
            from pynput import keyboard
            parts = [part.strip().lower() for part in self.shortcut.split("+") if part.strip()]
            aliases = {"ctrl": "<ctrl>", "control": "<ctrl>", "alt": "<alt>",
                       "shift": "<shift>", "cmd": "<cmd>", "command": "<cmd>",
                       "space": "<space>"}
            shortcut = "+".join(aliases.get(part, part) for part in parts)
            self.listener = keyboard.GlobalHotKeys({shortcut: self.pressed.emit})
            self.registered = True
            self.registration_method = "pynput"
            self.ready.set()
            self.registration_changed.emit(True, f"{self.shortcut} · pynput")
            self.listener.run()
        except Exception as exc:
            self.error = str(exc)
            self.registered = False
            self.ready.set()
            self.registration_changed.emit(False, self.error)

    @staticmethod
    def shortcut_is_down(user32, modifiers, virtual_key):
        vk_control, vk_menu, vk_shift = 0x11, 0x12, 0x10
        key_down = bool(user32.GetAsyncKeyState(virtual_key) & 0x8000)
        ctrl_ok = not (modifiers & MOD_CONTROL) or bool(
            user32.GetAsyncKeyState(vk_control) & 0x8000
        )
        alt_ok = not (modifiers & MOD_ALT) or bool(
            user32.GetAsyncKeyState(vk_menu) & 0x8000
        )
        shift_ok = not (modifiers & MOD_SHIFT) or bool(
            user32.GetAsyncKeyState(vk_shift) & 0x8000
        )
        return key_down and ctrl_ok and alt_ok and shift_ok

    def _poll_shortcut(self, user32, modifiers, virtual_key):
        was_down = False
        while not self.stop_event.wait(0.018):
            is_down = self.shortcut_is_down(user32, modifiers, virtual_key)
            if is_down and not was_down:
                self.pressed.emit()
            was_down = is_down

    def stop(self):
        self.stop_event.set()
        if os.name != "nt" and self.listener is not None:
            self.listener.stop()
            self.listener = None
        elif self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.6)
        self.thread_id = None
        self.registered = False
        self.registration_method = ""

    def restart(self, shortcut):
        self.stop()
        self.shortcut = shortcut
        self.start()


def app_icon():
    if ICON_PATH.exists():
        return QIcon(str(ICON_PATH))
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#111719"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#f4d8ca"))
    painter.setBrush(QColor("#ed5f3b"))
    painter.drawRoundedRect(23, 10, 18, 31, 9, 9)
    painter.drawArc(16, 25, 32, 26, 180 * 16, 180 * 16)
    painter.drawLine(32, 52, 32, 45)
    painter.drawLine(22, 52, 42, 52)
    painter.end()
    return QIcon(pixmap)


def color_icon(color):
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(2, 2, 12, 12))
    painter.end()
    return QIcon(pixmap)


def line_icon(name, color="#667085", size=20):
    """Font Awesome icons with one consistent professional visual language."""
    icon_names = {
        "mic": "fa6s.microphone", "stop": "fa6s.stop", "settings": "fa6s.gear",
        "file": "fa6s.file-lines", "history": "fa6s.clock-rotate-left",
        "keyboard": "fa6s.keyboard", "wand": "fa6s.wand-magic-sparkles",
        "sun": "fa6s.sun", "moon": "fa6s.moon", "close": "fa6s.xmark",
        "globe": "fa6s.globe", "home": "fa6s.house",
        "upload": "fa6s.cloud-arrow-up", "plus": "fa6s.plus",
        "folder": "fa6s.folder-open", "clipboard": "fa6s.paste",
        "wave": "fa6s.wave-square", "play": "fa6s.play", "pause": "fa6s.pause",
        "rewind": "fa6s.backward", "forward": "fa6s.forward",
        "meeting": "fa6s.people-group", "copy": "fa6s.copy",
        "back": "fa6s.arrow-left", "trash": "fa6s.trash-can",
    }
    return qta.icon(icon_names.get(name, "fa6s.circle"), color=color)


class AmbientBackground(QWidget):
    """Warm paper-like canvas shared by the playful pastel interface."""

    def __init__(self, theme="dark"):
        super().__init__()
        self.theme = theme

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = self.theme == "dark"
        base = QColor("#181A18" if dark else "#FBF9EF")
        painter.fillRect(self.rect(), base)

        width, height = max(1, self.width()), max(1, self.height())
        wash = QRadialGradient(QPointF(width * 0.12, height * 0.25), width * 0.75)
        wash.setColorAt(0, QColor(149, 213, 255, 18 if dark else 34))
        wash.setColorAt(1, QColor(149, 213, 255, 0))
        painter.fillRect(self.rect(), wash)
        mint = QRadialGradient(QPointF(width * 0.88, height * 0.78), width * 0.65)
        mint.setColorAt(0, QColor(166, 237, 180, 12 if dark else 30))
        mint.setColorAt(1, QColor(166, 237, 180, 0))
        painter.fillRect(self.rect(), mint)

        dot = QColor(255, 255, 255, 9) if dark else QColor(31, 34, 32, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        for x in range(26, width, 34):
            for y in range(26, height, 34):
                painter.drawEllipse(QPointF(x, y), 0.8, 0.8)
        painter.end()


class RecordButton(QPushButton):
    """A clear, animated recording action without the old bullet glyph."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(214, 64)
        self.setIconSize(QSize(22, 22))
        self.shadow = QGraphicsDropShadowEffect(self)
        self.shadow.setOffset(0, 9)
        self.shadow.setBlurRadius(24)
        self.shadow.setColor(QColor(255, 101, 71, 72))
        self.setGraphicsEffect(self.shadow)
        self.shadow_animation = QPropertyAnimation(self.shadow, b"blurRadius", self)
        self.shadow_animation.setDuration(180)
        self.shadow_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(620)
        self.pulse_timer.timeout.connect(self._pulse)
        self.pulse_wide = False
        self.preparing = False
        self.preparing_phase = 0
        self.preparing_timer = QTimer(self)
        self.preparing_timer.setInterval(42)
        self.preparing_timer.timeout.connect(self._advance_preparing)

    def _animate_shadow(self, radius):
        self.shadow_animation.stop()
        self.shadow_animation.setStartValue(self.shadow.blurRadius())
        self.shadow_animation.setEndValue(radius)
        self.shadow_animation.start()

    def enterEvent(self, event):
        self._animate_shadow(36)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.property("recording"):
            self._animate_shadow(24)
        super().leaveEvent(event)

    def set_recording_active(self, active):
        if active:
            self.set_preparing_active(False)
        self.setProperty("recording", bool(active))
        if active:
            self.pulse_timer.start()
            self._animate_shadow(42)
        else:
            self.pulse_timer.stop()
            self._animate_shadow(24)
        self.style().unpolish(self)
        self.style().polish(self)

    def _pulse(self):
        self.pulse_wide = not self.pulse_wide
        self._animate_shadow(46 if self.pulse_wide else 28)

    def set_preparing_active(self, active):
        """Show a light orbit animation while speech is being processed."""
        self.preparing = bool(active)
        self.setProperty("preparing", self.preparing)
        if self.preparing:
            self.preparing_timer.start()
            self._animate_shadow(40)
        else:
            self.preparing_timer.stop()
            if not self.property("recording"):
                self._animate_shadow(24)
        self.update()

    def _advance_preparing(self):
        self.preparing_phase = (self.preparing_phase + 7) % 360
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.preparing:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.rect().center()
        side = max(94.0, min(self.width(), self.height()) * 0.38)
        ring = QRectF(
            center.x() - side / 2, center.y() - side / 2, side, side
        )
        colours = ("#FF7B8C", "#8B7CF6", "#50D2B2")
        for index, colour in enumerate(colours):
            pen_colour = QColor(colour)
            pen_colour.setAlpha(205 - index * 28)
            pen = QPen(pen_colour, 4.0, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            start = self.preparing_phase + index * 120
            painter.drawArc(ring.adjusted(index * 5, index * 5, -index * 5, -index * 5),
                            int(start * 16), int(54 * 16))
        painter.end()


class CurrentPageStack(QStackedWidget):
    """Size the shell from the visible page instead of the tallest hidden page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()


def localize_widget_tree(root):
    """Translate a widget tree while retaining its Azerbaijani source strings."""
    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        if widget.windowTitle():
            source = widget.property("i18n_window_title")
            if source is None:
                source = widget.windowTitle()
                widget.setProperty("i18n_window_title", source)
            widget.setWindowTitle(i18n.t(source))

        if isinstance(widget, (QLabel, QAbstractButton)):
            source = widget.property("i18n_text")
            if source is None:
                source = widget.text()
                widget.setProperty("i18n_text", source)
            widget.setText(i18n.t(source))

        if isinstance(widget, (QLineEdit, QPlainTextEdit)):
            source = widget.property("i18n_placeholder")
            if source is None:
                source = widget.placeholderText()
                widget.setProperty("i18n_placeholder", source)
            if source:
                widget.setPlaceholderText(i18n.t(source))

        tooltip = widget.property("i18n_tooltip")
        if tooltip is None and widget.toolTip():
            tooltip = widget.toolTip()
            widget.setProperty("i18n_tooltip", tooltip)
        if tooltip:
            translated = i18n.t(tooltip)
            if not str(translated).lstrip().startswith("<"):
                translated = (
                    "<div style='max-width:380px; white-space:normal;'>"
                    f"{html.escape(str(translated))}</div>"
                )
            widget.setToolTip(translated)

        if isinstance(widget, QComboBox):
            sources = getattr(widget, "_i18n_item_sources", None)
            if sources is None or len(sources) != widget.count():
                sources = [widget.itemText(index) for index in range(widget.count())]
                widget._i18n_item_sources = sources
            for index, source in enumerate(sources):
                widget.setItemText(index, i18n.t(source))
            tooltip_sources = getattr(widget, "_i18n_item_tooltip_sources", None)
            if tooltip_sources is None or len(tooltip_sources) != widget.count():
                tooltip_sources = [
                    widget.itemData(index, Qt.ItemDataRole.ToolTipRole)
                    for index in range(widget.count())
                ]
                widget._i18n_item_tooltip_sources = tooltip_sources
            for index, source in enumerate(tooltip_sources):
                if source:
                    widget.setItemData(
                        index, i18n.t(source), Qt.ItemDataRole.ToolTipRole
                    )

        if isinstance(widget, QListWidget):
            sources = getattr(widget, "_i18n_item_sources", None)
            if sources is None or len(sources) != widget.count():
                sources = [widget.item(index).text() for index in range(widget.count())]
                widget._i18n_item_sources = sources
            for index, source in enumerate(sources):
                widget.item(index).setText(i18n.t(source))

        if isinstance(widget, QTabWidget):
            sources = getattr(widget, "_i18n_tab_sources", None)
            if sources is None or len(sources) != widget.count():
                sources = [widget.tabText(index) for index in range(widget.count())]
                widget._i18n_tab_sources = sources
            for index, source in enumerate(sources):
                widget.setTabText(index, i18n.t(source))


def localize_action(action):
    source = action.property("i18n_text")
    if source is None:
        source = action.text()
        action.setProperty("i18n_text", source)
    action.setText(i18n.t(source))


class MiniMic(QWidget):
    """Focusless, animated recording HUD; the active app keeps its caret."""
    clicked = pyqtSignal()
    menu_requested = pyqtSignal(object)
    position_changed = pyqtSignal(str, int)

    def __init__(self):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(i18n.t("DeYaz: kliklə səsyazmanı başlat / dayandır"))
        self.state = "idle"
        self.detail = ""
        self.level = 0.0
        self.wave = [0.08] * 14
        self.phase = 0
        self.started_at = 0.0
        self.hovered = False
        self.dock_side = "right"
        self.snap_y = None
        self._press_global = None
        self._press_window = None
        self._dragged = False
        self.mode_id = "dictation"
        self.mode_name = i18n.t(WORK_MODES["dictation"]["name"])
        self.mode_color = WORK_MODES["dictation"]["color"]
        self.context_label = ""
        self.resize(108, 74)

        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(33)
        self.frame_timer.timeout.connect(self._tick)
        self.frame_timer.start()

        self.collapse_timer = QTimer(self)
        self.collapse_timer.setSingleShot(True)
        self.collapse_timer.timeout.connect(lambda: self.set_state("idle"))
        self.resize_animation = QPropertyAnimation(self, b"geometry", self)
        self.resize_animation.setDuration(340)
        self.resize_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_state(self, state, detail=""):
        self.state = state
        self.detail = detail
        if state == "recording":
            self.started_at = time.monotonic()
        self.collapse_timer.stop()
        if state == "success":
            self.collapse_timer.start(2600)
        elif state == "mode":
            self.collapse_timer.start(1800)
        elif state == "error":
            self.collapse_timer.start(5200)
        self._animate_geometry(108 if state == "idle" else 316, 74)
        self.update()

    def set_recording(self, recording):
        self.set_state("recording" if recording else "idle")

    def set_mode(self, mode_id):
        selected = get_work_mode(mode_id)
        self.mode_id = mode_id if mode_id in all_modes() else "dictation"
        self.mode_name = i18n.t(selected["name"])
        self.mode_color = selected["color"]
        self._update_tooltip()
        self.update()

    def set_context(self, label=""):
        self.context_label = (label or "")[:36]
        self._update_tooltip()
        self.update()

    def _update_tooltip(self):
        context = f" • {self.context_label}" if self.context_label else ""
        self.setToolTip(
            f"DeYaz • {self.mode_name}{context}: "
            f"{i18n.t('kliklə səsyazmanı başlat / dayandır')}"
        )

    def set_level(self, value):
        self.level = max(0.0, min(1.0, float(value)))
        eased = max(0.06, self.level ** 0.55)
        self.wave = self.wave[1:] + [eased]
        self.update()

    def _animate_geometry(self, width, height):
        end = self._target_geometry(width, height)
        if not self.isVisible():
            self.setGeometry(end)
            return
        self.resize_animation.stop()
        self.resize_animation.setStartValue(self.geometry())
        self.resize_animation.setEndValue(end)
        self.resize_animation.start()

    def _tick(self):
        self.phase = (self.phase + 1) % 360
        if self.state == "recording":
            self.wave = self.wave[1:] + [max(0.05, self.wave[-1] * 0.91)]
        if self.state != "idle" or self.isVisible():
            self.update()

    def _elapsed(self):
        seconds = max(0, int(time.monotonic() - self.started_at))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        if self.state == "idle":
            self._paint_idle(p)
            return

        # Soft shadow and mineral-black glass body.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 78))
        p.drawRoundedRect(QRectF(5, 8, w - 8, h - 8), 28, 28)
        body = QLinearGradient(0, 4, w, h)
        body.setColorAt(0, QColor("#202b2c"))
        body.setColorAt(0.58, QColor("#131a1b"))
        body.setColorAt(1, QColor("#0d1213"))
        p.setBrush(body)
        p.setPen(QPen(QColor("#3d4e4c"), 1))
        p.drawRoundedRect(QRectF(2.5, 2.5, w - 6, h - 8), 28, 28)

        colour = {
            "idle": QColor(self.mode_color),
            "recording": QColor(self.mode_color),
            "preparing": QColor(self.mode_color),
            "transcribing": QColor(self.mode_color),
            "cleaning": QColor(self.mode_color),
            "mode": QColor(self.mode_color),
            "success": QColor("#58d597"),
            "error": QColor("#ff596f"),
        }.get(self.state, QColor(self.mode_color))

        # Status orb with a breathing halo.
        pulse = 2 + (self.phase % 45) / 45 * 3 if self.state == "recording" else 2
        orb = QRectF(10, 10, 50, 50)
        p.setPen(QPen(QColor(colour.red(), colour.green(), colour.blue(), 55), pulse))
        p.setBrush(QColor("#101718"))
        p.drawEllipse(orb)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawEllipse(QRectF(16, 16, 38, 38))

        # Mic / spinner / result glyph.
        p.setPen(QPen(QColor("#141313"), 2.4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        if self.state in ("idle", "recording"):
            p.setBrush(QColor("#141313"))
            p.drawRoundedRect(QRectF(31, 23, 8, 14), 4, 4)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(27, 29, 16, 13), 180 * 16, 180 * 16)
            p.drawLine(35, 42, 35, 46)
            p.drawLine(31, 46, 39, 46)
        elif self.state in ("preparing", "transcribing", "cleaning"):
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#141313"), 3.2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(25, 25, 20, 20), self.phase * 16, 245 * 16)
        elif self.state in ("success", "mode"):
            p.setPen(QPen(QColor("#10251c"), 3.2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawLine(26, 35, 33, 42)
            p.drawLine(33, 42, 45, 28)
        else:
            p.setPen(QPen(QColor("#281015"), 3.2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(29, 29, 41, 41)
            p.drawLine(41, 29, 29, 41)

        labels = {
            "recording": (
                i18n.t("Dinləyirəm"),
                "  •  ".join(x for x in (
                    self.mode_name, self.context_label, self._elapsed()
                ) if x),
            ),
            "preparing": (
                i18n.t("Səs hazırlanır"),
                "  •  ".join(x for x in (self.mode_name, self.context_label) if x),
            ),
            "transcribing": (
                i18n.t("Mətnə çevrilir"),
                "  •  ".join(x for x in (self.mode_name, self.context_label) if x),
            ),
            "cleaning": (
                i18n.t("Mode tətbiq olunur"),
                "  •  ".join(x for x in (self.mode_name, self.context_label) if x),
            ),
            "mode": (i18n.t("Mode dəyişdi"), self.mode_name),
            "success": (
                i18n.t("Hazırdır"),
                f"{self.mode_name}  •  {i18n.t('inputa əlavə olundu')}",
            ),
            "error": (
                i18n.t("Problem yarandı"),
                self.detail or i18n.t("Yenidən cəhd et"),
            ),
        }
        title, subtitle = labels.get(self.state, ("DeYaz", self.detail))
        p.setPen(QColor("#f5f0eb"))
        p.setFont(QFont("Bahnschrift", 11, QFont.Weight.DemiBold))
        p.drawText(QRectF(73, 13, 150, 25), Qt.AlignmentFlag.AlignVCenter, title)
        p.setPen(QColor("#91a6a3"))
        p.setFont(QFont("Aptos", 8))
        p.drawText(QRectF(73, 36, 195, 20), Qt.AlignmentFlag.AlignVCenter, subtitle)

        if self.state == "recording":
            base_x, middle = 211, 36
            for index, amplitude in enumerate(self.wave[-12:]):
                bar_h = 5 + amplitude * 30
                x = base_x + index * 7
                alpha = 100 + index * 11
                bar_colour = QColor(colour)
                bar_colour.setAlpha(min(230, alpha))
                p.setPen(QPen(bar_colour, 3,
                              Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(x, int(middle - bar_h / 2), x,
                           int(middle + bar_h / 2))
        elif self.state in ("preparing", "transcribing", "cleaning"):
            for index in range(3):
                active = ((self.phase // 18) + index) % 3
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(colour.red(), colour.green(), colour.blue(),
                                  235 if active == 0 else 70))
                p.drawEllipse(QRectF(274 + index * 10, 32, 5, 5))

    def _paint_idle(self, p):
        """Pastel edge control with a subtle ready pulse and a menu grip."""
        mic_x = 44 if self.dock_side == "right" else 10
        tab_x = 7 if self.dock_side == "right" else 64
        hover_alpha = 76 if self.hovered else 48

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, hover_alpha))
        p.drawRoundedRect(QRectF(tab_x + 2, 14, 38, 49), 17, 17)
        p.drawEllipse(QRectF(mic_x + 2, 8, 58, 58))

        p.setBrush(QColor("#FFF9EE"))
        p.setPen(QPen(QColor("#292C2A"), 2.4))
        p.drawRoundedRect(QRectF(tab_x, 10, 40, 51), 18, 18)

        # Three tactile menu dots.
        p.setPen(Qt.PenStyle.NoPen)
        for index, alpha in enumerate((130, 235, 130)):
            p.setBrush(QColor(41, 44, 42, alpha))
            p.drawEllipse(QRectF(tab_x + 16, 21 + index * 8, 5, 5))

        # Layered mic orb.
        p.setBrush(QColor("#FFF9EE"))
        p.setPen(QPen(QColor("#292C2A"), 2.6))
        p.drawEllipse(QRectF(mic_x, 4, 62, 62))
        mode_colour = QColor(self.mode_color)
        pulse = (self.phase % 90) / 90.0
        halo = QColor(mode_colour)
        halo.setAlpha(int(38 + pulse * 54))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(halo, 2.4 + pulse * 1.6))
        p.drawEllipse(QRectF(mic_x + 6 - pulse * 2, 10 - pulse * 2,
                            50 + pulse * 4, 50 + pulse * 4))
        p.setBrush(mode_colour)
        p.setPen(QPen(QColor("#292C2A"), 2.2))
        p.drawEllipse(QRectF(mic_x + 13, 17, 36, 36))

        center_x = mic_x + 31
        p.setPen(QPen(QColor("#161313"), 2.2, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.setBrush(QColor("#161313"))
        p.drawRoundedRect(QRectF(center_x - 4, 23, 8, 14), 4, 4)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(center_x - 8, 29, 16, 13), 180 * 16, 180 * 16)
        p.drawLine(center_x, 42, center_x, 46)
        p.drawLine(center_x - 4, 46, center_x + 4, 46)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#58D597"))
        p.drawEllipse(QRectF(mic_x + 49, 9, 8, 8))

    def enterEvent(self, event):
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_window = self.pos()
            self._dragged = False
            self.resize_animation.stop()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_global is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() > 5:
                self._dragged = True
                self.move(self._press_window + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragged:
                self.snap_to_edge()
            elif self.state == "idle" and self._menu_hit(event.position().toPoint()):
                self.menu_requested.emit(event.globalPosition().toPoint())
            elif self.state not in ("preparing", "transcribing", "cleaning"):
                self.clicked.emit()
        self._press_global = None
        self._press_window = None
        super().mouseReleaseEvent(event)

    def place(self):
        width = 108 if self.state == "idle" else 316
        self.setGeometry(self._target_geometry(width, 74))

    def _target_geometry(self, width, height):
        screen_obj = QApplication.screenAt(self.frameGeometry().center())
        screen = (screen_obj or QApplication.primaryScreen()).availableGeometry()
        x = (screen.left() + 18 if self.dock_side == "left"
             else screen.right() - width - 18)
        default_y = screen.bottom() - height - 70
        y = default_y if self.snap_y is None else max(
            screen.top() + 16, min(self.snap_y, screen.bottom() - height - 16)
        )
        return QRect(x, y, width, height)

    def _menu_hit(self, point):
        tab_x = 7 if self.dock_side == "right" else 64
        return QRect(tab_x, 9, 40, 52).contains(point)

    def snap_to_edge(self):
        screen_obj = QApplication.screenAt(self.frameGeometry().center())
        screen = (screen_obj or QApplication.primaryScreen()).availableGeometry()
        self.dock_side = (
            "left" if self.frameGeometry().center().x() < screen.center().x()
            else "right"
        )
        self.snap_y = max(
            screen.top() + 16,
            min(self.y(), screen.bottom() - self.height() - 16),
        )
        target = self._target_geometry(self.width(), self.height())
        self.resize_animation.stop()
        self.resize_animation.setDuration(420)
        self.resize_animation.setEasingCurve(QEasingCurve.Type.OutBack)
        self.resize_animation.setStartValue(self.geometry())
        self.resize_animation.setEndValue(target)
        self.resize_animation.start()
        self.position_changed.emit(self.dock_side, self.snap_y)
        self.update()


class WorkModeDialog(QDialog):
    """Small editor used to add and update persisted custom work modes."""

    def __init__(self, parent=None, item=None):
        super().__init__(parent)
        self.item = item or {}
        self.color = self.item.get("color", "#7C8CFF")
        self.setWindowTitle(i18n.t("İş modunu redaktə et") if item else i18n.t("Yeni iş modu"))
        self.setMinimumSize(500, 470)
        layout = QVBoxLayout(self)
        self.mode_root_layout = layout
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)
        title = QLabel(self.windowTitle(), objectName="settingsPageTitle")
        hint = QLabel(
            i18n.t("Bu mod danışığı necə hazır mətnə çevirməli olduğunu müəyyən edir."),
            objectName="muted",
        )
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)

        form = QFormLayout()
        self.mode_form_layout = form
        form.setVerticalSpacing(12)
        self.name = QLineEdit(self.item.get("name", ""))
        self.name.setPlaceholderText(i18n.t("Məsələn: Müştəri hesabatı"))
        self.short = QLineEdit(self.item.get("short", ""))
        self.short.setPlaceholderText(i18n.t("Qısa ad"))
        self.color_button = QPushButton(self.color)
        self.color_button.clicked.connect(self.pick_color)
        self.context = QComboBox()
        self.context.addItem(i18n.t("Kontekstsiz"), False)
        self.context.addItem(i18n.t("Yalnız təsdiqlənmiş layihə"), "verified")
        self.context.addItem(i18n.t("Aktiv layihə konteksti"), True)
        current_policy = self.item.get("project_context", False)
        self.context.setCurrentIndex(max(0, self.context.findData(current_policy)))
        form.addRow(i18n.t("Mod adı"), self.name)
        form.addRow(i18n.t("Qısa ad"), self.short)
        form.addRow(i18n.t("Rəng"), self.color_button)
        form.addRow(i18n.t("Project context"), self.context)
        layout.addLayout(form)

        self.prompt = QPlainTextEdit(self.item.get("prompt", ""))
        self.prompt.setPlaceholderText(
            i18n.t("Model üçün təlimatı yaz. Fakt uydurmamaq və output formatını burada müəyyən et.")
        )
        self.prompt.setMinimumHeight(180)
        layout.addWidget(QLabel(i18n.t("Mod təlimatı")))
        layout.addWidget(self.prompt, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def pick_color(self):
        selected = QColorDialog.getColor(QColor(self.color), self)
        if selected.isValid():
            self.color = selected.name().upper()
            self.color_button.setText(self.color)
            self.color_button.setStyleSheet(f"border-left: 12px solid {self.color};")

    def validate_and_accept(self):
        if not self.name.text().strip() or not self.prompt.toPlainText().strip():
            QMessageBox.warning(
                self, i18n.t("Məlumat çatışmır"),
                i18n.t("Mod adı və mod təlimatı boş ola bilməz."),
            )
            return
        self.accept()

    def value(self):
        return {
            "id": self.item.get("id", ""),
            "name": self.name.text().strip(),
            "short": self.short.text().strip() or self.name.text().strip(),
            "color": self.color,
            "project_context": self.context.currentData(),
            "prompt": self.prompt.toPlainText().strip(),
        }


class HistoryPopup(QWidget):
    """Right-side history drawer with a dedicated copy action per row."""

    def __init__(self):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(440, 620)
        self.setMinimumWidth(380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        self.panel = QFrame(objectName="historyPopup")
        self.set_theme("dark")
        outer.addWidget(self.panel)
        panel_l = QVBoxLayout(self.panel)
        panel_l.setContentsMargins(18, 16, 18, 16)
        panel_l.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("SON NƏTİCƏLƏR", objectName="popupTitle")
        hint = QLabel("Son hazır mətnlər • istədiyini bir kliklə kopyala",
                      objectName="popupHint")
        titles.addWidget(title)
        titles.addWidget(hint)
        close = QPushButton("✕")
        close.setFixedSize(34, 34)
        close.clicked.connect(self.hide)
        header.addLayout(titles)
        header.addStretch()
        header.addWidget(close)
        panel_l.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.results_widget = QWidget()
        self.results_l = QVBoxLayout(self.results_widget)
        self.results_l.setContentsMargins(0, 0, 0, 0)
        self.results_l.setSpacing(9)
        self.results_l.addStretch()
        scroll.setWidget(self.results_widget)
        panel_l.addWidget(scroll, 1)
        localize_widget_tree(self)

    def retranslate(self):
        localize_widget_tree(self)
        self.refresh(cfg.read_history(30))

    def set_theme(self, theme):
        dark = theme == "dark"
        bg = "#2c2c2e" if dark else "#ffffff"
        field = "#1f1f21" if dark else "#f5f5f7"
        text = "#f5f5f7" if dark else "#1d1d1f"
        muted = "#a1a1a6" if dark else "#6e6e73"
        border = "#48484a" if dark else "#d9d9de"
        self.panel.setStyleSheet(f"""
            #historyPopup {{ background: {bg}; border: 1px solid {border}; border-radius: 18px; }}
            QLabel {{ color: {text}; font-family: 'Segoe UI Variable', 'Segoe UI'; }}
            #popupTitle {{ font-size: 17px; font-weight: 750; }}
            #popupHint, #resultMeta {{ color: {muted}; font-size: 11px; }}
            #resultRow {{ background: {field}; border: 1px solid {border}; border-radius: 12px; }}
            #resultText {{ color: {text}; font-size: 12px; }}
            QPushButton {{ background: {field}; color: {text}; border: 1px solid {border};
                           border-radius: 8px; padding: 7px 11px; font-weight: 650; }}
            QPushButton:hover {{ background: #ff6b47; color: #22120d; border-color: #ff6b47; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ border: 0; background: {bg}; }}
        """)

    def refresh(self, rows):
        while self.results_l.count():
            item = self.results_l.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not rows:
            empty = QLabel(i18n.t("Hələ hazır nəticə yoxdur."), objectName="popupHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.results_l.addWidget(empty)
            self.results_l.addStretch()
            return

        for row in reversed(rows[-30:]):
            text = row.get("text") or row.get("raw") or ""
            mode = get_work_mode(row.get("mode") or "dictation")
            card = QFrame(objectName="resultRow")
            card_l = QVBoxLayout(card)
            card_l.setContentsMargins(12, 10, 10, 10)
            card_l.setSpacing(7)

            top = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {mode['color']}; font-size: 14px;")
            meta = QLabel(
                f"{i18n.t(mode['name'])}   •   {row.get('ts', '')}",
                objectName="resultMeta"
            )
            copy = QPushButton(i18n.t("Kopyala"))
            copy.setFixedWidth(82)
            copy.clicked.connect(
                lambda _checked=False, value=text, button=copy:
                self.copy_result(value, button)
            )
            top.addWidget(dot)
            top.addWidget(meta)
            top.addStretch()
            top.addWidget(copy)

            preview = text if len(text) <= 420 else text[:417].rstrip() + "…"
            result = QLabel(preview, objectName="resultText")
            result.setWordWrap(True)
            result.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            result.setToolTip(text)
            card_l.addLayout(top)
            card_l.addWidget(result)
            self.results_l.addWidget(card)
        self.results_l.addStretch()

    def copy_result(self, text, button):
        QApplication.clipboard().setText(text)
        button.setText(i18n.t("Kopyalandı"))
        QTimer.singleShot(1400, lambda: button.setText(i18n.t("Kopyala")))

    def show_near(self, anchor):
        screen_obj = QApplication.screenAt(anchor)
        screen = (screen_obj or QApplication.primaryScreen()).availableGeometry()
        x = max(screen.left() + 12, min(
            anchor.x() - self.width(),
            screen.right() - self.width() - 12,
        ))
        y = max(screen.top() + 12, min(
            anchor.y() - self.height() // 2,
            screen.bottom() - self.height() - 12,
        ))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def show_as_drawer(self, owner):
        """Align the panel to the right edge of the main window like a drawer."""
        screen_obj = QApplication.screenAt(owner.frameGeometry().center())
        screen = (screen_obj or QApplication.primaryScreen()).availableGeometry()
        owner_rect = owner.frameGeometry()
        height = max(480, min(owner_rect.height() - 20, screen.height() - 24))
        width = min(440, max(380, owner_rect.width() // 2))
        self.resize(width, height)
        x = min(owner_rect.right() - width - 10, screen.right() - width - 12)
        x = max(screen.left() + 12, x)
        y = max(screen.top() + 12, owner_rect.top() + 10)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()


class DeYazWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.conf = cfg.Config()
        set_custom_modes(self.conf.get("custom_work_modes", []))
        self.recorder = AudioRecorder()
        self.meeting = MeetingCapture(self.conf)
        self.pipeline = Transcription(self.conf)
        self.file_pipeline = filetranscribe.FileTranscriber(self.conf, self)
        self.file_path = ""
        self.file_segments = []
        self.meeting_live_items = []
        self.meeting_live_partials = {}
        self.latest_result_text = ""
        self._dictation_result_open = False
        self.current_context = None
        configured_mode = self.conf.get("work_mode", "dictation")
        self._dictation_work_mode = (
            "dictation" if configured_mode == "meeting_notes_live" else configured_mode
        )
        self._last_surface = (
            "meeting" if configured_mode == "meeting_notes_live" else "dictation"
        )
        self.current_surface = "home"
        self.hotkey = GlobalHotkey(self.conf["windows_hotkey"])
        self.bubble = MiniMic()
        self.history_popup = HistoryPopup()
        self.bubble.dock_side = (
            "left" if self.conf["mini_corner"].endswith("left") else "right"
        )
        stored_y = int(self.conf["mini_position_y"])
        self.bubble.snap_y = None if stored_y < 0 else stored_y
        self.bubble.set_mode(self.conf["work_mode"])
        self.recorder.device = self.conf["windows_mic_device"] or None
        self._audio_device_syncing = False
        self.setWindowTitle("DeYaz")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(500, 600)
        self.resize(900, 700)
        self._build_ui()
        self.meeting_partial_render_timer = QTimer(self)
        self.meeting_partial_render_timer.setSingleShot(True)
        self.meeting_partial_render_timer.setInterval(70)
        self.meeting_partial_render_timer.timeout.connect(
            self._render_meeting_live_transcript
        )
        # Older builds stored only a device name.  PortAudio can expose the
        # same Windows endpoint through several host APIs, making that name
        # ambiguous.  Persist the concrete selector chosen during UI migration.
        if hasattr(self, "dictation_microphone"):
            migrated_device = self.dictation_microphone.currentData() or ""
            if migrated_device != self.conf.get("windows_mic_device", ""):
                self.conf["windows_mic_device"] = migrated_device
                self.recorder.device = migrated_device or None
                self.conf.save()
        self.meeting_timer = QTimer(self)
        self.meeting_timer.setInterval(1000)
        self.meeting_timer.timeout.connect(self.update_meeting_clock)
        self.meeting_started_ui = 0.0
        self.last_meeting_path = ""
        # Windows does not push microphone hot-plug events through PortAudio or
        # SoundCard. Poll the lightweight endpoint lists and only touch the UI
        # when a device is actually added, removed or renamed.
        self._audio_device_signatures = self._current_audio_device_signatures()
        self.audio_device_timer = QTimer(self)
        self.audio_device_timer.setInterval(2000)
        self.audio_device_timer.timeout.connect(self.refresh_audio_devices_if_changed)
        self.audio_device_timer.start()
        self._build_tray()
        self.apply_work_mode_visual()
        self.recorder.level.connect(self.set_level)
        self.recorder.finished.connect(self.transcribe)
        self.recorder.failed.connect(self.fail)
        self.meeting.segment.connect(self.on_meeting_segment)
        self.meeting.partial.connect(self.on_meeting_partial)
        self.meeting.level.connect(self.on_meeting_level)
        self.meeting.status.connect(self.on_meeting_status)
        self.meeting.finished.connect(self.on_meeting_finished)
        self.meeting.failed.connect(self.on_meeting_failed)
        self.pipeline.stage.connect(self.set_status)
        self.pipeline.finished.connect(self.complete)
        self.pipeline.failed.connect(self.fail)
        self.file_pipeline.progress.connect(self.on_file_progress)
        self.file_pipeline.finished.connect(self.on_file_finished)
        self.file_pipeline.failed.connect(self.on_file_failed)
        self.hotkey.pressed.connect(self.toggle_recording)
        self.hotkey.registration_changed.connect(self.on_hotkey_registration)
        self.bubble.clicked.connect(self.toggle_recording)
        self.bubble.menu_requested.connect(self.show_bubble_menu)
        self.bubble.position_changed.connect(self.save_bubble_position)
        self.hotkey.start()
        self.refresh_history()
        QTimer.singleShot(900, self.maybe_show_model_onboarding)

    @staticmethod
    def _populate_audio_combo(combo, choices, selected):
        combo.blockSignals(True)
        combo.clear()
        for choice in choices:
            combo.addItem(line_icon("mic", "#64748B", 15), choice.label, choice.value)
            combo.setItemData(
                combo.count() - 1, choice.name, Qt.ItemDataRole.UserRole + 1
            )
        combo.setCurrentIndex(choice_index(choices, selected))
        combo.setToolTip(combo.currentText())
        combo.blockSignals(False)

    def _load_dictation_microphones(self, combo, selected=None):
        choices = self._dictation_microphone_choices()
        self._populate_audio_combo(
            combo, choices,
            self.conf.get("windows_mic_device", "") if selected is None else selected,
        )

    def _load_meeting_microphones(self, combo, selected=None):
        choices = self._meeting_microphone_choices()
        self._populate_audio_combo(
            combo, choices,
            self.conf.get("meeting_mic_target", "") if selected is None else selected,
        )

    @staticmethod
    def _dictation_microphone_choices():
        return sounddevice_input_choices(sd)

    @staticmethod
    def _meeting_microphone_choices():
        try:
            import soundcard as sc
            return soundcard_microphone_choices(sc)
        except Exception:
            return []

    def _current_audio_device_signatures(self):
        return (
            audio_choice_signature(self._dictation_microphone_choices()),
            audio_choice_signature(self._meeting_microphone_choices()),
        )

    def refresh_audio_devices_if_changed(self):
        """Refresh every microphone picker after a Windows hot-plug event."""
        dictation_choices = self._dictation_microphone_choices()
        meeting_choices = self._meeting_microphone_choices()
        signatures = (
            audio_choice_signature(dictation_choices),
            audio_choice_signature(meeting_choices),
        )
        if signatures == self._audio_device_signatures:
            return
        previous = self._audio_device_signatures
        self._audio_device_signatures = signatures

        if signatures[0] != previous[0]:
            selected = self.conf.get("windows_mic_device", "")
            for name in ("dictation_microphone", "microphone"):
                combo = getattr(self, name, None)
                if combo is not None:
                    self._populate_audio_combo(combo, dictation_choices, selected)

        if signatures[1] != previous[1]:
            combo = getattr(self, "meeting_microphone", None)
            if combo is not None:
                self._populate_audio_combo(
                    combo, meeting_choices,
                    self.conf.get("meeting_mic_target", ""),
                )

    def dictation_microphone_changed(self):
        if self._audio_device_syncing:
            return
        value = self.dictation_microphone.currentData() or ""
        self.dictation_microphone.setToolTip(
            self.dictation_microphone.currentText()
        )
        self._audio_device_syncing = True
        try:
            self.conf["windows_mic_device"] = value
            self.recorder.device = value or None
            if hasattr(self, "microphone"):
                self.microphone.setCurrentIndex(
                    max(0, self.microphone.findData(value))
                )
            self.conf.save()
        finally:
            self._audio_device_syncing = False

    def settings_microphone_changed(self):
        if self._audio_device_syncing:
            return
        value = self.microphone.currentData() or ""
        self.microphone.setToolTip(self.microphone.currentText())
        self._audio_device_syncing = True
        try:
            self.conf["windows_mic_device"] = value
            self.recorder.device = value or None
            if hasattr(self, "dictation_microphone"):
                self.dictation_microphone.setCurrentIndex(
                    max(0, self.dictation_microphone.findData(value))
                )
            self.conf.save()
        finally:
            self._audio_device_syncing = False

    def meeting_microphone_changed(self):
        if not hasattr(self, "meeting_microphone"):
            return
        self.conf["meeting_mic_target"] = (
            self.meeting_microphone.currentData() or ""
        )
        self.meeting_microphone.setToolTip(
            self.meeting_microphone.currentText()
        )
        self.conf.save()

    def on_hotkey_registration(self, registered, detail):
        if hasattr(self, "hotkey_value"):
            state = i18n.t("Hazır") if registered else i18n.t("Konflikt")
            self.hotkey_value.setText(f"{self.hotkey.shortcut.upper()}  ·  {state}")
            self.hotkey_value.setToolTip("" if registered else detail)
        if not registered and hasattr(self, "status"):
            self.set_status("Ctrl+Alt+R başqa tətbiq tərəfindən istifadə olunur")

    def resolved_appearance(self, preference=None):
        preference = preference or self.conf.get("appearance", "auto")
        if preference in {"light", "dark"}:
            return preference
        return (
            "dark" if self.app.palette().window().color().lightness() < 128
            else "light"
        )

    def theme_stylesheet(self, theme):
        dark = theme == "dark"
        c = {
            "bg": "#181A18" if dark else "#FBF9EF",
            "chrome": "#20231F" if dark else "#FFFDF5",
            "surface": "#252923" if dark else "#FFFDF8",
            "surface2": "#2E332B" if dark else "#FFF3EE",
            "field": "#1C1F1B" if dark else "#FFFDF8",
            "text": "#F8F3E8" if dark else "#202321",
            "muted": "#BEB9AD" if dark else "#66706D",
            "separator": "#ECE5D7" if dark else "#292C2A",
            "soft": "#343930" if dark else "#EEE9DD",
            "hover": "#3C4238" if dark else "#F1ECDF",
            "accent": "#C9B7FF",
            "accent_hover": "#B7A0FF",
            "accent_text": "#202321",
            "pink": "#F2AEB4" if dark else "#FFC4C7",
            "blue": "#77B6DF" if dark else "#A6D7F7",
            "green": "#78C58D" if dark else "#AFECB9",
            "yellow": "#D9BE6A" if dark else "#FFEAA0",
            "purple": "#9D8BD8" if dark else "#CCBEFF",
            "mint": "#78CEB8" if dark else "#9BE8D1",
            "danger": "#E96A72" if dark else "#FFD7D9",
            "tooltip_bg": "#F8F3E8" if dark else "#202321",
            "tooltip_text": "#202321" if dark else "#FFFDF8",
        }
        self.theme_tokens = c
        return f"""
            QMainWindow {{ background: {c['bg']}; color: {c['text']}; }}
            #shell {{ background: transparent; color: {c['text']}; }}
            QWidget {{ font-family: 'Segoe UI'; font-size: 13px; color: {c['text']}; }}
            #top {{ background: {c['chrome']}; border-bottom: 2px solid {c['separator']}; }}
            #logoImage {{ background: transparent; border: 0; }}
            #brand {{ font-size: 20px; font-weight: 700; letter-spacing: .6px; }}
            #eyebrow {{ color: {c['muted']}; font-size: 10px; font-weight: 650; letter-spacing: 1.2px; }}
            #card {{ background: {c['surface']}; border: 1px solid {c['separator']}; border-radius: 28px; }}
            #heroCopy {{ background: transparent; }}
            #signalPanel {{ background: {c['surface2']}; border: 1px solid {c['separator']}; border-radius: 22px; }}
            #voiceVisual {{ background: {c['soft']}; border: 1px solid {c['separator']}; border-radius: 40px; }}
            #status {{ color: {c['text']}; font-size: 34px; font-weight: 700; letter-spacing: -.3px; }}
            #muted {{ color: {c['muted']}; }}
            #modeBadge {{ padding: 5px 11px; border-radius: 10px; font-size: 10px;
                          font-weight: 750; letter-spacing: .8px; }}
            #contextBadge {{ color: {c['muted']}; font-size: 10px; font-weight: 650; letter-spacing: .7px; }}
            #record {{ background: {c['accent']}; color: {c['accent_text']}; border: 1px solid {c['accent_hover']};
                       border-radius: 21px; padding: 0 24px; font-size: 14px; font-weight: 760; }}
            #record:hover {{ background: {c['accent_hover']}; }}
            #record:pressed {{ padding-top: 2px; }}
            #record[recording='true'] {{ background: {c['text']}; color: {c['bg']}; border-color: {c['text']}; }}
            #orbHint {{ color: {c['muted']}; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; }}
            #shortcutChip {{ background: {c['soft']}; color: {c['text']}; border: 1px solid {c['separator']};
                             border-radius: 10px; padding: 8px 12px; font-size: 11px; font-weight: 650; }}
            #workflowCard, #recentCard, #meetingCard {{ background: {c['surface']}; border: 1px solid {c['separator']}; border-radius: 20px; }}
            #sectionEyebrow {{ color: {c['muted']}; font-size: 9px; font-weight: 800; letter-spacing: 1.4px; }}
            #sectionDescription {{ color: {c['muted']}; font-size: 11px; }}
            #flowStatus {{ background: rgba(43, 199, 181, 22); color: #2bc7b5; border: 1px solid rgba(43, 199, 181, 78); border-radius: 10px; padding: 7px 10px; font-size: 10px; font-weight: 750; }}
            #flowStep {{ background: {c['surface2']}; border: 1px solid {c['separator']}; border-radius: 16px; }}
            #flowStep:hover {{ border-color: rgba(255, 101, 71, 90); background: {c['hover']}; }}
            #stepIndex {{ background: {c['soft']}; color: {c['muted']}; border: 1px solid {c['separator']}; border-radius: 12px; font-size: 10px; font-weight: 800; }}
            #flowIcon {{ background: transparent; }}
            #flowTitle {{ color: {c['muted']}; font-size: 9px; font-weight: 800; letter-spacing: 1.2px; }}
            #flowHelp {{ color: {c['muted']}; font-size: 10px; min-height: 30px; }}
            #quickIcon {{ color: {c['accent']}; font-size: 19px; font-weight: 700; }}
            #quickTitle {{ color: {c['muted']}; font-size: 9px; font-weight: 700; letter-spacing: 1px; }}
            #quickValue {{ color: {c['text']}; font-size: 13px; font-weight: 650; }}
            #recentPreview {{ color: {c['text']}; font-size: 12px; padding: 3px 0; }}
            #recentTime {{ color: {c['muted']}; font-size: 10px; }}
            #secondaryAction {{ background: transparent; color: {c['muted']}; border: 1px solid {c['separator']}; padding: 7px 10px; font-size: 10px; }}
            #secondaryAction:hover {{ color: {c['text']}; background: {c['hover']}; border-color: {c['muted']}; }}
            #secondaryAction:pressed {{ background: {c['soft']}; border-color: {c['text']}; }}
            #secondaryAction:focus {{ color: {c['text']}; border: 2px solid {c['accent']}; padding: 6px 9px; }}
            #meetingState {{ background: rgba(43, 199, 181, 24); color: #2bc7b5; border: 1px solid rgba(43, 199, 181, 80); border-radius: 10px; padding: 7px 11px; font-size: 10px; font-weight: 750; }}
            #meetingState[live="true"] {{ background: rgba(239, 91, 91, 24); color: #ef6b6b; border-color: rgba(239, 91, 91, 95); }}
            #meetingClock {{ color: {c['text']}; font-size: 16px; font-weight: 760; min-width: 54px; }}
            #sourceChip {{ background: {c['soft']}; color: {c['text']}; border: 1px solid {c['separator']}; border-radius: 10px; padding: 7px 10px; font-size: 10px; font-weight: 650; }}
            #sourceLevel {{ background: {c['soft']}; border: 0; border-radius: 3px; height: 6px; }}
            #sourceLevel::chunk {{ background: #2bc7b5; border-radius: 3px; }}
            #meetingTranscript {{ background: {c['field']}; border: 1px solid {c['separator']}; border-radius: 14px; padding: 14px; font-family: 'Cascadia Code', 'Consolas'; font-size: 11px; }}
            #meetingTranscript:focus {{ border-color: rgba(43, 199, 181, 100); }}
            #meetingPrimary {{ background: #2bc7b5; color: #10211f; border: 1px solid #60d8ca; padding: 10px 16px; font-weight: 800; }}
            #meetingPrimary:hover {{ background: #45d1c1; }}
            #meetingPrimary:pressed {{ background: #20aa9b; border-color: #178d82; }}
            #meetingPrimary:focus {{ border: 2px solid {c['text']}; padding: 9px 15px; }}
            #topTools {{ background: {c['soft']}; border: 1px solid {c['separator']}; border-radius: 14px; }}
            #topAction, #topIcon, #appearanceSwitch {{ background: transparent; border: 1px solid transparent; padding: 7px 10px; border-radius: 9px; font-weight: 650; }}
            #topAction:hover, #topIcon:hover, #appearanceSwitch:hover {{ background: {c['hover']}; border-color: {c['separator']}; }}
            #topAction:pressed, #topIcon:pressed, #appearanceSwitch:pressed, #appearanceSwitch:on {{ background: {c['soft']}; border-color: {c['muted']}; }}
            #topAction:focus, #topIcon:focus, #appearanceSwitch:focus {{ border: 2px solid {c['accent']}; padding: 6px 9px; }}
            #topIcon {{ padding: 7px; }}
            #modeSwitch {{ background: {c['accent']}; color: {c['accent_text']}; border: 0; border-radius: 9px; padding: 8px 13px; font-weight: 750; }}
            #modeSwitch:hover {{ background: {c['accent_hover']}; color: {c['accent_text']}; }}
            #appearanceSwitch {{ min-width: 92px; padding-right: 12px; text-align: left; }}
            #appearanceSwitch::menu-indicator {{ image: none; width: 0; }}
            #appearanceSwitch QMenu {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['separator']}; border-radius: 10px; padding: 6px; }}
            #appearanceSwitch QMenu::item {{ padding: 8px 28px 8px 10px; border-radius: 7px; }}
            #appearanceSwitch QMenu::item:selected {{ background: {c['hover']}; }}
            QMenu {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['separator']};
                     border-radius: 11px; padding: 7px; }}
            QMenu::item {{ background: transparent; color: {c['text']}; padding: 9px 28px 9px 13px;
                          border-radius: 7px; margin: 1px 0; }}
            QMenu::item:selected {{ background: {c['hover']}; color: {c['text']}; }}
            QMenu::item:pressed {{ background: {c['soft']}; color: {c['text']}; }}
            QMenu::item:disabled {{ color: {c['muted']}; }}
            QMenu::item:checked {{ color: {c['accent']}; font-weight: 750; }}
            QMenu::separator {{ height: 1px; background: {c['separator']}; margin: 6px 8px; }}
            QMenu::right-arrow {{ width: 8px; height: 8px; }}
            QMessageBox, QDialog {{ background: {c['surface']}; color: {c['text']}; }}
            QMessageBox QLabel {{ background: transparent; color: {c['text']}; min-width: 280px;
                                  font-size: 13px; padding: 2px; }}
            QMessageBox QPushButton {{ min-width: 82px; min-height: 24px; }}
            QToolTip {{ background-color: {c['tooltip_bg']}; color: {c['tooltip_text']};
                        border: 1px solid {c['muted']}; border-radius: 8px; padding: 7px 9px;
                        max-width: 420px; }}
            QPushButton {{ background: {c['soft']}; color: {c['text']}; padding: 9px 13px;
                           border: 1px solid {c['separator']}; border-radius: 10px; font-weight: 600; }}
            QPushButton:hover {{ background: {c['hover']}; border-color: {c['muted']}; }}
            QPushButton:pressed {{ background: {c['separator']}; border-color: {c['text']}; }}
            QPushButton:focus {{ border: 2px solid {c['accent']}; padding: 8px 12px; }}
            QPushButton:disabled {{ color: {c['muted']}; background: transparent; border-color: {c['separator']}; }}
            #surfaceNav {{ background: {c['surface']}; border: 1px solid {c['separator']}; border-radius: 15px; }}
            #surfaceModeBar {{ background: {c['surface2']}; border: 1px solid {c['separator']}; border-radius: 15px; }}
            #audioDeviceBar {{ background: {c['surface']}; border: 1px solid {c['separator']}; border-radius: 15px; }}
            #audioDeviceIcon {{ background: {c['pink']}; border: 1px solid {c['separator']}; border-radius: 8px; }}
            #audioDevicePicker {{ min-height: 30px; font-weight: 680; }}
            #surfaceModeIcon {{ background: {c['soft']}; border: 1px solid {c['separator']}; border-radius: 10px; padding: 7px; }}
            #surfaceModeHelp {{ color: {c['muted']}; font-size: 10px; }}
            #modePicker {{ min-height: 30px; font-weight: 680; padding-left: 12px; }}
            #actionDock {{ background: {c['surface']}; border: 1px solid {c['separator']}; border-radius: 17px; }}
            #actionDock:hover {{ border-color: {c['muted']}; }}
            #actionTitle {{ color: {c['text']}; font-size: 11px; font-weight: 820; letter-spacing: .8px; }}
            #actionHelp {{ color: {c['muted']}; font-size: 10px; }}
            #surfaceTab {{ background: transparent; color: {c['muted']}; border: 1px solid transparent;
                           border-radius: 11px; padding: 10px 17px; font-size: 11px; font-weight: 720; }}
            #surfaceTab:hover:!checked {{ background: {c['hover']}; color: {c['text']}; border-color: {c['separator']}; }}
            #surfaceTab:checked {{ background: {c['field']}; color: {c['text']}; border-color: {c['separator']}; font-weight: 800; }}
            #surfaceTab[surface="dictation"]:checked, #surfaceTab[surface="file"]:checked {{
                background: rgba(255, 101, 71, 24); color: {c['accent']}; border-color: {c['accent']}; }}
            #surfaceTab[surface="meeting"]:checked {{ background: rgba(43, 199, 181, 24);
                color: #22aa9b; border-color: #2bc7b5; }}
            #surfaceTab:pressed {{ background: {c['soft']}; }}
            #surfaceTab:focus {{ border: 2px solid {c['accent']}; padding: 9px 16px; }}
            QLineEdit, QComboBox, QPlainTextEdit, QSpinBox {{ background: {c['field']}; border: 1px solid {c['separator']};
                border-radius: 8px; padding: 8px 10px; color: {c['text']}; selection-background-color: {c['accent']}; }}
            QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QSpinBox:focus {{ border: 2px solid {c['accent']}; }}
            QComboBox:hover {{ border-color: {c['muted']}; background: {c['surface']}; }}
            QComboBox:on {{ border: 2px solid {c['accent']}; background: {c['surface']}; }}
            QComboBox QAbstractItemView {{ background: {c['surface']}; color: {c['text']};
                selection-background-color: {c['accent']}; selection-color: {c['accent_text']};
                border: 1px solid {c['separator']}; border-radius: 10px; padding: 5px; outline: 0; }}
            QComboBox QAbstractItemView::item {{ color: {c['text']}; min-height: 34px; padding: 5px 10px; border-radius: 7px; }}
            QComboBox QAbstractItemView::item:selected {{ background: {c['accent']}; color: {c['accent_text']}; }}
            QComboBox QAbstractItemView::item:hover:!selected {{ background: {c['hover']}; color: {c['text']}; }}
            QCheckBox {{ color: {c['text']}; padding: 4px; spacing: 8px; }}
            #quickControl {{ min-width: 140px; }}
            #history, #fileOutput {{ background: {c['field']}; border: 1px solid {c['separator']};
                                     border-radius: 12px; color: {c['text']}; padding: 12px; }}
            #fileHero {{ background: {c['surface2']}; border: 1px solid {c['separator']}; border-radius: 16px; }}
            #fileDrop {{ background: {c['surface2']}; border: 1px dashed {c['muted']}; border-radius: 14px;
                         padding: 22px; font-size: 14px; }}
            #fileDrop:hover {{ background: {c['soft']}; border-color: {c['accent']}; }}
            #primaryFile {{ background: {c['accent']}; color: {c['accent_text']}; border: 0; padding: 11px 18px; font-weight: 750; }}
            #primaryFile:hover {{ background: {c['accent_hover']}; }}
            #primaryFile:pressed {{ background: #e95034; }}
            #primaryFile:focus {{ border: 2px solid {c['text']}; padding: 9px 16px; }}
            #oauthCard {{ background: {c['field']}; border: 1px solid {c['separator']}; border-radius: 14px; }}
            #oauthTitle {{ color: {c['text']}; font-size: 14px; font-weight: 750; }}
            #oauthHint, #oauthStatus {{ color: {c['muted']}; font-size: 11px; }}
            #oauthDot {{ color: {c['muted']}; font-size: 13px; }}
            #oauthDot[connected="true"] {{ color: #38c98b; }}
            #accountCard {{ background: {c['soft']}; border: 1px solid {c['separator']}; border-radius: 12px; }}
            #accountCard[state="healthy"] {{ background: rgba(56, 201, 139, 22); border-color: rgba(56, 201, 139, 85); }}
            #accountCard[state="warning"] {{ background: rgba(245, 168, 55, 24); border-color: rgba(245, 168, 55, 95); }}
            #accountCard[state="empty"] {{ background: rgba(239, 91, 91, 24); border-color: rgba(239, 91, 91, 105); }}
            #accountIcon {{ background: {c['surface']}; color: {c['muted']}; border: 1px solid {c['separator']}; border-radius: 10px; font-size: 16px; font-weight: 850; }}
            #accountCard[state="healthy"] #accountIcon {{ color: #28a975; border-color: rgba(56, 201, 139, 95); }}
            #accountCard[state="warning"] #accountIcon {{ color: #d58a18; border-color: rgba(245, 168, 55, 105); }}
            #accountCard[state="empty"] #accountIcon {{ color: #df4d4d; border-color: rgba(239, 91, 91, 115); }}
            #accountTitle {{ color: {c['text']}; font-size: 12px; font-weight: 780; }}
            #accountDetail {{ color: {c['muted']}; font-size: 10px; }}
            #accountAction {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['separator']}; padding: 7px 10px; }}
            #accountCard[state="empty"] #accountAction {{ background: #ef5b5b; color: white; border-color: #ef5b5b; }}
            #primaryAction {{ background: {c['accent']}; color: {c['accent_text']}; border: 0; padding: 10px 15px; font-weight: 750; }}
            #primaryAction:hover {{ background: {c['accent_hover']}; }}
            #quietDanger {{ background: transparent; color: {c['muted']}; border: 0; padding: 6px 8px; }}
            #quietDanger:hover {{ color: #ef5b5b; background: transparent; }}
            #modelDialog, #creditDialog {{ background: {c['surface']}; }}
            #contextAddDialog, #contextTextDialog {{ background: {c['yellow']};
                border: 3px solid #292C2A; border-radius: 26px; }}
            #contextManagerDialog {{ background: {c['surface']};
                border: 3px solid #292C2A; border-radius: 26px; }}
            #contextManagerHeader {{ background: {c['yellow']}; color: #202321;
                border: 3px solid #292C2A; border-radius: 20px; }}
            #contextDialogTitle, #contextManagerTitle {{ color: #202321;
                font-family: 'Segoe Print'; font-size: 31px; font-weight: 760; }}
            #contextManagerTitle {{ font-size: 28px; }}
            #contextProjectAction, #contextPasteAction, #contextFileAction,
            #contextPlusAction, #contextEntry,
            #contextProjectChoice {{ color: #202321;
                border: 3px solid #292C2A; border-radius: 20px;
                font-family: 'Segoe Print'; font-size: 18px; font-weight: 700; }}
            #contextProjectAction, #contextPlusAction {{ background: {c['pink']}; }}
            #contextPasteAction {{ background: {c['green']}; }}
            #contextFileAction {{ background: {c['blue']}; }}
            #contextProjectAction:hover, #contextPasteAction:hover,
            #contextFileAction:hover, #contextPlusAction:hover {{
                border-width: 4px; }}
            #contextProjectAction:pressed, #contextPasteAction:pressed,
            #contextFileAction:pressed, #contextPlusAction:pressed {{ padding-top: 6px; }}
            #contextProjectPanel {{ background: {c['pink']}; border: 3px solid #292C2A;
                border-radius: 22px; }}
            #contextReferencePanel {{ background: {c['green']}; border: 3px solid #292C2A;
                border-radius: 22px; }}
            #contextListScroll, #contextListContent {{ background: transparent; border: 0; }}
            #contextColumnIcon {{ background: rgba(255,255,255,120); border: 2px solid #292C2A;
                border-radius: 9px; padding: 3px; }}
            #contextProjectChoice, #contextEntry {{ background: {c['surface']};
                color: {c['text']};
                text-align: left; padding: 13px 16px; min-height: 66px;
                font-family: 'Segoe UI'; font-size: 13px; font-weight: 700; }}
            #contextProjectChoice:hover, #contextEntry:hover {{ background: {c['yellow']};
                color: #202321; border-width: 4px; padding: 12px 15px; }}
            #contextProjectChoice:pressed, #contextEntry:pressed {{
                background: {c['soft']}; padding-top: 15px; padding-bottom: 11px; }}
            #contextProjectChoice:checked {{ background: {c['purple']}; border-width: 4px;
                color: #202321; padding: 12px 15px; }}
            #contextEntry:checked {{ background: {c['blue']}; border-width: 4px;
                color: #202321; padding: 12px 15px; }}
            #contextProjectChoice:focus, #contextEntry:focus {{ border: 4px solid {c['accent']};
                padding: 12px 15px; }}
            #contextColumnTitle {{ color: #202321; font-size: 18px; font-weight: 800;
                padding: 0 4px 4px 4px; }}
            #contextEmpty {{ color: #4E5551; background: rgba(255,255,255,95);
                border: 2px dashed rgba(41,44,42,120); border-radius: 16px;
                font-size: 13px; padding: 22px 12px; }}
            #contextTextEditor {{ background: {c['surface']}; color: {c['text']};
                border: 3px solid #292C2A; border-radius: 16px; padding: 14px; }}
            #contextSaveAction {{ background: {c['green']}; color: #202321;
                border: 3px solid #292C2A; border-radius: 13px; padding: 9px 18px; }}
            #dialogEyebrow {{ color: {c['accent']}; font-size: 10px; font-weight: 800; letter-spacing: 1.4px; }}
            #dialogTitle {{ color: {c['text']}; font-size: 25px; font-weight: 760; }}
            #dialogCopy {{ color: {c['muted']}; font-size: 13px; }}
            #dialogNote {{ background: {c['soft']}; color: {c['muted']}; border: 1px solid {c['separator']}; border-radius: 10px; padding: 10px 12px; }}
            #modelColumn {{ background: {c['surface2']}; border: 1px solid {c['separator']}; border-radius: 16px; }}
            #modelHeading {{ color: {c['text']}; font-size: 12px; font-weight: 800; letter-spacing: .7px; }}
            #modelSubheading {{ color: {c['muted']}; font-size: 11px; padding-bottom: 4px; }}
            #modelResponsiveTabs::pane {{ background: transparent; border: 0; top: -1px; }}
            #modelResponsiveTabs QTabBar::tab {{ background: {c['soft']}; color: {c['muted']};
                border: 2px solid {c['separator']}; border-bottom: 0;
                border-top-left-radius: 11px; border-top-right-radius: 11px;
                padding: 9px 13px; min-width: 150px; font-weight: 760; }}
            #modelResponsiveTabs QTabBar::tab:selected {{ background: {c['surface2']};
                color: {c['text']}; border-color: {c['accent']}; }}
            #modelResponsiveTabs QTabBar::tab:hover:!selected {{ background: {c['hover']};
                color: {c['text']}; }}
            #modelChoices {{ background: transparent; border: 0; padding: 0; }}
            #modelChoices::item {{ background: {c['field']}; color: {c['text']}; border: 1px solid {c['separator']}; border-radius: 11px; padding: 0; margin: 3px 0; }}
            #modelChoices::item:selected {{ background: {c['soft']}; color: {c['text']}; border: 2px solid {c['accent']}; }}
            #modelChoices::item:disabled {{ color: {c['muted']}; background: transparent; }}
            #modelChoiceCard {{ background: transparent; }}
            #modelName {{ color: {c['text']}; font-size: 13px; font-weight: 760; }}
            #modelDescription {{ color: {c['muted']}; font-size: 10px; }}
            #modelTag {{ background: {c['soft']}; color: {c['muted']}; border: 1px solid {c['separator']};
                         border-radius: 7px; padding: 3px 7px; font-size: 8px; font-weight: 850; letter-spacing: .6px; }}
            #modelTag[tone="free"] {{ background: rgba(56, 201, 139, 28); color: #28a975; border-color: rgba(56, 201, 139, 90); }}
            #modelTag[tone="cheap"] {{ background: rgba(50, 145, 255, 25); color: #2580dc; border-color: rgba(50, 145, 255, 85); }}
            #modelTag[tone="balanced"] {{ background: rgba(245, 168, 55, 28); color: #c47a0b; border-color: rgba(245, 168, 55, 95); }}
            #modelTag[tone="speed"] {{ background: rgba(156, 99, 255, 26); color: #8551df; border-color: rgba(156, 99, 255, 90); }}
            #modelTag[tone="quality"] {{ background: rgba(255, 101, 71, 25); color: {c['accent']}; border-color: rgba(255, 101, 71, 95); }}
            #creditIcon {{ background: {c['accent']}; color: {c['accent_text']}; border-radius: 14px; font-size: 22px; font-weight: 800; }}
            #modelSelectButton {{ background: {c['soft']}; border: 1px solid {c['separator']}; }}
            #modelSelectButton:hover {{ border-color: {c['accent']}; }}
            QProgressBar {{ background: {c['soft']}; border: 0; border-radius: 4px; height: 8px; color: transparent; }}
            QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
            #settingsShell {{ background: {c['surface']}; border: 1px solid {c['separator']}; border-radius: 24px; }}
            #settingsSidebar {{ background: {c['chrome']}; border: 0; border-right: 1px solid {c['separator']}; border-radius: 18px; }}
            QListWidget {{ background: transparent; border: 0; outline: 0; padding: 8px; }}
            QListWidget::item {{ color: {c['text']}; padding: 10px 11px; margin: 2px 0; border-radius: 8px; }}
            QListWidget::item:selected {{ background: {c['accent']}; color: {c['accent_text']}; }}
            QListWidget::item:hover:!selected {{ background: {c['hover']}; }}
            #settingsPageTitle {{ font-size: 24px; font-weight: 700; color: {c['text']}; }}
            #settingsGroup {{ background: {c['surface2']}; border: 1px solid {c['separator']}; border-radius: 14px; }}
            QScrollArea {{ border: 0; background: transparent; }}
            QScrollArea > QWidget > QWidget {{ background: transparent; }}
            QScrollBar:vertical {{ width: 7px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {c['separator']}; border-radius: 3px; min-height: 30px; }}

            /* Pastel contour design language */
            #brandButton {{ background: transparent; border: 0; padding: 4px 8px 4px 2px;
                            font-size: 20px; font-weight: 780; text-align: left; }}
            #brandButton:hover {{ background: {c['hover']}; border: 0; }}
            #brandIdentity {{ background: transparent; border: 0; }}
            #creatorCredit {{ color: {c['muted']}; font-size: 12px; font-weight: 720;
                              padding: 0 5px 0 1px; }}
            #socialGithub, #socialLinkedIn {{ color: {c['text']}; border: 2px solid {c['separator']};
                border-radius: 12px; padding: 7px; min-width: 36px; max-width: 36px;
                min-height: 36px; max-height: 36px; }}
            #socialGithub {{ background: transparent; }}
            #socialLinkedIn {{ background: {c['blue']}; }}
            #socialGithub:hover, #socialLinkedIn:hover {{ border-width: 3px; padding: 6px; }}
            #socialGithub:pressed, #socialLinkedIn:pressed {{ padding-top: 9px; padding-bottom: 5px; }}
            #socialGithub:focus, #socialLinkedIn:focus {{ border: 3px solid {c['accent']}; padding: 6px; }}
            #topTools {{ background: transparent; border: 0; }}
            #topIcon, #appearanceSwitch {{ background: {c['surface']}; border: 2px solid {c['separator']};
                                           border-radius: 13px; padding: 8px; min-width: 40px; }}
            #topIcon:hover, #appearanceSwitch:hover {{ background: {c['yellow']}; border-color: {c['separator']}; }}
            #topIcon:pressed, #appearanceSwitch:pressed {{ background: {c['soft']}; padding-top: 10px; }}
            #appearanceSwitch {{ min-width: 40px; max-width: 40px; padding: 0;
                                 text-align: center; qproperty-iconSize: 21px 21px; }}
            #appearanceSwitch:focus, #appearanceSwitch:pressed {{ padding: 0; }}
            #authBanner {{ background: {c['danger']}; border: 2px solid {c['separator']}; border-radius: 16px; }}
            #authBanner QLabel {{ color: {c['text']}; font-size: 13px; font-weight: 720; }}
            #authAction {{ background: {c['surface']}; border: 2px solid {c['separator']}; border-radius: 11px;
                           padding: 8px 16px; font-weight: 800; }}
            #authAction:hover {{ background: {c['yellow']}; }}
            #homePanel, #mainArea {{ background: transparent; border: 0; }}
            QToolButton#launchDictation, QToolButton#launchFile, QToolButton#launchMeeting {{
                                      color: #202321; border: 3px solid #292C2A; border-radius: 28px;
                                      padding: 26px; font-size: 21px; font-weight: 760; }}
            QToolButton#launchDictation {{ background-color: {c['pink']}; }}
            QToolButton#launchFile {{ background-color: {c['blue']}; }}
            QToolButton#launchMeeting {{ background-color: {c['green']}; }}
            QToolButton#launchDictation:hover, QToolButton#launchFile:hover, QToolButton#launchMeeting:hover {{
                border-width: 4px; padding: 25px; }}
            QToolButton#launchDictation:pressed, QToolButton#launchFile:pressed, QToolButton#launchMeeting:pressed {{
                border-width: 4px; padding-top: 31px; padding-bottom: 21px; }}
            QToolButton#launchDictation:disabled, QToolButton#launchFile:disabled, QToolButton#launchMeeting:disabled {{
                                               background-color: {c['soft']}; color: {c['muted']};
                                               border-color: {c['muted']}; }}
            #pageHeading {{ color: {c['text']}; font-size: 25px; font-weight: 780; }}
            #surfaceNav {{ background: transparent; border: 0; }}
            #surfaceTabDictation, #surfaceTabFile, #surfaceTabMeeting {{
                           color: {c['text']}; border: 2px solid {c['separator']}; border-radius: 13px;
                           padding: 10px 18px; font-size: 12px; font-weight: 720; }}
            #surfaceTabDictation {{ background-color: {c['pink']}; }}
            #surfaceTabFile {{ background-color: {c['blue']}; }}
            #surfaceTabMeeting {{ background-color: {c['green']}; }}
            #surfaceTabDictation:hover:!checked, #surfaceTabFile:hover:!checked, #surfaceTabMeeting:hover:!checked {{
                border-width: 3px; padding: 9px 17px; color: #202321; }}
            #surfaceTabDictation:checked, #surfaceTabFile:checked, #surfaceTabMeeting:checked {{
                border: 3px solid {c['separator']}; padding: 9px 17px; color: #202321; }}
            #surfaceModeBar, #actionDock {{ background: {c['surface']}; border: 2px solid {c['separator']}; border-radius: 18px; }}
            #card, #workflowCard, #recentCard, #meetingCard, #fileHero, #settingsShell,
            #settingsGroup {{ background: {c['surface']}; border: 2px solid {c['separator']}; border-radius: 24px; }}
            #heroCopy {{ background: {c['purple']}; border: 2px solid {c['separator']}; border-radius: 20px; padding: 14px; }}
            #signalPanel {{ background: {c['surface2']}; border: 2px solid {c['separator']}; border-radius: 20px; }}
            #voiceVisual {{ background: {c['purple']}; border: 2px solid {c['separator']}; border-radius: 40px; }}
            #record {{ background: {c['purple']}; color: #202321; border: 3px solid #292C2A;
                       border-radius: 20px; font-size: 15px; font-weight: 800; }}
            #record:hover {{ background: {c['accent_hover']}; border-width: 4px; }}
            #record:disabled, #meetingPrimary:disabled, #primaryFile:disabled, #fileDrop:disabled {{
                background: {c['soft']}; color: {c['muted']}; border: 2px solid {c['muted']}; }}
            #meetingCard {{ background: {c['surface']}; }}
            #meetingPrimary {{ background: {c['green']}; color: #202321; border: 3px solid #292C2A;
                               border-radius: 16px; font-weight: 800; }}
            #meetingPrimary:hover {{ background: #95E0A6; border-width: 4px; }}
            #fileDrop {{ background: {c['blue']}; color: #202321; border: 3px solid #292C2A;
                         border-radius: 20px; font-size: 15px; font-weight: 780; }}
            #fileDrop:hover {{ background: #B8E2FA; border-width: 4px; }}
            #primaryFile, #primaryAction {{ background: {c['green']}; color: #202321;
                                           border: 3px solid #292C2A; border-radius: 15px; font-weight: 800; }}
            #primaryFile:hover, #primaryAction:hover {{ background: #95E0A6; border-width: 4px; }}
            #fileOutput, #meetingTranscript, #history {{ background: {c['surface2']}; border: 2px solid {c['separator']};
                                                       border-radius: 18px; padding: 16px; }}
            QLineEdit, QComboBox, QPlainTextEdit, QSpinBox {{ border: 2px solid {c['separator']}; border-radius: 12px; }}
            QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover, QSpinBox:hover {{ border-color: {c['accent']}; }}
            #settingsSidebar {{ background: {c['blue']}; border: 0; border-right: 2px solid #292C2A;
                               border-radius: 22px 0 0 22px; }}
            QListWidget::item {{ border: 2px solid transparent; border-radius: 11px; }}
            QListWidget::item:selected {{ background: {c['yellow']}; color: #202321; border-color: #292C2A; }}
            QListWidget::item:hover:!selected {{ background: rgba(255,255,255,70); border-color: #292C2A; }}
            #settingsPageTitle {{ font-size: 25px; font-weight: 780; }}
            #settingsBack {{ background: {c['pink']}; border: 2px solid {c['separator']}; border-radius: 12px; }}
            #settingsBack:hover {{ background: {c['yellow']}; }}
            #brandButton, #panelTitle, #dictationModePill, #contextButton,
            #launchDictation, #launchFile, #launchMeeting {{
                font-family: 'Segoe Print'; font-weight: 700; }}
            #dictationLeft, #fileLeft {{ background: transparent; border: 0; }}
            #dictationModePill {{ background-color: {c['purple']}; color: #202321;
                                  border: 3px solid #292C2A; border-radius: 18px;
                                  padding: 10px 18px; font-size: 17px; }}
            #audioDeviceBar {{ background-color: {c['surface']}; border: 3px solid #292C2A;
                               border-radius: 18px; }}
            #audioDevicePicker {{ border: 0; background: transparent; color: {c['text']};
                                  padding: 8px 10px; font-size: 12px; font-weight: 720; }}
            #audioDevicePicker:hover, #audioDevicePicker:on {{ border: 0; background: {c['yellow']}; }}
            #dictationModelBar {{ background-color: {c['surface']}; border: 3px solid #292C2A;
                                  border-radius: 18px; }}
            #dictationModelBar QLabel {{ color: {c['muted']}; font-size: 10px; font-weight: 750; }}
            #dictationModelPicker {{ min-height: 34px; background: {c['field']}; color: {c['text']};
                                     border: 2px solid {c['separator']}; border-radius: 11px;
                                     padding: 5px 10px; font-size: 11px; font-weight: 720; }}
            #dictationModelPicker:hover {{ background: {c['yellow']}; border-color: #292C2A; }}
            #dictationModelPicker:on, #dictationModelPicker:focus {{ border-color: {c['accent']}; }}
            #contextButton {{ background-color: {c['mint']}; color: #202321;
                              border: 3px solid #292C2A; border-radius: 18px;
                              font-size: 17px; }}
            #contextButton:hover {{ background-color: #82DCC4; border-width: 4px; }}
            #dictationLeft #card {{ background-color: {c['purple']}; border: 3px solid #292C2A; }}
            #dictationLeft #signalPanel {{ background: transparent; border: 0; }}
            #dictationLeft #voiceVisual {{ background: transparent; border: 0; }}
            #dictationLeft #actionDock {{ background: transparent; border: 0; }}
            #dictationLeft #record {{ background-color: {c['purple']}; border: 3px solid #292C2A; }}
            #recentCard, #resultPanel {{ background-color: {c['surface2']};
                                        border: 3px solid #292C2A; border-radius: 22px; }}
            #panelTitle {{ color: {c['text']}; font-size: 20px; }}
            #fileLeft #fileDrop {{ background-color: {c['blue']}; }}
            #meetingControlPanel {{ background-color: {c['green']}; border: 3px solid #292C2A;
                                     border-radius: 22px; }}
            #livePanel {{ background-color: {c['surface2']}; border: 3px solid #292C2A;
                          border-radius: 22px; }}
            #meetingResult {{ background: transparent; border: 0; padding: 8px; }}
        """

    def apply_theme(self, preference=None):
        theme = self.resolved_appearance(preference)
        self.theme = theme
        stylesheet = self.theme_stylesheet(theme)
        self.app.setStyleSheet(stylesheet)
        self.setStyleSheet(stylesheet)
        if hasattr(self, "theme_tokens"):
            c = self.theme_tokens
            popup_style = f"""
                QAbstractItemView {{ background: {c['surface']}; color: {c['text']};
                    border: 1px solid {c['separator']}; border-radius: 10px;
                    padding: 5px; outline: 0; selection-background-color: {c['accent']};
                    selection-color: {c['accent_text']}; }}
                QAbstractItemView::item {{ color: {c['text']}; min-height: 34px;
                    padding: 5px 10px; border-radius: 7px; }}
                QAbstractItemView::item:selected {{ background: {c['accent']};
                    color: {c['accent_text']}; }}
            """
            for combo in self.findChildren(QComboBox):
                combo.view().setStyleSheet(popup_style)
        if hasattr(self, "background"):
            self.background.set_theme(theme)
        if hasattr(self, "file_wave"):
            self.file_wave.set_theme(theme)
        if "scroll" in self.__dict__:
            self.scroll.viewport().setStyleSheet("background: transparent;")
        if hasattr(self, "history_popup"):
            self.history_popup.set_theme(theme)
        if hasattr(self, "appearance_switch"):
            self.update_appearance_button(self.appearance_preference)
        if hasattr(self, "language_button"):
            colour = self.theme_tokens["text"]
            self.github_button.setIcon(qta.icon("fa6b.github", color=colour))
            self.linkedin_button.setIcon(qta.icon("fa6b.linkedin-in", color=colour))
            self.language_button.setIcon(line_icon("globe", colour, 21))
            self.history_button.setIcon(line_icon("history", colour, 21))
            self.settings_button.setIcon(line_icon(
                "close" if self.settings_box.isVisible() else "settings", colour, 21
            ))
            if hasattr(self, "settings_back"):
                self.settings_back.setIcon(line_icon("back", colour, 18))
            if hasattr(self, "page_home"):
                self.page_home.setIcon(line_icon("home", colour, 19))
            for button_name, icon_name in (
                ("copy_recent_button", "copy"),
                ("file_copy", "copy"),
                ("file_save_txt", "file"),
                ("file_save_srt", "file"),
                ("file_stop", "stop"),
                ("meeting_copy", "copy"),
                ("meeting_open", "file"),
            ):
                button = getattr(self, button_name, None)
                if button is not None:
                    button.setIcon(line_icon(icon_name, colour, 18))
            if hasattr(self, "file_rewind"):
                self.file_rewind.setIcon(line_icon("rewind", colour, 22))
                self.file_forward.setIcon(line_icon("forward", colour, 22))
        if hasattr(self, "record"):
            self.apply_work_mode_visual()
        if hasattr(self, "home_cards"):
            self.refresh_page_chrome()

    def _build_ui(self):
        self.apply_theme(self.conf.get("appearance", "auto"))
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top = QFrame(objectName="top")
        top_l = QHBoxLayout(top)
        self.top_layout = top_l
        top_l.setContentsMargins(24, 12, 22, 12)
        self.home_button = QPushButton("DeYaz", objectName="brandButton")
        self.home_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.home_button.setIcon(app_icon())
        self.home_button.setIconSize(QSize(38, 38))
        self.home_button.setFixedHeight(48)
        self.home_button.clicked.connect(lambda: self.set_main_surface("home"))
        self.brand_identity = QFrame(objectName="brandIdentity")
        brand_l = QHBoxLayout(self.brand_identity)
        self.brand_layout = brand_l
        brand_l.setContentsMargins(0, 0, 0, 0)
        brand_l.setSpacing(7)
        self.creator_credit = QLabel("by Ali Hasanov", objectName="creatorCredit")
        self.github_button = QPushButton(objectName="socialGithub")
        self.github_button.setIcon(qta.icon(
            "fa6b.github", color=self.theme_tokens["text"]
        ))
        self.github_button.setIconSize(QSize(20, 20))
        self.github_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.github_button.setToolTip("GitHub-da ulduz ver")
        self.github_button.setAccessibleName("GitHub-da ulduz ver")
        self.github_star = QLabel(parent=self.github_button)
        self.github_star.setText(chr(0x2605))
        self.github_star.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.github_star.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.github_star.setFixedSize(16, 16)
        self.github_star.setStyleSheet(
            "background: transparent; color: #FFC52F; border: 0; "
            "font-family: 'Segoe UI Symbol'; font-size: 12px; font-weight: 900;"
        )
        self.github_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/hasan0v/deyaz"))
        )
        self.linkedin_button = QPushButton(objectName="socialLinkedIn")
        self.linkedin_button.setIcon(qta.icon(
            "fa6b.linkedin-in", color=self.theme_tokens["text"]
        ))
        self.linkedin_button.setIconSize(QSize(19, 19))
        self.linkedin_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.linkedin_button.setToolTip("LinkedIn-də məni izləyin")
        self.linkedin_button.setAccessibleName("LinkedIn-də məni izləyin")
        self.linkedin_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://www.linkedin.com/in/ali-hasanov"))
        )
        brand_l.addWidget(self.home_button)
        brand_l.addWidget(self.creator_credit)
        brand_l.addWidget(self.github_button)
        brand_l.addWidget(self.linkedin_button)
        # Keep the GitHub control visually native to the header. Only its star
        # breathes softly, so the call to action is visible without a loud tile.
        self.social_animations = []
        star_glow = QGraphicsDropShadowEffect(self.github_star)
        star_glow.setOffset(0, 0)
        star_glow.setBlurRadius(5)
        star_glow.setColor(QColor(255, 197, 47, 220))
        self.github_star.setGraphicsEffect(star_glow)
        star_animation = QPropertyAnimation(star_glow, b"blurRadius", self.github_star)
        star_animation.setDuration(1500)
        star_animation.setKeyValueAt(0.0, 4.0)
        star_animation.setKeyValueAt(0.5, 14.0)
        star_animation.setKeyValueAt(1.0, 4.0)
        star_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        star_animation.setLoopCount(-1)
        star_animation.start()
        self.social_animations.append(star_animation)

        linkedin_glow = QGraphicsDropShadowEffect(self.linkedin_button)
        linkedin_glow.setOffset(0, 0)
        linkedin_glow.setBlurRadius(9)
        linkedin_glow.setColor(QColor(55, 160, 230, 115))
        self.linkedin_button.setGraphicsEffect(linkedin_glow)
        self.eyebrow = QLabel("", objectName="eyebrow")
        self.eyebrow.hide()

        self.appearance_switch = QPushButton(objectName="appearanceSwitch")
        self.appearance_switch.setIconSize(QSize(21, 21))
        self.appearance_switch.clicked.connect(self.toggle_appearance)
        self.update_appearance_button(self.conf.get("appearance", "auto"))

        self.language_button = QPushButton(objectName="topIcon")
        self.language_button.setIcon(line_icon("globe", self.theme_tokens["text"], 21))
        self.language_button.setFixedSize(42, 42)
        self.language_button.setToolTip("Dil")
        self.language_menu = QMenu(self.language_button)
        for label, code in (
            ("Avtomatik", "auto"), ("Azərbaycanca", "az"),
            ("Türkçe", "tr"), ("English", "en"), ("Русский", "ru"),
        ):
            action = self.language_menu.addAction(label)
            action.setProperty("i18n_text", label)
            action.setProperty("language_code", code)
            action.setCheckable(True)
            action.setChecked(code == self.conf.get("ui_language", "auto"))
            action.triggered.connect(
                lambda _checked=False, selected=code: self.header_language_changed(selected)
            )
        self.language_button.setMenu(self.language_menu)

        self.history_button = QPushButton(objectName="topIcon")
        self.history_button.setIcon(line_icon("history", self.theme_tokens["text"], 21))
        self.history_button.setFixedSize(42, 42)
        self.history_button.setToolTip("History")
        self.history_button.clicked.connect(self.open_history_drawer)

        self.settings_button = QPushButton(objectName="topIcon")
        self.settings_button.setIcon(line_icon("settings", self.theme_tokens["text"], 21))
        self.settings_button.setFixedSize(42, 42)
        self.settings_button.setToolTip("Ayarlar")
        self.settings_button.clicked.connect(self.toggle_settings)
        top_l.addWidget(self.brand_identity)
        top_l.addStretch()
        self.appearance_switch.setFixedSize(42, 42)
        tools = QFrame(objectName="topTools")
        tools_l = QHBoxLayout(tools)
        self.tools_layout = tools_l
        tools_l.setContentsMargins(4, 4, 4, 4)
        tools_l.setSpacing(8)
        tools_l.addWidget(self.appearance_switch)
        tools_l.addWidget(self.language_button)
        tools_l.addWidget(self.history_button)
        tools_l.addWidget(self.settings_button)
        top_l.addWidget(tools)
        layout.addWidget(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setStyleSheet("background: transparent;")
        shell = AmbientBackground(self.theme)
        shell.setObjectName("shell")
        self.background = shell
        shell_l = QHBoxLayout(shell)
        self.shell_layout = shell_l
        shell_l.setContentsMargins(26, 32, 26, 38)
        shell_l.addStretch()
        self.content = QWidget()
        self.content.setMaximumWidth(1240)
        self.content.setMinimumWidth(0)
        self.content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body_l = QVBoxLayout(self.content)
        self.body_layout = body_l
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(20)

        # Primary surfaces are peers. Each surface owns its own output mode.
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 2)
        nav_row.addStretch()
        self.surface_nav = QFrame(objectName="surfaceNav")
        surface_nav_l = QHBoxLayout(self.surface_nav)
        surface_nav_l.setContentsMargins(4, 4, 4, 4)
        surface_nav_l.setSpacing(3)
        self.surface_tabs = QButtonGroup(self)
        self.surface_tabs.setExclusive(True)
        self.surface_buttons = {}
        for surface_id, label, icon_name, tip in (
            ("dictation", "DeYaz", "mic", "Mikrofondan danış və hazır mətni aktiv tətbiqə əlavə et"),
            ("file", "File", "file", "Audio və video fayllarını transkripsiya et"),
            ("meeting", "Meeting Notes", "meeting", "Mikrofon və sistem səsindən canlı görüş qeydləri hazırla"),
        ):
            button = QPushButton(
                label, objectName={
                    "dictation": "surfaceTabDictation",
                    "file": "surfaceTabFile",
                    "meeting": "surfaceTabMeeting",
                }[surface_id],
            )
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setProperty("surface", surface_id)
            button.setIcon(line_icon(icon_name))
            button.setToolTip(tip)
            button.setAccessibleName(label)
            button.setText("")
            button.setFixedWidth(56)
            button.setFixedHeight(42)
            button.clicked.connect(
                lambda _checked=False, selected=surface_id: self.set_main_surface(selected)
            )
            self.surface_tabs.addButton(button)
            self.surface_buttons[surface_id] = button
            surface_nav_l.addWidget(button)
        nav_row.addWidget(self.surface_nav)
        nav_row.addStretch()
        body_l.addLayout(nav_row)

        self.dictation_mode_bar = QFrame(objectName="surfaceModeBar")
        dictation_mode_l = QGridLayout(self.dictation_mode_bar)
        dictation_mode_l.setContentsMargins(16, 11, 12, 11)
        dictation_mode_l.setHorizontalSpacing(12)
        dictation_mode_l.setVerticalSpacing(8)
        self.dictation_mode_layout = dictation_mode_l
        dictation_mode_icon = QLabel(objectName="surfaceModeIcon")
        dictation_mode_icon.setPixmap(line_icon(
            "wand", self.theme_tokens["accent"], 18
        ).pixmap(20, 20))
        dictation_mode_copy_widget = QWidget()
        dictation_mode_copy = QVBoxLayout(dictation_mode_copy_widget)
        dictation_mode_copy.setContentsMargins(0, 0, 0, 0)
        dictation_mode_copy.setSpacing(1)
        dictation_mode_copy.addWidget(QLabel("DEYAZ WORK MODE", objectName="flowTitle"))
        dictation_mode_copy.addWidget(QLabel(
            "Danışığın hansı formada hazır mətnə çevriləcəyini seç.",
            objectName="surfaceModeHelp",
        ))
        self.dictation_mode = QComboBox(objectName="modePicker")
        for mode_id, item in all_modes().items():
            if mode_id != "meeting_notes_live":
                self.dictation_mode.addItem(
                    color_icon(item["color"]), i18n.t(item["name"]), mode_id
                )
        selected_dictation_mode = self._dictation_work_mode
        self.dictation_mode.setCurrentIndex(max(
            0, self.dictation_mode.findData(selected_dictation_mode)
        ))
        self.dictation_mode.currentIndexChanged.connect(
            self.dictation_surface_mode_changed
        )
        self.dictation_mode.setMinimumWidth(250)
        self.dictation_mode_widgets = [
            dictation_mode_icon, dictation_mode_copy_widget, self.dictation_mode
        ]
        dictation_mode_l.addWidget(dictation_mode_icon, 0, 0)
        dictation_mode_l.addWidget(dictation_mode_copy_widget, 0, 1)
        dictation_mode_l.addWidget(self.dictation_mode, 0, 2)
        dictation_mode_l.setColumnStretch(1, 1)
        body_l.addWidget(self.dictation_mode_bar)

        self.hero_card = QFrame(objectName="card")
        self.hero_layout = QGridLayout(self.hero_card)
        self.hero_layout.setContentsMargins(40, 38, 32, 38)
        self.hero_layout.setHorizontalSpacing(42)
        self.hero_layout.setVerticalSpacing(22)

        self.hero_copy = QFrame(objectName="heroCopy")
        copy_l = QVBoxLayout(self.hero_copy)
        copy_l.setContentsMargins(0, 0, 0, 0)
        copy_l.setSpacing(11)
        self.mode_badge = QLabel("", objectName="modeBadge")
        self.context_badge = QLabel("AUTO CONTEXT • GÖZLƏYİR", objectName="contextBadge")
        self.status = QLabel("Danış. Qalanını DeYaz etsin.", objectName="status")
        self.status.setWordWrap(True)
        self.detail = QLabel(
            "Danış, DeYaz mətni hazırlayıb işlədiyin tətbiqə əlavə etsin.",
            objectName="muted",
        )
        self.detail.setWordWrap(True)
        badge_row = QHBoxLayout()
        badge_row.setSpacing(9)
        badge_row.addWidget(self.mode_badge)
        badge_row.addWidget(self.context_badge)
        badge_row.addStretch()
        self.shortcut_chip = QLabel(
            f"⌨  {self.conf['windows_hotkey'].upper()}", objectName="shortcutChip"
        )
        self.shortcut_chip.setToolTip("İstənilən tətbiqdən səsyazmanı başladıb dayandır")
        copy_l.addLayout(badge_row)
        copy_l.addSpacing(7)
        copy_l.addWidget(self.status)
        copy_l.addWidget(self.detail)
        copy_l.addStretch()
        copy_l.addWidget(self.shortcut_chip, alignment=Qt.AlignmentFlag.AlignLeft)

        self.signal_panel = QFrame(objectName="signalPanel")
        signal_l = QVBoxLayout(self.signal_panel)
        signal_l.setContentsMargins(24, 21, 24, 20)
        signal_l.setSpacing(12)
        self.signal_title = QLabel("SƏS GİRİŞİ", objectName="orbHint",
                                   alignment=Qt.AlignmentFlag.AlignCenter)
        self.meter = QFrame()
        self.meter.setFixedHeight(6)
        self.meter.setStyleSheet(
            f"background: {self.theme_tokens['soft']}; border-radius: 3px;"
        )
        self.record = RecordButton("Səsyazmaya başla")
        self.record.setObjectName("record")
        self.record.setIcon(line_icon("mic", "#21100c", 22))
        self.record.setToolTip("Səsyazmanı başlat")
        self.record.clicked.connect(self.toggle_recording)
        self.voice_visual = QLabel(objectName="voiceVisual")
        self.voice_visual.setPixmap(line_icon(
            "mic", self.theme_tokens["muted"], 34
        ).pixmap(46, 46))
        self.voice_visual.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.voice_visual.setFixedSize(82, 82)
        self.signal_hint = QLabel(
            "Mikrofon hazırdır", objectName="muted",
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        signal_l.addWidget(self.signal_title)
        signal_l.addStretch()
        signal_l.addWidget(self.voice_visual, alignment=Qt.AlignmentFlag.AlignCenter)
        signal_l.addWidget(self.signal_hint)
        signal_l.addStretch()
        signal_l.addWidget(self.meter)
        self.hero_layout.addWidget(self.hero_copy, 0, 0)
        self.hero_layout.addWidget(self.signal_panel, 0, 1)
        self.hero_layout.setColumnStretch(0, 3)
        self.hero_layout.setColumnStretch(1, 2)
        self.hero_shadow = QGraphicsDropShadowEffect(self.hero_card)
        self.hero_shadow.setOffset(0, 18)
        self.hero_shadow.setBlurRadius(52)
        self.hero_shadow.setColor(QColor(0, 0, 0, 72))
        self.hero_card.setGraphicsEffect(self.hero_shadow)
        body_l.addWidget(self.hero_card)

        self.workflow_card = QFrame(objectName="workflowCard")
        workflow_l = QVBoxLayout(self.workflow_card)
        workflow_l.setContentsMargins(22, 20, 22, 22)
        workflow_l.setSpacing(16)
        workflow_head = QHBoxLayout()
        workflow_head_copy = QVBoxLayout()
        workflow_head_copy.setSpacing(3)
        workflow_head_copy.addWidget(QLabel("DEYAZ AXINI", objectName="sectionEyebrow"))
        workflow_head_copy.addWidget(QLabel(
            "Səsin hansı addımlarla hazır mətnə çevrildiyini bir baxışda gör.",
            objectName="sectionDescription",
        ))
        self.workflow_status = QLabel("●  Sistem hazırdır", objectName="flowStatus")
        workflow_head.addLayout(workflow_head_copy)
        workflow_head.addStretch()
        workflow_head.addWidget(self.workflow_status)
        workflow_l.addLayout(workflow_head)

        self.quick_grid = QGridLayout()
        self.quick_grid.setContentsMargins(0, 0, 0, 0)
        self.quick_grid.setHorizontalSpacing(12)
        self.quick_grid.setVerticalSpacing(12)

        def flow_step(number, icon_name, title, value, description, control):
            panel = QFrame(objectName="flowStep")
            panel_l = QVBoxLayout(panel)
            panel_l.setContentsMargins(16, 15, 16, 15)
            panel_l.setSpacing(9)
            step_head = QHBoxLayout()
            index_label = QLabel(str(number), objectName="stepIndex",
                                 alignment=Qt.AlignmentFlag.AlignCenter)
            index_label.setFixedSize(25, 25)
            icon_label = QLabel(objectName="flowIcon")
            icon_label.setPixmap(line_icon(
                icon_name, self.theme_tokens["accent"], 18
            ).pixmap(20, 20))
            step_head.addWidget(index_label)
            step_head.addStretch()
            step_head.addWidget(icon_label)
            panel_l.addLayout(step_head)
            panel_l.addWidget(QLabel(title, objectName="flowTitle"))
            value_label = QLabel(value, objectName="quickValue")
            value_label.setWordWrap(True)
            panel_l.addWidget(value_label)
            help_label = QLabel(description, objectName="flowHelp")
            help_label.setWordWrap(True)
            panel_l.addWidget(help_label)
            panel_l.addStretch()
            panel_l.addWidget(control)
            return panel, value_label

        self.quick_hotkey = QComboBox(objectName="quickControl")
        for shortcut_text in ["Ctrl+Alt+R", "Ctrl+Shift+Space", "Alt+Space"]:
            self.quick_hotkey.addItem(shortcut_text, shortcut_text)
        self.quick_hotkey.setCurrentIndex(max(
            0, self.quick_hotkey.findData(self.conf["windows_hotkey"])
        ))
        self.quick_hotkey.currentIndexChanged.connect(self.quick_hotkey_changed)

        context_control = QWidget(objectName="quickControl")
        context_control_l = QHBoxLayout(context_control)
        context_control_l.setContentsMargins(0, 0, 0, 0)
        context_control_l.setSpacing(6)
        self.quick_context = QCheckBox("Aktiv")
        self.quick_context.setChecked(self.conf["context_enabled"])
        self.quick_context.toggled.connect(self.quick_context_changed)
        context_pick_main = QPushButton("Qovluq")
        context_pick_main.clicked.connect(self.browse_context_folder)
        context_control_l.addWidget(self.quick_context)
        context_control_l.addWidget(context_pick_main)
        context_control_l.addStretch()

        self.quick_paste = QCheckBox("Aktiv", objectName="quickControl")
        self.quick_paste.setChecked(self.conf["auto_paste"])
        self.quick_paste.toggled.connect(self.quick_paste_changed)

        self.hotkey_card, self.hotkey_value = flow_step(
            1, "keyboard", "BAŞLAT", self.conf["windows_hotkey"].upper(),
            "İstənilən tətbiqdə səs yazmanı dərhal başladır.", self.quick_hotkey
        )
        self.context_card, self.context_value = flow_step(
            2, "wand", "ANLA",
            "Avtomatik" if self.conf["context_enabled"] else "Sönülü",
            "Aktiv layihəni yoxlayıb mətni düzgün kontekstdə hazırlayır.",
            context_control,
        )
        self.paste_card, self.paste_value = flow_step(
            3, "file", "YERLƏŞDİR",
            "Aktivdir" if self.conf["auto_paste"] else "Sönülü",
            "Hazır nəticəni işlədiyin input sahəsinə əlavə edir.",
            self.quick_paste,
        )
        self.quick_cards = [self.hotkey_card, self.context_card, self.paste_card]
        for index, panel in enumerate(self.quick_cards):
            self.quick_grid.addWidget(panel, 0, index)
            self.quick_grid.setColumnStretch(index, 1)
        workflow_l.addLayout(self.quick_grid)
        body_l.addWidget(self.workflow_card)

        self.meeting_card = QFrame(objectName="meetingCard")
        meeting_l = QVBoxLayout(self.meeting_card)
        meeting_l.setContentsMargins(24, 22, 24, 24)
        meeting_l.setSpacing(15)
        meeting_head = QHBoxLayout()
        meeting_title_l = QVBoxLayout()
        meeting_title_l.setSpacing(3)
        meeting_title_l.addWidget(QLabel("MEETING NOTES", objectName="sectionEyebrow"))
        meeting_title_l.addWidget(QLabel(
            "Mikrofon və görüş səsi ayrıca dinlənir, transkript canlı yenilənir.",
            objectName="sectionDescription",
        ))
        self.meeting_state = QLabel("Hazır", objectName="meetingState")
        self.meeting_elapsed = QLabel("00:00", objectName="meetingClock")
        meeting_head.addLayout(meeting_title_l)
        meeting_head.addStretch()
        meeting_head.addWidget(self.meeting_state)
        meeting_head.addWidget(self.meeting_elapsed)
        meeting_l.addLayout(meeting_head)

        meeting_mode_bar = QFrame(objectName="surfaceModeBar")
        self.meeting_mode_bar = meeting_mode_bar
        meeting_mode_l = QGridLayout(meeting_mode_bar)
        meeting_mode_l.setContentsMargins(14, 10, 10, 10)
        meeting_mode_l.setHorizontalSpacing(12)
        meeting_mode_l.setVerticalSpacing(8)
        self.meeting_mode_layout = meeting_mode_l
        meeting_mode_copy_widget = QWidget()
        self.meeting_mode_copy_widget = meeting_mode_copy_widget
        meeting_mode_copy = QVBoxLayout(meeting_mode_copy_widget)
        meeting_mode_copy.setContentsMargins(0, 0, 0, 0)
        meeting_mode_copy.setSpacing(1)
        meeting_mode_copy.addWidget(QLabel("MEETING NƏTİCƏSİ", objectName="flowTitle"))
        meeting_mode_copy.addWidget(QLabel(
            "Görüş bitəndə DeYaz-ın nə hazırlayacağını seç.",
            objectName="surfaceModeHelp",
        ))
        self.meeting_result_type = QComboBox(objectName="modePicker")
        for label, value in (
            ("Görüş xülasəsi", "meeting_notes"),
            ("Tam transkript", "transcript"),
            ("Əsas məqamlar", "key_points"),
            ("Ətraflı icmal", "detailed_summary"),
            ("Tapşırıqlar", "action_items"),
        ):
            self.meeting_result_type.addItem(label, value)
        self.meeting_result_type.setCurrentIndex(max(
            0, self.meeting_result_type.findData(
                self.conf.get("meeting_result_type", "meeting_notes")
            )
        ))
        self.meeting_result_type.currentIndexChanged.connect(
            self.meeting_result_type_changed
        )
        self.meeting_result_type.setMinimumWidth(340)
        self.meeting_mode_widgets = [meeting_mode_copy_widget, self.meeting_result_type]
        meeting_mode_l.addWidget(meeting_mode_copy_widget, 0, 0)
        meeting_mode_l.addWidget(self.meeting_result_type, 0, 1)
        meeting_mode_l.setColumnStretch(0, 1)
        meeting_l.addWidget(meeting_mode_bar)

        meeting_models = QFrame(objectName="surfaceModeBar")
        self.meeting_models_panel = meeting_models
        meeting_models_l = QGridLayout(meeting_models)
        self.meeting_models_layout = meeting_models_l
        meeting_models_l.setContentsMargins(14, 12, 14, 12)
        meeting_models_l.setHorizontalSpacing(12)
        meeting_models_l.setVerticalSpacing(8)
        meeting_models.setMinimumHeight(315)
        self.meeting_model_labels = (
            QLabel("DİL", objectName="flowTitle"),
            QLabel("CANLI MODEL", objectName="flowTitle"),
            QLabel("QEYD MODELİ", objectName="flowTitle"),
            QLabel("NƏTİCƏ DİLİ", objectName="flowTitle"),
            QLabel("MİKROFON", objectName="flowTitle"),
        )
        self.meeting_input_language = QComboBox(objectName="modePicker")
        for label, value in (
            ("Avtomatik tanı", "auto"), ("Azərbaycanca", "az"),
            ("English", "en"), ("Türkçe", "tr"), ("Русский", "ru"),
        ):
            self.meeting_input_language.addItem(label, value)
        self.meeting_input_language.setCurrentIndex(max(
            0, self.meeting_input_language.findData(
                self.conf.get("meeting_language", "auto") or "auto"
            )
        ))
        self.meeting_transcribe_model = QComboBox(objectName="modePicker")
        for badge, name, provider, model, description in MEETING_LIVE_TRANSCRIPTION_CHOICES:
            self.meeting_transcribe_model.addItem(
                name, f"{provider}|{model}"
            )
            self.meeting_transcribe_model.setItemData(
                self.meeting_transcribe_model.count() - 1, description,
                Qt.ItemDataRole.ToolTipRole,
            )
        live_value = (
            f"{self.conf.get('meeting_transcribe_provider', 'openai')}|"
            f"{self.conf.get('meeting_transcribe_model', 'gpt-transcribe')}"
        )
        self.meeting_transcribe_model.setCurrentIndex(max(
            0, self.meeting_transcribe_model.findData(live_value)
        ))
        self.meeting_text_model = QComboBox(objectName="modePicker")
        for badge, name, provider, model, description in MEETING_TEXT_CHOICES:
            self.meeting_text_model.addItem(
                name, f"{provider}|{model}"
            )
            self.meeting_text_model.setItemData(
                self.meeting_text_model.count() - 1, description,
                Qt.ItemDataRole.ToolTipRole,
            )
        text_value = (
            f"{self.conf.get('meeting_text_provider', 'openai')}|"
            f"{self.conf.get('meeting_text_model', 'gpt-5.6-terra')}"
        )
        text_index = self.meeting_text_model.findData(text_value)
        if text_index < 0:
            self.meeting_text_model.addItem(
                f"{i18n.t('FƏRDİ')} · {self.conf.get('meeting_text_model', 'gpt-5.6-terra')}",
                text_value,
            )
            text_index = self.meeting_text_model.count() - 1
        self.meeting_text_model.setCurrentIndex(text_index)
        self.meeting_output_language = QComboBox(objectName="modePicker")
        for label, value in (
            ("Orijinal dil · tərcümə etmə", "original"),
            ("Azərbaycan dili", "az"),
            ("English", "en"),
            ("Türkçe", "tr"),
            ("Русский", "ru"),
        ):
            self.meeting_output_language.addItem(label, value)
        self.meeting_output_language.setCurrentIndex(max(
            0, self.meeting_output_language.findData(
                self.conf.get("meeting_live_output_language", "original")
            )
        ))
        self.meeting_microphone = QComboBox(objectName="modePicker")
        self._load_meeting_microphones(self.meeting_microphone)
        for combo in (
            self.meeting_input_language,
            self.meeting_transcribe_model,
            self.meeting_text_model,
            self.meeting_output_language,
        ):
            combo.currentIndexChanged.connect(self.meeting_models_changed)
        controls = (
            self.meeting_input_language, self.meeting_transcribe_model,
            self.meeting_text_model, self.meeting_output_language,
            self.meeting_microphone,
        )
        for index, (label, control) in enumerate(zip(self.meeting_model_labels, controls)):
            meeting_models_l.addWidget(label, index * 2, 0)
            meeting_models_l.addWidget(control, index * 2 + 1, 0)
            control.setMinimumWidth(0)
            control.setMinimumContentsLength(8)
            control.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            control.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
            )
        meeting_models_l.setColumnStretch(0, 1)
        self.meeting_model_controls = (
            self.meeting_input_language,
            self.meeting_transcribe_model,
            self.meeting_text_model,
            self.meeting_output_language,
            self.meeting_microphone,
        )
        self.meeting_microphone.currentIndexChanged.connect(
            self.meeting_microphone_changed
        )
        meeting_l.addWidget(meeting_models)

        source_row = QGridLayout()
        source_row.setSpacing(10)
        self.meeting_source_layout = source_row
        self.meeting_mic_source = QLabel("●  Sən · Mikrofon", objectName="sourceChip")
        self.meeting_system_source = QLabel(
            "●  Görüş səsi · Sistem audio", objectName="sourceChip"
        )
        self.meeting_mic_level = QProgressBar(objectName="sourceLevel")
        self.meeting_system_level = QProgressBar(objectName="sourceLevel")
        for level in (self.meeting_mic_level, self.meeting_system_level):
            level.setRange(0, 100)
            level.setValue(0)
            level.setTextVisible(False)
            level.setFixedWidth(72)
        self.meeting_source_widgets = [
            self.meeting_mic_source, self.meeting_mic_level,
            self.meeting_system_source, self.meeting_system_level,
        ]
        source_row.addWidget(self.meeting_mic_source, 0, 0)
        source_row.addWidget(self.meeting_mic_level, 0, 1)
        source_row.addWidget(self.meeting_system_source, 0, 2)
        source_row.addWidget(self.meeting_system_level, 0, 3)
        source_row.setColumnStretch(4, 1)
        meeting_l.addLayout(source_row)
        speaker_note = QLabel(
            "Kanallar mənbəyə görə ayrılır: mikrofon “Sən”, kompüter səsi “Görüş səsi”. "
            "Uzaq iştirakçılar ayrıca Speaker 1/2/3 kimi uydurulmur.",
            objectName="flowHelp",
        )
        speaker_note.setWordWrap(True)
        self.meeting_speaker_note = speaker_note
        meeting_l.addWidget(speaker_note)

        self.meeting_transcript = QPlainTextEdit(objectName="meetingTranscript")
        self.meeting_transcript.setReadOnly(True)
        self.meeting_transcript.setMinimumHeight(210)
        self.meeting_transcript.setPlaceholderText(
            "Görüş başlayanda canlı transkript burada görünəcək…"
        )
        meeting_l.addWidget(self.meeting_transcript)

        meeting_action_dock = QFrame(objectName="actionDock")
        meeting_actions = QGridLayout(meeting_action_dock)
        meeting_actions.setContentsMargins(14, 12, 12, 12)
        meeting_actions.setHorizontalSpacing(9)
        meeting_actions.setVerticalSpacing(9)
        self.meeting_actions_layout = meeting_actions
        self.meeting_action = QPushButton("Görüşü başlat", objectName="meetingPrimary")
        self.meeting_action.setIcon(line_icon("mic", "#10211f", 19))
        self.meeting_action.clicked.connect(self.toggle_meeting)
        self.meeting_action_shadow = QGraphicsDropShadowEffect(self.meeting_action)
        self.meeting_action_shadow.setBlurRadius(22)
        self.meeting_action_shadow.setOffset(0, 7)
        self.meeting_action_shadow.setColor(QColor(31, 199, 184, 72))
        self.meeting_action.setGraphicsEffect(self.meeting_action_shadow)
        self.meeting_keep_audio = QCheckBox("Audio faylını da saxla")
        self.meeting_keep_audio.setChecked(bool(self.conf["meeting_keep_audio"]))
        self.meeting_copy = QPushButton("Mətni kopyala", objectName="secondaryAction")
        self.meeting_copy.clicked.connect(self.copy_meeting_text)
        self.meeting_open = QPushButton("Qeydi aç", objectName="secondaryAction")
        self.meeting_open.clicked.connect(self.open_last_meeting)
        self.meeting_open.setEnabled(False)
        self.meeting_action_widgets = [
            self.meeting_action, self.meeting_keep_audio,
            self.meeting_copy, self.meeting_open,
        ]
        meeting_actions.addWidget(self.meeting_action, 0, 0)
        meeting_actions.addWidget(self.meeting_keep_audio, 0, 1)
        meeting_actions.setColumnStretch(2, 1)
        meeting_actions.addWidget(self.meeting_copy, 0, 3)
        meeting_actions.addWidget(self.meeting_open, 0, 4)
        self.meeting_action.setMinimumWidth(210)
        self.meeting_action.setMinimumHeight(52)
        meeting_l.addWidget(meeting_action_dock)
        self.meeting_card.hide()
        body_l.addWidget(self.meeting_card)

        self.recent_card = QFrame(objectName="recentCard")
        self.recent_layout = QGridLayout(self.recent_card)
        self.recent_layout.setContentsMargins(20, 16, 18, 16)
        self.recent_layout.setHorizontalSpacing(16)
        self.recent_layout.setVerticalSpacing(10)
        self.recent_copy = QWidget()
        recent_copy_l = QVBoxLayout(self.recent_copy)
        recent_copy_l.setContentsMargins(0, 0, 0, 0)
        recent_copy_l.setSpacing(5)
        recent_head = QHBoxLayout()
        recent_head.addWidget(QLabel("SON NƏTİCƏ", objectName="sectionEyebrow"))
        recent_head.addStretch()
        self.recent_time = QLabel("", objectName="recentTime")
        recent_head.addWidget(self.recent_time)
        recent_copy_l.addLayout(recent_head)
        self.recent_preview = QLabel(
            "İlk transkriptin burada görünəcək.", objectName="recentPreview"
        )
        self.recent_preview.setWordWrap(True)
        recent_copy_l.addWidget(self.recent_preview)
        recent_actions = QHBoxLayout()
        self.copy_recent_button = QPushButton("Kopyala", objectName="secondaryAction")
        self.copy_recent_button.clicked.connect(self.copy_recent_result)
        self.recent_history_button = QPushButton(
            "Tarixçəni aç", objectName="secondaryAction"
        )
        self.recent_history_button.clicked.connect(self.open_history_tab)
        recent_actions.addWidget(self.copy_recent_button)
        recent_actions.addWidget(self.recent_history_button)
        recent_actions.addStretch()
        recent_copy_l.addLayout(recent_actions)
        self.recent_layout.addWidget(self.recent_copy, 0, 0)
        self.recent_layout.setColumnStretch(0, 1)
        body_l.addWidget(self.recent_card)

        self.dictation_action_bar = QFrame(objectName="actionDock")
        dictation_action_l = QHBoxLayout(self.dictation_action_bar)
        dictation_action_l.setContentsMargins(18, 14, 14, 14)
        dictation_action_l.setSpacing(14)
        action_copy = QVBoxLayout()
        action_copy.setSpacing(2)
        action_copy.addWidget(QLabel("DANIŞMAĞA HAZIRSAN", objectName="actionTitle"))
        action_copy.addWidget(QLabel(
            "Düyməni bas və ya qısa yoldan istifadə et.", objectName="actionHelp"
        ))
        self.record.setMinimumWidth(260)
        self.record.setFixedHeight(58)
        dictation_action_l.addLayout(action_copy, 1)
        dictation_action_l.addWidget(self.record)
        body_l.addWidget(self.dictation_action_bar)

        self.settings_box = QFrame(objectName="settingsShell")
        settings_l = QHBoxLayout(self.settings_box)
        settings_l.setContentsMargins(0, 0, 0, 0)
        settings_l.setSpacing(0)

        self.settings_sidebar = QFrame(objectName="settingsSidebar")
        self.settings_sidebar.setFixedWidth(210)
        sidebar_l = QVBoxLayout(self.settings_sidebar)
        sidebar_l.setContentsMargins(14, 20, 14, 16)
        sidebar_l.setSpacing(12)
        sidebar_title = QLabel("DeYaz", objectName="brand")
        sidebar_hint = QLabel("Ayarlar", objectName="eyebrow")
        self.settings_nav = QListWidget()
        self.settings_nav.setIconSize(QSize(18, 18))
        self.settings_nav.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        sidebar_l.addWidget(sidebar_title)
        sidebar_l.addWidget(sidebar_hint)
        sidebar_l.addSpacing(4)
        sidebar_l.addWidget(self.settings_nav, 1)

        settings_detail = QWidget()
        self.settings_detail_layout = QVBoxLayout(settings_detail)
        detail_l = self.settings_detail_layout
        detail_l.setContentsMargins(28, 24, 28, 24)
        detail_l.setSpacing(16)
        self.settings_mobile_nav = QComboBox()
        self.settings_mobile_nav.hide()
        settings_title_row = QHBoxLayout()
        settings_title_row.setSpacing(10)
        self.settings_back = QPushButton(objectName="settingsBack")
        self.settings_back.setIcon(line_icon("back", self.theme_tokens["text"], 18))
        self.settings_back.setFixedSize(40, 40)
        self.settings_back.setToolTip("Geri")
        self.settings_back.clicked.connect(self.toggle_settings)
        self.settings_page_title = QLabel("Ümumi", objectName="settingsPageTitle")
        settings_title_row.addWidget(self.settings_back)
        settings_title_row.addWidget(self.settings_page_title)
        settings_title_row.addStretch()
        self.settings_pages = QStackedWidget()
        self.settings_pages.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.settings_tabs = self.settings_pages
        self.settings_page_sources = []
        self.settings_forms = []
        detail_l.addWidget(self.settings_mobile_nav)
        detail_l.addLayout(settings_title_row)
        detail_l.addWidget(self.settings_pages, 1)

        save = QPushButton("Ayarları yadda saxla", objectName="primaryFile")
        save.setIcon(line_icon("settings", "#22120d"))
        save.clicked.connect(self.save_settings)
        detail_l.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)
        settings_l.addWidget(self.settings_sidebar)
        settings_l.addWidget(settings_detail, 1)

        def add_settings_page(page, title, icon_name):
            page.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            self.settings_page_sources.append(title)
            item = QListWidgetItem(
                line_icon(icon_name, self.theme_tokens["muted"]), title
            )
            item.setSizeHint(QSize(170, 42))
            self.settings_nav.addItem(item)
            self.settings_mobile_nav.addItem(
                line_icon(icon_name, self.theme_tokens["muted"]), title
            )
            self.settings_pages.addWidget(page)

        def form_page():
            page = QWidget()
            page.setObjectName("settingsGroup")
            form = QFormLayout(page)
            form.setContentsMargins(18, 18, 18, 18)
            form.setHorizontalSpacing(20)
            form.setVerticalSpacing(13)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            self.settings_forms.append(form)
            return page, form

        # General
        general, general_form = form_page()
        self.ui_language = QComboBox()
        for label, code in [
            ("Auto · Sistem dili", "auto"),
            ("AZ · Azərbaycanca", "az"),
            ("TR · Türkçe", "tr"),
            ("EN · English", "en"),
            ("RU · Русский", "ru"),
        ]:
            self.ui_language.addItem(label, code)
        self.ui_language.setCurrentIndex(max(
            0, self.ui_language.findData(self.conf["ui_language"])
        ))
        self.ui_language.currentIndexChanged.connect(self.ui_language_changed)
        self.language = QComboBox()
        for label, code in [("Azərbaycanca", "az"), ("English", "en"),
                            ("Türkçe", "tr"), ("Avtomatik", "auto")]:
            self.language.addItem(label, code)
        self.language.setCurrentIndex(max(0, self.language.findData(self.conf["language"])))
        self.microphone = QComboBox()
        self._load_dictation_microphones(self.microphone)
        self.microphone.currentIndexChanged.connect(
            self.settings_microphone_changed
        )
        self.auto_paste = QCheckBox("Hazır mətni aktiv inputa avtomatik əlavə et")
        self.auto_paste.setChecked(self.conf["auto_paste"])
        self.context_enabled = QCheckBox(
            "Xüsusi mode-larda aktiv app və layihə kontekstini əlavə et"
        )
        self.context_enabled.setChecked(self.conf["context_enabled"])
        context_field = QWidget()
        context_l = QHBoxLayout(context_field)
        context_l.setContentsMargins(0, 0, 0, 0)
        context_l.setSpacing(8)
        self.context_dir = QLineEdit(self.conf["context_project_dir"])
        self.context_dir.setPlaceholderText("")
        context_pick = QPushButton("Seç")
        context_pick.clicked.connect(self.browse_context_folder)
        context_l.addWidget(self.context_dir, 1)
        context_l.addWidget(context_pick)
        self.keep_audio = QCheckBox("Səs fayllarını saxla")
        self.keep_audio.setChecked(self.conf["keep_audio"])
        self.history_limit = QSpinBox()
        self.history_limit.setRange(20, 5000)
        self.history_limit.setValue(int(self.conf["history_limit"]))
        general_form.addRow("İnterfeys dili", self.ui_language)
        general_form.addRow("Danışıq dili", self.language)
        general_form.addRow("Mikrofon", self.microphone)
        general_form.addRow("", self.auto_paste)
        general_form.addRow("", self.context_enabled)
        general_form.addRow("Kontekst qovluğu", context_field)
        general_form.addRow("", self.keep_audio)
        general_form.addRow("Tarixçə limiti", self.history_limit)
        add_settings_page(general, "Ümumi", "settings")

        # API and models
        api_page, api_form = form_page()
        self.provider = QComboBox()
        self.provider.addItem("OpenAI", "openai")
        self.provider.addItem("OpenRouter", "openrouter")
        self.provider.setCurrentIndex(max(
            0, self.provider.findData(self.conf["transcribe_provider"])
        ))
        self.provider.currentIndexChanged.connect(self.provider_changed)
        self.openai = QLineEdit(self.conf["openai_api_key"])
        self.openai.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai.setPlaceholderText("OpenAI API açarı")
        self.openai.textChanged.connect(self.refresh_auth_gate)
        self.openrouter = QLineEdit(self.conf["openrouter_api_key"])
        self.openrouter.setEchoMode(QLineEdit.EchoMode.Password)
        self.openrouter.setPlaceholderText("Alternativ: API açarını manual daxil et")
        self.openrouter.textChanged.connect(self.refresh_auth_gate)
        self.oauth_worker = OpenRouterOAuth()
        self.oauth_worker.connected.connect(self.openrouter_oauth_connected)
        self.oauth_worker.failed.connect(self.openrouter_oauth_failed)
        self.account_worker = OpenRouterAccountStatus()
        self.account_worker.finished.connect(self.openrouter_account_loaded)
        self.account_worker.failed.connect(self.openrouter_account_failed)
        self.oauth_card = QFrame(objectName="oauthCard")
        oauth_l = QVBoxLayout(self.oauth_card)
        oauth_l.setContentsMargins(18, 16, 18, 16)
        oauth_l.setSpacing(10)
        oauth_head = QHBoxLayout()
        oauth_copy = QVBoxLayout()
        oauth_copy.setSpacing(3)
        oauth_title = QLabel("OpenRouter hesabı", objectName="oauthTitle")
        oauth_hint = QLabel(
            "Brauzerdə daxil olun — API açarı avtomatik və təhlükəsiz saxlanacaq.",
            objectName="oauthHint",
        )
        oauth_hint.setWordWrap(True)
        oauth_copy.addWidget(oauth_title)
        oauth_copy.addWidget(oauth_hint)
        self.oauth_connect = QPushButton("OpenRouter ilə qoşul", objectName="primaryAction")
        self.oauth_connect.clicked.connect(self.connect_openrouter)
        oauth_head.addLayout(oauth_copy, 1)
        oauth_head.addWidget(self.oauth_connect)
        oauth_l.addLayout(oauth_head)
        oauth_status_l = QHBoxLayout()
        oauth_status_l.setSpacing(8)
        self.oauth_dot = QLabel("●", objectName="oauthDot")
        self.oauth_status = QLabel(objectName="oauthStatus")
        self.oauth_disconnect = QPushButton("Əlaqəni kəs", objectName="quietDanger")
        self.oauth_disconnect.clicked.connect(self.disconnect_openrouter)
        oauth_status_l.addWidget(self.oauth_dot)
        oauth_status_l.addWidget(self.oauth_status, 1)
        oauth_status_l.addWidget(self.oauth_disconnect)
        oauth_l.addLayout(oauth_status_l)

        self.account_card = QFrame(objectName="accountCard")
        self.account_card.setProperty("state", "loading")
        account_l = QHBoxLayout(self.account_card)
        account_l.setContentsMargins(13, 11, 11, 11)
        account_l.setSpacing(10)
        self.account_icon = QLabel("↗", objectName="accountIcon",
                                   alignment=Qt.AlignmentFlag.AlignCenter)
        self.account_icon.setFixedSize(34, 34)
        account_copy = QVBoxLayout()
        account_copy.setSpacing(2)
        self.account_title = QLabel("Balans yoxlanılır…", objectName="accountTitle")
        self.account_detail = QLabel(
            "OpenRouter hesab vəziyyəti alınır", objectName="accountDetail"
        )
        self.account_detail.setWordWrap(True)
        account_copy.addWidget(self.account_title)
        account_copy.addWidget(self.account_detail)
        self.account_credit = QPushButton("Kredit əlavə et", objectName="accountAction")
        self.account_credit.clicked.connect(self.open_openrouter_credits)
        account_l.addWidget(self.account_icon)
        account_l.addLayout(account_copy, 1)
        account_l.addWidget(self.account_credit)
        oauth_l.addWidget(self.account_card)
        self.refresh_openrouter_connection()
        self.transcribe_model = QLineEdit(
            self.conf["openrouter_transcribe_model"]
            if self.conf["transcribe_provider"] == "openrouter"
            else self.conf["transcribe_model"]
        )
        transcribe_model_field = QWidget()
        transcribe_model_l = QHBoxLayout(transcribe_model_field)
        transcribe_model_l.setContentsMargins(0, 0, 0, 0)
        transcribe_model_l.setSpacing(8)
        choose_models = QPushButton("Hər iki modeli seç", objectName="modelSelectButton")
        choose_models.setIcon(line_icon("wand"))
        choose_models.clicked.connect(self.show_model_selector)
        transcribe_model_l.addWidget(self.transcribe_model, 1)
        transcribe_model_l.addWidget(choose_models)
        initial_cleanup_model = (
            self.conf["openai_cleanup_model"]
            if self.conf["cleanup_provider"] == "openai"
            else self.conf["openrouter_cleanup_model"]
        )
        self.text_model_display = QLineEdit(initial_cleanup_model)
        self.text_model_display.setReadOnly(True)
        self.text_model_display.setToolTip(
            "Transkripsiyadan sonra mətni təmizləyən və iş mode-unu tətbiq edən model"
        )
        api_form.addRow("Transkripsiya provider-i", self.provider)
        api_form.addRow(self.oauth_card)
        api_form.addRow("OpenAI API key", self.openai)
        api_form.addRow("Manual OpenRouter key", self.openrouter)
        api_form.addRow("Transkripsiya modeli", transcribe_model_field)
        api_form.addRow("Mətn modeli", self.text_model_display)
        add_settings_page(api_page, "API", "wand")

        # Modify / cleanup rules
        modify, modify_form = form_page()
        self.work_mode = QComboBox()
        for mode_id, item in all_modes().items():
            if mode_id == "meeting_notes_live":
                continue
            self.work_mode.addItem(
                color_icon(item["color"]), item["name"], mode_id
            )
        self.work_mode.setCurrentIndex(max(
            0, self.work_mode.findData(self.conf["work_mode"])
        ))
        self.work_mode.currentIndexChanged.connect(self.work_mode_changed)
        mode_actions = QWidget()
        mode_actions_l = QHBoxLayout(mode_actions)
        mode_actions_l.setContentsMargins(0, 0, 0, 0)
        mode_actions_l.setSpacing(7)
        add_mode = QPushButton("＋ Yeni mode")
        edit_mode = QPushButton("Redaktə et")
        delete_mode = QPushButton("Sil")
        add_mode.clicked.connect(self.add_work_mode)
        edit_mode.clicked.connect(self.edit_work_mode)
        delete_mode.clicked.connect(self.delete_work_mode)
        mode_actions_l.addWidget(add_mode)
        mode_actions_l.addWidget(edit_mode)
        mode_actions_l.addWidget(delete_mode)
        mode_actions_l.addStretch()
        self.modify_preset = QComboBox()
        self.modify_preset.addItem("Minimal — yalnız durğu və təkrarlar", "minimal")
        self.modify_preset.addItem("Balanced — təmiz və təbii", "balanced")
        self.modify_preset.addItem("Polished — daha səliqəli yazı", "polished")
        self.modify_preset.setCurrentIndex(max(
            0, self.modify_preset.findData(self.conf["modify_preset"])
        ))
        self.modify_preset.currentIndexChanged.connect(self.apply_modify_preset)
        self.cleanup = QCheckBox("Mətni transkripsiyadan sonra təmizlə")
        self.cleanup.setChecked(self.conf["cleanup_enabled"])
        self.cleanup_model = QComboBox()
        for model in dict.fromkeys(
            [choice[2] for choice in OPENROUTER_CLEANUP_CHOICES]
            + [choice[2] for choice in OPENAI_CLEANUP_CHOICES]
        ):
            self.cleanup_model.addItem(model, model)
        if self.cleanup_model.findData(initial_cleanup_model) < 0:
            self.cleanup_model.addItem(initial_cleanup_model, initial_cleanup_model)
        self.cleanup_model.setCurrentIndex(self.cleanup_model.findData(initial_cleanup_model))
        self.cleanup_model.currentIndexChanged.connect(self.sync_text_model_display)
        self.reasoning = QComboBox()
        for label, value in [("Model default", ""), ("Minimal", "minimal"),
                             ("Low", "low"), ("Medium", "medium"), ("High", "high")]:
            self.reasoning.addItem(label, value)
        self.reasoning.setCurrentIndex(max(
            0, self.reasoning.findData(self.conf["cleanup_reasoning"])
        ))
        self.glossary = QPlainTextEdit(self.conf["transcribe_prompt"])
        self.glossary.setPlaceholderText(
            "Tez-tez işlətdiyin adlar və terminlər: Kubernetes, n8n, Azərbaycan…"
        )
        self.glossary.setMaximumHeight(90)
        self.cleanup_prompt = QPlainTextEdit(
            self.conf["cleanup_prompt"] or cfg.default_cleanup_prompt()
        )
        self.cleanup_prompt.setPlaceholderText("Təmizləmə modelinə verilən sistem təlimatı")
        self.cleanup_prompt.setMinimumHeight(150)
        modify_form.addRow("İş mode-u", self.work_mode)
        modify_form.addRow("Mode idarəetməsi", mode_actions)
        modify_form.addRow("Modify səviyyəsi", self.modify_preset)
        modify_form.addRow("", self.cleanup)
        modify_form.addRow("Təmizləmə modeli", self.cleanup_model)
        modify_form.addRow("Düşünmə səviyyəsi", self.reasoning)
        modify_form.addRow("Sözlük və xüsusi terminlər", self.glossary)
        modify_form.addRow("Custom modify prompt", self.cleanup_prompt)
        add_settings_page(modify, "DeYaz", "wand")

        # Audio / video file transcription studio
        file_page = QWidget(objectName="settingsGroup")
        file_l = QVBoxLayout(file_page)
        file_l.setContentsMargins(4, 12, 4, 12)
        file_l.setSpacing(14)

        file_hero = QFrame(objectName="fileHero")
        self.file_hero = file_hero
        hero_l = QVBoxLayout(file_hero)
        hero_l.setContentsMargins(20, 18, 20, 18)
        file_title = QLabel("MEDIA TRANSCRIBE STUDIO", objectName="status")
        file_hint = QLabel(
            "Audio və ya video yüklə — video səsi avtomatik çıxarılır, uzun fayllar "
            "hissələrə bölünür, sonra istədiyin dildə və formatda hazırlanır.",
            objectName="muted"
        )
        file_hint.setWordWrap(True)
        hero_l.addWidget(file_title)
        hero_l.addWidget(file_hint)
        file_l.addWidget(file_hero)

        self.file_button = QPushButton(
            "＋  AUDIO / VİDEO FAYLI SEÇ\nMP3 · WAV · M4A · MP4 · MKV · WEBM",
            objectName="fileDrop",
        )
        self.file_button.setMinimumHeight(92)
        self.file_button.clicked.connect(self.transcribe_audio_file)
        file_l.addWidget(self.file_button)

        self.file_selected = QLabel("Heç bir fayl seçilməyib", objectName="muted")
        self.file_selected.setWordWrap(True)
        file_l.addWidget(self.file_selected)

        file_options = QFrame(objectName="fileHero")
        self.file_options_panel = file_options
        options_form = QFormLayout(file_options)
        options_form.setContentsMargins(18, 16, 18, 16)
        options_form.setHorizontalSpacing(18)
        options_form.setVerticalSpacing(12)
        options_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        options_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.settings_forms.append(options_form)

        self.file_language = QComboBox()
        for label, code in [
            ("Avtomatik tanı", "auto"), ("Azərbaycanca", "az"),
            ("English", "en"), ("Türkçe", "tr"), ("Русский", "ru"),
        ]:
            self.file_language.addItem(label, code)
        self.file_language.setCurrentIndex(max(
            0, self.file_language.findData(self.conf["file_language"])
        ))

        self.file_transcribe_model = QComboBox(objectName="modePicker")
        self.refresh_file_transcribe_models()
        self.file_transcribe_model.currentIndexChanged.connect(
            self.file_transcribe_model_changed
        )

        self.file_output_language = QComboBox()
        self.file_output_language.addItem("Orijinal dildə", "original")
        self.file_output_language.addItem("Azərbaycanca", "az")
        self.file_output_language.setCurrentIndex(max(
            0, self.file_output_language.findData(
                self.conf["file_output_language"]
            )
        ))

        self.file_result_type = QComboBox()
        self.file_result_type.setObjectName("modePicker")
        for label, value in [
            ("Tam transkript", "transcript"),
            ("Qısa xülasə · 5–8 əsas fikir", "short_summary"),
            ("Ətraflı xülasə · bölmələrlə", "detailed_summary"),
            ("Görüş qeydləri · qərarlar və suallar", "meeting_notes"),
            ("Action items · tapşırıq və deadline", "action_items"),
            ("Dərs qeydləri · anlayış və nümunələr", "study_notes"),
        ]:
            self.file_result_type.addItem(label, value)
        self.file_result_type.setCurrentIndex(max(
            0, self.file_result_type.findData(self.conf["file_result_type"])
        ))
        self.file_result_type.currentIndexChanged.connect(
            self.update_file_option_state
        )

        self.file_cleanup = QCheckBox("Danışıq səhvlərini və təkrarları təmizlə")
        self.file_cleanup.setChecked(bool(self.conf["file_cleanup"]))
        self.file_timestamps = QCheckBox("Zaman damğaları əlavə et [mm:ss]")
        self.file_timestamps.setChecked(bool(self.conf["file_timestamps"]))
        option_checks = QWidget()
        option_checks_l = QHBoxLayout(option_checks)
        option_checks_l.setContentsMargins(0, 0, 0, 0)
        option_checks_l.addWidget(self.file_cleanup)
        option_checks_l.addWidget(self.file_timestamps)
        option_checks_l.addStretch()

        self.file_summary_focus = QLineEdit(self.conf["file_summary_focus"])
        self.file_summary_focus.setPlaceholderText(
            "Məsələn: yalnız biznes qərarlarına və büdcəyə fokuslan"
        )

        options_form.addRow("Transkripsiya modeli", self.file_transcribe_model)
        options_form.addRow("Danışığın dili", self.file_language)
        options_form.addRow("Nəticənin dili", self.file_output_language)
        options_form.addRow("FILE WORK MODE", self.file_result_type)
        options_form.addRow("Emal", option_checks)
        options_form.addRow("Xüsusi fokus", self.file_summary_focus)
        file_l.addWidget(file_options)

        self.file_run = QPushButton("▶  TRANSKRİPSİYANI BAŞLAT", objectName="primaryFile")
        self.file_run.clicked.connect(self.start_file_transcription)
        self.file_run.setEnabled(False)
        self.file_run_shadow = QGraphicsDropShadowEffect(self.file_run)
        self.file_run_shadow.setBlurRadius(22)
        self.file_run_shadow.setOffset(0, 7)
        self.file_run_shadow.setColor(QColor(255, 93, 64, 68))
        self.file_run.setGraphicsEffect(self.file_run_shadow)
        self.file_stop = QPushButton("Dayandır")
        self.file_stop.clicked.connect(self.stop_file_transcription)
        self.file_stop.setEnabled(False)

        self.file_progress = QProgressBar()
        self.file_progress.setRange(0, 1)
        self.file_progress.setValue(0)
        self.file_progress.hide()
        file_l.addWidget(self.file_progress)
        self.file_status = QLabel("Fayl seç və emal seçimlərini qur.", objectName="muted")
        self.file_status.setWordWrap(True)
        file_l.addWidget(self.file_status)

        self.file_output = QPlainTextEdit(objectName="fileOutput")
        self.file_output.setPlaceholderText("Hazır nəticə burada görünəcək…")
        self.file_output.setMinimumHeight(220)
        file_l.addWidget(self.file_output)

        copy_file = QPushButton("Kopyala")
        self.file_copy = copy_file
        copy_file.clicked.connect(self.copy_file_output)
        save_file = QPushButton("TXT saxla")
        self.file_save_txt = save_file
        save_file.clicked.connect(self.save_file_output)
        self.file_save_srt = QPushButton("SRT saxla")
        self.file_save_srt.clicked.connect(self.save_file_srt)
        self.file_save_srt.setEnabled(False)
        file_action_dock = QFrame(objectName="actionDock")
        self.file_action_dock = file_action_dock
        file_action_l = QHBoxLayout(file_action_dock)
        file_action_l.setContentsMargins(14, 12, 12, 12)
        file_action_l.setSpacing(9)
        file_action_l.addWidget(copy_file)
        file_action_l.addWidget(save_file)
        file_action_l.addWidget(self.file_save_srt)
        file_action_l.addStretch()
        file_action_l.addWidget(self.file_stop)
        file_action_l.addWidget(self.file_run)
        self.file_run.setMinimumWidth(250)
        self.file_run.setMinimumHeight(52)
        file_l.addWidget(file_action_dock)
        self.update_file_option_state()
        self.file_mode_panel = file_page
        self.file_mode_panel.hide()
        body_l.addWidget(self.file_mode_panel)

        # Shortcut and mini HUD
        shortcut, shortcut_form = form_page()
        self.hotkey_choice = QComboBox()
        for shortcut_text in ["Ctrl+Alt+R", "Ctrl+Shift+Space", "Alt+Space"]:
            self.hotkey_choice.addItem(shortcut_text, shortcut_text)
        self.hotkey_choice.setCurrentIndex(max(
            0, self.hotkey_choice.findData(self.conf["windows_hotkey"])
        ))
        self.mini_corner = QComboBox()
        for label, value in [
            ("Sağ-alt", "bottom-right"), ("Sol-alt", "bottom-left"),
            ("Sağ-üst", "top-right"), ("Sol-üst", "top-left"),
        ]:
            self.mini_corner.addItem(label, value)
        self.mini_corner.setCurrentIndex(max(
            0, self.mini_corner.findData(self.conf["mini_corner"])
        ))
        shortcut_hint = QLabel(
            "Qısa yol Windows-un istənilən tətbiqində səsyazmanı başladıb dayandırır.",
            objectName="muted"
        )
        shortcut_hint.setWordWrap(True)
        shortcut_form.addRow("Səsyazma qısa yolu", self.hotkey_choice)
        shortcut_form.addRow("Mini HUD mövqeyi", self.mini_corner)
        shortcut_form.addRow("", shortcut_hint)
        add_settings_page(shortcut, "Qısa yol", "keyboard")

        # History
        history_page = QWidget(objectName="settingsGroup")
        history_l = QVBoxLayout(history_page)
        history_l.setContentsMargins(4, 12, 4, 12)
        self.history = QLabel("Hələ transkript yoxdur.", objectName="history")
        self.history.setWordWrap(True)
        refresh = QPushButton("Tarixçəni yenilə")
        refresh.clicked.connect(self.refresh_history)
        history_l.addWidget(self.history)
        history_l.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignLeft)
        history_l.addStretch()
        self.history_page = history_page
        add_settings_page(history_page, "Tarixçə", "history")
        self.settings_nav.currentRowChanged.connect(self.settings_page_changed)
        self.settings_mobile_nav.currentIndexChanged.connect(
            self.settings_nav.setCurrentRow
        )
        self.settings_nav.setCurrentRow(0)
        self.settings_box.hide()
        body_l.addWidget(self.settings_box)
        self._compose_template_pages(body_l)
        body_l.addStretch()
        shell_l.addWidget(self.content, 1)
        shell_l.addStretch()
        self.scroll.setWidget(shell)
        layout.addWidget(self.scroll)
        localize_widget_tree(root)
        self.setCentralWidget(root)
        self.apply_theme(self.conf.get("appearance", "auto"))
        self.set_main_surface(self.current_surface, force=True)
        self.refresh_auth_gate()

    def _compose_template_pages(self, body_l):
        """Recompose existing feature widgets into the new stable page shell."""
        for label in self.settings_box.findChildren(QLabel):
            if label.objectName() in {"muted", "oauthHint", "accountDetail"}:
                label.hide()
        self.auth_banner = QFrame(objectName="authBanner")
        auth_l = QHBoxLayout(self.auth_banner)
        auth_l.setContentsMargins(16, 10, 10, 10)
        auth_l.setSpacing(12)
        auth_l.addWidget(line_label := QLabel())
        line_label.setPixmap(line_icon("wand", "#202321", 19).pixmap(22, 22))
        self.auth_message = QLabel("OpenAI və ya OpenRouter qoşulmayıb")
        self.auth_message.setWordWrap(True)
        auth_l.addWidget(self.auth_message, 1)
        self.auth_action = QPushButton("Qoş", objectName="authAction")
        self.auth_action.clicked.connect(self.open_api_settings)
        auth_l.addWidget(self.auth_action)

        self.main_area = QFrame(objectName="mainArea")
        self.main_area.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        main_l = QVBoxLayout(self.main_area)
        main_l.setContentsMargins(0, 0, 0, 0)
        main_l.setSpacing(18)
        self.page_header = QFrame()
        page_header_l = QHBoxLayout(self.page_header)
        page_header_l.setContentsMargins(0, 0, 0, 0)
        self.page_home = QPushButton(objectName="topIcon")
        self.page_home.setIcon(line_icon("home", self.theme_tokens["text"], 19))
        self.page_home.setFixedSize(42, 42)
        self.page_home.setToolTip("Əsas səhifə")
        self.page_home.clicked.connect(lambda: self.set_main_surface("home"))
        page_header_l.addWidget(self.page_home)
        page_header_l.addStretch()
        page_header_l.addWidget(self.surface_nav)
        main_l.addWidget(self.page_header)

        self.page_stack = CurrentPageStack()
        self.page_stack.setObjectName("pageStack")
        self.page_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.surface_pages = {}

        self.home_panel = QFrame(objectName="homePanel")
        self.home_layout = QGridLayout(self.home_panel)
        self.home_layout.setContentsMargins(18, 52, 18, 52)
        self.home_layout.setHorizontalSpacing(34)
        self.home_layout.setVerticalSpacing(24)
        self.home_cards = []
        for column, (surface, label, icon_name) in enumerate((
            ("dictation", "Səsyazma", "mic"),
            ("file", "Fayl transkripti", "file"),
            ("meeting", "Görüş qeydi", "meeting"),
        )):
            card = QToolButton(objectName={
                "dictation": "launchDictation",
                "file": "launchFile",
                "meeting": "launchMeeting",
            }[surface])
            card.setProperty("surface", surface)
            card.setText(label)
            card.setIcon(line_icon(icon_name, "#202321", 76))
            card.setIconSize(QSize(112, 112))
            card.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setMinimumSize(300, 250)
            card.setMaximumSize(380, 270)
            card.clicked.connect(
                lambda _checked=False, selected=surface: self.set_main_surface(selected)
            )
            shadow = QGraphicsDropShadowEffect(card)
            shadow.setBlurRadius(28)
            shadow.setOffset(0, 10)
            shadow.setColor(QColor(32, 35, 33, 42))
            card.setGraphicsEffect(shadow)
            self.home_cards.append(card)
            self.home_layout.addWidget(
                card, 0, column, alignment=Qt.AlignmentFlag.AlignCenter
            )
            self.home_layout.setColumnStretch(column, 1)
        self.page_stack.addWidget(self.home_panel)
        self.surface_pages["home"] = self.home_panel

        self._template_minimal = True
        self.dictation_mode_widgets[0].hide()
        self.dictation_mode_widgets[1].hide()
        for widget in self.dictation_mode_widgets:
            self.dictation_mode_layout.removeWidget(widget)
        self.dictation_mode_layout.addWidget(self.dictation_mode, 0, 0)
        self.dictation_mode_bar.layout().setContentsMargins(0, 0, 0, 0)
        self.dictation_mode.setMinimumHeight(76)
        self.dictation_mode.setMaximumHeight(82)
        self.dictation_mode.setMinimumWidth(420)
        self.dictation_mode.setMaximumWidth(420)
        self.dictation_mode.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.dictation_mode.setObjectName("dictationModePill")
        self.hero_copy.hide()
        self.signal_title.hide()
        self.signal_hint.hide()
        self.hero_layout.removeWidget(self.hero_copy)
        self.hero_layout.removeWidget(self.signal_panel)
        self.signal_panel.hide()
        self.dictation_action_bar.layout().removeWidget(self.record)
        self.hero_layout.setContentsMargins(0, 0, 0, 0)
        self.hero_layout.addWidget(self.record, 0, 0)
        self.hero_card.setMinimumHeight(300)
        self.hero_card.setMaximumHeight(330)
        self.record.setMinimumHeight(300)
        self.record.setMaximumHeight(330)
        self.record.setMinimumWidth(420)
        self.record.setMaximumWidth(420)
        self.record.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.record.setIconSize(QSize(96, 96))
        self.record.setText("")
        for label in self.dictation_action_bar.findChildren(QLabel):
            label.hide()
        self.recent_time.hide()
        self.recent_preview.setText(self.latest_result_text or "")
        for label in self.recent_card.findChildren(QLabel, "sectionEyebrow"):
            label.setText("Nəticə")
            label.setObjectName("panelTitle")
        self.copy_recent_button.setText("")
        self.copy_recent_button.setIcon(line_icon("copy", self.theme_tokens["text"], 18))
        self.copy_recent_button.setToolTip("Kopyala")
        self.clear_recent_button = QPushButton(objectName="topIcon")
        self.clear_recent_button.setIcon(
            line_icon("trash", self.theme_tokens["text"], 18)
        )
        self.clear_recent_button.setToolTip("Nəticəni təmizlə")
        self.clear_recent_button.clicked.connect(self.clear_dictation_result)
        self.recent_history_button.setText("")
        self.recent_history_button.setIcon(line_icon("history", self.theme_tokens["text"], 18))
        self.recent_history_button.setToolTip("Tarixçə")
        self.context_button = QPushButton("Kontekst əlavə et", objectName="contextButton")
        self.context_button.setIcon(line_icon("upload", "#202321", 20))
        self.context_button.setMinimumHeight(120)
        self.context_button.setMaximumHeight(130)
        self.context_button.clicked.connect(self.open_context_manager)

        self.dictation_audio_bar = QFrame(objectName="audioDeviceBar")
        dictation_audio_l = QHBoxLayout(self.dictation_audio_bar)
        dictation_audio_l.setContentsMargins(14, 9, 12, 9)
        dictation_audio_l.setSpacing(10)
        self.dictation_microphone = QComboBox(objectName="audioDevicePicker")
        self.dictation_microphone.setMinimumWidth(0)
        self.dictation_microphone.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self._load_dictation_microphones(self.dictation_microphone)
        self.dictation_microphone.currentIndexChanged.connect(
            self.dictation_microphone_changed
        )
        dictation_audio_l.addWidget(self.dictation_microphone, 1)

        self.dictation_model_bar = QFrame(objectName="dictationModelBar")
        dictation_model_l = QGridLayout(self.dictation_model_bar)
        dictation_model_l.setContentsMargins(14, 10, 14, 12)
        dictation_model_l.setHorizontalSpacing(12)
        dictation_model_l.setVerticalSpacing(5)
        dictation_transcribe_label = QLabel("Transkripsiya modeli")
        dictation_text_label = QLabel("Mətn modeli")
        self.dictation_transcribe_model = QComboBox(
            objectName="dictationModelPicker"
        )
        self.dictation_text_model = QComboBox(objectName="dictationModelPicker")
        for combo in (self.dictation_transcribe_model, self.dictation_text_model):
            combo.setMinimumWidth(0)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(14)
            combo.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
            )
        dictation_model_l.addWidget(dictation_transcribe_label, 0, 0)
        dictation_model_l.addWidget(self.dictation_transcribe_model, 0, 1)
        dictation_model_l.addWidget(dictation_text_label, 1, 0)
        dictation_model_l.addWidget(self.dictation_text_model, 1, 1)
        dictation_model_l.setColumnStretch(0, 0)
        dictation_model_l.setColumnStretch(1, 1)
        self.refresh_dictation_models()
        self.dictation_transcribe_model.currentIndexChanged.connect(
            self.dictation_transcribe_model_changed
        )
        self.dictation_text_model.currentIndexChanged.connect(
            self.dictation_text_model_changed
        )

        self.dictation_page = QWidget()
        dictation_l = QGridLayout(self.dictation_page)
        dictation_l.setContentsMargins(0, 0, 0, 0)
        dictation_l.setHorizontalSpacing(24)
        dictation_l.setVerticalSpacing(16)
        self.dictation_left = QFrame(objectName="dictationLeft")
        # Keep a small effect gutter around the 420 px cards. Without it,
        # QGraphicsDropShadowEffect is clipped by the fixed-width parent and
        # makes the right border look cut off.
        self.dictation_left.setMinimumWidth(468)
        self.dictation_left.setMaximumWidth(468)
        dictation_left_l = QVBoxLayout(self.dictation_left)
        dictation_left_l.setContentsMargins(24, 0, 24, 18)
        dictation_left_l.setSpacing(14)
        dictation_left_l.addWidget(self.dictation_mode_bar)
        dictation_left_l.addWidget(self.dictation_audio_bar)
        dictation_left_l.addWidget(self.dictation_model_bar)
        dictation_left_l.addWidget(self.hero_card, 1)
        # Reuse the card's existing effect for RecordButton's pulse animation.
        # This avoids stacking two effects and keeps the glow inside the real
        # gutter instead of clipping it at the button's fixed right edge.
        self.record.shadow_animation.stop()
        self.record.setGraphicsEffect(None)
        self.record.shadow = self.hero_shadow
        self.record.shadow.setOffset(0, 9)
        self.record.shadow.setBlurRadius(24)
        self.record.shadow_animation.setTargetObject(self.hero_shadow)
        self.dictation_action_bar.layout().setContentsMargins(0, 0, 0, 0)
        self.dictation_action_bar.hide()
        dictation_left_l.addWidget(self.context_button)
        self.dictation_workspace = QGridLayout()
        self.dictation_workspace.setHorizontalSpacing(24)
        self.dictation_workspace.setVerticalSpacing(16)
        self.dictation_workspace.addWidget(
            self.dictation_left, 0, 0, alignment=Qt.AlignmentFlag.AlignTop
        )
        self.recent_copy.hide()
        result_head = QHBoxLayout()
        result_title = QLabel("Nəticə", objectName="panelTitle")
        result_head.addWidget(result_title)
        result_head.addStretch()
        result_head.addWidget(self.copy_recent_button)
        result_head.addWidget(self.clear_recent_button)
        result_head.addWidget(self.recent_history_button)
        result_head_widget = QWidget()
        result_head_widget.setLayout(result_head)
        result_line = QFrame()
        result_line.setFixedHeight(3)
        result_line.setStyleSheet("background: #292C2A; border: 0;")
        self.dictation_result_output = QPlainTextEdit(objectName="meetingResult")
        self.dictation_result_output.setReadOnly(True)
        self.dictation_result_output.setPlainText(self.latest_result_text or "")
        self.recent_layout.addWidget(result_head_widget, 0, 0)
        self.recent_layout.addWidget(result_line, 1, 0)
        self.recent_layout.addWidget(self.dictation_result_output, 2, 0)
        self.recent_layout.setRowStretch(2, 1)
        self.recent_card.setMinimumHeight(540)
        self.recent_card.setMaximumHeight(610)
        self._update_dictation_result_layout()
        dictation_l.addLayout(self.dictation_workspace, 0, 0)
        self.workflow_card.hide()
        self.page_stack.addWidget(self.dictation_page)
        self.surface_pages["dictation"] = self.dictation_page

        self.file_hero.hide()
        self.file_button.setText("Fayl yüklə")
        self.file_button.setIcon(line_icon("upload", "#202321", 30))
        self.file_button.setIconSize(QSize(42, 42))
        self.file_button.setMinimumHeight(82)
        self.file_button.setMaximumHeight(92)
        self.file_selected.setVisible(bool(self.file_path))
        self.file_status.hide()
        self.file_output.setPlaceholderText("")
        for label in self.file_options_panel.findChildren(QLabel):
            label.setText({
                "FILE WORK MODE": "Nəticə",
                "Danışığın dili": "Dil",
                "Nəticənin dili": "Nəticə dili",
            }.get(label.text(), label.text()))
        self.file_copy.setText("")
        self.file_copy.setIcon(line_icon("copy", self.theme_tokens["text"], 18))
        self.file_copy.setToolTip("Kopyala")
        self.file_save_txt.setText("")
        self.file_save_txt.setIcon(line_icon("file", self.theme_tokens["text"], 18))
        self.file_save_txt.setToolTip("TXT saxla")
        self.file_save_srt.setText("")
        self.file_save_srt.setIcon(line_icon("file", self.theme_tokens["text"], 18))
        self.file_save_srt.setToolTip("SRT saxla")
        self.file_stop.setText("")
        self.file_stop.setIcon(line_icon("stop", self.theme_tokens["text"], 18))
        self.file_stop.setToolTip("Dayandır")
        self.file_run.setText("Başla")
        self.file_run.setMinimumWidth(150)
        self.file_page_container = QWidget()
        file_page_l = QGridLayout(self.file_page_container)
        file_page_l.setContentsMargins(0, 0, 0, 0)
        file_page_l.setHorizontalSpacing(24)
        file_page_l.setVerticalSpacing(16)
        self.file_left = QFrame(objectName="fileLeft")
        file_left_l = QVBoxLayout(self.file_left)
        file_left_l.setContentsMargins(0, 0, 0, 0)
        file_left_l.setSpacing(14)
        file_left_l.addWidget(self.file_button)
        self.file_media_card = QFrame(objectName="fileMediaCard")
        file_media_l = QVBoxLayout(self.file_media_card)
        file_media_l.setContentsMargins(30, 28, 30, 24)
        file_media_l.setSpacing(14)
        self.file_preview_stack = QStackedWidget(objectName="filePreviewStack")
        self.file_audio_screen = QFrame(objectName="fileAudioScreen")
        file_audio_l = QVBoxLayout(self.file_audio_screen)
        file_audio_l.setContentsMargins(18, 18, 18, 18)
        self.file_wave = AudioWaveformWidget()
        self.file_wave.set_theme(getattr(self, "theme", "light"))
        file_audio_l.addWidget(self.file_wave, 1)
        self.file_preview_stack.addWidget(self.file_audio_screen)

        self.file_video_screen = QFrame(objectName="fileVideoScreen")
        video_l = QVBoxLayout(self.file_video_screen)
        video_l.setContentsMargins(6, 6, 6, 6)
        self.file_video = SubtitleVideoWidget(objectName="fileVideo")
        self.file_video.setAspectRatioMode(
            Qt.AspectRatioMode.KeepAspectRatioByExpanding
        )
        video_l.addWidget(self.file_video)
        self.file_subtitle = self.file_video.subtitle_label
        self.file_preview_stack.addWidget(self.file_video_screen)
        self.file_preview_stack.setMinimumHeight(250)
        file_media_l.addWidget(self.file_preview_stack, 1)

        file_media_l.addWidget(self.file_selected)
        self.file_transport = QFrame(objectName="fileTransport")
        transport_l = QVBoxLayout(self.file_transport)
        transport_l.setContentsMargins(14, 10, 14, 12)
        transport_l.setSpacing(8)
        seek_row = QHBoxLayout()
        seek_row.setSpacing(10)
        self.file_position_label = QLabel("00:00", objectName="mediaTime")
        self.file_duration_label = QLabel("00:00", objectName="mediaTime")
        self.file_seek = QSlider(Qt.Orientation.Horizontal, objectName="mediaSeek")
        # The styled handle is 21 px tall (groove + negative margins). Give it
        # explicit vertical room so its top and bottom are never clipped.
        self.file_seek.setFixedHeight(28)
        self.file_seek.setRange(0, 0)
        self.file_seek.sliderMoved.connect(self.seek_file_media)
        seek_row.addWidget(self.file_position_label)
        seek_row.addWidget(self.file_seek, 1)
        seek_row.addWidget(self.file_duration_label)
        transport_l.addLayout(seek_row)
        control_row = QHBoxLayout()
        control_row.setSpacing(12)
        control_row.addStretch()
        self.file_rewind = QPushButton(objectName="mediaSkip")
        self.file_rewind.setIcon(line_icon(
            "rewind", self.theme_tokens["text"], 22
        ))
        self.file_rewind.setIconSize(QSize(28, 28))
        self.file_rewind.setFixedSize(48, 42)
        self.file_rewind.setToolTip("10 saniyə geri")
        self.file_rewind.clicked.connect(lambda: self.skip_file_media(-10000))
        self.file_play = QPushButton(objectName="mediaPlay")
        self.file_play.setIcon(line_icon("play", "#202321", 26))
        self.file_play.setIconSize(QSize(34, 34))
        self.file_play.setFixedSize(62, 58)
        self.file_play.setToolTip("Oynat")
        self.file_play.clicked.connect(self.toggle_file_media)
        self.file_forward = QPushButton(objectName="mediaSkip")
        self.file_forward.setIcon(line_icon(
            "forward", self.theme_tokens["text"], 22
        ))
        self.file_forward.setIconSize(QSize(28, 28))
        self.file_forward.setFixedSize(48, 42)
        self.file_forward.setToolTip("10 saniyə irəli")
        self.file_forward.clicked.connect(lambda: self.skip_file_media(10000))
        control_row.addWidget(self.file_rewind)
        control_row.addWidget(self.file_play)
        control_row.addWidget(self.file_forward)
        control_row.addStretch()
        transport_l.addLayout(control_row)
        self.file_transport.hide()
        file_media_l.addWidget(self.file_transport)

        self.file_audio_output = QAudioOutput(self)
        self.file_audio_output.setVolume(0.8)
        self.file_player = QMediaPlayer(self)
        self.file_player.setAudioOutput(self.file_audio_output)
        self.file_player.setVideoOutput(self.file_video)
        self.file_player.positionChanged.connect(self.file_media_position_changed)
        self.file_player.durationChanged.connect(self.file_media_duration_changed)
        self.file_player.playbackStateChanged.connect(self.file_media_state_changed)
        self.file_player.errorOccurred.connect(self.file_media_error)
        self.file_media_card.setMinimumHeight(430)
        file_left_l.addWidget(self.file_media_card)
        file_left_l.addWidget(self.file_options_panel)
        self.file_result_panel = QFrame(objectName="resultPanel")
        file_result_l = QVBoxLayout(self.file_result_panel)
        file_result_l.setContentsMargins(18, 14, 18, 18)
        file_result_l.setSpacing(10)
        file_result_head = QHBoxLayout()
        file_result_head.addWidget(QLabel("Nəticə", objectName="panelTitle"))
        file_result_head.addStretch()
        self.file_clear = QPushButton(objectName="topIcon")
        self.file_clear.setIcon(line_icon("trash", self.theme_tokens["text"], 18))
        self.file_clear.setToolTip("Nəticəni təmizlə")
        self.file_clear.clicked.connect(self.clear_file_result)
        self.file_clear.setEnabled(False)
        file_result_head.addWidget(self.file_clear)
        file_result_l.addLayout(file_result_head)
        file_result_l.addWidget(self.file_output, 1)
        file_result_l.addWidget(self.file_progress)
        file_result_l.addWidget(self.file_status)
        file_result_l.addWidget(self.file_action_dock)
        file_page_l.addWidget(self.file_left, 0, 0)
        file_page_l.addWidget(self.file_result_panel, 0, 1)
        file_page_l.setColumnStretch(0, 2)
        file_page_l.setColumnStretch(1, 3)
        self.page_stack.addWidget(self.file_page_container)
        self.surface_pages["file"] = self.file_page_container

        for widget in (
            self.meeting_mode_copy_widget,
            self.meeting_mic_source, self.meeting_mic_level,
            self.meeting_system_source, self.meeting_system_level,
            self.meeting_speaker_note, self.meeting_keep_audio,
        ):
            widget.hide()
        self.meeting_result_type.setMinimumHeight(58)
        self.meeting_result_type.setMaximumHeight(76)
        self.meeting_result_output = QPlainTextEdit(objectName="meetingResult")
        self.meeting_result_output.setReadOnly(True)
        self.meeting_result_output.setMinimumHeight(300)
        self.meeting_transcript.setPlaceholderText("")
        self.meeting_copy.setText("")
        self.meeting_copy.setIcon(line_icon("copy", self.theme_tokens["text"], 18))
        self.meeting_copy.setToolTip("Kopyala")
        self.meeting_open.setText("")
        self.meeting_open.setIcon(line_icon("file", self.theme_tokens["text"], 18))
        self.meeting_open.setToolTip("Qeydi aç")
        self.meeting_page_container = QWidget()
        meeting_page_l = QGridLayout(self.meeting_page_container)
        meeting_page_l.setContentsMargins(0, 0, 0, 0)
        meeting_page_l.setHorizontalSpacing(20)
        meeting_page_l.setVerticalSpacing(16)
        self.meeting_control_panel = QFrame(objectName="meetingControlPanel")
        self.meeting_control_panel.setMinimumWidth(360)
        self.meeting_control_panel.setMaximumWidth(430)
        meeting_control_l = QVBoxLayout(self.meeting_control_panel)
        meeting_control_l.setContentsMargins(18, 18, 18, 18)
        meeting_control_l.setSpacing(12)
        self.meeting_result_type.setMinimumWidth(0)
        self.meeting_result_type.setMinimumContentsLength(10)
        self.meeting_result_type.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.meeting_result_type.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        meeting_control_l.addWidget(self.meeting_result_type)
        meeting_control_l.addWidget(self.meeting_models_panel)
        self.meeting_visual = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.meeting_visual.setPixmap(
            line_icon("meeting", "#202321", 72).pixmap(108, 108)
        )
        self.meeting_visual.hide()
        self.meeting_state.hide()
        self.meeting_elapsed.hide()
        self.meeting_action.setText("")
        self.meeting_action.setIcon(line_icon("mic", "#202321", 86))
        self.meeting_action.setIconSize(QSize(112, 112))
        self.meeting_action.setMinimumHeight(180)
        self.meeting_action.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        meeting_control_l.addWidget(self.meeting_action, 1)
        self.meeting_live_panel = QFrame(objectName="livePanel")
        meeting_live_l = QVBoxLayout(self.meeting_live_panel)
        meeting_live_l.setContentsMargins(18, 14, 18, 18)
        meeting_live_l.addWidget(QLabel("Canlı", objectName="panelTitle"))
        meeting_live_line = QFrame(objectName="panelRule")
        meeting_live_line.setFixedHeight(3)
        meeting_live_l.addWidget(meeting_live_line)
        meeting_live_l.addWidget(self.meeting_transcript, 1)
        self.meeting_result_panel = QFrame(objectName="resultPanel")
        meeting_result_l = QVBoxLayout(self.meeting_result_panel)
        meeting_result_l.setContentsMargins(18, 14, 18, 18)
        meeting_result_head = QHBoxLayout()
        meeting_result_head.addWidget(QLabel("Nəticə", objectName="panelTitle"))
        meeting_result_head.addStretch()
        meeting_result_head.addWidget(self.meeting_copy)
        self.meeting_clear = QPushButton(objectName="topIcon")
        self.meeting_clear.setIcon(line_icon("trash", self.theme_tokens["text"], 18))
        self.meeting_clear.setToolTip("Nəticəni təmizlə")
        self.meeting_clear.clicked.connect(self.clear_meeting_result)
        self.meeting_clear.setEnabled(False)
        meeting_result_head.addWidget(self.meeting_clear)
        meeting_result_head.addWidget(self.meeting_open)
        meeting_result_l.addLayout(meeting_result_head)
        meeting_result_line = QFrame(objectName="panelRule")
        meeting_result_line.setFixedHeight(3)
        meeting_result_l.addWidget(meeting_result_line)
        meeting_result_l.addWidget(self.meeting_result_output, 1)
        for panel in (
            self.meeting_control_panel, self.meeting_live_panel,
            self.meeting_result_panel,
        ):
            panel.setMinimumHeight(560)
        self.meeting_control_panel.setMaximumHeight(620)
        self.meeting_control_panel.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        meeting_page_l.addWidget(
            self.meeting_control_panel, 0, 0, alignment=Qt.AlignmentFlag.AlignTop
        )
        meeting_page_l.addWidget(self.meeting_live_panel, 0, 1)
        meeting_page_l.addWidget(self.meeting_result_panel, 0, 2)
        meeting_page_l.setColumnStretch(0, 2)
        meeting_page_l.setColumnStretch(1, 3)
        meeting_page_l.setColumnStretch(2, 3)
        self.page_stack.addWidget(self.meeting_page_container)
        self.surface_pages["meeting"] = self.meeting_page_container
        main_l.addWidget(self.page_stack, 1)

        body_l.insertWidget(0, self.auth_banner)
        body_l.insertWidget(1, self.main_area, 1)
        self.refresh_page_chrome()

    def refresh_page_chrome(self):
        """Apply per-button colour states; Qt styles dynamic properties inconsistently."""
        c = self.theme_tokens
        card_colours = (c["pink"], c["blue"], c["green"])
        medium_home = getattr(self, "_home_layout_mode", "wide") == "medium"
        home_font = 17 if medium_home else 21
        home_padding = 18 if medium_home else 26
        home_hover_padding = home_padding - 1
        for card, colour in zip(self.home_cards, card_colours):
            card.setStyleSheet(f"""
                QToolButton {{ background-color: {colour}; color: #202321;
                    border: 3px solid #292C2A; border-radius: 28px; padding: {home_padding}px;
                    font-size: {home_font}px; font-weight: 760; }}
                QToolButton:hover {{ border-width: 4px; padding: {home_hover_padding}px; }}
                QToolButton:pressed {{ padding-top: 31px; padding-bottom: 21px; }}
                QToolButton:disabled {{ background-color: {c['soft']}; color: {c['muted']};
                    border: 2px solid {c['muted']}; }}
            """)
        for button, colour in zip(
            (self.surface_buttons["dictation"], self.surface_buttons["file"],
             self.surface_buttons["meeting"]), card_colours,
        ):
            button.setStyleSheet(f"""
                QPushButton {{ background-color: {colour}; color: #202321;
                    border: 2px solid #292C2A; border-radius: 13px;
                    padding: 10px 18px; font-size: 12px; font-weight: 720; }}
                QPushButton:hover {{ border-width: 3px; padding: 9px 17px; }}
                QPushButton:checked {{ border-width: 3px; padding: 9px 17px; }}
            """)
        self.dictation_mode_bar.setStyleSheet("background: transparent; border: 0;")
        self.dictation_mode.setStyleSheet(f"""
            QComboBox {{ background-color: {c['purple']}; color: #202321;
                border: 3px solid #292C2A; border-radius: 18px;
                padding: 10px 48px 10px 18px; font-family: 'Segoe Print';
                font-size: 17px; font-weight: 700; }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 36px;
                margin: 5px 7px 5px 0;
                background: transparent;
                border: 0;
            }}
            QComboBox:hover, QComboBox:on {{ border: 4px solid #292C2A; }}
        """)
        self.hero_card.setStyleSheet(
            f"QFrame#card {{ background-color: {c['purple']}; border: 3px solid #292C2A; border-radius: 24px; }}"
        )
        self.signal_panel.setStyleSheet(
            "QFrame#signalPanel { background: transparent; border: 0; }"
        )
        self.dictation_action_bar.setStyleSheet(
            "QFrame#actionDock { background: transparent; border: 0; }"
        )
        self.record.setStyleSheet(f"""
            QPushButton {{ background-color: {c['purple']}; color: #202321;
                border: 3px solid #292C2A; border-radius: 18px;
                font-weight: 800; }}
            QPushButton:hover {{ background-color: {c['accent_hover']}; border-width: 4px; }}
            QPushButton:disabled {{ background-color: {c['soft']}; color: {c['muted']}; }}
        """)
        self.context_button.setStyleSheet(f"""
            QPushButton {{ background-color: {c['mint']}; color: #202321;
                border: 3px solid #292C2A; border-radius: 18px;
                font-family: 'Segoe Print'; font-size: 17px; font-weight: 700; }}
            QPushButton:hover {{ background-color: #82DCC4; border-width: 4px; }}
        """)
        self.recent_card.setStyleSheet(
            f"QFrame#recentCard {{ background-color: {c['surface2']}; border: 3px solid #292C2A; border-radius: 24px; }}"
        )
        self.file_result_panel.setStyleSheet(
            f"QFrame#resultPanel {{ background-color: {c['surface2']}; border: 3px solid #292C2A; border-radius: 24px; }}"
        )
        self.file_button.setStyleSheet(f"""
            QPushButton#fileDrop {{ background-color: {c['yellow']}; color: #202321;
                border: 3px solid #292C2A; border-radius: 20px;
                font-family: 'Segoe Print'; font-size: 17px; font-weight: 700; }}
            QPushButton#fileDrop:hover {{ background-color: #FFE37A; border-width: 4px; }}
            QPushButton#fileDrop:disabled {{ background-color: {c['soft']}; color: {c['muted']}; }}
        """)
        self.file_media_card.setStyleSheet(f"""
            QFrame#fileMediaCard {{ background-color: {c['blue']};
                border: 3px solid #292C2A; border-radius: 24px; }}
            QFrame#fileAudioScreen {{ background-color: {c['green']};
                border: 3px solid #292C2A; border-radius: 22px; }}
            QFrame#fileVideoScreen {{ background-color: #111514;
                border: 6px solid #292C2A; border-radius: 22px; }}
            QLabel#fileWave {{ background: transparent; border: 0; }}
            QVideoWidget#fileVideo {{ background: #111514; border: 0; border-radius: 14px; }}
            QWidget#fileVideoOverlay {{ background: transparent; border: 0; }}
            QLabel#fileSubtitle {{ background: rgba(17, 21, 20, 210); color: white;
                border: 2px solid rgba(255, 255, 255, 90); border-radius: 12px;
                padding: 9px 14px; font-family: 'Segoe UI'; font-size: 15px;
                font-weight: 650; }}
            QFrame#fileTransport {{ background-color: {c['surface']};
                border: 3px solid {c['separator']}; border-radius: 20px; }}
            QPushButton#mediaPlay {{ background-color: {c['pink']}; color: #202321;
                border: 3px solid #292C2A; border-radius: 28px; padding: 8px; }}
            QPushButton#mediaPlay:hover {{ background-color: #FFADB2; border-width: 4px; }}
            QPushButton#mediaSkip {{ background-color: {c['surface2']}; color: {c['text']};
                border: 2px solid {c['separator']}; border-radius: 18px; padding: 6px; }}
            QPushButton#mediaSkip:hover {{ background-color: {c['yellow']}; color: #202321;
                border-color: #292C2A; border-width: 3px; }}
            QLabel#mediaTime {{ color: {c['text']}; font-family: 'Segoe UI';
                font-size: 11px; font-weight: 700; }}
            QSlider#mediaSeek::groove:horizontal {{ height: 7px; background: {c['soft']};
                border-radius: 3px; }}
            QSlider#mediaSeek::sub-page:horizontal {{ background: {c['text']}; border-radius: 3px; }}
            QSlider#mediaSeek::handle:horizontal {{ background: {c['pink']};
                border: 2px solid #292C2A; width: 18px; margin: -7px 0;
                border-radius: 9px; }}
            QLabel {{ color: #202321; font-family: 'Segoe Print'; font-size: 14px; }}
        """)
        self.meeting_control_panel.setStyleSheet(
            "QFrame#meetingControlPanel { background: transparent; border: 0; }"
        )
        self.meeting_result_type.setStyleSheet(f"""
            QComboBox {{ background-color: {c['blue']}; color: #202321;
                border: 3px solid #292C2A; border-radius: 18px;
                padding: 11px 48px 11px 18px; font-family: 'Segoe Print';
                font-size: 17px; font-weight: 700; }}
            QComboBox::drop-down {{ subcontrol-origin: padding;
                subcontrol-position: center right; width: 36px;
                margin: 5px 7px 5px 0; background: transparent; border: 0; }}
            QComboBox:hover, QComboBox:on {{ border-width: 4px; }}
        """)
        self.meeting_action.setStyleSheet(f"""
            QPushButton {{ background-color: {c['purple']}; color: #202321;
                border: 3px solid #292C2A; border-radius: 24px;
                font-family: 'Segoe Print'; font-size: 18px; font-weight: 700; }}
            QPushButton:hover {{ background-color: {c['accent_hover']}; border-width: 4px; }}
            QPushButton:pressed {{ padding-top: 10px; }}
            QPushButton:disabled {{ background-color: {c['soft']}; color: {c['muted']}; }}
        """)
        for panel in (self.meeting_live_panel, self.meeting_result_panel):
            panel.setStyleSheet(
                f"QFrame#{panel.objectName()} {{ background-color: {c['surface2']}; border: 3px solid #292C2A; border-radius: 24px; }}"
            )
        for rule in self.meeting_page_container.findChildren(QFrame, "panelRule"):
            rule.setStyleSheet("background: #292C2A; border: 0;")

    def _update_dictation_result_layout(self):
        """Keep an opened result workspace stable even after its text is cleared."""
        if not hasattr(self, "dictation_workspace"):
            return
        show_result = bool(self._dictation_result_open)
        self.dictation_workspace.removeWidget(self.dictation_left)
        self.dictation_workspace.removeWidget(self.recent_card)
        if show_result:
            self.dictation_workspace.addWidget(
                self.dictation_left, 0, 0, alignment=Qt.AlignmentFlag.AlignTop
            )
            self.dictation_workspace.addWidget(
                self.recent_card, 0, 1, alignment=Qt.AlignmentFlag.AlignTop
            )
            self.dictation_workspace.setColumnStretch(0, 2)
            self.dictation_workspace.setColumnStretch(1, 5)
        else:
            self.dictation_workspace.addWidget(
                self.dictation_left, 0, 0,
                alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
            )
            self.dictation_workspace.setColumnStretch(0, 1)
            self.dictation_workspace.setColumnStretch(1, 0)
        self.recent_card.setVisible(show_result)
        if hasattr(self, "dictation_result_output"):
            self.dictation_result_output.setPlainText(self.latest_result_text)
        self._dictation_layout_state = None
        self.dictation_workspace.invalidate()

    def open_api_settings(self):
        self.show_window()
        if not self.settings_box.isVisible():
            self.toggle_settings()
        api_index = 1 if self.settings_pages.count() > 1 else 0
        self.settings_nav.setCurrentRow(api_index)
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.settings_box))

    def has_model_credentials(self):
        visible_openai = self.openai.text().strip() if hasattr(self, "openai") else ""
        visible_openrouter = self.openrouter.text().strip() if hasattr(self, "openrouter") else ""
        return bool(
            visible_openai or visible_openrouter
            or self.conf.openai_key() or self.conf.openrouter_key()
        )

    def file_model_credentials_ready(self):
        value = (
            self.file_transcribe_model.currentData()
            if hasattr(self, "file_transcribe_model") else ""
        ) or (
            f"{self.conf.get('file_transcribe_provider', 'openai')}|"
            f"{self.conf.get('file_transcribe_model', 'gpt-transcribe')}"
        )
        provider = value.split("|", 1)[0]
        if provider == "openrouter":
            visible = self.openrouter.text().strip() if hasattr(self, "openrouter") else ""
            return bool(visible or self.conf.openrouter_key())
        visible = self.openai.text().strip() if hasattr(self, "openai") else ""
        return bool(visible or self.conf.openai_key())

    def refresh_auth_gate(self):
        if not hasattr(self, "auth_banner"):
            return
        ready = self.has_model_credentials()
        self.auth_banner.setVisible(not ready)
        self.home_cards[0].setEnabled(ready)
        self.home_cards[1].setEnabled(ready)
        self.home_cards[2].setEnabled(ready)
        self.record.setEnabled(ready and not self.recorder.active and not self.pipeline.busy)
        self.file_button.setEnabled(ready and not self.file_pipeline.busy)
        self.file_run.setEnabled(
            self.file_model_credentials_ready()
            and bool(self.file_path) and not self.file_pipeline.busy
        )
        self.meeting_action.setEnabled(ready and not self.pipeline.busy)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu(self)
        self.tray_open_action = QAction("Pəncərəni aç", self)
        self.tray_mini_action = QAction("Mini düyməni göstər", self)
        self.tray_toggle_action = QAction("Səsyazmanı başlat / dayandır", self)
        self.tray_quit_action = QAction("DeYaz-ı bağla", self)
        self.tray_open_action.triggered.connect(self.show_window)
        self.tray_mini_action.triggered.connect(self.minimize_to_bubble)
        self.tray_toggle_action.triggered.connect(self.toggle_recording)
        self.tray_quit_action.triggered.connect(self.quit)
        menu.addAction(self.tray_open_action)
        menu.addAction(self.tray_mini_action)
        menu.addAction(self.tray_toggle_action)
        menu.addSeparator()
        menu.addAction(self.tray_quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.setToolTip("DeYaz")
        self.retranslate_actions()
        self.tray.show()

    def refresh_top_mode_menu(self):
        if not hasattr(self, "work_mode_menu"):
            return
        self.work_mode_menu.clear()
        current = self.conf.get("work_mode", "dictation")
        for mode_id, item in all_modes().items():
            if mode_id == "meeting_notes_live":
                continue
            action = self.work_mode_menu.addAction(
                color_icon(item["color"]), i18n.t(item["name"])
            )
            action.setCheckable(True)
            action.setChecked(mode_id == current)
            action.triggered.connect(
                lambda _checked=False, selected=mode_id: self.select_work_mode(selected)
            )
        selected = get_work_mode(current)
        self.work_mode_button.setText(i18n.t(selected["short"]))
        self.work_mode_button.setIcon(color_icon(selected["color"]))

    def toggle_settings(self):
        show_settings = not self.settings_box.isVisible()
        self.settings_box.setVisible(show_settings)
        self.main_area.setVisible(not show_settings)
        if show_settings:
            self.page_header.hide()
        else:
            self.set_main_surface(self.current_surface, force=True)
        compact = responsive_content_width(self.width()) < 720
        self.settings_sidebar.setVisible(show_settings and not compact)
        self.settings_mobile_nav.setVisible(show_settings and compact)
        self.settings_button.setToolTip(i18n.t("Bağla") if show_settings else i18n.t("Ayarlar"))
        self.settings_button.setIcon(
            line_icon(
                "settings" if not show_settings else "close",
                self.theme_tokens["text"], 21,
            )
        )
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def toggle_file_mode(self):
        """Backward-compatible entry point used by older actions."""
        self.set_main_surface(
            "dictation" if self.current_surface == "file" else "file"
        )

    def set_main_surface(self, surface, force=False):
        """Show one stable primary surface without changing its business logic."""
        if surface not in {"home", "dictation", "file", "meeting"}:
            return
        if self.meeting.active and surface != "meeting":
            self.surface_buttons["meeting"].setChecked(True)
            self.meeting_state.setToolTip(i18n.t(
                "Əvvəl görüş qeydini tamamla, sonra başqa moda keç."
            ))
            return
        if self.settings_box.isVisible() and not force:
            self.settings_box.hide()
            self.settings_button.setToolTip(i18n.t("Ayarlar"))
            self.settings_button.setIcon(line_icon(
                "settings", self.theme_tokens["text"], 21
            ))
        self.main_area.show()

        current_mode = self.conf.get("work_mode", "dictation")
        if current_mode != "meeting_notes_live":
            self._dictation_work_mode = current_mode
        if surface == "meeting":
            if current_mode != "meeting_notes_live":
                self.set_work_mode("meeting_notes_live")
        elif surface != "home" and current_mode == "meeting_notes_live":
            restore = self._dictation_work_mode
            if restore not in all_modes() or restore == "meeting_notes_live":
                restore = "dictation"
            self.set_work_mode(restore)

        previous_surface = self.current_surface
        self.current_surface = surface
        if surface != "home":
            self._last_surface = surface
        self.page_stack.setCurrentWidget(self.surface_pages[surface])
        if surface != previous_surface and not force:
            self._animate_surface_entry(self.surface_pages[surface])
        self.page_header.setVisible(surface != "home")
        self.file_mode_panel.hide()
        self.meeting_card.hide()
        self.hero_card.show()
        self.dictation_mode_bar.show()
        self.dictation_action_bar.hide()
        self.settings_sidebar.hide()
        self.settings_mobile_nav.hide()
        for key, button in self.surface_buttons.items():
            button.setChecked(key == surface)
        if surface != previous_surface and not force and surface in self.surface_buttons:
            self._animate_surface_tab(self.surface_buttons[surface])
        if surface == "dictation":
            self._update_dictation_result_layout()
        self.refresh_auth_gate()
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def _animate_surface_entry(self, page):
        """A short GPU-light fade that never changes layout geometry."""
        self._animate_page_entry(page, "surface_transition")

    def _animate_page_entry(self, page, slot):
        """Fade a stacked page without resizing or moving surrounding widgets."""
        previous_animation = getattr(self, slot, None)
        previous_page = getattr(self, f"{slot}_page", None)
        if previous_animation:
            previous_animation.stop()
        if previous_page is not None and previous_page is not page:
            previous_page.setGraphicsEffect(None)
        old_effect = page.graphicsEffect()
        if old_effect is not None:
            page.setGraphicsEffect(None)
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.18)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(240)
        animation.setStartValue(0.18)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: page.setGraphicsEffect(None))
        setattr(self, slot, animation)
        setattr(self, f"{slot}_page", page)
        animation.start()

    def _animate_surface_tab(self, button):
        """Give the selected tab a brief, non-blocking accent glow."""
        old_animation = getattr(self, "surface_tab_animation", None)
        if old_animation:
            old_animation.stop()
        old_button = getattr(self, "surface_tab_animation_button", None)
        if old_button is not None and old_button is not button:
            old_button.setGraphicsEffect(None)
        effect = QGraphicsDropShadowEffect(button)
        effect.setOffset(0, 2)
        effect.setBlurRadius(4)
        effect.setColor(QColor(self.theme_tokens["accent"]))
        button.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"blurRadius", self)
        animation.setDuration(320)
        animation.setStartValue(4.0)
        animation.setKeyValueAt(0.48, 22.0)
        animation.setEndValue(7.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(
            lambda: button.setGraphicsEffect(None)
            if button.graphicsEffect() is effect else None
        )
        self.surface_tab_animation = animation
        self.surface_tab_animation_button = button
        animation.start()

    def open_history_drawer(self):
        self.history_popup.refresh(cfg.read_history(30))
        self.history_popup.show_as_drawer(self)

    def quick_hotkey_changed(self):
        shortcut = self.quick_hotkey.currentData()
        if not shortcut or shortcut == self.conf["windows_hotkey"]:
            return
        self.conf["windows_hotkey"] = shortcut
        self.conf.save()
        if hasattr(self, "hotkey_choice"):
            self.hotkey_choice.blockSignals(True)
            self.hotkey_choice.setCurrentIndex(self.hotkey_choice.findData(shortcut))
            self.hotkey_choice.blockSignals(False)
        if hasattr(self, "hotkey"):
            self.hotkey.restart(shortcut)
        self.shortcut_chip.setText(f"⌨  {shortcut.upper()}")

    def quick_context_changed(self, enabled):
        self.conf["context_enabled"] = bool(enabled)
        self.conf.save()
        if hasattr(self, "context_enabled"):
            self.context_enabled.blockSignals(True)
            self.context_enabled.setChecked(bool(enabled))
            self.context_enabled.blockSignals(False)
        self.context_value.setText(i18n.t("Avtomatik") if enabled else i18n.t("Sönülü"))
        self.apply_work_mode_visual()

    def quick_paste_changed(self, enabled):
        self.conf["auto_paste"] = bool(enabled)
        self.conf.save()
        if hasattr(self, "auto_paste"):
            self.auto_paste.blockSignals(True)
            self.auto_paste.setChecked(bool(enabled))
            self.auto_paste.blockSignals(False)
        self.paste_value.setText(i18n.t("Aktivdir") if enabled else i18n.t("Sönülü"))

    def settings_page_changed(self, index):
        if not 0 <= index < len(self.settings_page_sources):
            return
        previous = self.settings_pages.currentIndex()
        self.settings_pages.setCurrentIndex(index)
        if previous != index:
            self._animate_page_entry(
                self.settings_pages.widget(index), "settings_transition"
            )
        if self.settings_mobile_nav.currentIndex() != index:
            self.settings_mobile_nav.setCurrentIndex(index)
        self.settings_page_title.setText(
            i18n.t(self.settings_page_sources[index])
        )

    def update_appearance_button(self, preference):
        icon = "sun" if self.resolved_appearance(preference) == "dark" else "moon"
        self.appearance_preference = preference
        self.appearance_switch.setText("")
        colour = getattr(self, "theme_tokens", {}).get("text", "#202321")
        self.appearance_switch.setIcon(line_icon(icon, colour, 21))
        self.appearance_switch.setToolTip(
            i18n.t("Açıq görünüşə keç")
            if self.resolved_appearance(preference) == "dark"
            else i18n.t("Tünd görünüşə keç")
        )

    def toggle_appearance(self):
        current = self.resolved_appearance(
            self.conf.get("appearance", self.appearance_preference)
        )
        self.appearance_changed("light" if current == "dark" else "dark")

    def appearance_changed(self, preference):
        preference = preference or "auto"
        self.update_appearance_button(preference)
        self.conf["appearance"] = preference
        self.conf.save()
        self.apply_theme(preference)

    def header_language_changed(self, code):
        self.conf["ui_language"] = code or "auto"
        self.conf.save()
        if hasattr(self, "ui_language"):
            self.ui_language.blockSignals(True)
            self.ui_language.setCurrentIndex(max(0, self.ui_language.findData(code)))
            self.ui_language.blockSignals(False)
        for action in self.language_menu.actions():
            action.setChecked(action.property("language_code") == code)
        self.refresh_dictation_models()
        self.refresh_file_transcribe_models()
        localize_widget_tree(self.centralWidget())
        self.retranslate_actions()
        self.history_popup.retranslate()
        self.apply_work_mode_visual()
        self.settings_page_changed(self.settings_nav.currentRow())

    def retranslate_actions(self):
        for action in self.findChildren(QAction):
            localize_action(action)

    def ui_language_changed(self):
        code = self.ui_language.currentData() or "auto"
        self.conf["ui_language"] = code
        self.conf.save()
        self.refresh_dictation_models()
        self.refresh_file_transcribe_models()
        localize_widget_tree(self.centralWidget())
        self.retranslate_actions()
        self.history_popup.retranslate()
        self.bubble.set_mode(self.conf["work_mode"])
        self.bubble.set_context(
            self.current_context.label if self.current_context else ""
        )
        self.apply_work_mode_visual()
        self.settings_page_changed(self.settings_nav.currentRow())
        self.refresh_history()

    def provider_changed(self):
        provider = self.provider.currentData()
        if provider == "openrouter":
            self.transcribe_model.setText(self.conf["openrouter_transcribe_model"])
            cleanup_model = self.conf["openrouter_cleanup_model"]
        else:
            self.transcribe_model.setText(self.conf["transcribe_model"])
            cleanup_model = self.conf["openai_cleanup_model"]
        if hasattr(self, "cleanup_model"):
            index = self.cleanup_model.findData(cleanup_model)
            if index < 0:
                self.cleanup_model.addItem(cleanup_model, cleanup_model)
                index = self.cleanup_model.findData(cleanup_model)
            self.cleanup_model.setCurrentIndex(index)
        if hasattr(self, "text_model_display"):
            self.text_model_display.setText(cleanup_model)
        self.refresh_dictation_models()
        self.refresh_file_transcribe_models()

    def refresh_dictation_models(self):
        """Keep the two Dictation selectors aligned with the active targets."""
        if not hasattr(self, "dictation_transcribe_model"):
            return
        transcribe_current = (
            f"{self.conf.get('transcribe_provider', 'openai')}|"
            f"{self.conf.transcribe_target().model}"
        )
        cleanup_current = (
            f"{self.conf.get('cleanup_provider', 'openrouter')}|"
            f"{self.conf.cleanup_target().model}"
        )
        available_providers = set()
        if self.conf.openai_key():
            available_providers.add("openai")
        if self.conf.openrouter_key():
            available_providers.add("openrouter")
        if not available_providers:
            available_providers.update(
                (transcribe_current.split("|", 1)[0],
                 cleanup_current.split("|", 1)[0])
            )
        catalogs = (
            (
                self.dictation_transcribe_model,
                tuple((badge, name, "openai", model, description)
                      for badge, name, model, description
                      in OPENAI_TRANSCRIPTION_CHOICES)
                + tuple((badge, name, "openrouter", model, description)
                        for badge, name, model, description
                        in OPENROUTER_TRANSCRIPTION_CHOICES),
                transcribe_current,
            ),
            (
                self.dictation_text_model,
                tuple((badge, name, "openai", model, description)
                      for badge, name, model, description
                      in OPENAI_CLEANUP_CHOICES)
                + tuple((badge, name, "openrouter", model, description)
                        for badge, name, model, description
                        in OPENROUTER_CLEANUP_CHOICES),
                cleanup_current,
            ),
        )
        for combo, choices, current in catalogs:
            combo_providers = set(available_providers)
            combo_providers.add(current.split("|", 1)[0])
            show_provider = len(combo_providers) > 1
            combo.blockSignals(True)
            combo.clear()
            for badge, name, provider, model, description in choices:
                if provider not in combo_providers:
                    continue
                combo.addItem(
                    (f"{name} · {'OpenAI' if provider == 'openai' else 'OpenRouter'}"
                     if show_provider else name),
                    f"{provider}|{model}",
                )
                combo.setItemData(
                    combo.count() - 1,
                    f"{i18n.t(badge)} · {i18n.t(description)}",
                    Qt.ItemDataRole.ToolTipRole,
                )
            index = combo.findData(current)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)

    def dictation_transcribe_model_changed(self):
        value = self.dictation_transcribe_model.currentData() or ""
        if "|" not in value:
            return
        provider, model = value.split("|", 1)
        self.conf["transcribe_provider"] = provider
        if provider == "openai":
            self.conf["transcribe_model"] = model
        else:
            self.conf["openrouter_transcribe_model"] = model
        self.conf.save()
        if hasattr(self, "transcribe_model"):
            self.transcribe_model.setText(model)

    def dictation_text_model_changed(self):
        value = self.dictation_text_model.currentData() or ""
        if "|" not in value:
            return
        provider, model = value.split("|", 1)
        self.conf["cleanup_provider"] = provider
        self.conf["cleanup_model"] = model
        if provider == "openai":
            self.conf["openai_cleanup_model"] = model
        else:
            self.conf["openrouter_cleanup_model"] = model
        self.conf.save()
        if hasattr(self, "cleanup_model"):
            index = self.cleanup_model.findData(model)
            if index < 0:
                self.cleanup_model.addItem(model, model)
                index = self.cleanup_model.findData(model)
            self.cleanup_model.setCurrentIndex(index)
        self.sync_text_model_display()

    def refresh_file_transcribe_models(self):
        if not hasattr(self, "file_transcribe_model"):
            return
        provider = self.conf.get("file_transcribe_provider", "openai")
        model = self.conf.get("file_transcribe_model", "gpt-transcribe")
        current = f"{provider}|{model}"
        self.file_transcribe_model.blockSignals(True)
        self.file_transcribe_model.clear()
        for badge, name, item_provider, item_model, description in FILE_TRANSCRIPTION_CHOICES:
            value = f"{item_provider}|{item_model}"
            self.file_transcribe_model.addItem(f"{name} · {i18n.t(badge)}", value)
            self.file_transcribe_model.setItemData(
                self.file_transcribe_model.count() - 1, description,
                Qt.ItemDataRole.ToolTipRole,
            )
        self.file_transcribe_model._i18n_item_sources = [
            self.file_transcribe_model.itemText(index)
            for index in range(self.file_transcribe_model.count())
        ]
        self.file_transcribe_model._i18n_item_tooltip_sources = [
            description for _badge, _name, _provider, _model, description
            in FILE_TRANSCRIPTION_CHOICES
        ]
        index = self.file_transcribe_model.findData(current)
        if index < 0:
            self.file_transcribe_model.addItem(current, current)
            index = self.file_transcribe_model.count() - 1
        self.file_transcribe_model.setCurrentIndex(index)
        self.file_transcribe_model.blockSignals(False)

    def file_transcribe_model_changed(self):
        if not hasattr(self, "file_transcribe_model"):
            return
        model = self.file_transcribe_model.currentData()
        if not model:
            return
        provider, model_id = model.split("|", 1)
        self.conf["file_transcribe_provider"] = provider
        self.conf["file_transcribe_model"] = model_id
        self.conf.save()
        self.file_run.setEnabled(
            self.file_model_credentials_ready()
            and bool(self.file_path) and not self.file_pipeline.busy
        )

    def sync_text_model_display(self):
        if hasattr(self, "text_model_display"):
            self.text_model_display.setText(self.cleanup_model.currentData() or "")

    def work_mode_changed(self):
        mode_id = self.work_mode.currentData()
        if not mode_id:
            return
        self._dictation_work_mode = mode_id
        if hasattr(self, "dictation_mode"):
            self.dictation_mode.blockSignals(True)
            self.dictation_mode.setCurrentIndex(
                max(0, self.dictation_mode.findData(mode_id))
            )
            self.dictation_mode.blockSignals(False)
        effective_mode = (
            "meeting_notes_live"
            if getattr(self, "current_surface", "dictation") == "meeting"
            else mode_id
        )
        self.conf["work_mode"] = effective_mode
        self.conf.save()
        self.apply_work_mode_visual(effective_mode)

    def refresh_work_mode_controls(self, selected_id=None):
        selected_id = selected_id or self.conf.get("work_mode", "dictation")
        self.work_mode.blockSignals(True)
        self.work_mode.clear()
        for mode_id, item in all_modes().items():
            if mode_id == "meeting_notes_live":
                continue
            self.work_mode.addItem(color_icon(item["color"]), item["name"], mode_id)
        index = self.work_mode.findData(selected_id)
        self.work_mode.setCurrentIndex(index if index >= 0 else 0)
        self.work_mode.blockSignals(False)
        if hasattr(self, "dictation_mode"):
            self.dictation_mode.blockSignals(True)
            self.dictation_mode.clear()
            for mode_id, item in all_modes().items():
                if mode_id != "meeting_notes_live":
                    self.dictation_mode.addItem(
                        color_icon(item["color"]), i18n.t(item["name"]), mode_id
                    )
            mode_index = self.dictation_mode.findData(selected_id)
            self.dictation_mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
            self.dictation_mode.blockSignals(False)
        selected_mode = self.work_mode.currentData() or "dictation"
        self._dictation_work_mode = selected_mode
        self.conf["work_mode"] = (
            "meeting_notes_live"
            if getattr(self, "current_surface", "dictation") == "meeting"
            else selected_mode
        )
        self.conf.save()
        self.apply_work_mode_visual(self.conf["work_mode"])

    def _persist_custom_modes(self, items, selected_id=None):
        cleaned = []
        used = set(WORK_MODES)
        for item in items:
            clean = normalise_custom_mode(item)
            if not clean:
                continue
            base = clean["id"]
            candidate = base
            suffix = 2
            while candidate in used:
                candidate = f"{base}_{suffix}"
                suffix += 1
            clean["id"] = candidate
            used.add(candidate)
            cleaned.append(clean)
        self.conf["custom_work_modes"] = cleaned
        set_custom_modes(cleaned)
        self.refresh_work_mode_controls(selected_id)

    def add_work_mode(self):
        dialog = WorkModeDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        item = normalise_custom_mode(dialog.value())
        items = list(self.conf.get("custom_work_modes", []))
        items.append(item)
        self._persist_custom_modes(items, item["id"])

    def edit_work_mode(self):
        mode_id = self.work_mode.currentData()
        selected = dict(get_work_mode(mode_id))
        is_custom = mode_id not in WORK_MODES
        selected["id"] = mode_id if is_custom else ""
        if not is_custom:
            selected["name"] = f"{selected['name']} — Custom"
        dialog = WorkModeDialog(self, selected)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = normalise_custom_mode(dialog.value())
        items = list(self.conf.get("custom_work_modes", []))
        if is_custom:
            items = [item for item in items if item.get("id") != mode_id]
        items.append(updated)
        self._persist_custom_modes(items, updated["id"])

    def delete_work_mode(self):
        mode_id = self.work_mode.currentData()
        if mode_id in WORK_MODES:
            QMessageBox.information(
                self, i18n.t("Standart mode"),
                i18n.t("Standart mode silinmir. Redaktə et düyməsi onun custom nüsxəsini yaradır."),
            )
            return
        answer = QMessageBox.question(
            self, i18n.t("İş modunu sil"),
            i18n.t("Bu custom iş modunu silmək istəyirsən?"),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        items = [
            item for item in self.conf.get("custom_work_modes", [])
            if item.get("id") != mode_id
        ]
        self._persist_custom_modes(items, "dictation")

    def apply_work_mode_visual(self, mode_id=None):
        mode_id = mode_id or self.conf["work_mode"]
        selected = get_work_mode(mode_id)
        meeting_mode = mode_id == "meeting_notes_live"
        self.mode_color = selected["color"]
        mode_colour = QColor(selected["color"])
        if hasattr(self, "work_mode_button"):
            self.work_mode_button.setText(i18n.t(selected["short"]))
            self.work_mode_button.setIcon(color_icon(selected["color"]))
            self.work_mode_button.setStyleSheet(
                f"QPushButton#modeSwitch {{ background: {selected['color']}; color: #15201f; "
                f"border: 0; border-radius: 9px; padding: 8px 13px; font-weight: 780; }}"
                f"QPushButton#modeSwitch:hover {{ background: {mode_colour.lighter(108).name()}; }}"
                f"QPushButton#modeSwitch:pressed, QPushButton#modeSwitch:on {{ background: {mode_colour.darker(108).name()}; }}"
                f"QPushButton#modeSwitch:focus {{ border: 2px solid {self.theme_tokens['text']}; padding: 6px 11px; }}"
                f"QMenu {{ background: {self.theme_tokens['surface']}; color: {self.theme_tokens['text']}; "
                f"border: 1px solid {self.theme_tokens['separator']}; border-radius: 11px; padding: 7px; }}"
                f"QMenu::item {{ background: transparent; color: {self.theme_tokens['text']}; "
                f"padding: 9px 28px 9px 13px; border-radius: 7px; margin: 1px 0; }}"
                f"QMenu::item:selected {{ background: {self.theme_tokens['hover']}; }}"
                f"QMenu::item:pressed {{ background: {self.theme_tokens['soft']}; }}"
                f"QMenu::item:checked {{ color: {selected['color']}; font-weight: 750; }}"
            )
            self.refresh_top_mode_menu()
        self.bubble.set_mode(mode_id)
        self.mode_badge.setText(i18n.t(selected["name"]).upper())
        self.mode_badge.setStyleSheet(
            f"color: {selected['color']}; border: 1px solid {selected['color']}; "
            f"background: rgba({mode_colour.red()}, {mode_colour.green()}, "
            f"{mode_colour.blue()}, 28);"
        )
        self.record.setStyleSheet(
            f"QPushButton {{ background: {selected['color']}; color: #1d1d1f; "
            f"border: 1px solid {mode_colour.lighter(120).name()}; border-radius: 21px; "
            "padding: 0 24px; font-size: 14px; font-weight: 760; }"
            f"QPushButton:hover {{ background: {selected['color']}; "
            f"border-color: {mode_colour.lighter(145).name()}; }}"
            f"QPushButton:pressed {{ background: {mode_colour.darker(108).name()}; padding-top: 2px; }}"
            f"QPushButton:focus {{ border: 2px solid {self.theme_tokens['text']}; }}"
            f"QPushButton:disabled {{ background: {self.theme_tokens['soft']}; color: {self.theme_tokens['muted']}; border-color: {self.theme_tokens['separator']}; }}"
            f"QPushButton[recording='true'] {{ background: {self.theme_tokens['text']}; "
            f"color: {self.theme_tokens['bg']}; border-color: {self.theme_tokens['text']}; }}"
        )
        self.record.shadow.setColor(QColor(
            mode_colour.red(), mode_colour.green(), mode_colour.blue(), 82
        ))
        if getattr(self, "_template_minimal", False):
            self.hero_card.setStyleSheet(
                "QFrame#card { background: transparent; border: 0; }"
            )
            self.record.setStyleSheet(
                f"QPushButton {{ background-color: {selected['color']}; color: #202321; "
                "border: 3px solid #292C2A; border-radius: 24px; padding: 0; "
                "font-family: 'Segoe Print'; font-size: 18px; font-weight: 700; }"
                f"QPushButton:hover {{ background-color: {mode_colour.lighter(105).name()}; "
                "border-width: 4px; }}"
                f"QPushButton:pressed {{ background-color: {mode_colour.darker(105).name()}; }}"
                f"QPushButton[recording='true'] {{ background-color: {selected['color']}; "
                "color: #202321; border: 4px solid #292C2A; }}"
                f"QPushButton:disabled {{ background-color: {self.theme_tokens['soft']}; "
                f"color: {self.theme_tokens['muted']}; border-color: {self.theme_tokens['muted']}; }}"
            )
            self.dictation_mode.setStyleSheet(
                f"QComboBox {{ background-color: {selected['color']}; color: #202321; "
                "border: 3px solid #292C2A; border-radius: 18px; padding: 10px 18px; "
                "font-family: 'Segoe Print'; font-size: 17px; font-weight: 700; }"
                "QComboBox:hover, QComboBox:on { border: 4px solid #292C2A; }"
            )
        if not self.record.property("recording"):
            self.record.setIcon(line_icon("mic", "#202321", 72))
            self.record.setIconSize(QSize(96, 96))
        dashboard_visible = not self.settings_box.isVisible()
        surface = getattr(
            self, "current_surface", "meeting" if meeting_mode else "dictation"
        )
        if getattr(self, "_template_minimal", False):
            self.workflow_card.hide()
            self.meeting_card.hide()
        else:
            self.workflow_card.setVisible(dashboard_visible and surface == "dictation")
            self.meeting_card.setVisible(dashboard_visible and surface == "meeting")
        if meeting_mode:
            self.status.setText(i18n.t("Görüş qeydlərini canlı hazırla."))
            if os.name == "nt":
                self.detail.setText(i18n.t(
                    "DeYaz həm mikrofonu, həm də kompüterdə çalınan görüş səsini dinləyir."
                ))
                self.context_badge.setText(i18n.t("MIC + SYSTEM AUDIO • HAZIR"))
                self.bubble.set_context("Mic + system")
            else:
                self.detail.setText(i18n.t(
                    "DeYaz bu platformada görüş üçün mikrofonu dinləyir."
                ))
                self.context_badge.setText(i18n.t("MIC AUDIO • HAZIR"))
                self.bubble.set_context("Mic")
            self.context_value.setText(i18n.t("Meeting mode"))
            if not self.meeting.active:
                self.record.setText(i18n.t("Görüşü başlat"))
                self.record.setToolTip(i18n.t("Canlı meeting transkriptini başlat"))
            return
        if not self.record.property("recording"):
            self.status.setText(i18n.t("Danış. Qalanını DeYaz etsin."))
            self.detail.setText(i18n.t(
                "Danış, DeYaz mətni hazırlayıb işlədiyin tətbiqə əlavə etsin."
            ))
            self.record.setText("" if getattr(self, "_template_minimal", False)
                                else i18n.t("Səsyazmaya başla"))
            self.record.setToolTip(i18n.t("Səsyazmanı başlat"))
        if mode_id == "dictation":
            self.context_badge.setText(i18n.t("AUTO CONTEXT • SÖNÜLÜ"))
            self.context_value.setText(i18n.t("Bu mode-da istifadə edilmir"))
            self.quick_context.setText(i18n.t("Digər mode-lar üçün aktiv"))
            self.bubble.set_context("")
        elif not self.conf["context_enabled"]:
            self.context_badge.setText(i18n.t("AUTO CONTEXT • SÖNÜLÜ"))
            self.context_value.setText(i18n.t("Sönülü"))
            self.quick_context.setText(i18n.t("Aktiv"))
            self.bubble.set_context("")
        elif project_context_policy(mode_id) == "verified":
            self.quick_context.setText(i18n.t("Aktiv"))
            self.context_badge.setText(i18n.t("CONTEXT • VERIFIED PROJECT AUTO"))
            self.context_value.setText(i18n.t("Aşkarlanacaq"))
            self.context_badge.setToolTip(
                i18n.t("Yalnız yüksək etibarlı aktiv layihə və oxunan fayl faktları istifadə edilir.")
            )
            self.bubble.set_context("Verified auto")
        elif self.current_context is None:
            self.quick_context.setText(i18n.t("Aktiv"))
            self.context_badge.setText(i18n.t("AUTO CONTEXT • SƏSYAZMADA AŞKAR EDİLƏCƏK"))

    def apply_modify_preset(self):
        preset = self.modify_preset.currentData()
        base = cfg.default_cleanup_prompt()
        additions = {
            "minimal": (
                "\n\nMODIFY LEVEL: MINIMAL\nOnly remove fillers, repetitions and "
                "fix punctuation. Preserve every meaningful word and the speaker's style."
            ),
            "balanced": (
                "\n\nMODIFY LEVEL: BALANCED\nMake the transcript clean and natural "
                "while preserving its meaning, tone and level of detail."
            ),
            "polished": (
                "\n\nMODIFY LEVEL: POLISHED\nProduce polished professional prose. "
                "Improve sentence flow and paragraphing, but never add new information."
            ),
        }
        self.cleanup_prompt.setPlainText(base + additions.get(preset, ""))

    def save_settings(self):
        manual_openai = self.openai.text().strip()
        if manual_openai:
            try:
                credential_store.set_secret(
                    credential_store.OPENAI_TARGET, manual_openai,
                    username="DeYaz manual OpenAI key",
                )
                self.conf["openai_api_key"] = ""
                self.openai.clear()
            except credential_store.CredentialStoreError as exc:
                QMessageBox.warning(self, "OpenAI", str(exc))
                return
        manual_openrouter = self.openrouter.text().strip()
        if manual_openrouter:
            try:
                credential_store.set_secret(
                    credential_store.OPENROUTER_TARGET, manual_openrouter,
                    username="DeYaz manual key",
                )
                self.conf["openrouter_api_key"] = ""
                self.openrouter.clear()
            except credential_store.CredentialStoreError as exc:
                QMessageBox.warning(self, "OpenRouter", str(exc))
                return
        self.conf["language"] = self.language.currentData()
        self.conf["ui_language"] = self.ui_language.currentData()
        self.conf["appearance"] = self.appearance_preference
        self.conf["transcribe_provider"] = self.provider.currentData()
        if self.provider.currentData() == "openrouter":
            self.conf["openrouter_transcribe_model"] = self.transcribe_model.text().strip()
        else:
            self.conf["transcribe_model"] = self.transcribe_model.text().strip()
        self.conf["cleanup_enabled"] = self.cleanup.isChecked()
        self.conf["cleanup_provider"] = self.provider.currentData()
        self.conf["cleanup_model"] = self.cleanup_model.currentData()
        if self.provider.currentData() == "openrouter":
            self.conf["openrouter_cleanup_model"] = self.cleanup_model.currentData()
        else:
            self.conf["openai_cleanup_model"] = self.cleanup_model.currentData()
        self.conf["cleanup_reasoning"] = self.reasoning.currentData()
        self.conf["cleanup_prompt"] = self.cleanup_prompt.toPlainText().strip()
        self.conf["transcribe_prompt"] = self.glossary.toPlainText().strip()
        self.conf["work_mode"] = (
            "meeting_notes_live"
            if getattr(self, "current_surface", "dictation") == "meeting"
            else self.work_mode.currentData()
        )
        self.conf["auto_paste"] = self.auto_paste.isChecked()
        self.conf["context_enabled"] = self.context_enabled.isChecked()
        self.conf["context_project_dir"] = self.context_dir.text().strip()
        self.conf["keep_audio"] = self.keep_audio.isChecked()
        self.conf["meeting_keep_audio"] = self.meeting_keep_audio.isChecked()
        self.conf["meeting_result_type"] = self.meeting_result_type.currentData()
        self.conf["history_limit"] = self.history_limit.value()
        self.conf["file_language"] = self.file_language.currentData()
        self.conf["file_output_language"] = self.file_output_language.currentData()
        self.conf["file_result_type"] = self.file_result_type.currentData()
        self.conf["file_summary_focus"] = self.file_summary_focus.text().strip()
        self.conf["file_cleanup"] = self.file_cleanup.isChecked()
        self.conf["file_timestamps"] = self.file_timestamps.isChecked()
        self.conf["windows_mic_device"] = self.microphone.currentData() or ""
        if hasattr(self, "meeting_microphone"):
            self.conf["meeting_mic_target"] = (
                self.meeting_microphone.currentData() or ""
            )
        self.conf["modify_preset"] = self.modify_preset.currentData()
        old_hotkey = self.conf["windows_hotkey"]
        self.conf["windows_hotkey"] = self.hotkey_choice.currentData()
        self.conf["mini_corner"] = self.mini_corner.currentData()
        screen = QApplication.primaryScreen().availableGeometry()
        self.conf["mini_position_y"] = (
            screen.top() + 70 if self.conf["mini_corner"].startswith("top")
            else screen.bottom() - 74 - 70
        )
        self.conf.save()
        self.apply_work_mode_visual(self.conf["work_mode"])
        self.recorder.device = self.conf["windows_mic_device"] or None
        self.bubble.dock_side = (
            "left" if self.conf["mini_corner"].endswith("left") else "right"
        )
        self.bubble.snap_y = self.conf["mini_position_y"]
        if self.bubble.isVisible():
            self.bubble.place()
        if old_hotkey != self.conf["windows_hotkey"]:
            self.hotkey.restart(self.conf["windows_hotkey"])
        self.shortcut_chip.setText(
            f"⌨  {self.conf['windows_hotkey'].upper()}"
        )
        self.on_hotkey_registration(
            self.hotkey.registered,
            self.hotkey.shortcut if self.hotkey.registered else self.hotkey.error,
        )
        self.context_value.setText(
            i18n.t("Avtomatik") if self.conf["context_enabled"] else i18n.t("Sönülü")
        )
        self.paste_value.setText(
            i18n.t("Aktivdir") if self.conf["auto_paste"] else i18n.t("Sönülü")
        )
        self.quick_hotkey.blockSignals(True)
        self.quick_hotkey.setCurrentIndex(
            max(0, self.quick_hotkey.findData(self.conf["windows_hotkey"]))
        )
        self.quick_hotkey.blockSignals(False)
        self.quick_context.blockSignals(True)
        self.quick_context.setChecked(self.conf["context_enabled"])
        self.quick_context.blockSignals(False)
        self.quick_paste.blockSignals(True)
        self.quick_paste.setChecked(self.conf["auto_paste"])
        self.quick_paste.blockSignals(False)
        self.refresh_openrouter_connection()
        self.refresh_auth_gate()
        self.set_status("Ayarlar yadda saxlanıldı")

    def refresh_openrouter_connection(self):
        key = self.conf.openrouter_key()
        connected = bool(key)
        self.oauth_status.setText(
            i18n.t("Qoşulub · açar Windows Credential Manager-dədir")
            if connected else i18n.t("Qoşulmayıb")
        )
        self.oauth_dot.setProperty("connected", connected)
        self.oauth_dot.style().unpolish(self.oauth_dot)
        self.oauth_dot.style().polish(self.oauth_dot)
        self.oauth_connect.setText(
            i18n.t("Yenidən qoş") if connected else i18n.t("OpenRouter ilə qoşul")
        )
        self.oauth_disconnect.setVisible(connected)
        if hasattr(self, "account_card"):
            self.account_card.setVisible(connected)
            if connected:
                try:
                    self.account_credit.clicked.disconnect()
                except TypeError:
                    pass
                self.account_credit.clicked.connect(self.open_openrouter_credits)
                self.set_openrouter_account_state(
                    "loading", "Balans yoxlanılır…",
                    "OpenRouter hesab vəziyyəti alınır",
                )
                self.account_worker.refresh(key)
        self.refresh_auth_gate()

    def open_openrouter_credits(self):
        QDesktopServices.openUrl(QUrl("https://openrouter.ai/settings/credits"))

    def set_openrouter_account_state(self, state, title, detail):
        if not hasattr(self, "account_card"):
            return
        self.account_card.setProperty("state", state)
        self.account_title.setText(i18n.t(title))
        self.account_detail.setText(i18n.t(detail))
        self.account_icon.setText({
            "healthy": "✓", "warning": "!", "empty": "!", "loading": "↗"
        }.get(state, "·"))
        self.account_credit.setText(
            i18n.t("Balansı idarə et")
            if state == "healthy" else i18n.t("Kredit əlavə et")
        )
        self.account_card.style().unpolish(self.account_card)
        self.account_card.style().polish(self.account_card)

    def openrouter_account_loaded(self, info):
        key_remaining = info.get("limit_remaining")
        balance = info.get("account_balance")
        free_tier = bool(info.get("is_free_tier"))
        if key_remaining is not None and float(key_remaining) <= 0:
            self.set_openrouter_account_state(
                "empty", "Bu açarın istifadə limiti bitib",
                "Hesabda kredit olsa da bu açarın ayrıca limiti $0-dır. Açar limitini yeniləyin.",
            )
            return
        if balance is not None and float(balance) <= 0:
            self.set_openrouter_account_state(
                "empty", "OpenRouter balansı bitib",
                "Ödənişli audio transkripsiyası üçün hesabınıza kredit əlavə edin.",
            )
            return
        amount = (
            i18n.t("${amount} hesab balansı", amount=f"{float(balance):.2f}")
            if balance is not None else (
                i18n.t("${amount} açar limiti qalıb", amount=f"{float(key_remaining):.2f}")
                if key_remaining is not None else i18n.t("Açar aktivdir")
            )
        )
        if free_tier:
            self.set_openrouter_account_state(
                "warning", "Pulsuz hesab rejimi",
                i18n.t(
                    "{amount}. Pulsuz cleanup işləyir; audio transkripsiyası kredit tələb edə bilər.",
                    amount=amount,
                ),
            )
        else:
            self.set_openrouter_account_state(
                "healthy", "OpenRouter istifadəyə hazırdır",
                i18n.t(
                    "{amount}. DeYaz OpenAI açarı olmadan işləyə bilər.",
                    amount=amount,
                ),
            )

    @staticmethod
    def is_openrouter_auth_error(message):
        lowered = str(message).lower()
        if "model provider" in lowered or "transkripsiya modeli" in lowered:
            return False
        return (
            "401" in lowered
            or "rejected the api key" in lowered
            or "açarını qəbul etmədi" in lowered
            or "invalid api key" in lowered
        )

    @staticmethod
    def is_model_provider_error(message):
        lowered = str(message).lower()
        return (
            "model provider" in lowered
            or "provider returned" in lowered
            or "başqa transkripsiya modeli" in lowered
        )

    def mark_openrouter_auth_invalid(self, message=""):
        self.set_openrouter_account_state(
            "empty", "OpenRouter bağlantısı yenilənməlidir",
            "Saxlanmış açar qəbul edilmədi. Yenidən qoşulmaq üçün düyməyə basın.",
        )
        self.oauth_status.setText(i18n.t("Açar etibarsızdır və ya ləğv edilib"))
        self.oauth_dot.setProperty("connected", False)
        self.oauth_dot.style().unpolish(self.oauth_dot)
        self.oauth_dot.style().polish(self.oauth_dot)
        self.oauth_connect.setText(i18n.t("Yenidən qoş"))
        self.account_credit.setText(i18n.t("Yenidən qoş"))
        try:
            self.account_credit.clicked.disconnect()
        except TypeError:
            pass
        self.account_credit.clicked.connect(self.connect_openrouter)

    def openrouter_account_failed(self, message):
        if self.is_openrouter_auth_error(message):
            self.mark_openrouter_auth_invalid(message)
            return
        self.set_openrouter_account_state(
            "warning", "Balans hazırda oxunmadı",
            "OpenRouter cavab vermədi. Bir az sonra yenidən yoxlanacaq.",
        )

    def connect_openrouter(self):
        self.oauth_connect.setEnabled(False)
        self.oauth_connect.setText(i18n.t("Brauzer gözlənilir…"))
        self.oauth_status.setText(i18n.t("OpenRouter icazə səhifəsini tamamlayın"))
        self.set_status("OpenRouter giriş səhifəsi brauzerdə açıldı")
        self.oauth_worker.start()

    def openrouter_oauth_connected(self, key):
        try:
            credential_store.set_secret(
                credential_store.OPENROUTER_TARGET, key,
                username="DeYaz OpenRouter OAuth",
            )
        except credential_store.CredentialStoreError as exc:
            self.openrouter_oauth_failed(str(exc))
            return
        self.conf["openrouter_api_key"] = ""
        self.conf["transcribe_provider"] = "openrouter"
        self.conf.save()
        self.provider.setCurrentIndex(max(0, self.provider.findData("openrouter")))
        self.openrouter.clear()
        self.oauth_connect.setEnabled(True)
        self.refresh_openrouter_connection()
        self.set_status("OpenRouter hesabı uğurla qoşuldu")
        self.show_model_selector(first_run=True)
        self.check_openrouter_credit()

    def openrouter_oauth_failed(self, message):
        self.oauth_connect.setEnabled(True)
        self.refresh_openrouter_connection()
        self.set_status("OpenRouter bağlantısı tamamlanmadı")
        QMessageBox.warning(self, "OpenRouter", i18n.t(message))

    def disconnect_openrouter(self):
        answer = QMessageBox.question(
            self, i18n.t("OpenRouter əlaqəsini kəs"),
            i18n.t("DeYaz-da saxlanmış OpenRouter girişini silmək istəyirsiniz?"),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            credential_store.delete_secret(credential_store.OPENROUTER_TARGET)
        except credential_store.CredentialStoreError as exc:
            QMessageBox.warning(self, "OpenRouter", str(exc))
            return
        self.conf["openrouter_api_key"] = ""
        self.conf.save()
        self.openrouter.clear()
        self.refresh_openrouter_connection()
        self.set_status("OpenRouter əlaqəsi kəsildi")

    def maybe_show_model_onboarding(self):
        if self.conf.openrouter_key() and not self.conf.openai_key():
            self.conf["transcribe_provider"] = "openrouter"
            self.conf.save()
            self.provider.setCurrentIndex(max(0, self.provider.findData("openrouter")))
        if (self.conf.openrouter_key()
                and not self.conf.get("model_onboarding_complete", False)):
            self.show_model_selector(first_run=True)

    def show_model_selector(self, first_run=False):
        provider = self.provider.currentData()
        is_openrouter = provider == "openrouter"
        key = self.conf.openrouter_key() if is_openrouter else self.conf.openai_key()
        if not key:
            QMessageBox.information(
                self, i18n.t("Model seçimi"),
                i18n.t("Əvvəl OpenRouter hesabını qoşun, sonra modelləri seçin."
                 if is_openrouter else
                 "Əvvəl OpenAI API açarını daxil edib ayarları yadda saxlayın."),
            )
            return
        try:
            if is_openrouter:
                available = set(api.openrouter_models(key))
                available.update(api.openrouter_models(key, transcription=True))
            else:
                available = set(api.openai_models(
                    key, self.conf["openai_base_url"], transcription=False
                ))
        except api.ApiError:
            available = set()
        transcription_choices = (
            OPENROUTER_TRANSCRIPTION_CHOICES if is_openrouter
            else OPENAI_TRANSCRIPTION_CHOICES
        )
        cleanup_choices = (
            OPENROUTER_CLEANUP_CHOICES if is_openrouter
            else OPENAI_CLEANUP_CHOICES
        )
        current_transcription = (
            self.conf["openrouter_transcribe_model"] if is_openrouter
            else self.conf["transcribe_model"]
        )
        current_cleanup = (
            self.conf["openrouter_cleanup_model"] if is_openrouter
            else self.conf["openai_cleanup_model"]
        )
        dialog = ModelOnboardingDialog(
            self, available, current_transcription, current_cleanup,
            transcription_choices, cleanup_choices,
            "OpenRouter" if is_openrouter else "OpenAI",
        )
        dialog.setStyleSheet(self.styleSheet())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        transcription, cleanup = dialog.selected_models()
        if not transcription or not cleanup:
            return
        self.conf["transcribe_provider"] = provider
        self.conf["cleanup_provider"] = provider
        if is_openrouter:
            self.conf["openrouter_transcribe_model"] = transcription
            self.conf["openrouter_cleanup_model"] = cleanup
        else:
            self.conf["transcribe_model"] = transcription
            self.conf["openai_cleanup_model"] = cleanup
        self.conf["cleanup_model"] = cleanup
        self.conf["model_onboarding_complete"] = True
        self.conf.save()
        self.transcribe_model.setText(transcription)
        self.refresh_dictation_models()
        self.refresh_file_transcribe_models()
        cleanup_index = self.cleanup_model.findData(cleanup)
        if cleanup_index < 0:
            self.cleanup_model.addItem(cleanup, cleanup)
            cleanup_index = self.cleanup_model.findData(cleanup)
        self.cleanup_model.setCurrentIndex(cleanup_index)
        self.sync_text_model_display()
        self.set_status("Model seçimi yadda saxlanıldı")

    def check_openrouter_credit(self):
        key = self.conf.openrouter_key()
        if not key:
            return
        try:
            info = api.openrouter_account_info(key)
            key_remaining = info.get("limit_remaining")
            balance = info.get("account_balance")
        except api.ApiError:
            return
        self.openrouter_account_loaded(info)
        if key_remaining is not None and float(key_remaining) <= 0:
            self.show_credit_dialog(
                "OpenRouter", "Bu açarın ayrıca istifadə limiti bitib (qalıq: $0)."
            )
        elif balance is not None and float(balance) <= 0:
            self.show_credit_dialog(
                "OpenRouter", "Hesab balansı bitib (qalıq: $0)."
            )

    def show_credit_dialog(self, provider, message):
        dialog = CreditDialog(self, provider, message)
        dialog.setStyleSheet(self.styleSheet())
        result = dialog.exec()
        if result == 2:
            self.show_model_selector()

    def handle_credit_error(self, message):
        lowered = message.lower()
        if "402" not in lowered and "out of credit" not in lowered and "kredit" not in lowered:
            return False
        provider = "OpenRouter" if "openrouter" in lowered else (
            "OpenAI" if "openai" in lowered else
            ("OpenRouter" if self.conf["transcribe_provider"] == "openrouter" else "OpenAI")
        )
        if provider == "OpenRouter":
            self.set_openrouter_account_state(
                "empty", "OpenRouter krediti yoxdur",
                "Sorğu HTTP 402 ilə dayandırıldı. Kredit əlavə etdikdən sonra yenidən yoxlayın.",
            )
        self.show_credit_dialog(provider, message)
        return True

    def handle_auth_error(self, message):
        if not self.is_openrouter_auth_error(message):
            return False
        self.mark_openrouter_auth_invalid(message)
        dialog = QMessageBox(self)
        dialog.setWindowTitle(i18n.t("OpenRouter bağlantısı"))
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(i18n.t("OpenRouter bağlantısını yeniləmək lazımdır"))
        dialog.setInformativeText(i18n.t(
            "Saxlanmış açar qəbul edilmədi. Brauzerdə yenidən giriş etdikdən "
            "sonra transkripsiyanı təkrar başladın."
        ))
        reconnect = dialog.addButton(i18n.t("Yenidən qoş"), QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(i18n.t("Sonra"), QMessageBox.ButtonRole.RejectRole)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()
        if dialog.clickedButton() is reconnect:
            self.connect_openrouter()
        return True

    def handle_model_provider_error(self, message):
        if not self.is_model_provider_error(message):
            return False
        dialog = QMessageBox(self)
        dialog.setWindowTitle(i18n.t("Transkripsiya modeli"))
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(i18n.t("Seçilmiş model hazırda işləmir"))
        dialog.setInformativeText(i18n.t(
            "OpenRouter bu modelin providerinə sorğunu ötürə bilmədi. "
            "Testdən keçmiş başqa transkripsiya modeli seçin."
        ))
        choose = dialog.addButton(i18n.t("Modeli dəyiş"), QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(i18n.t("Sonra"), QMessageBox.ButtonRole.RejectRole)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()
        if dialog.clickedButton() is choose:
            self.show_model_selector()
        return True

    def browse_context_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self, i18n.t("Project context qovluğunu seç"),
            self.context_dir.text().strip() or str(Path.home())
        )
        if selected:
            self.context_dir.setText(selected)

    def open_context_manager(self):
        ContextManagerDialog(self).exec()

    def open_context_add_dialog(self):
        ContextAddDialog(self).exec()

    def _context_items(self):
        items = self.conf.get("context_items", []) or []
        normalized = [dict(item) for item in items if isinstance(item, dict)]
        legacy_path = (self.conf.get("context_project_dir", "") or "").strip()
        if legacy_path and not any(
            item.get("kind") == "project" for item in normalized
        ):
            normalized.insert(0, {
                "label": Path(legacy_path).name or "Proyekt",
                "text": "",
                "path": legacy_path,
                "kind": "project",
                "enabled": True,
            })
        return normalized

    def _save_context_items(self, items):
        # Context may contain many references, but at most one explicit project.
        # Keeping this invariant in persistence also protects non-UI callers.
        selected_project = ""
        has_selected_project = False
        for item in items:
            if item.get("kind") != "project" or not item.get("enabled", False):
                continue
            if has_selected_project:
                item["enabled"] = False
            else:
                selected_project = (item.get("path") or "").strip()
                has_selected_project = True
        self.conf["context_items"] = items
        self.conf["context_project_dir"] = selected_project
        if hasattr(self, "context_dir"):
            self.context_dir.setText(selected_project)
        self.conf["context_enabled"] = True
        if hasattr(self, "context_enabled"):
            self.context_enabled.setChecked(True)
        self.conf.save()

    def add_manual_context(self, label, text, kind="text", path=""):
        text = (text or "").strip()
        path = (path or "").strip()
        if not text and not path:
            return False
        items = self._context_items()
        items.append({
            "label": (label or "Kontekst")[:80],
            "text": text[:24000],
            "path": path,
            "kind": kind,
            "enabled": True,
        })
        self._save_context_items(items[-12:])
        return True

    def add_project_context(self):
        selected = QFileDialog.getExistingDirectory(
            self, i18n.t("Project context qovluğunu seç"),
            self.conf["context_project_dir"] or str(Path.home()),
        )
        if not selected:
            return False
        self.conf["context_project_dir"] = selected
        if hasattr(self, "context_dir"):
            self.context_dir.setText(selected)
        items = []
        for item in self._context_items():
            if item.get("kind") == "project":
                if item.get("path") == selected:
                    continue
                item["enabled"] = False
            items.append(item)
        items.insert(0, {
            "label": Path(selected).name or "Proyekt",
            "text": "",
            "path": selected,
            "kind": "project",
            "enabled": True,
        })
        self._save_context_items(items[-12:])
        return True

    def add_context_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("Kontekst faylı seç"),
            self.conf["context_project_dir"] or str(Path.home()),
            i18n.t("Mətn faylları (*.txt *.md *.json *.yaml *.yml *.py *.js *.ts *.tsx "
                   "*.jsx *.html *.css *.csv);;Bütün fayllar (*.*)"),
        )
        if not path:
            return False
        try:
            raw = Path(path).read_bytes()
            if len(raw) > 1_000_000:
                raise ValueError(i18n.t("Kontekst faylı 1 MB-dan böyük ola bilməz."))
            content = raw.decode("utf-8", errors="replace").strip()
            if not content:
                raise ValueError(i18n.t("Faylda oxuna bilən mətn yoxdur."))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, i18n.t("Kontekst"), str(exc))
            return False
        return self.add_manual_context(Path(path).name, content, "file", path)

    def set_context_item_enabled(self, index, enabled):
        items = self._context_items()
        if 0 <= index < len(items):
            if items[index].get("kind") == "project" and enabled:
                for row, item in enumerate(items):
                    if item.get("kind") == "project":
                        item["enabled"] = row == index
            else:
                items[index]["enabled"] = bool(enabled)
            self._save_context_items(items)

    def selected_project_context_path(self):
        for item in self._context_items():
            if item.get("kind") == "project" and item.get("enabled", False):
                return (item.get("path") or "").strip()
        return ""

    def manual_context_text(self):
        chunks = []
        for item in self._context_items():
            if not item.get("enabled", True) or item.get("kind") == "project":
                continue
            text = (item.get("text") or "").strip()
            if text:
                chunks.append(f"{item.get('label', 'Kontekst')}:\n{text}")
        return "\n\n".join(chunks)[:16000]

    def load_file_media(self, path):
        video_suffixes = {
            ".mp4", ".mkv", ".webm", ".mov", ".avi", ".mpeg", ".mpg", ".m4v",
        }
        self.file_player.stop()
        self.file_media_is_video = Path(path).suffix.lower() in video_suffixes
        self.file_preview_stack.setCurrentIndex(1 if self.file_media_is_video else 0)
        if not self.file_media_is_video:
            self.file_wave.set_media(path)
        self.file_subtitle.clear()
        self.file_subtitle.hide()
        self.file_seek.setRange(0, 0)
        self.file_position_label.setText("00:00")
        self.file_duration_label.setText("00:00")
        self.file_play.setIcon(line_icon("play", "#202321", 26))
        self.file_play.setToolTip("Oynat")
        self.file_player.setSource(QUrl.fromLocalFile(path))
        self.file_transport.show()

    def toggle_file_media(self):
        if not self.file_path:
            return
        if (
            self.file_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        ):
            self.file_player.pause()
        else:
            self.file_player.play()

    def skip_file_media(self, delta):
        duration = max(0, self.file_player.duration())
        position = max(0, self.file_player.position() + int(delta))
        if duration:
            position = min(duration, position)
        self.file_player.setPosition(position)

    def seek_file_media(self, position):
        self.file_player.setPosition(max(0, int(position)))

    def file_media_duration_changed(self, duration):
        duration = max(0, int(duration or 0))
        self.file_seek.setRange(0, duration)
        self.file_duration_label.setText(format_media_time(duration))
        self.file_wave.set_position(self.file_player.position(), duration)

    def file_media_position_changed(self, position):
        position = max(0, int(position or 0))
        if not self.file_seek.isSliderDown():
            self.file_seek.setValue(position)
        self.file_position_label.setText(format_media_time(position))
        self.file_wave.set_position(position, self.file_player.duration())
        caption = (
            subtitle_at_position(self.file_segments, position)
            if getattr(self, "file_media_is_video", False) else ""
        )
        self.file_subtitle.setText(caption)
        self.file_subtitle.setVisible(bool(caption))

    def file_media_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.file_wave.set_playing(playing)
        self.file_video.set_playing(
            playing and getattr(self, "file_media_is_video", False)
        )
        self.file_play.setIcon(
            line_icon("pause" if playing else "play", "#202321", 26)
        )
        self.file_play.setToolTip(i18n.t("Pauza") if playing else i18n.t("Oynat"))

    def file_media_error(self, _error, message=""):
        if message:
            self.file_status.setText(i18n.t("Media player: {message}", message=message))
            self.file_status.show()

    def transcribe_audio_file(self):
        if self.file_pipeline.busy:
            self.file_status.setText(i18n.t("Başqa bir əməliyyat hazırda davam edir."))
            return
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("Səs və ya video faylı seç"),
            self.conf["file_last_dir"] or str(Path.home()),
            i18n.t("Audio və video (*.mp3 *.wav *.m4a *.ogg *.opus *.flac *.aac *.wma "
                   "*.mp4 *.mkv *.webm *.mov *.avi *.mpeg *.mpga);;Bütün fayllar (*.*)")
        )
        if not path:
            return
        self.file_path = path
        self.conf["file_last_dir"] = os.path.dirname(path)
        self.conf.save()
        size = os.path.getsize(path)
        size_text = (
            f"{size / (1024 * 1024):.1f} MB"
            if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"
        )
        kind = i18n.t("VİDEO") if Path(path).suffix.lower() in {
            ".mp4", ".mkv", ".webm", ".mov", ".avi", ".mpeg"
        } else i18n.t("AUDIO")
        self.file_selected.setText(
            f"{kind}  •  {Path(path).name}  •  {size_text}"
        )
        self.file_selected.show()
        self.file_selected.setToolTip(path)
        self.load_file_media(path)
        self.file_status.setText(i18n.t("Fayl hazırdır — seçimləri yoxlayıb başlat."))
        self.file_run.setEnabled(self.file_model_credentials_ready())

    def update_file_option_state(self):
        if not hasattr(self, "file_result_type"):
            return
        transcript = self.file_result_type.currentData() == "transcript"
        self.file_timestamps.setEnabled(transcript)
        self.file_save_srt.setEnabled(
            transcript and bool(getattr(self, "file_segments", []))
        )
        self.file_summary_focus.setEnabled(True)

    def start_file_transcription(self):
        if not self.file_model_credentials_ready():
            self.open_api_settings()
            return
        if not self.file_path or self.file_pipeline.busy:
            return
        if self.pipeline.busy or self.recorder.active:
            self.file_status.setText(
                i18n.t("Əvvəl mikrofon yazısının emalını tamamla, sonra faylı başlat.")
            )
            return
        # The visible API/model fields should apply even before the user presses Save.
        manual_openai = self.openai.text().strip()
        if manual_openai:
            try:
                credential_store.set_secret(
                    credential_store.OPENAI_TARGET, manual_openai,
                    username="DeYaz manual OpenAI key",
                )
                self.conf["openai_api_key"] = ""
                self.openai.clear()
            except credential_store.CredentialStoreError as exc:
                self.file_status.setText(str(exc))
                return
        manual_openrouter = self.openrouter.text().strip()
        if manual_openrouter:
            try:
                credential_store.set_secret(
                    credential_store.OPENROUTER_TARGET, manual_openrouter,
                    username="DeYaz manual key",
                )
                self.conf["openrouter_api_key"] = ""
                self.openrouter.clear()
                self.refresh_openrouter_connection()
            except credential_store.CredentialStoreError as exc:
                self.file_status.setText(str(exc))
                return
        selected_file_model = self.file_transcribe_model.currentData() or ""
        if "|" in selected_file_model:
            file_provider, file_model = selected_file_model.split("|", 1)
            self.conf["file_transcribe_provider"] = file_provider
            self.conf["file_transcribe_model"] = file_model
        self.conf["cleanup_provider"] = self.provider.currentData()
        self.conf["cleanup_model"] = self.cleanup_model.currentData()
        if self.provider.currentData() == "openrouter":
            self.conf["openrouter_cleanup_model"] = self.cleanup_model.currentData()
        else:
            self.conf["openai_cleanup_model"] = self.cleanup_model.currentData()
        self.conf["cleanup_reasoning"] = self.reasoning.currentData()
        self.conf["transcribe_prompt"] = self.glossary.toPlainText().strip()
        self.conf["file_language"] = self.file_language.currentData()
        self.conf["file_output_language"] = self.file_output_language.currentData()
        self.conf["file_result_type"] = self.file_result_type.currentData()
        self.conf["file_summary_focus"] = self.file_summary_focus.text().strip()
        self.conf["file_cleanup"] = self.file_cleanup.isChecked()
        self.conf["file_timestamps"] = self.file_timestamps.isChecked()
        self.conf.save()

        self.file_output.clear()
        self.file_status.show()
        self.file_segments = []
        self.file_save_srt.setEnabled(False)
        self.file_button.setEnabled(False)
        self.file_result_type.setEnabled(False)
        self.file_run.setEnabled(False)
        self.file_stop.setEnabled(True)
        self.file_progress.setRange(0, 0)
        self.file_progress.show()
        self.bubble.set_state("transcribing")
        self.file_pipeline.start(
            self.file_path,
            self.file_timestamps.isChecked()
            and self.file_result_type.currentData() == "transcript",
            self.file_cleanup.isChecked(),
            language=self.file_language.currentData(),
            result_type=self.file_result_type.currentData(),
            output_language=self.file_output_language.currentData(),
            summary_focus=self.file_summary_focus.text().strip(),
        )

    def stop_file_transcription(self):
        if self.file_pipeline.busy:
            self.file_status.setText(i18n.t("Dayandırılır…"))
            self.file_pipeline.stop()

    def on_file_progress(self, message):
        self.file_status.show()
        shown = i18n.t(message)
        self.file_status.setText(shown)
        self.status.setText(shown)
        if message == "Stopped." or shown == "Əməliyyat dayandırıldı.":
            self.file_idle()
            self.bubble.set_state("idle")

    def on_file_finished(self, text, segments, warning=""):
        self.file_output.setPlainText(text)
        self.file_clear.setEnabled(bool(text.strip()))
        self.file_segments = segments
        self.file_wave.set_cues(segments)
        self.file_media_position_changed(self.file_player.position())
        self.app.clipboard().setText(text)
        if warning:
            self.file_status.setText(warning)
            self.status.setText(i18n.t("Xam transkript qorundu; son emal alınmadı"))
            self.bubble.set_state("error", warning)
        else:
            self.file_status.setText(
                i18n.t(
                    "Hazırdır  •  {count} simvol  •  clipboard-a köçürüldü",
                    count=f"{len(text):,}",
                )
            )
            self.status.setText(i18n.t("Fayl transkripsiyası hazırdır"))
            self.bubble.set_state("success")
        self.file_save_srt.setEnabled(
            bool(segments) and self.file_result_type.currentData() == "transcript"
        )
        cfg.append_history({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": 0,
            "elapsed": 0,
            "model": self.conf.file_transcribe_target().model,
            "raw": text,
            "text": text,
            "mode": f"file:{self.file_result_type.currentData()}",
            "source": Path(self.file_path).name,
            "output_language": self.file_output_language.currentData(),
        })
        self.refresh_history()
        self.file_idle()

    def on_file_failed(self, message):
        self.file_status.setText(i18n.t("Xəta: {message}", message=message))
        self.status.setText(i18n.t("Fayl emal edilə bilmədi"))
        self.bubble.set_state("error", message)
        self.file_idle()
        self.handle_credit_error(message)

    def file_idle(self):
        ready = self.has_model_credentials()
        self.file_button.setEnabled(ready)
        self.file_result_type.setEnabled(True)
        self.file_run.setEnabled(
            self.file_model_credentials_ready() and bool(self.file_path)
        )
        self.file_stop.setEnabled(False)
        self.file_progress.setRange(0, 1)
        self.file_progress.setValue(0)
        self.file_progress.hide()

    def copy_file_output(self):
        text = self.file_output.toPlainText().strip()
        if text:
            self.app.clipboard().setText(text)
            self.file_status.setText(i18n.t("Nəticə clipboard-a köçürüldü."))

    def clear_file_result(self):
        self.file_output.clear()
        self.file_segments = []
        self.file_save_srt.setEnabled(False)
        if hasattr(self, "file_wave"):
            self.file_wave.set_cues([])
        if hasattr(self, "file_subtitle"):
            self.file_subtitle.clear()
        self.file_status.setText(i18n.t("Nəticə təmizləndi"))
        self.file_clear.setEnabled(False)

    def save_file_output(self):
        self.write_file_result(
            self.file_output.toPlainText(), ".txt", "Mətn faylı (*.txt)"
        )

    def save_file_srt(self):
        text = filetranscribe.to_srt(
            self.file_output.toPlainText(), self.file_segments
        )
        if not text:
            self.file_status.setText(i18n.t("SRT üçün zaman damğalı transkript yoxdur."))
            return
        self.write_file_result(text, ".srt", "Subtitr faylı (*.srt)")

    def write_file_result(self, text, suffix, file_filter):
        if not text.strip():
            self.file_status.setText(i18n.t("Saxlanacaq nəticə yoxdur."))
            return
        base = Path(self.file_path).stem if self.file_path else "transcript"
        start = str(Path(self.conf["file_last_dir"] or Path.home()) / f"{base}{suffix}")
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.t("Nəticəni saxla"), start, i18n.t(file_filter)
        )
        if not path:
            return
        if not path.lower().endswith(suffix):
            path += suffix
        try:
            Path(path).write_text(text, encoding="utf-8")
            self.file_status.setText(i18n.t("Saxlanıldı: {path}", path=path))
        except OSError as exc:
            self.file_status.setText(i18n.t("Saxlanmadı: {error}", error=exc))

    def show_bubble_menu(self, global_position):
        menu = QMenu()
        title = menu.addAction(i18n.t("DEYAZ  •  CONTROL"))
        title.setEnabled(False)
        menu.addSeparator()
        work_modes_menu = menu.addMenu(i18n.t("DeYaz work mode"))
        for mode_id, item in all_modes().items():
            if mode_id == "meeting_notes_live":
                continue
            action = work_modes_menu.addAction(
                color_icon(item["color"]), i18n.t(item["name"])
            )
            action.setCheckable(True)
            action.setChecked(self.conf["work_mode"] == mode_id)
            action.triggered.connect(
                lambda _checked=False, value=mode_id: self.select_work_mode(value)
            )
        modes = menu.addMenu(i18n.t("Modify səviyyəsi"))
        for label, preset in [
            ("Minimal", "minimal"), ("Balanced", "balanced"),
            ("Polished", "polished"),
        ]:
            action = modes.addAction(i18n.t(label))
            action.setCheckable(True)
            action.setChecked(self.conf["modify_preset"] == preset)
            action.triggered.connect(
                lambda _checked=False, value=preset: self.set_modify_mode(value)
            )
        context_menu = menu.addMenu(i18n.t("Project context"))
        auto_context = context_menu.addAction(i18n.t("Auto context"))
        auto_context.setCheckable(True)
        auto_context.setChecked(self.conf["context_enabled"])
        auto_context.triggered.connect(self.toggle_context)
        choose_context = context_menu.addAction(i18n.t("Fallback qovluq seç…"))
        choose_context.triggered.connect(
            lambda: QTimer.singleShot(0, self.choose_context_from_menu)
        )
        inspect_context = context_menu.addAction(i18n.t("Cari konteksti göstər"))
        inspect_context.triggered.connect(
            lambda: QTimer.singleShot(0, self.show_current_context)
        )
        menu.addSeparator()
        recent = menu.addAction(i18n.t("Son nəticələr"))
        recent.triggered.connect(
            lambda: QTimer.singleShot(
                0, lambda: self.open_recent_results(global_position)
            )
        )
        menu.addSeparator()
        settings = menu.addAction(i18n.t("Əsas ayarları aç"))
        hide = menu.addAction(i18n.t("Mini düyməni gizlət"))
        menu.addSeparator()
        quit_action = menu.addAction(i18n.t("DeYaz-ı dayandır"))
        settings.triggered.connect(self.open_main_settings)
        hide.triggered.connect(self.bubble.hide)
        quit_action.triggered.connect(self.quit)
        menu.exec(global_position)

    def set_modify_mode(self, preset):
        index = self.modify_preset.findData(preset)
        if index >= 0:
            self.modify_preset.setCurrentIndex(index)
        self.conf["modify_preset"] = preset
        self.conf["cleanup_prompt"] = self.cleanup_prompt.toPlainText().strip()
        self.conf.save()
        self.tray.showMessage(
            "DeYaz", f"Modify mode: {preset.title()}",
            QSystemTrayIcon.MessageIcon.Information, 1600
        )

    def set_work_mode(self, mode_id):
        if mode_id not in all_modes():
            return
        index = self.work_mode.findData(mode_id)
        if index >= 0:
            self.work_mode.setCurrentIndex(index)
        if mode_id != "meeting_notes_live" and hasattr(self, "dictation_mode"):
            self._dictation_work_mode = mode_id
            self.dictation_mode.blockSignals(True)
            mode_index = self.dictation_mode.findData(mode_id)
            if mode_index >= 0:
                self.dictation_mode.setCurrentIndex(mode_index)
            self.dictation_mode.blockSignals(False)
        self.conf["work_mode"] = mode_id
        self.conf.save()
        self.apply_work_mode_visual(mode_id)
        selected = get_work_mode(mode_id)
        if self.bubble.isVisible():
            self.bubble.set_state("mode", i18n.t(selected["name"]))

    def select_work_mode(self, mode_id):
        """Select a text transformation mode and return to the DeYaz surface."""
        if mode_id == "meeting_notes_live":
            self.set_main_surface("meeting")
            return
        self._dictation_work_mode = mode_id
        self.set_work_mode(mode_id)
        self.set_main_surface("dictation", force=True)

    def dictation_surface_mode_changed(self):
        mode_id = self.dictation_mode.currentData()
        if mode_id:
            self.select_work_mode(mode_id)

    def meeting_result_type_changed(self):
        result_type = self.meeting_result_type.currentData() or "meeting_notes"
        self.conf["meeting_result_type"] = result_type
        self.conf.save()

    def meeting_models_changed(self):
        live = self.meeting_transcribe_model.currentData() or "openai|gpt-transcribe"
        text = self.meeting_text_model.currentData() or "openai|gpt-5.6-terra"
        live_provider, live_model = live.split("|", 1)
        text_provider, text_model = text.split("|", 1)
        self.conf["meeting_transcribe_provider"] = live_provider
        self.conf["meeting_transcribe_model"] = live_model
        self.conf["meeting_text_provider"] = text_provider
        self.conf["meeting_text_model"] = text_model
        self.conf["meeting_live_output_language"] = (
            self.meeting_output_language.currentData() or "original"
        )
        self.conf["meeting_language"] = (
            self.meeting_input_language.currentData() or "auto"
        )
        if hasattr(self, "meeting_microphone"):
            self.conf["meeting_mic_target"] = (
                self.meeting_microphone.currentData() or ""
            )
        self.conf.save()

    def open_recent_results(self, anchor):
        self.history_popup.refresh(cfg.read_history(5))
        self.history_popup.show_near(anchor)

    def toggle_context(self, enabled):
        self.conf["context_enabled"] = bool(enabled)
        self.context_enabled.setChecked(bool(enabled))
        self.conf.save()
        self.apply_work_mode_visual()

    def choose_context_from_menu(self):
        selected = QFileDialog.getExistingDirectory(
            None, i18n.t("Project context fallback qovluğunu seç"),
            self.conf["context_project_dir"] or str(Path.home())
        )
        if selected:
            self.conf["context_project_dir"] = selected
            self.context_dir.setText(selected)
            self.conf.save()

    def show_current_context(self):
        self.open_context_manager()

    def open_main_settings(self):
        self.show_window()
        if not self.settings_box.isVisible():
            self.toggle_settings()
        self.settings_nav.setCurrentRow(0)

    def save_bubble_position(self, side, y):
        self.conf["mini_corner"] = f"bottom-{side}"
        self.conf["mini_position_y"] = int(y)
        self.conf.save()

    def toggle_meeting(self):
        if not self.has_model_credentials():
            self.open_api_settings()
            return
        if self.meeting.active:
            self.meeting_action.setEnabled(False)
            self.record.setEnabled(False)
            self.meeting_state.setText(i18n.t("Tamamlanır…"))
            self.meeting.stop()
            return

        self.conf["meeting_keep_audio"] = self.meeting_keep_audio.isChecked()
        self.conf["meeting_result_type"] = (
            self.meeting_result_type.currentData() or "meeting_notes"
        )
        self.meeting_models_changed()
        required_targets = [self.conf.meeting_transcribe_target()]
        needs_text = (
            self.conf.get("meeting_live_output_language", "original") != "original"
            or (
                self.conf.get("meeting_result_type", "meeting_notes") != "transcript"
                and self.conf.get("meeting_cleanup", True)
            )
        )
        if needs_text:
            required_targets.append(self.conf.meeting_text_target())
        missing = [target.service for target in required_targets if not target.api_key]
        if missing:
            QMessageBox.information(
                self, i18n.t("Meeting model bağlantısı"),
                i18n.t(
                    "Əvvəl {services} bağlantısını Ayarlarda qoş.",
                    services=", ".join(dict.fromkeys(missing)),
                ),
            )
            return
        self.meeting_transcript.clear()
        self.meeting_live_items = []
        self.meeting_live_partials = {}
        self.meeting_partial_render_timer.stop()
        self.last_meeting_path = ""
        self.meeting_open.setEnabled(False)
        if not self.meeting.start():
            return
        self.meeting_result_type.setEnabled(False)
        for control in self.meeting_model_controls:
            control.setEnabled(False)
        mic_name = getattr(self.meeting.devices.get("mic"), "name", "Mikrofon")
        system_name = getattr(
            self.meeting.devices.get("system"), "name", "Sistem audio"
        )
        self.meeting_mic_source.setText(
            i18n.t("●  Sən · {microphone}", microphone=mic_name)
        )
        self.meeting_system_source.setText(
            i18n.t("●  Görüş səsi · {source}", source=system_name)
        )
        self.meeting_started_ui = time.monotonic()
        self.meeting_timer.start()
        self.meeting_state.setText(i18n.t("●  Canlı"))
        self.meeting_state.setProperty("live", True)
        self.meeting_state.style().unpolish(self.meeting_state)
        self.meeting_state.style().polish(self.meeting_state)
        self.meeting_action.setText(i18n.t("Dinlənilir…"))
        self.meeting_action.setIcon(line_icon("stop", "#202321", 82))
        self.meeting_action.setIconSize(QSize(108, 108))
        self.record.set_recording_active(True)
        self.record.setIcon(line_icon("stop", self.theme_tokens["bg"], 20))
        self.record.setText(i18n.t("Görüşü bitir"))
        self.bubble.set_state("recording")
        self.set_status("Görüş canlı transkripsiya olunur")
        output_language = self.conf.get("meeting_live_output_language", "original")
        self.detail.setText(i18n.t(
            "Danışıq təxminən hər 7 saniyədə yeni hissə kimi görünəcək."
            if output_language == "original" else
            "Hər yeni hissə seçilmiş dilə çevrilərək canlı göstəriləcək."
        ))

    def update_meeting_clock(self):
        if not self.meeting_started_ui:
            return
        self.meeting_elapsed.setText(
            meeting_timestamp(time.monotonic() - self.meeting_started_ui)
        )

    def on_meeting_segment(self, item):
        self.meeting_live_items.append(dict(item))
        source = item.get("source", "")
        partial = self.meeting_live_partials.get(source)
        if partial and float(partial.get("start", 0) or 0) < float(
            item.get("end", 0) or 0
        ):
            self.meeting_live_partials.pop(source, None)
        self._render_meeting_live_transcript()
        self.meeting_state.setText(i18n.t("●  Canlı"))

    def on_meeting_partial(self, item):
        self.meeting_live_partials[item.get("source", "mic")] = dict(item)
        if not self.meeting_partial_render_timer.isActive():
            self.meeting_partial_render_timer.start()

    def _render_meeting_live_transcript(self):
        text = compose_meeting_live_text(
            self.meeting_live_items, self.meeting_live_partials
        )
        self.meeting_transcript.setPlainText(text)
        bar = self.meeting_transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def on_meeting_level(self, source, value):
        level = self.meeting_mic_level if source == "mic" else self.meeting_system_level
        level.setValue(max(0, min(100, int(value * 100))))

    def on_meeting_status(self, text):
        self.meeting_state.setText(i18n.t(text))
        self.meeting_state.setToolTip(text)

    def _reset_meeting_controls(self):
        self.meeting_timer.stop()
        self.meeting_started_ui = 0.0
        ready = self.has_model_credentials()
        self.meeting_action.setEnabled(ready)
        self.meeting_result_type.setEnabled(True)
        for control in self.meeting_model_controls:
            control.setEnabled(True)
        self.meeting_action.setText("")
        self.meeting_action.setIcon(line_icon("mic", "#202321", 86))
        self.meeting_action.setIconSize(QSize(112, 112))
        self.meeting_state.setProperty("live", False)
        self.meeting_state.style().unpolish(self.meeting_state)
        self.meeting_state.style().polish(self.meeting_state)
        self.record.set_recording_active(False)
        self.record.setEnabled(ready)
        self.record.setIcon(line_icon("mic", "#21100c", 22))
        self.record.setText(i18n.t("Görüşü başlat"))
        self.meeting_mic_level.setValue(0)
        self.meeting_system_level.setValue(0)

    def on_meeting_finished(self, transcript, notes, path, duration):
        self._reset_meeting_controls()
        self.last_meeting_path = path
        self.meeting_open.setEnabled(True)
        self.meeting_state.setText(i18n.t("Qeydlər hazırdır"))
        self.meeting_elapsed.setText(meeting_timestamp(duration))
        result_type = self.conf.get("meeting_result_type", "meeting_notes")
        # The Live column is a transcript-only surface. Generated notes belong
        # exclusively in Result and must never be copied back into the live log.
        self.meeting_transcript.setPlainText(transcript)
        result_text = transcript if result_type == "transcript" else notes
        if hasattr(self, "meeting_result_output"):
            self.meeting_result_output.setPlainText(result_text)
        self.meeting_copy.setEnabled(bool(result_text.strip()))
        self.meeting_clear.setEnabled(bool(result_text.strip()))
        self.latest_result_text = result_text
        self.recent_time.setText(time.strftime("%H:%M"))
        self.recent_preview.setText(result_text.replace("\n", " ")[:360])
        self.app.clipboard().setText(result_text)
        self.bubble.set_state("success")
        finished_copy = {
            "transcript": ("Tam transkript hazırdır", "Danışıq dəyişdirilmədən yadda saxlanıldı."),
            "key_points": ("Əsas məqamlar hazırdır", "Mövzular və əsas key point-lər yadda saxlanıldı."),
            "detailed_summary": ("Ətraflı icmal hazırdır", "Müzakirə və nəticələr bölmələrlə yadda saxlanıldı."),
            "action_items": ("Tapşırıqlar hazırdır", "Qərarlar və action item-lər yadda saxlanıldı."),
        }
        title, detail = finished_copy.get(
            result_type,
            ("Görüş qeydləri hazırdır", "Xülasə, qərarlar və tapşırıqlar yadda saxlanıldı."),
        )
        self.set_status(title)
        self.detail.setText(i18n.t(detail))

    def on_meeting_failed(self, message):
        self._reset_meeting_controls()
        self.meeting_state.setText(i18n.t("Problem yarandı"))
        self.meeting_transcript.appendPlainText(f"\n⚠ {message}")
        self.bubble.set_state("error", message)
        self.set_status("Problem yarandı")
        self.detail.setText(message)
        self.tray.showMessage(
            "DeYaz", message, QSystemTrayIcon.MessageIcon.Warning, 5000
        )

    def copy_meeting_text(self):
        result = (
            self.meeting_result_output.toPlainText().strip()
            if hasattr(self, "meeting_result_output") else ""
        )
        text = result or self.meeting_transcript.toPlainText().strip()
        if text:
            self.app.clipboard().setText(text)
            self.meeting_copy.setText(i18n.t("Kopyalandı"))
            QTimer.singleShot(
                1300, lambda: self.meeting_copy.setText(i18n.t("Mətni kopyala"))
            )

    def clear_meeting_result(self):
        self.meeting_result_output.clear()
        self.meeting_copy.setEnabled(False)
        self.meeting_clear.setEnabled(False)
        self.meeting_state.setText(i18n.t("Nəticə təmizləndi"))

    def open_last_meeting(self):
        if self.last_meeting_path and Path(self.last_meeting_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_meeting_path))

    def toggle_recording(self):
        if not self.has_model_credentials():
            self.open_api_settings()
            return
        if self.conf["work_mode"] == "meeting_notes_live":
            self.toggle_meeting()
            return
        if self.file_pipeline.busy:
            self.set_status("Əvvəl fayl transkripsiyasını tamamla…")
            return
        if self.pipeline.busy:
            self.set_status("Əvvəlki mətn hələ işlənir…")
            return
        if self.recorder.active:
            self.record.setEnabled(False)
            self.record.set_preparing_active(True)
            self.set_status("Yazı tamamlanır…")
            self.bubble.set_state("preparing")
            self.recorder.stop()
            return
        self.current_context = self.capture_work_context()
        if hasattr(self, "dictation_microphone"):
            self.recorder.device = self.dictation_microphone.currentData() or None
        self.recorder.start()
        if self.recorder.active:
            self.bubble.set_recording(True)
            self.record.set_preparing_active(False)
            self.record.set_recording_active(True)
            self.record.setIcon(line_icon("stop", self.theme_tokens["bg"], 20))
            self.record.setIconSize(QSize(54, 54))
            self.record.setText(
                i18n.t("Dinləyirəm") if getattr(self, "_template_minimal", False)
                else i18n.t("Səsyazmanı dayandır")
            )
            self.record.setToolTip(i18n.t("Dayandır və mətnə çevir"))
            self.set_status("Dinləyirəm")
            self.detail.setText(i18n.t("Danışığını bitirəndə eyni düyməyə yenidən bas."))

    def set_level(self, value):
        self.bubble.set_level(value)
        colour = getattr(self, "mode_color", "#ed5f3b")
        self.meter.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {colour}, stop:{value:.3f} {colour}, "
            f"stop:{value:.3f} {self.theme_tokens['soft']}); border-radius: 3px;"
        )

    def transcribe(self, path, duration):
        self.bubble.set_state("transcribing")
        self.record.set_recording_active(False)
        self.record.set_preparing_active(True)
        self.record.setIcon(line_icon("mic", "#202321", 72))
        self.record.setIconSize(QSize(96, 96))
        self.record.setText(
            i18n.t("Hazırlanır…") if getattr(self, "_template_minimal", False)
            else i18n.t("Səsyazmaya başla")
        )
        self.record.setToolTip(i18n.t("Səsyazmanı başlat"))
        self.record.setEnabled(self.has_model_credentials())
        self.pipeline.run(path, duration, self.current_context)

    def capture_work_context(self):
        manual = self.manual_context_text()
        if (not self.conf["context_enabled"] or
                (self.conf["work_mode"] == "dictation" and not manual)):
            self.context_badge.setText(i18n.t("AUTO CONTEXT • SÖNÜLÜ"))
            self.context_value.setText(i18n.t("Sönülü"))
            self.bubble.set_context("")
            return None
        snapshot = capture_context(self.selected_project_context_path())
        if manual:
            combined = "\n\n".join(
                part for part in (snapshot.text.strip(), "USER SELECTED CONTEXT\n" + manual)
                if part.strip()
            )
            snapshot = ContextSnapshot(
                text=combined[:24000],
                label=snapshot.label if snapshot.project_root else "Kontekst",
                app=snapshot.app,
                title=snapshot.title,
                project_root=snapshot.project_root,
                confidence="verified",
                evidence="User-selected context",
            )
            self.context_badge.setText(
                f"{i18n.t('VERIFIED PROJECT')} • {snapshot.label.upper()}"
            )
            self.context_badge.setToolTip(snapshot.text)
            self.context_value.setText(snapshot.label)
            self.bubble.set_context(snapshot.label)
            return snapshot
        policy = project_context_policy(self.conf["work_mode"])
        if policy == "disabled" or not uses_project_context(
            self.conf["work_mode"], snapshot
        ):
            self.context_badge.setText(i18n.t("CONTEXT • USER DETAILS ONLY"))
            self.context_badge.setToolTip(
                i18n.t("Aktiv layihə yüksək etibarlılıqla tapılmadı; stack uydurulmayacaq.")
            )
            self.context_value.setText(i18n.t("Yalnız user detalları"))
            self.bubble.set_context("User details")
            return None
        if policy == "verified":
            self.context_badge.setText(
                f"{i18n.t('VERIFIED PROJECT')} • {snapshot.label.upper()}"
            )
            self.context_badge.setToolTip(snapshot.text)
            self.context_value.setText(snapshot.label)
            self.bubble.set_context(snapshot.label)
            return snapshot
        self.context_badge.setText(
            f"{i18n.t('AUTO CONTEXT')} • {snapshot.label.upper()}"
        )
        self.context_badge.setToolTip(snapshot.text)
        self.context_value.setText(snapshot.label)
        self.bubble.set_context(snapshot.label)
        return snapshot

    def set_status(self, text):
        shown = i18n.t(text)
        self.status.setText(shown)
        self.tray.setToolTip(f"DeYaz — {shown}")
        if text.startswith("Mətnə çevrilir"):
            self.bubble.set_state("transcribing")
        elif text.startswith("Mətn təmizlənir"):
            self.bubble.set_state("cleaning")
        elif text.endswith("hazırlanır…"):
            self.bubble.set_state("cleaning")
        if hasattr(self, "record") and self.current_surface == "dictation":
            preparing = (
                text.startswith("Mətnə çevrilir")
                or text.startswith("Mətn təmizlənir")
                or text.startswith("Yazı tamamlanır")
                or text.endswith("hazırlanır…")
            )
            self.record.set_preparing_active(preparing)

    def complete(self, _raw, text):
        self.app.clipboard().setText(text)
        self.latest_result_text = text.strip()
        self._dictation_result_open = True
        self.recent_preview.setText(self.latest_result_text)
        self.recent_time.setText(time.strftime("%H:%M"))
        self.copy_recent_button.setEnabled(bool(self.latest_result_text))
        self._update_dictation_result_layout()
        self.record.set_preparing_active(False)
        self.record.setText("")
        self.record.setIcon(line_icon("mic", "#202321", 72))
        self.record.setIconSize(QSize(96, 96))
        self.bubble.set_state("success")
        self.set_status("Hazır — clipboard-a köçürüldü")
        self.detail.setText(i18n.t("Mətn aktiv pəncərəyə yapışdırılır."))
        self.file_status.setText(i18n.t("Hazır — nəticə clipboard-a köçürüldü."))
        if self.conf["auto_paste"]:
            QTimer.singleShot(180, self.paste)
        self.tray.showMessage(
            "DeYaz", i18n.t("Transkript hazırdır."),
            QSystemTrayIcon.MessageIcon.Information, 3000,
        )
        self.refresh_history()
        if self.history_popup.isVisible():
            self.history_popup.refresh(cfg.read_history(5))

    def clear_dictation_result(self):
        """Clear only generated copy; keep mode, models, context and layout."""
        self.latest_result_text = ""
        self.recent_preview.clear()
        self.recent_time.clear()
        if hasattr(self, "dictation_result_output"):
            self.dictation_result_output.clear()
        self.copy_recent_button.setEnabled(False)
        self._update_dictation_result_layout()

    def paste(self):
        if os.name == "nt":
            user32 = ctypes.windll.user32
            user32.keybd_event(0x11, 0, 0, 0)       # Ctrl down
            user32.keybd_event(0x56, 0, 0, 0)       # V down
            user32.keybd_event(0x56, 0, 0x0002, 0)  # V up
            user32.keybd_event(0x11, 0, 0x0002, 0)  # Ctrl up
            return
        try:
            from pynput.keyboard import Controller, Key
            keyboard = Controller()
            modifier = Key.cmd if sys.platform == "darwin" else Key.ctrl
            with keyboard.pressed(modifier):
                keyboard.press("v")
                keyboard.release("v")
        except Exception as exc:
            self.detail.setText(i18n.t(
                "Avtomatik yapışdırma alınmadı: {error}", error=exc
            ))

    def fail(self, message):
        self.bubble.set_state("error", message)
        self.record.set_recording_active(False)
        self.record.set_preparing_active(False)
        self.record.setIcon(line_icon("mic", "#202321", 72))
        self.record.setIconSize(QSize(96, 96))
        self.record.setText("" if getattr(self, "_template_minimal", False)
                            else i18n.t("Səsyazmaya başla"))
        self.record.setToolTip(i18n.t("Səsyazmanı başlat"))
        self.record.setEnabled(self.has_model_credentials())
        self.set_status("Problem yarandı")
        self.detail.setText(message)
        self.tray.showMessage("DeYaz", message, QSystemTrayIcon.MessageIcon.Warning, 5000)
        if self.handle_model_provider_error(message):
            return
        if not self.handle_auth_error(message):
            self.handle_credit_error(message)

    def refresh_history(self):
        rows = cfg.read_history(3)
        if not rows:
            self.history.setText(i18n.t("Hələ transkript yoxdur."))
            self.recent_preview.setText(i18n.t("İlk transkriptin burada görünəcək."))
            self.recent_time.setText("")
            self.latest_result_text = ""
            self.copy_recent_button.setEnabled(False)
            if hasattr(self, "dictation_workspace"):
                self._update_dictation_result_layout()
            return
        text = "\n\n".join(f"{row.get('ts', '')}  ·  {row.get('text', '')[:180]}" for row in reversed(rows))
        self.history.setText(text)
        # History persists, but the Result panel is session-only and must not
        # be refilled from disk when DeYaz starts again.
        if self.latest_result_text:
            preview = self.latest_result_text.replace("\n", " ").strip()
            self.recent_preview.setText(preview[:360])
            self.copy_recent_button.setEnabled(True)
        if hasattr(self, "dictation_workspace"):
            self._update_dictation_result_layout()

    def copy_recent_result(self):
        if not self.latest_result_text:
            return
        self.app.clipboard().setText(self.latest_result_text)
        self.copy_recent_button.setText(i18n.t("Kopyalandı"))
        QTimer.singleShot(
            1300, lambda: self.copy_recent_button.setText(i18n.t("Kopyala"))
        )

    def open_history_tab(self):
        if not self.settings_box.isVisible():
            self.toggle_settings()
        self.settings_nav.setCurrentRow(self.settings_pages.indexOf(self.history_page))
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.settings_box))

    def show_window(self):
        self.bubble.hide()
        # The dashboard is a three-column workspace; opening it in the old
        # 900x700 normal state hides useful controls below the fold.
        self.showMaximized()
        self.raise_()
        self.activateWindow()
        if os.name == "nt":
            hwnd = int(self.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            ctypes.windll.user32.SetForegroundWindow(hwnd)

    def _apply_shell_responsiveness(self, width):
        density = responsive_density_for_width(width)
        if getattr(self, "_shell_density", None) == density:
            return
        self._shell_density = density
        if density == "narrow":
            top_margins, tool_gap = (10, 7, 10, 7), 2
            shell_margins, body_gap = (12, 16, 12, 24), 14
            tool_size, tab_size, brand_height, brand_icon, social_size = 38, (48, 40), 42, 31, 34
        elif density == "compact":
            top_margins, tool_gap = (16, 9, 16, 9), 5
            shell_margins, body_gap = (18, 24, 18, 30), 17
            tool_size, tab_size, brand_height, brand_icon, social_size = 40, (52, 41), 44, 34, 36
        else:
            top_margins, tool_gap = (24, 12, 22, 12), 8
            shell_margins, body_gap = (26, 32, 26, 38), 20
            tool_size, tab_size, brand_height, brand_icon, social_size = 42, (56, 42), 48, 38, 38
        self.top_layout.setContentsMargins(*top_margins)
        self.tools_layout.setContentsMargins(3, 3, 3, 3)
        self.tools_layout.setSpacing(tool_gap)
        self.shell_layout.setContentsMargins(*shell_margins)
        self.body_layout.setSpacing(body_gap)
        self.home_button.setFixedHeight(brand_height)
        self.home_button.setIconSize(QSize(brand_icon, brand_icon))
        self.creator_credit.setVisible(density == "roomy")
        for button in (self.github_button, self.linkedin_button):
            button.setFixedSize(social_size, social_size)
            icon_size = 20 if density == "roomy" else 18
            button.setIconSize(QSize(icon_size, icon_size))
        self.github_star.move(social_size - 16, 0)
        self.github_star.raise_()
        for button in (
            self.appearance_switch, self.language_button,
            self.history_button, self.settings_button,
        ):
            button.setFixedSize(tool_size, tool_size)
        for button in self.surface_buttons.values():
            button.setFixedSize(*tab_size)
        self.content.updateGeometry()

    def resizeEvent(self, event):
        width = event.size().width()
        self._apply_shell_responsiveness(width)
        content_width = responsive_content_width(width)
        self.eyebrow.hide()
        compact = content_width < 720
        self.settings_sidebar.setVisible(not compact)
        self.settings_mobile_nav.setVisible(compact and self.settings_box.isVisible())
        self.settings_detail_layout.setContentsMargins(
            16 if compact else 28, 18 if compact else 24,
            16 if compact else 28, 18 if compact else 24,
        )
        for form in self.settings_forms:
            form.setRowWrapPolicy(
                QFormLayout.RowWrapPolicy.WrapAllRows
                if compact else QFormLayout.RowWrapPolicy.WrapLongRows
            )
        if getattr(self, "_template_minimal", False):
            self._resize_template_pages(content_width)
            super().resizeEvent(event)
            return
        if getattr(self, "_compact_layout", None) != compact:
            self._compact_layout = compact
            self.hero_layout.removeWidget(self.hero_copy)
            self.hero_layout.removeWidget(self.signal_panel)
            if compact:
                self.hero_layout.addWidget(self.hero_copy, 0, 0)
                self.hero_layout.addWidget(self.signal_panel, 1, 0)
                self.hero_layout.setColumnStretch(0, 1)
                self.hero_layout.setColumnStretch(1, 0)
                self.hero_card.setMinimumHeight(500)
            else:
                self.hero_layout.addWidget(self.hero_copy, 0, 0)
                self.hero_layout.addWidget(self.signal_panel, 0, 1)
                self.hero_layout.setColumnStretch(0, 3)
                self.hero_layout.setColumnStretch(1, 2)
                self.hero_card.setMinimumHeight(0)
            for panel in self.quick_cards:
                self.quick_grid.removeWidget(panel)
            for index, panel in enumerate(self.quick_cards):
                row, column = ((index, 0) if compact else (0, index))
                self.quick_grid.addWidget(panel, row, column)
            self.quick_grid.setColumnStretch(0, 1)
            self.quick_grid.setColumnStretch(1, 0 if compact else 1)
            self.quick_grid.setColumnStretch(2, 0 if compact else 1)
            for widget in self.meeting_source_widgets:
                self.meeting_source_layout.removeWidget(widget)
            if compact:
                self.meeting_source_layout.addWidget(self.meeting_mic_source, 0, 0)
                self.meeting_source_layout.addWidget(self.meeting_mic_level, 0, 1)
                self.meeting_source_layout.addWidget(self.meeting_system_source, 1, 0)
                self.meeting_source_layout.addWidget(self.meeting_system_level, 1, 1)
                self.meeting_source_layout.setColumnStretch(2, 1)
            else:
                self.meeting_source_layout.addWidget(self.meeting_mic_source, 0, 0)
                self.meeting_source_layout.addWidget(self.meeting_mic_level, 0, 1)
                self.meeting_source_layout.addWidget(self.meeting_system_source, 0, 2)
                self.meeting_source_layout.addWidget(self.meeting_system_level, 0, 3)
                self.meeting_source_layout.setColumnStretch(4, 1)
            for widget in self.meeting_action_widgets:
                self.meeting_actions_layout.removeWidget(widget)
            if compact:
                self.meeting_actions_layout.addWidget(self.meeting_action, 0, 0)
                self.meeting_actions_layout.addWidget(self.meeting_keep_audio, 0, 1)
                self.meeting_actions_layout.addWidget(self.meeting_copy, 1, 0)
                self.meeting_actions_layout.addWidget(self.meeting_open, 1, 1)
                self.meeting_actions_layout.setColumnStretch(2, 1)
            else:
                self.meeting_actions_layout.addWidget(self.meeting_action, 0, 0)
                self.meeting_actions_layout.addWidget(self.meeting_keep_audio, 0, 1)
                self.meeting_actions_layout.setColumnStretch(2, 1)
                self.meeting_actions_layout.addWidget(self.meeting_copy, 0, 3)
                self.meeting_actions_layout.addWidget(self.meeting_open, 0, 4)
            for widget in self.dictation_mode_widgets:
                self.dictation_mode_layout.removeWidget(widget)
            if compact:
                self.dictation_mode_layout.addWidget(self.dictation_mode_widgets[0], 0, 0)
                self.dictation_mode_layout.addWidget(self.dictation_mode_widgets[1], 0, 1)
                self.dictation_mode_layout.addWidget(self.dictation_mode_widgets[2], 1, 0, 1, 2)
            else:
                self.dictation_mode_layout.addWidget(self.dictation_mode_widgets[0], 0, 0)
                self.dictation_mode_layout.addWidget(self.dictation_mode_widgets[1], 0, 1)
                self.dictation_mode_layout.addWidget(self.dictation_mode_widgets[2], 0, 2)
            for widget in self.meeting_mode_widgets:
                self.meeting_mode_layout.removeWidget(widget)
            if compact:
                self.meeting_mode_layout.addWidget(self.meeting_mode_widgets[0], 0, 0)
                self.meeting_mode_layout.addWidget(self.meeting_mode_widgets[1], 1, 0)
            else:
                self.meeting_mode_layout.addWidget(self.meeting_mode_widgets[0], 0, 0)
                self.meeting_mode_layout.addWidget(self.meeting_mode_widgets[1], 0, 1)
            for widget in (*self.meeting_model_labels, *self.meeting_model_controls):
                self.meeting_models_layout.removeWidget(widget)
            if compact:
                for row, (label, control) in enumerate(zip(
                    self.meeting_model_labels, self.meeting_model_controls
                )):
                    self.meeting_models_layout.addWidget(label, row * 2, 0)
                    self.meeting_models_layout.addWidget(control, row * 2 + 1, 0)
                self.meeting_models_layout.setColumnStretch(1, 0)
                self.meeting_models_layout.setColumnStretch(2, 0)
            else:
                for column, (label, control) in enumerate(zip(
                    self.meeting_model_labels, self.meeting_model_controls
                )):
                    self.meeting_models_layout.addWidget(label, 0, column)
                    self.meeting_models_layout.addWidget(control, 1, column)
                    self.meeting_models_layout.setColumnStretch(column, 1)
            self.hero_layout.invalidate()
            self.quick_grid.invalidate()
            self.meeting_source_layout.invalidate()
            self.meeting_actions_layout.invalidate()
            self.dictation_mode_layout.invalidate()
            self.meeting_mode_layout.invalidate()
            self.meeting_models_layout.invalidate()
            self.content.updateGeometry()
        page_compact = width < 860
        if hasattr(self, "home_cards") and getattr(self, "_page_compact", None) != page_compact:
            self._page_compact = page_compact
            for card in self.home_cards:
                self.home_layout.removeWidget(card)
            if page_compact:
                for row, card in enumerate(self.home_cards):
                    card.setMinimumSize(240, 210)
                    card.setMaximumSize(520, 210)
                    card.setIconSize(QSize(82, 82))
                    self.home_layout.addWidget(
                        card, row, 0, alignment=Qt.AlignmentFlag.AlignCenter
                    )
                self.home_layout.setColumnStretch(1, 0)
                self.home_layout.setColumnStretch(2, 0)
            else:
                for column, card in enumerate(self.home_cards):
                    card.setMinimumSize(300, 250)
                    card.setMaximumSize(380, 270)
                    card.setIconSize(QSize(102, 102))
                    self.home_layout.addWidget(
                        card, 0, column, alignment=Qt.AlignmentFlag.AlignCenter
                    )
                    self.home_layout.setColumnStretch(column, 1)
            self.dictation_workspace.removeWidget(self.hero_card)
            self.dictation_workspace.removeWidget(self.recent_card)
            if page_compact:
                self.dictation_workspace.addWidget(self.hero_card, 0, 0)
                self.dictation_workspace.addWidget(self.recent_card, 1, 0)
                self.dictation_workspace.setColumnStretch(1, 0)
            else:
                self.dictation_workspace.addWidget(self.hero_card, 0, 0)
                self.dictation_workspace.addWidget(self.recent_card, 0, 1)
                self.dictation_workspace.setColumnStretch(0, 3)
                self.dictation_workspace.setColumnStretch(1, 2)
            self.home_layout.invalidate()
            self.dictation_workspace.invalidate()
        super().resizeEvent(event)

    def _resize_template_pages(self, width):
        home_layout_mode = "stack" if width < 900 else (
            "medium" if width < 1200 else "wide"
        )
        if getattr(self, "_home_layout_mode", None) != home_layout_mode:
            self._home_layout_mode = home_layout_mode
            for card in self.home_cards:
                self.home_layout.removeWidget(card)
            if home_layout_mode == "stack":
                self.home_layout.setHorizontalSpacing(16)
                card_width = max(240, min(620, width - 100))
                for row, card in enumerate(self.home_cards):
                    card.setMinimumSize(card_width, 210)
                    card.setMaximumSize(card_width, 210)
                    card.setIconSize(QSize(82, 82))
                    self.home_layout.addWidget(
                        card, row, 0, alignment=Qt.AlignmentFlag.AlignCenter
                    )
            else:
                medium = home_layout_mode == "medium"
                self.home_layout.setHorizontalSpacing(14 if medium else 34)
                for column, card in enumerate(self.home_cards):
                    card.setMinimumSize(235 if medium else 300, 230 if medium else 250)
                    card.setMaximumSize(275 if medium else 380, 240 if medium else 270)
                    card.setIconSize(QSize(88, 88) if medium else QSize(102, 102))
                    self.home_layout.addWidget(
                        card, 0, column, alignment=Qt.AlignmentFlag.AlignCenter
                    )
                    self.home_layout.setColumnStretch(column, 1)
            self.refresh_page_chrome()

        dictation_compact = width < 790
        dictation_has_result = bool(self._dictation_result_open)
        dictation_layout_state = (dictation_compact, dictation_has_result)
        if getattr(self, "_dictation_layout_state", None) != dictation_layout_state:
            self._dictation_layout_state = dictation_layout_state
            self.dictation_workspace.removeWidget(self.dictation_left)
            self.dictation_workspace.removeWidget(self.recent_card)
            if not dictation_has_result:
                self.dictation_workspace.addWidget(
                    self.dictation_left, 0, 0,
                    alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                )
                self.recent_card.hide()
            elif dictation_compact:
                self.dictation_workspace.addWidget(
                    self.dictation_left, 0, 0, alignment=Qt.AlignmentFlag.AlignTop
                )
                self.dictation_workspace.addWidget(self.recent_card, 1, 0)
                self.recent_card.show()
            else:
                self.dictation_workspace.addWidget(
                    self.dictation_left, 0, 0, alignment=Qt.AlignmentFlag.AlignTop
                )
                self.dictation_workspace.addWidget(
                    self.recent_card, 0, 1, alignment=Qt.AlignmentFlag.AlignTop
                )
                self.dictation_workspace.setColumnStretch(0, 2)
                self.dictation_workspace.setColumnStretch(1, 5)
                self.recent_card.show()

        file_compact = width < 920
        if getattr(self, "_file_page_compact", None) != file_compact:
            self._file_page_compact = file_compact
            layout = self.file_page_container.layout()
            layout.removeWidget(self.file_left)
            layout.removeWidget(self.file_result_panel)
            if file_compact:
                layout.addWidget(self.file_left, 0, 0)
                layout.addWidget(self.file_result_panel, 1, 0)
            else:
                layout.addWidget(self.file_left, 0, 0)
                layout.addWidget(self.file_result_panel, 0, 1)
                layout.setColumnStretch(0, 2)
                layout.setColumnStretch(1, 3)

        meeting_layout_mode = meeting_layout_mode_for_width(width)
        if getattr(self, "_meeting_layout_mode", None) != meeting_layout_mode:
            self._meeting_layout_mode = meeting_layout_mode
            layout = self.meeting_page_container.layout()
            for panel in (
                self.meeting_control_panel, self.meeting_live_panel,
                self.meeting_result_panel,
            ):
                layout.removeWidget(panel)

            for row in range(3):
                layout.setRowStretch(row, 0)
            for column in range(3):
                layout.setColumnStretch(column, 0)

            # The reference layout has a control deck plus two text surfaces.
            # On normal laptop windows keep that relationship visible instead
            # of pushing Live and Result several screens below the controls.
            if meeting_layout_mode == "stack":
                self.meeting_control_panel.setMinimumWidth(0)
                self.meeting_control_panel.setMaximumWidth(16777215)
                layout.addWidget(
                    self.meeting_control_panel, 0, 0,
                    alignment=Qt.AlignmentFlag.AlignTop,
                )
                layout.addWidget(self.meeting_live_panel, 1, 0)
                layout.addWidget(self.meeting_result_panel, 2, 0)
                layout.setColumnStretch(0, 1)
                self.meeting_control_panel.setMinimumHeight(520)
                self.meeting_control_panel.setMaximumHeight(16777215)
                for panel in (self.meeting_live_panel, self.meeting_result_panel):
                    panel.setMinimumHeight(400)
                    panel.setMaximumHeight(16777215)
            elif meeting_layout_mode == "split":
                self.meeting_control_panel.setMinimumWidth(310)
                self.meeting_control_panel.setMaximumWidth(390)
                layout.addWidget(
                    self.meeting_control_panel, 0, 0, 2, 1,
                    alignment=Qt.AlignmentFlag.AlignTop,
                )
                layout.addWidget(self.meeting_live_panel, 0, 1)
                layout.addWidget(self.meeting_result_panel, 1, 1)
                layout.setColumnStretch(0, 2)
                layout.setColumnStretch(1, 3)
                layout.setRowStretch(0, 1)
                layout.setRowStretch(1, 1)
                self.meeting_control_panel.setMinimumHeight(510)
                self.meeting_control_panel.setMaximumHeight(530)
                for panel in (self.meeting_live_panel, self.meeting_result_panel):
                    panel.setMinimumHeight(247)
                    panel.setMaximumHeight(257)
            else:
                self.meeting_control_panel.setMinimumWidth(360)
                self.meeting_control_panel.setMaximumWidth(430)
                layout.addWidget(
                    self.meeting_control_panel, 0, 0,
                    alignment=Qt.AlignmentFlag.AlignTop,
                )
                layout.addWidget(self.meeting_live_panel, 0, 1)
                layout.addWidget(self.meeting_result_panel, 0, 2)
                layout.setColumnStretch(0, 2)
                layout.setColumnStretch(1, 3)
                layout.setColumnStretch(2, 3)
                self.meeting_control_panel.setMinimumHeight(560)
                self.meeting_control_panel.setMaximumHeight(620)
                for panel in (self.meeting_live_panel, self.meeting_result_panel):
                    panel.setMinimumHeight(560)
                    panel.setMaximumHeight(16777215)

            # Four vertical selects make the record button disappear at common
            # Windows window sizes. Use a two-column settings deck whenever the
            # control panel has enough width; reserve one column for phones.
            model_columns = 1 if meeting_layout_mode == "stack" and width < 520 else 2
            for widget in (*self.meeting_model_labels, *self.meeting_model_controls):
                self.meeting_models_layout.removeWidget(widget)
            for column in range(4):
                self.meeting_models_layout.setColumnStretch(column, 0)
            for index, (label, control) in enumerate(zip(
                self.meeting_model_labels, self.meeting_model_controls
            )):
                row, column = divmod(index, model_columns)
                span = (
                    model_columns
                    if model_columns > 1
                    and index == len(self.meeting_model_controls) - 1
                    and len(self.meeting_model_controls) % model_columns
                    else 1
                )
                self.meeting_models_layout.addWidget(
                    label, row * 2, column, 1, span
                )
                self.meeting_models_layout.addWidget(
                    control, row * 2 + 1, column, 1, span
                )
                self.meeting_models_layout.setColumnStretch(column, 1)
            if model_columns == 1:
                self.meeting_models_panel.setMinimumHeight(380)
                self.meeting_models_panel.setMaximumHeight(16777215)
                self.meeting_action.setMinimumHeight(180)
                self.meeting_control_panel.setMinimumHeight(700)
                self.meeting_control_panel.setMaximumHeight(16777215)
            else:
                self.meeting_models_panel.setMinimumHeight(248)
                self.meeting_models_panel.setMaximumHeight(268)
                self.meeting_action.setMinimumHeight(
                    150 if meeting_layout_mode == "split" else 185
                )
            layout.invalidate()
            self.meeting_models_layout.invalidate()
            if getattr(self, "current_surface", "") == "meeting":
                QTimer.singleShot(
                    0, lambda: self.scroll.verticalScrollBar().setValue(0)
                )

    def minimize_to_bubble(self):
        """Hide the dashboard but leave a one-click recorder above every app."""
        self.bubble.place()
        self.bubble.show()
        self.hide()

    def closeEvent(self, event):
        event.ignore()
        self.minimize_to_bubble()
        self.tray.showMessage(
            "DeYaz", i18n.t("Mini düymə ilə arxa planda işləyir."),
            QSystemTrayIcon.MessageIcon.Information, 1800,
        )

    def quit(self):
        if self.meeting.active:
            self.meeting.stop()
            self.show_window()
            self.meeting_state.setText(i18n.t("Tamamlanır…"))
            self.tray.showMessage(
                "DeYaz", i18n.t("Görüş qeydi tamamlanır; hazır olanda tətbiqi bağlaya bilərsən."),
                QSystemTrayIcon.MessageIcon.Information, 3000,
            )
            return
        self.hotkey.stop()
        self.tray.hide()
        self.app.quit()


def main():
    log_path = diagnostics.setup_logging()
    diagnostics.install_exception_hook()
    import logging
    logging.getLogger("deyaz.app").info(
        "application_start version=1.0.13 platform=%s log=%s",
        sys.platform, log_path,
    )
    # Set this before QApplication so Windows groups the process under DeYaz and
    # uses DeYaz's icon instead of pythonw.exe's default icon.
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID
        )
    app = QApplication([])
    app.setApplicationName("DeYaz")
    app.setOrganizationName("DeYaz")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    # Use each OS's native UI font; all three defaults cover ə, ğ, ö, ü, ş, ç.
    ui_font = QFont()
    if os.name == "nt":
        ui_font.setFamily("Segoe UI")
    elif sys.platform == "darwin":
        ui_font.setFamily(".AppleSystemUIFont")
    ui_font.setPointSize(10)
    app.setFont(ui_font)
    requested_command = (
        "shutdown-for-update" if "--shutdown-for-update" in sys.argv[1:]
        else "show"
    )
    existing = QLocalSocket()
    existing.connectToServer(INSTANCE_NAME)
    if existing.waitForConnected(150):
        existing.write(requested_command.encode("utf-8"))
        existing.flush()
        existing.waitForBytesWritten(300)
        return 0
    if requested_command == "shutdown-for-update":
        # No running instance means the installer can continue immediately.
        return 0
    QLocalServer.removeServer(INSTANCE_NAME)
    single_instance = QLocalServer(app)
    if not single_instance.listen(INSTANCE_NAME):
        return 1
    window = DeYazWindow(app)

    def receive_command():
        connection = single_instance.nextPendingConnection()
        if connection is None:
            return

        def read_command():
            command = bytes(connection.readAll()).decode("utf-8", "replace").strip()
            if command == "show":
                window.show_window()
            elif command == "shutdown-for-update":
                window.hotkey.stop()
                window.tray.hide()
                window.app.quit()
            connection.disconnectFromServer()

        connection.readyRead.connect(read_command)
        if connection.bytesAvailable():
            read_command()

    single_instance.newConnection.connect(receive_command)
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
