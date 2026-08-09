"""Native Windows edition of Dikte: record, transcribe, clean up, paste."""

import ctypes
from ctypes import wintypes
import os
import shutil
import tempfile
import threading
import time
import wave
from pathlib import Path

import sounddevice as sd
from PyQt6.QtCore import (
    QEasingCurve, QObject, QPropertyAnimation, QRect, QRectF, QTimer, Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget,
)

import api
import config as cfg
import filetranscribe
import i18n
from work_modes import WORK_MODES, mode as get_work_mode
from project_context import CONTEXT_RULES, ContextSnapshot, capture_context


RATE = 16000
HOTKEY_ID = 0xD17E
MOD_ALT, MOD_CONTROL, WM_HOTKEY, WM_QUIT = 0x0001, 0x0002, 0x0312, 0x0012
INSTANCE_NAME = "dikte-windows-native"
APP_USER_MODEL_ID = "Dikte.Windows.Native.1"
ICON_PATH = Path(__file__).resolve().parent / "assets" / "dikte.ico"


class WindowsRecorder(QObject):
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
                device=self.device, samplerate=RATE, channels=1,
                dtype="int16", blocksize=1024,
                callback=self._audio,
            )
            self.stream.start()
            self.started = time.monotonic()
        except Exception as exc:
            self.stream = None
            self.failed.emit(f"Mikrofon başlatıla bilmədi: {exc}")

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
            self.failed.emit("Yazı çox qısadır — ən azı 0.3 saniyə danış.")
            return
        try:
            import numpy as np
            data = np.concatenate(self.samples, axis=0).astype("int16", copy=False)
            fd, path = tempfile.mkstemp(prefix="dikte-windows-", suffix=".wav")
            with os.fdopen(fd, "wb") as raw, wave.open(raw, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(RATE)
                wav.writeframes(data.tobytes())
            self.finished.emit(path, duration)
        except Exception as exc:
            self.failed.emit(f"Səs faylı hazırlana bilmədi: {exc}")
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
            if self.conf["cleanup_enabled"] or mode_active:
                self.stage.emit(
                    f"{work_mode['name']} hazırlanır…"
                    if mode_active else "Mətn təmizlənir…"
                )
                system_prompt = (
                    work_mode["prompt"] + (CONTEXT_RULES if context else "")
                    if mode_active else self.conf.cleanup_prompt()
                )
                text = api.cleanup(
                    raw, self.conf.openrouter_key(), self.conf["cleanup_model"],
                    system_prompt, self.conf["cleanup_reasoning"],
                    self.conf["openrouter_base_url"],
                    context=context.text if (mode_active and context) else "",
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

    def __init__(self, shortcut="Ctrl+Alt+R"):
        super().__init__()
        self.thread = None
        self.thread_id = None
        self.shortcut = shortcut

    def start(self):
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()

    def _listen(self):
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        parts = [part.strip().upper() for part in self.shortcut.split("+")]
        modifiers = 0
        if "CTRL" in parts or "CONTROL" in parts:
            modifiers |= MOD_CONTROL
        if "ALT" in parts:
            modifiers |= MOD_ALT
        if "SHIFT" in parts:
            modifiers |= 0x0004
        key_name = parts[-1]
        virtual_key = 0x20 if key_name == "SPACE" else ord(key_name[:1])
        if not ctypes.windll.user32.RegisterHotKey(
                None, HOTKEY_ID, modifiers, virtual_key):
            return
        msg = wintypes.MSG()
        while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.pressed.emit()
        ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self):
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.6)
        self.thread_id = None

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
        self.setToolTip("Dikte: kliklə səsyazmanı başlat / dayandır")
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
        self.mode_name = WORK_MODES["dictation"]["name"]
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
        self.mode_id = mode_id if mode_id in WORK_MODES else "dictation"
        self.mode_name = selected["name"]
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
            f"Dikte • {self.mode_name}{context}: kliklə səsyazmanı başlat / dayandır"
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
        if self.state != "idle":
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
                "Dinləyirəm",
                "  •  ".join(x for x in (
                    self.mode_name, self.context_label, self._elapsed()
                ) if x),
            ),
            "preparing": (
                "Səs hazırlanır",
                "  •  ".join(x for x in (self.mode_name, self.context_label) if x),
            ),
            "transcribing": (
                "Mətnə çevrilir",
                "  •  ".join(x for x in (self.mode_name, self.context_label) if x),
            ),
            "cleaning": (
                "Mode tətbiq olunur",
                "  •  ".join(x for x in (self.mode_name, self.context_label) if x),
            ),
            "mode": ("Mode dəyişdi", self.mode_name),
            "success": ("Hazırdır", f"{self.mode_name}  •  inputa əlavə olundu"),
            "error": ("Problem yarandı", self.detail or "Yenidən cəhd et"),
        }
        title, subtitle = labels.get(self.state, ("Dikte", self.detail))
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
        """Compact two-part control: recording orb plus an inward menu tab."""
        mic_x = 44 if self.dock_side == "right" else 10
        tab_x = 7 if self.dock_side == "right" else 64
        hover_alpha = 110 if self.hovered else 72

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, hover_alpha))
        p.drawRoundedRect(QRectF(tab_x + 1, 13, 38, 49), 18, 18)
        p.drawEllipse(QRectF(mic_x + 1, 7, 58, 58))

        tab_gradient = QLinearGradient(tab_x, 10, tab_x + 38, 60)
        tab_gradient.setColorAt(0, QColor("#263534"))
        tab_gradient.setColorAt(1, QColor("#12191a"))
        p.setBrush(tab_gradient)
        p.setPen(QPen(QColor("#405250"), 1))
        p.drawRoundedRect(QRectF(tab_x, 10, 38, 50), 18, 18)

        # Three tactile menu dots.
        p.setPen(Qt.PenStyle.NoPen)
        for index, alpha in enumerate((110, 180, 110)):
            p.setBrush(QColor(166, 190, 185, alpha))
            p.drawEllipse(QRectF(tab_x + 16, 21 + index * 8, 5, 5))

        # Layered mic orb.
        p.setBrush(QColor("#11191a"))
        p.setPen(QPen(QColor("#40504e"), 1))
        p.drawEllipse(QRectF(mic_x, 4, 62, 62))
        mode_colour = QColor(self.mode_color)
        halo = QColor(mode_colour)
        halo.setAlpha(35)
        ring = QColor(mode_colour)
        ring.setAlpha(110)
        p.setBrush(halo)
        p.setPen(QPen(ring, 2))
        p.drawEllipse(QRectF(mic_x + 7, 11, 48, 48))
        p.setBrush(mode_colour)
        p.setPen(Qt.PenStyle.NoPen)
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


class HistoryPopup(QWidget):
    """Compact last-results panel with a dedicated copy action per row."""

    def __init__(self):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(520, 520)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        panel = QFrame(objectName="historyPopup")
        panel.setStyleSheet("""
            #historyPopup { background: #162021; border: 1px solid #40514f;
                            border-radius: 18px; }
            QLabel { color: #f4efea; font-family: Bahnschrift; }
            #popupTitle { font-size: 17px; font-weight: 800; letter-spacing: 1px; }
            #popupHint { color: #8fa5a1; font-size: 11px; }
            #resultRow { background: #0f1718; border: 1px solid #2d3d3b;
                         border-radius: 12px; }
            #resultMeta { color: #8fa5a1; font-size: 10px; }
            #resultText { color: #e8efed; font-size: 12px; }
            QPushButton { background: #263634; color: #f4efea; border: 1px solid #425653;
                          border-radius: 8px; padding: 7px 11px; font-weight: 700; }
            QPushButton:hover { background: #f26440; color: #17110f; border-color: #f26440; }
            QScrollArea { border: 0; background: transparent; }
        """)
        outer.addWidget(panel)
        panel_l = QVBoxLayout(panel)
        panel_l.setContentsMargins(18, 16, 18, 16)
        panel_l.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("SON NƏTİCƏLƏR", objectName="popupTitle")
        hint = QLabel("Son 5 hazır mətn • istədiyini bir kliklə kopyala",
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

    def refresh(self, rows):
        while self.results_l.count():
            item = self.results_l.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not rows:
            empty = QLabel("Hələ hazır nəticə yoxdur.", objectName="popupHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.results_l.addWidget(empty)
            self.results_l.addStretch()
            return

        for row in reversed(rows[-5:]):
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
                f"{mode['name']}   •   {row.get('ts', '')}",
                objectName="resultMeta"
            )
            copy = QPushButton("Kopyala")
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
        button.setText("Kopyalandı")
        QTimer.singleShot(1400, lambda: button.setText("Kopyala"))

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


class DikteWindows(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.conf = cfg.Config()
        self.recorder = WindowsRecorder()
        self.pipeline = Transcription(self.conf)
        self.file_pipeline = filetranscribe.FileTranscriber(self.conf, self)
        self.file_path = ""
        self.file_segments = []
        self.current_context = None
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
        self.setWindowTitle("Dikte — Windows")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(560, 600)
        self.resize(900, 700)
        self._build_ui()
        self._build_tray()
        self.apply_work_mode_visual()
        self.recorder.level.connect(self.set_level)
        self.recorder.finished.connect(self.transcribe)
        self.recorder.failed.connect(self.fail)
        self.pipeline.stage.connect(self.set_status)
        self.pipeline.finished.connect(self.complete)
        self.pipeline.failed.connect(self.fail)
        self.file_pipeline.progress.connect(self.on_file_progress)
        self.file_pipeline.finished.connect(self.on_file_finished)
        self.file_pipeline.failed.connect(self.on_file_failed)
        self.hotkey.pressed.connect(self.toggle_recording)
        self.bubble.clicked.connect(self.toggle_recording)
        self.bubble.menu_requested.connect(self.show_bubble_menu)
        self.bubble.position_changed.connect(self.save_bubble_position)
        self.hotkey.start()
        self.refresh_history()

    def _build_ui(self):
        self.setStyleSheet("""
            QMainWindow { background: #111719; color: #f3ede8; }
            QWidget { font-family: Bahnschrift, 'Segoe UI'; font-size: 14px; }
            #top { background: #172021; border-bottom: 1px solid #2b3939; }
            #brand { font-size: 25px; font-weight: 700; letter-spacing: 2px; }
            #eyebrow { color: #9ab1ad; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
            #card { background: #192324; border: 1px solid #2b3c3b; border-radius: 22px; }
            #status { color: #f3ede8; font-size: 19px; font-weight: 600; }
            #muted { color: #9ab1ad; }
            #modeBadge { padding: 5px 12px; border-radius: 10px; font-size: 11px;
                         font-weight: 800; letter-spacing: 1px; }
            #contextBadge { color: #84aaa4; font-size: 10px; font-weight: 700;
                            letter-spacing: 1px; }
            #record { background: #ed5f3b; color: #16110f; border: 0; border-radius: 44px; font-size: 16px; font-weight: 800; }
            #record:hover { background: #ff7953; }
            #record[recording='true'] { background: #f1d2c4; }
            QPushButton { background: #263433; color: #f3ede8; padding: 10px 14px; border: 1px solid #405250; border-radius: 10px; font-weight: 600; }
            QPushButton:hover { background: #30413f; }
            QLineEdit, QComboBox, QPlainTextEdit, QSpinBox { background: #101617; border: 1px solid #3a4e4b; border-radius: 8px; padding: 8px; color: #f3ede8; }
            QComboBox QAbstractItemView { background: #192324; color: #f3ede8; selection-background-color: #ed5f3b; }
            QCheckBox { color: #dce5e2; padding: 4px; }
            #history { background: #101617; border: 1px solid #2b3c3b; border-radius: 12px; color: #d7e1de; padding: 12px; }
            #fileHero { background: #111b1d; border: 1px solid #31504d; border-radius: 18px; }
            #fileDrop { background: #172827; border: 2px dashed #4d7771; border-radius: 16px; padding: 22px; font-size: 15px; }
            #fileDrop:hover { background: #203431; border-color: #ed7655; }
            #fileOutput { background: #0d1314; border: 1px solid #304442; border-radius: 14px; padding: 14px; font-family: 'Segoe UI'; }
            #primaryFile { background: #ed5f3b; color: #17110f; border: 0; padding: 12px 20px; font-weight: 800; }
            #primaryFile:hover { background: #ff7652; }
            #primaryFile:disabled { background: #31403e; color: #71817e; }
            QProgressBar { background: #101617; border: 1px solid #304442; border-radius: 5px; height: 9px; text-align: center; color: transparent; }
            QProgressBar::chunk { background: #ed5f3b; border-radius: 4px; }
            QTabWidget::pane { border: 0; margin-top: 12px; }
            QTabBar::tab { background: #121a1b; color: #8fa4a1; border: 1px solid #2d3d3c; padding: 10px 16px; margin-right: 5px; border-radius: 9px; font-weight: 700; }
            QTabBar::tab:selected { background: #ed5f3b; color: #17110f; border-color: #ed5f3b; }
            QTabBar::tab:hover:!selected { background: #253231; color: #f3ede8; }
            QScrollArea { border: 0; background: transparent; }
            QScrollBar:vertical { width: 8px; background: #111719; }
            QScrollBar::handle:vertical { background: #40504e; border-radius: 4px; min-height: 30px; }
        """)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top = QFrame(objectName="top")
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(32, 22, 32, 22)
        brand = QLabel("DIKTE", objectName="brand")
        self.eyebrow = QLabel(
            f"WINDOWS NATIVE  •  SƏSYAZMA: {self.conf['windows_hotkey'].upper()}",
            objectName="eyebrow"
        )
        settings = QPushButton("Ayarlar")
        settings.clicked.connect(self.toggle_settings)
        mini = QPushButton("Mini düymə")
        mini.clicked.connect(self.minimize_to_bubble)
        top_l.addWidget(brand)
        top_l.addSpacing(18)
        top_l.addWidget(self.eyebrow)
        top_l.addStretch()
        top_l.addWidget(mini)
        top_l.addWidget(settings)
        layout.addWidget(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        shell = QWidget()
        shell_l = QHBoxLayout(shell)
        shell_l.setContentsMargins(18, 26, 18, 30)
        shell_l.addStretch()
        self.content = QWidget()
        self.content.setMaximumWidth(1120)
        self.content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body_l = QVBoxLayout(self.content)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(20)

        card = QFrame(objectName="card")
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(32, 30, 32, 28)
        card_l.setSpacing(12)
        self.mode_badge = QLabel("", objectName="modeBadge")
        self.context_badge = QLabel("AUTO CONTEXT • GÖZLƏYİR", objectName="contextBadge")
        self.status = QLabel("Danışmağa hazırsan", objectName="status", alignment=Qt.AlignmentFlag.AlignCenter)
        self.detail = QLabel("Qısa yolu bas və ya aşağıdakı düymədən başla.", objectName="muted", alignment=Qt.AlignmentFlag.AlignCenter)
        self.meter = QFrame()
        self.meter.setFixedHeight(6)
        self.meter.setStyleSheet("background: #2a3937; border-radius: 3px;")
        self.record = QPushButton("●  SƏSYAZMANI BAŞLAT", objectName="record")
        self.record.setFixedSize(280, 88)
        self.record.clicked.connect(self.toggle_recording)
        card_l.addWidget(self.mode_badge, alignment=Qt.AlignmentFlag.AlignCenter)
        card_l.addWidget(self.context_badge, alignment=Qt.AlignmentFlag.AlignCenter)
        card_l.addWidget(self.status)
        card_l.addWidget(self.detail)
        card_l.addSpacing(8)
        card_l.addWidget(self.meter)
        card_l.addSpacing(16)
        card_l.addWidget(self.record, alignment=Qt.AlignmentFlag.AlignCenter)
        body_l.addWidget(card)

        self.settings_box = QFrame(objectName="card")
        settings_l = QVBoxLayout(self.settings_box)
        settings_l.setContentsMargins(22, 20, 22, 22)
        settings_l.setSpacing(14)
        section_title = QLabel("İDARƏETMƏ MƏRKƏZİ", objectName="eyebrow")
        settings_l.addWidget(section_title)
        self.settings_tabs = QTabWidget()
        self.settings_tabs.tabBar().setUsesScrollButtons(True)
        self.settings_tabs.setDocumentMode(True)

        def form_page():
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(2, 8, 2, 8)
            form.setHorizontalSpacing(20)
            form.setVerticalSpacing(13)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            return page, form

        # General
        general, general_form = form_page()
        self.language = QComboBox()
        for label, code in [("Azərbaycanca", "az"), ("English", "en"),
                            ("Türkçe", "tr"), ("Avtomatik", "auto")]:
            self.language.addItem(label, code)
        self.language.setCurrentIndex(max(0, self.language.findData(self.conf["language"])))
        self.microphone = QComboBox()
        self.microphone.addItem("Windows standart mikrofonu", "")
        try:
            for device in sd.query_devices():
                if device["max_input_channels"] > 0:
                    self.microphone.addItem(device["name"], device["name"])
        except Exception:
            pass
        self.microphone.setCurrentIndex(max(
            0, self.microphone.findData(self.conf["windows_mic_device"])
        ))
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
        self.context_dir.setPlaceholderText(
            "Auto-detection alınmasa istifadə ediləcək layihə qovluğu"
        )
        context_pick = QPushButton("Seç")
        context_pick.clicked.connect(self.browse_context_folder)
        context_l.addWidget(self.context_dir, 1)
        context_l.addWidget(context_pick)
        self.keep_audio = QCheckBox("Səs fayllarını saxla")
        self.keep_audio.setChecked(self.conf["keep_audio"])
        self.history_limit = QSpinBox()
        self.history_limit.setRange(20, 5000)
        self.history_limit.setValue(int(self.conf["history_limit"]))
        general_form.addRow("İnterfeys və danışıq dili", self.language)
        general_form.addRow("Mikrofon", self.microphone)
        general_form.addRow("", self.auto_paste)
        general_form.addRow("", self.context_enabled)
        general_form.addRow("Project context fallback", context_field)
        general_form.addRow("", self.keep_audio)
        general_form.addRow("Tarixçə limiti", self.history_limit)
        self.settings_tabs.addTab(general, "General")

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
        self.openrouter = QLineEdit(self.conf["openrouter_api_key"])
        self.openrouter.setEchoMode(QLineEdit.EchoMode.Password)
        self.openrouter.setPlaceholderText("OpenRouter API açarı")
        self.transcribe_model = QLineEdit(
            self.conf["openrouter_transcribe_model"]
            if self.conf["transcribe_provider"] == "openrouter"
            else self.conf["transcribe_model"]
        )
        api_form.addRow("Transkripsiya provider-i", self.provider)
        api_form.addRow("OpenAI API key", self.openai)
        api_form.addRow("OpenRouter API key", self.openrouter)
        api_form.addRow("Transkripsiya modeli", self.transcribe_model)
        self.settings_tabs.addTab(api_page, "API & Models")

        # Modify / cleanup rules
        modify, modify_form = form_page()
        self.work_mode = QComboBox()
        for mode_id, item in WORK_MODES.items():
            self.work_mode.addItem(
                color_icon(item["color"]), item["name"], mode_id
            )
        self.work_mode.setCurrentIndex(max(
            0, self.work_mode.findData(self.conf["work_mode"])
        ))
        self.work_mode.currentIndexChanged.connect(self.work_mode_changed)
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
        for model in [
            "google/gemini-3.5-flash-lite", "google/gemini-3.1-flash-lite",
            "anthropic/claude-haiku-4.5", "openai/gpt-5-mini",
        ]:
            self.cleanup_model.addItem(model, model)
        if self.cleanup_model.findData(self.conf["cleanup_model"]) < 0:
            self.cleanup_model.addItem(self.conf["cleanup_model"], self.conf["cleanup_model"])
        self.cleanup_model.setCurrentIndex(self.cleanup_model.findData(self.conf["cleanup_model"]))
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
        modify_form.addRow("Modify səviyyəsi", self.modify_preset)
        modify_form.addRow("", self.cleanup)
        modify_form.addRow("Təmizləmə modeli", self.cleanup_model)
        modify_form.addRow("Düşünmə səviyyəsi", self.reasoning)
        modify_form.addRow("Sözlük və xüsusi terminlər", self.glossary)
        modify_form.addRow("Custom modify prompt", self.cleanup_prompt)
        self.settings_tabs.addTab(modify, "Modify")

        # Audio / video file transcription studio
        file_page = QWidget()
        file_l = QVBoxLayout(file_page)
        file_l.setContentsMargins(4, 12, 4, 12)
        file_l.setSpacing(14)

        file_hero = QFrame(objectName="fileHero")
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
        options_form = QFormLayout(file_options)
        options_form.setContentsMargins(18, 16, 18, 16)
        options_form.setHorizontalSpacing(18)
        options_form.setVerticalSpacing(12)
        options_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        options_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.file_language = QComboBox()
        for label, code in [
            ("Avtomatik tanı", "auto"), ("Azərbaycanca", "az"),
            ("English", "en"), ("Türkçe", "tr"),
        ]:
            self.file_language.addItem(label, code)
        self.file_language.setCurrentIndex(max(
            0, self.file_language.findData(self.conf["file_language"])
        ))

        self.file_output_language = QComboBox()
        self.file_output_language.addItem("Orijinal dildə", "original")
        self.file_output_language.addItem("Azərbaycanca", "az")
        self.file_output_language.setCurrentIndex(max(
            0, self.file_output_language.findData(
                self.conf["file_output_language"]
            )
        ))

        self.file_result_type = QComboBox()
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

        options_form.addRow("Danışığın dili", self.file_language)
        options_form.addRow("Nəticənin dili", self.file_output_language)
        options_form.addRow("Nəticə tipi", self.file_result_type)
        options_form.addRow("Emal", option_checks)
        options_form.addRow("Xüsusi fokus", self.file_summary_focus)
        file_l.addWidget(file_options)

        run_row = QHBoxLayout()
        self.file_run = QPushButton("▶  TRANSKRİPSİYANI BAŞLAT", objectName="primaryFile")
        self.file_run.clicked.connect(self.start_file_transcription)
        self.file_run.setEnabled(False)
        self.file_stop = QPushButton("Dayandır")
        self.file_stop.clicked.connect(self.stop_file_transcription)
        self.file_stop.setEnabled(False)
        run_row.addWidget(self.file_run)
        run_row.addWidget(self.file_stop)
        run_row.addStretch()
        file_l.addLayout(run_row)

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

        output_row = QHBoxLayout()
        copy_file = QPushButton("Kopyala")
        copy_file.clicked.connect(self.copy_file_output)
        save_file = QPushButton("TXT saxla")
        save_file.clicked.connect(self.save_file_output)
        self.file_save_srt = QPushButton("SRT saxla")
        self.file_save_srt.clicked.connect(self.save_file_srt)
        self.file_save_srt.setEnabled(False)
        output_row.addWidget(copy_file)
        output_row.addWidget(save_file)
        output_row.addWidget(self.file_save_srt)
        output_row.addStretch()
        file_l.addLayout(output_row)
        self.update_file_option_state()
        self.settings_tabs.addTab(file_page, "File Transcribe")

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
        self.settings_tabs.addTab(shortcut, "Shortcut")

        # History
        history_page = QWidget()
        history_l = QVBoxLayout(history_page)
        history_l.setContentsMargins(4, 12, 4, 12)
        self.history = QLabel("Hələ transkript yoxdur.", objectName="history")
        self.history.setWordWrap(True)
        refresh = QPushButton("Tarixçəni yenilə")
        refresh.clicked.connect(self.refresh_history)
        history_l.addWidget(self.history)
        history_l.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignLeft)
        history_l.addStretch()
        self.settings_tabs.addTab(history_page, "History")

        settings_l.addWidget(self.settings_tabs)
        save = QPushButton("Ayarları yadda saxla")
        save.clicked.connect(self.save_settings)
        settings_l.addWidget(save)
        self.settings_box.hide()
        body_l.addWidget(self.settings_box)
        body_l.addStretch()
        shell_l.addWidget(self.content, 1)
        shell_l.addStretch()
        self.scroll.setWidget(shell)
        layout.addWidget(self.scroll)
        self.setCentralWidget(root)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu(self)
        open_action = QAction("Pəncərəni aç", self)
        mini_action = QAction("Mini düyməni göstər", self)
        toggle_action = QAction("Səsyazmanı başlat / dayandır", self)
        quit_action = QAction("Dikte-ni bağla", self)
        open_action.triggered.connect(self.show_window)
        mini_action.triggered.connect(self.minimize_to_bubble)
        toggle_action.triggered.connect(self.toggle_recording)
        quit_action.triggered.connect(self.quit)
        menu.addAction(open_action)
        menu.addAction(mini_action)
        menu.addAction(toggle_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.setToolTip("Dikte — Windows native")
        self.tray.show()

    def toggle_settings(self):
        self.settings_box.setVisible(not self.settings_box.isVisible())

    def provider_changed(self):
        if self.provider.currentData() == "openrouter":
            self.transcribe_model.setText(self.conf["openrouter_transcribe_model"])
        else:
            self.transcribe_model.setText(self.conf["transcribe_model"])

    def work_mode_changed(self):
        mode_id = self.work_mode.currentData()
        self.conf["work_mode"] = mode_id
        self.conf.save()
        self.apply_work_mode_visual(mode_id)

    def apply_work_mode_visual(self, mode_id=None):
        mode_id = mode_id or self.conf["work_mode"]
        selected = get_work_mode(mode_id)
        mode_colour = QColor(selected["color"])
        self.bubble.set_mode(mode_id)
        self.mode_badge.setText(f"●  {selected['name'].upper()}")
        self.mode_badge.setStyleSheet(
            f"color: {selected['color']}; border: 1px solid {selected['color']}; "
            f"background: rgba({mode_colour.red()}, {mode_colour.green()}, "
            f"{mode_colour.blue()}, 28);"
        )
        self.record.setStyleSheet(
            f"QPushButton {{ background: {selected['color']}; color: #141313; "
            "border: 0; border-radius: 44px; font-size: 16px; font-weight: 800; }"
            f"QPushButton:hover {{ background: {selected['color']}; border: 3px solid #f4efea; }}"
        )
        if mode_id == "dictation" or not self.conf["context_enabled"]:
            self.context_badge.setText("AUTO CONTEXT • SÖNÜLÜ")
            self.bubble.set_context("")
        elif self.current_context is None:
            self.context_badge.setText("AUTO CONTEXT • SƏSYAZMADA AŞKAR EDİLƏCƏK")

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
        self.conf["openai_api_key"] = self.openai.text().strip()
        self.conf["openrouter_api_key"] = self.openrouter.text().strip()
        self.conf["language"] = self.language.currentData()
        self.conf["transcribe_provider"] = self.provider.currentData()
        if self.provider.currentData() == "openrouter":
            self.conf["openrouter_transcribe_model"] = self.transcribe_model.text().strip()
        else:
            self.conf["transcribe_model"] = self.transcribe_model.text().strip()
        self.conf["cleanup_enabled"] = self.cleanup.isChecked()
        self.conf["cleanup_model"] = self.cleanup_model.currentData()
        self.conf["cleanup_reasoning"] = self.reasoning.currentData()
        self.conf["cleanup_prompt"] = self.cleanup_prompt.toPlainText().strip()
        self.conf["transcribe_prompt"] = self.glossary.toPlainText().strip()
        self.conf["work_mode"] = self.work_mode.currentData()
        self.conf["auto_paste"] = self.auto_paste.isChecked()
        self.conf["context_enabled"] = self.context_enabled.isChecked()
        self.conf["context_project_dir"] = self.context_dir.text().strip()
        self.conf["keep_audio"] = self.keep_audio.isChecked()
        self.conf["history_limit"] = self.history_limit.value()
        self.conf["file_language"] = self.file_language.currentData()
        self.conf["file_output_language"] = self.file_output_language.currentData()
        self.conf["file_result_type"] = self.file_result_type.currentData()
        self.conf["file_summary_focus"] = self.file_summary_focus.text().strip()
        self.conf["file_cleanup"] = self.file_cleanup.isChecked()
        self.conf["file_timestamps"] = self.file_timestamps.isChecked()
        self.conf["windows_mic_device"] = self.microphone.currentData() or ""
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
        self.eyebrow.setText(
            f"WINDOWS NATIVE  •  SƏSYAZMA: {self.conf['windows_hotkey'].upper()}"
        )
        self.set_status("Ayarlar yadda saxlanıldı")

    def browse_context_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self, "Project context qovluğunu seç",
            self.context_dir.text().strip() or str(Path.home())
        )
        if selected:
            self.context_dir.setText(selected)

    def transcribe_audio_file(self):
        if self.file_pipeline.busy:
            self.file_status.setText("Başqa bir əməliyyat hazırda davam edir.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Səs və ya video faylı seç",
            self.conf["file_last_dir"] or str(Path.home()),
            "Audio və video (*.mp3 *.wav *.m4a *.ogg *.opus *.flac *.aac *.wma "
            "*.mp4 *.mkv *.webm *.mov *.avi *.mpeg *.mpga);;Bütün fayllar (*.*)"
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
        kind = "VİDEO" if Path(path).suffix.lower() in {
            ".mp4", ".mkv", ".webm", ".mov", ".avi", ".mpeg"
        } else "AUDIO"
        self.file_selected.setText(
            f"{kind}  •  {Path(path).name}  •  {size_text}"
        )
        self.file_selected.setToolTip(path)
        self.file_status.setText("Fayl hazırdır — seçimləri yoxlayıb başlat.")
        self.file_run.setEnabled(True)

    def update_file_option_state(self):
        if not hasattr(self, "file_result_type"):
            return
        transcript = self.file_result_type.currentData() == "transcript"
        self.file_timestamps.setEnabled(transcript)
        self.file_save_srt.setEnabled(
            transcript and bool(getattr(self, "file_segments", []))
        )
        self.file_summary_focus.setEnabled(not transcript)

    def start_file_transcription(self):
        if not self.file_path or self.file_pipeline.busy:
            return
        if self.pipeline.busy or self.recorder.active:
            self.file_status.setText(
                "Əvvəl mikrofon yazısının emalını tamamla, sonra faylı başlat."
            )
            return
        # The visible API/model fields should apply even before the user presses Save.
        self.conf["openai_api_key"] = self.openai.text().strip()
        self.conf["openrouter_api_key"] = self.openrouter.text().strip()
        self.conf["transcribe_provider"] = self.provider.currentData()
        if self.provider.currentData() == "openrouter":
            self.conf["openrouter_transcribe_model"] = self.transcribe_model.text().strip()
        else:
            self.conf["transcribe_model"] = self.transcribe_model.text().strip()
        self.conf["cleanup_model"] = self.cleanup_model.currentData()
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
        self.file_segments = []
        self.file_save_srt.setEnabled(False)
        self.file_button.setEnabled(False)
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
            self.file_status.setText("Dayandırılır…")
            self.file_pipeline.stop()

    def on_file_progress(self, message):
        translations = {
            "Converting audio…": "Videodan/səs faylından audio hazırlanır…",
            "Cleaning up…": "Transkript təmizlənir…",
            "Stopped.": "Əməliyyat dayandırıldı.",
        }
        shown = translations.get(message, message)
        if message.startswith("Transcribing chunk"):
            shown = message.replace("Transcribing chunk", "Transkripsiya olunur: hissə")
        elif message.startswith("Splitting into"):
            shown = message.replace("Splitting into", "Uzun fayl hissələrə bölünür:")
        self.file_status.setText(shown)
        self.status.setText(shown)
        if message == "Stopped." or shown == "Əməliyyat dayandırıldı.":
            self.file_idle()
            self.bubble.set_state("idle")

    def on_file_finished(self, text, segments):
        self.file_output.setPlainText(text)
        self.file_segments = segments
        self.app.clipboard().setText(text)
        self.file_status.setText(
            f"Hazırdır  •  {len(text):,} simvol  •  clipboard-a köçürüldü"
        )
        self.status.setText("Fayl transkripsiyası hazırdır")
        self.bubble.set_state("success")
        self.file_save_srt.setEnabled(
            bool(segments) and self.file_result_type.currentData() == "transcript"
        )
        cfg.append_history({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": 0,
            "elapsed": 0,
            "model": self.conf.transcribe_target().model,
            "raw": text,
            "text": text,
            "mode": f"file:{self.file_result_type.currentData()}",
            "source": Path(self.file_path).name,
            "output_language": self.file_output_language.currentData(),
        })
        self.refresh_history()
        self.file_idle()

    def on_file_failed(self, message):
        self.file_status.setText(f"Xəta: {message}")
        self.status.setText("Fayl emal edilə bilmədi")
        self.bubble.set_state("error", message)
        self.file_idle()

    def file_idle(self):
        self.file_button.setEnabled(True)
        self.file_run.setEnabled(bool(self.file_path))
        self.file_stop.setEnabled(False)
        self.file_progress.setRange(0, 1)
        self.file_progress.setValue(0)
        self.file_progress.hide()

    def copy_file_output(self):
        text = self.file_output.toPlainText().strip()
        if text:
            self.app.clipboard().setText(text)
            self.file_status.setText("Nəticə clipboard-a köçürüldü.")

    def save_file_output(self):
        self.write_file_result(
            self.file_output.toPlainText(), ".txt", "Mətn faylı (*.txt)"
        )

    def save_file_srt(self):
        text = filetranscribe.to_srt(
            self.file_output.toPlainText(), self.file_segments
        )
        if not text:
            self.file_status.setText("SRT üçün zaman damğalı transkript yoxdur.")
            return
        self.write_file_result(text, ".srt", "Subtitr faylı (*.srt)")

    def write_file_result(self, text, suffix, file_filter):
        if not text.strip():
            self.file_status.setText("Saxlanacaq nəticə yoxdur.")
            return
        base = Path(self.file_path).stem if self.file_path else "transcript"
        start = str(Path(self.conf["file_last_dir"] or Path.home()) / f"{base}{suffix}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Nəticəni saxla", start, file_filter
        )
        if not path:
            return
        if not path.lower().endswith(suffix):
            path += suffix
        try:
            Path(path).write_text(text, encoding="utf-8")
            self.file_status.setText(f"Saxlanıldı: {path}")
        except OSError as exc:
            self.file_status.setText(f"Saxlanmadı: {exc}")

    def show_bubble_menu(self, global_position):
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu { background: #182223; color: #f4efea; border: 1px solid #3b4d4b;
                    border-radius: 12px; padding: 8px; font: 10pt Bahnschrift; }
            QMenu::item { padding: 9px 26px 9px 14px; border-radius: 7px; }
            QMenu::item:selected { background: #2d3d3b; }
            QMenu::item:checked { color: #ff7652; font-weight: 700; }
            QMenu::separator { height: 1px; background: #334341; margin: 6px 8px; }
        """)
        title = menu.addAction("DIKTE  •  CONTROL")
        title.setEnabled(False)
        menu.addSeparator()
        work_modes_menu = menu.addMenu("İş mode-u")
        work_modes_menu.setStyleSheet(menu.styleSheet())
        for mode_id, item in WORK_MODES.items():
            action = work_modes_menu.addAction(
                color_icon(item["color"]), item["name"]
            )
            action.setCheckable(True)
            action.setChecked(self.conf["work_mode"] == mode_id)
            action.triggered.connect(
                lambda _checked=False, value=mode_id: self.set_work_mode(value)
            )
        modes = menu.addMenu("Modify səviyyəsi")
        modes.setStyleSheet(menu.styleSheet())
        for label, preset in [
            ("Minimal", "minimal"), ("Balanced", "balanced"),
            ("Polished", "polished"),
        ]:
            action = modes.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.conf["modify_preset"] == preset)
            action.triggered.connect(
                lambda _checked=False, value=preset: self.set_modify_mode(value)
            )
        context_menu = menu.addMenu("Project context")
        context_menu.setStyleSheet(menu.styleSheet())
        auto_context = context_menu.addAction("Auto context")
        auto_context.setCheckable(True)
        auto_context.setChecked(self.conf["context_enabled"])
        auto_context.triggered.connect(self.toggle_context)
        choose_context = context_menu.addAction("Fallback qovluq seç…")
        choose_context.triggered.connect(
            lambda: QTimer.singleShot(0, self.choose_context_from_menu)
        )
        inspect_context = context_menu.addAction("Cari konteksti göstər")
        inspect_context.triggered.connect(
            lambda: QTimer.singleShot(0, self.show_current_context)
        )
        menu.addSeparator()
        recent = menu.addAction("Son nəticələr")
        recent.triggered.connect(
            lambda: QTimer.singleShot(
                0, lambda: self.open_recent_results(global_position)
            )
        )
        menu.addSeparator()
        settings = menu.addAction("Əsas ayarları aç")
        hide = menu.addAction("Mini düyməni gizlət")
        menu.addSeparator()
        quit_action = menu.addAction("Dikte-ni dayandır")
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
            "Dikte", f"Modify mode: {preset.title()}",
            QSystemTrayIcon.MessageIcon.Information, 1600
        )

    def set_work_mode(self, mode_id):
        if mode_id not in WORK_MODES:
            return
        index = self.work_mode.findData(mode_id)
        if index >= 0:
            self.work_mode.setCurrentIndex(index)
        self.conf["work_mode"] = mode_id
        self.conf.save()
        self.apply_work_mode_visual(mode_id)
        selected = WORK_MODES[mode_id]
        if self.bubble.isVisible():
            self.bubble.set_state("mode", selected["name"])

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
            None, "Project context fallback qovluğunu seç",
            self.conf["context_project_dir"] or str(Path.home())
        )
        if selected:
            self.conf["context_project_dir"] = selected
            self.context_dir.setText(selected)
            self.conf.save()

    def show_current_context(self):
        snapshot = capture_context(self.conf["context_project_dir"])
        QMessageBox.information(
            None, f"Project context • {snapshot.label}", snapshot.text[:6500]
        )

    def open_main_settings(self):
        self.show_window()
        self.settings_box.show()
        self.settings_tabs.setCurrentIndex(0)

    def save_bubble_position(self, side, y):
        self.conf["mini_corner"] = f"bottom-{side}"
        self.conf["mini_position_y"] = int(y)
        self.conf.save()

    def toggle_recording(self):
        if self.file_pipeline.busy:
            self.set_status("Əvvəl fayl transkripsiyasını tamamla…")
            return
        if self.pipeline.busy:
            self.set_status("Əvvəlki mətn hələ işlənir…")
            return
        if self.recorder.active:
            self.record.setEnabled(False)
            self.set_status("Yazı tamamlanır…")
            self.bubble.set_state("preparing")
            self.recorder.stop()
            return
        self.current_context = self.capture_work_context()
        self.recorder.start()
        if self.recorder.active:
            self.bubble.set_recording(True)
            self.record.setProperty("recording", True)
            self.record.style().unpolish(self.record)
            self.record.style().polish(self.record)
            self.record.setText("■  DAYANDIR VƏ MƏTNƏ ÇEVİR")
            self.set_status("Dinləyirəm")
            self.detail.setText("Danışığını bitirəndə eyni düyməyə yenidən bas.")

    def set_level(self, value):
        self.bubble.set_level(value)
        self.meter.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ed5f3b, stop:{value:.3f} #ed5f3b, stop:{value:.3f} #2a3937); border-radius: 3px;"
        )

    def transcribe(self, path, duration):
        self.bubble.set_state("transcribing")
        self.record.setProperty("recording", False)
        self.record.setText("●  SƏSYAZMANI BAŞLAT")
        self.record.setEnabled(True)
        self.pipeline.run(path, duration, self.current_context)

    def capture_work_context(self):
        if (not self.conf["context_enabled"] or
                self.conf["work_mode"] == "dictation"):
            self.context_badge.setText("AUTO CONTEXT • SÖNÜLÜ")
            self.bubble.set_context("")
            return None
        snapshot = capture_context(self.conf["context_project_dir"])
        self.context_badge.setText(f"AUTO CONTEXT • {snapshot.label.upper()}")
        self.context_badge.setToolTip(snapshot.text)
        self.bubble.set_context(snapshot.label)
        return snapshot

    def set_status(self, text):
        self.status.setText(text)
        self.tray.setToolTip(f"Dikte — {text}")
        if text.startswith("Mətnə çevrilir"):
            self.bubble.set_state("transcribing")
        elif text.startswith("Mətn təmizlənir"):
            self.bubble.set_state("cleaning")
        elif text.endswith("hazırlanır…"):
            self.bubble.set_state("cleaning")

    def complete(self, _raw, text):
        self.app.clipboard().setText(text)
        self.bubble.set_state("success")
        self.set_status("Hazır — clipboard-a köçürüldü")
        self.detail.setText("Mətn aktiv pəncərəyə yapışdırılır.")
        self.file_status.setText("Hazır — nəticə clipboard-a köçürüldü.")
        if self.conf["auto_paste"]:
            QTimer.singleShot(180, self.paste)
        self.tray.showMessage("Dikte", "Transkript hazırdır.", QSystemTrayIcon.MessageIcon.Information, 3000)
        self.refresh_history()
        if self.history_popup.isVisible():
            self.history_popup.refresh(cfg.read_history(5))

    def paste(self):
        user32 = ctypes.windll.user32
        user32.keybd_event(0x11, 0, 0, 0)       # Ctrl down
        user32.keybd_event(0x56, 0, 0, 0)       # V down
        user32.keybd_event(0x56, 0, 0x0002, 0)  # V up
        user32.keybd_event(0x11, 0, 0x0002, 0)  # Ctrl up

    def fail(self, message):
        self.bubble.set_state("error", message)
        self.record.setProperty("recording", False)
        self.record.setText("●  SƏSYAZMANI BAŞLAT")
        self.record.setEnabled(True)
        self.set_status("Problem yarandı")
        self.detail.setText(message)
        self.tray.showMessage("Dikte", message, QSystemTrayIcon.MessageIcon.Warning, 5000)

    def refresh_history(self):
        rows = cfg.read_history(3)
        if not rows:
            return
        text = "\n\n".join(f"{row.get('ts', '')}  ·  {row.get('text', '')[:180]}" for row in reversed(rows))
        self.history.setText(text)

    def show_window(self):
        self.bubble.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if os.name == "nt":
            hwnd = int(self.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)

    def resizeEvent(self, event):
        width = event.size().width()
        self.eyebrow.setVisible(width >= 780)
        self.record.setFixedWidth(max(230, min(300, width - 100)))
        self.settings_tabs.tabBar().setExpanding(width >= 1050)
        super().resizeEvent(event)

    def minimize_to_bubble(self):
        """Hide the dashboard but leave a one-click recorder above every app."""
        self.bubble.place()
        self.bubble.show()
        self.hide()

    def closeEvent(self, event):
        event.ignore()
        self.minimize_to_bubble()
        self.tray.showMessage("Dikte", "Mini düymə ilə arxa planda işləyir.", QSystemTrayIcon.MessageIcon.Information, 1800)

    def quit(self):
        self.hotkey.stop()
        self.tray.hide()
        self.app.quit()


def main():
    # Set this before QApplication so Windows groups the process under Dikte and
    # uses Dikte's icon instead of pythonw.exe's default icon.
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID
        )
    app = QApplication([])
    app.setApplicationName("Dikte")
    app.setOrganizationName("Dikte")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Bahnschrift", 10))
    existing = QLocalSocket()
    existing.connectToServer(INSTANCE_NAME)
    if existing.waitForConnected(150):
        existing.write(b"show")
        existing.flush()
        existing.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(INSTANCE_NAME)
    single_instance = QLocalServer(app)
    if not single_instance.listen(INSTANCE_NAME):
        return 1
    window = DikteWindows(app)

    def receive_command():
        connection = single_instance.nextPendingConnection()
        if connection is None:
            return

        def read_command():
            command = bytes(connection.readAll()).decode("utf-8", "replace").strip()
            if command == "show":
                window.show_window()
            connection.disconnectFromServer()

        connection.readyRead.connect(read_command)
        if connection.bytesAvailable():
            read_command()

    single_instance.newConnection.connect(receive_command)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
