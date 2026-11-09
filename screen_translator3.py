import sys
import time
import re
import difflib
import threading
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

import cv2
import numpy as np
import mss

# === Timeout для requests ===
import requests as _requests
_orig_req = _requests.Session.request
def _req_t(self, method, url, **kw):
    kw.setdefault("timeout", 5)
    return _orig_req(self, method, url, **kw)
_requests.Session.request = _req_t

from deep_translator import GoogleTranslator, MyMemoryTranslator
import easyocr
from PyQt5 import QtCore
from PyQt5.QtCore import Qt, pyqtSignal, QRect, QPoint
from PyQt5.QtGui import QPainter, QColor, QFont, QPen, QFontMetrics
from PyQt5.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout,
                             QHBoxLayout, QLabel, QComboBox, QDoubleSpinBox,
                             QMessageBox, QCheckBox, QTextEdit)

if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

try:
    import win32gui
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


EASYOCR_LANG_MAP = {
    "zh": ["ch_sim", "en"],
    "ja": ["ja", "en"],
    "ko": ["ko", "en"],
}
MYMEMORY_LOCALE = {
    "en": "en-GB", "ru": "ru-RU", "de": "de-DE", "fr": "fr-FR",
    "es": "es-ES", "it": "it-IT", "pt": "pt-PT", "ja": "ja-JP",
    "zh": "zh-CN", "ko": "ko-KR",
}

_reader_cache: Dict = {}
_reader_lock = threading.Lock()

def get_reader(ocr_langs, use_gpu=False):
    key = (tuple(ocr_langs), use_gpu)
    with _reader_lock:
        if key not in _reader_cache:
            try:
                _reader_cache[key] = easyocr.Reader(list(ocr_langs), gpu=use_gpu)
            except Exception:
                _reader_cache[key] = easyocr.Reader(list(ocr_langs), gpu=False)
        return _reader_cache[key]


def _norm_text(s: str) -> str:
    return " ".join(s.strip().lower().split())


def _has_cyrillic(s: str) -> bool:
    return bool(re.search('[а-яА-Я]', s))


@dataclass
class TextBlock:
    bbox: Tuple[int, int, int, int]
    text: str
    original: str
    bg_color: Tuple[int, int, int]
    failed: bool = False


def _pick_text_color(bg_bgr):
    b, g, r = bg_bgr
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return QColor(0, 0, 0) if lum > 128 else QColor(255, 255, 255)


# ======================================================================
# 1. Селектор области
# ======================================================================
class RegionSelector(QWidget):
    region_selected = pyqtSignal(QRect)
    cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(QApplication.primaryScreen().virtualGeometry())
        self.start = QPoint()
        self.end = QPoint()
        self.drawing = False
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 140))
        if self.drawing:
            r = QRect(self.start, self.end).normalized()
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(r, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(0, 200, 255), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            p.setPen(Qt.white)
            p.setFont(QFont("Arial", 10, QFont.Bold))
            label = f"{r.width()} × {r.height()}"
            tx = r.x() + 4
            ty = r.y() - 8 if r.y() > 20 else r.y() + r.height() + 16
            p.drawText(tx, ty, label)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.start = e.pos()
            self.end = e.pos()
            self.drawing = True
            self.update()

    def mouseMoveEvent(self, e):
        if self.drawing:
            self.end = e.pos()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.drawing:
            self.end = e.pos()
            self.drawing = False
            r = QRect(self.start, self.end).normalized()
            if r.width() > 5 and r.height() > 5:
                tl = self.mapToGlobal(r.topLeft())
                br = self.mapToGlobal(r.bottomRight())
                self.region_selected.emit(QRect(tl, br).normalized())
            else:
                self.cancelled.emit()
            self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()


# ======================================================================
# 2. Оверлей
# ======================================================================
class TranslationOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.blocks: List[TextBlock] = []
        self.region_rect: Optional[QRect] = None
        self.base_font = QFont("Arial")
        self._click_through_applied = False
        self._font_cache: Dict = {}

    def showEvent(self, event):
        super().showEvent(event)
        if HAS_WIN32 and not self._click_through_applied:
            QtCore.QTimer.singleShot(0, self._make_click_through)

    def _make_click_through(self):
        try:
            hwnd = int(self.winId())
            ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                hwnd, win32con.GWL_EXSTYLE,
                ex | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT |
                win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE)
            self._click_through_applied = True
        except Exception as e:
            print(f"[Overlay] click-through failed: {e}")

    def set_region(self, rect):
        self.region_rect = rect
        self.setGeometry(rect)
        self.update()

    def update_blocks(self, blocks):
        self.blocks = blocks
        self.update()

    def paintEvent(self, event):
        if not self.region_rect:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        for b in self.blocks:
            x, y, w, h = b.bbox
            bg = QColor(b.bg_color[2], b.bg_color[1], b.bg_color[0])
            
            p.fillRect(x, y, w, h, bg)
            p.setPen(_pick_text_color(b.bg_color))
            p.setFont(self._fit_font(b.text, w, h))
            p.drawText(x, y, w, h,
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap,
                       b.text)

            p.setBrush(Qt.NoBrush)
            if b.failed:
                p.setPen(QPen(QColor(255, 50, 50, 255), 2, Qt.DashLine))
            else:
                p.setPen(QPen(QColor(0, 220, 0, 200), 2))
            p.drawRect(x, y, w, h)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 200, 255, 200), 2))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def _fit_font(self, text, w, h):
        if not text:
            return QFont(self.base_font)
        key = (text, w, h)
        sz = self._font_cache.get(key)
        if sz is None:
            sz = self._compute_size(text, w, h)
            if len(self._font_cache) > 2000:
                self._font_cache.clear()
            self._font_cache[key] = sz
        f = QFont(self.base_font)
        f.setPixelSize(sz)
        return f

    def _compute_size(self, text, w, h):
        max_w = max(w - 4, 10)
        max_h = max(h - 4, 10)
        for sz in range(max(10, min(48, max_h)), 5, -1):
            f = QFont(self.base_font)
            f.setPixelSize(sz)
            fm = QFontMetrics(f)
            br = fm.boundingRect(0, 0, max_w, 10000, Qt.TextWordWrap, text)
            if br.width() <= max_w and br.height() <= max_h:
                return sz
        return 6


# ======================================================================
# 3. Воркер
# ======================================================================
class TranslationWorker(QtCore.QThread):
    blocks_ready = pyqtSignal(list)
    error = pyqtSignal(str)
    status = pyqtSignal(str)
    debug = pyqtSignal(str)

    HOLD_TIME = 1.5

    def __init__(self, region, source_lang, target_lang, interval=1.0,
                 use_gpu=False, bypass_interval=4.0):
        super().__init__()
        self.region_tuple = (region.x(), region.y(), region.width(), region.height())
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.interval = max(0.5, float(interval)) # Минимум 0.5 сек, чтобы не спамить API
        self.use_gpu = use_gpu
        self.bypass_interval = max(1.0, float(bypass_interval))

        self._stop_event = threading.Event()
        self._need_refresh = threading.Event()
        self._overlay_hwnd = None

        self._cache_lock = threading.Lock()
        self._text_cache: Dict[str, str] = {}
        self._is_running = True

        self._translator = None
        self._fallback = None
        self._last_bypass = 0.0
        self._warned_lang = False
        
        self._google_dead_until = 0.0
        self._mymemory_dead_until = 0.0

    def set_overlay_hwnd(self, hwnd: int):
        self._overlay_hwnd = hwnd

    def request_refresh(self):
        with self._cache_lock:
            self._text_cache.clear()
        self._need_refresh.set()
        self.debug.emit("🔄 REFRESH: кэш очищен")

    def _capture(self, sct, monitor, bypass):
        if bypass and self._overlay_hwnd and HAS_WIN32:
            try:
                win32gui.ShowWindow(self._overlay_hwnd, win32con.SW_HIDE)
                time.sleep(0.015)
            except Exception:
                bypass = False
        img = np.array(sct.grab(monitor))
        if bypass and self._overlay_hwnd and HAS_WIN32:
            try:
                win32gui.ShowWindow(self._overlay_hwnd, win32con.SW_SHOWNA)
            except Exception:
                pass
        return img

    def _preprocess_image(self, img_bgr):
        scale = 2
        pad = 20

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        
        if np.mean(gray) < 127:
            gray = cv2.bitwise_not(gray)
            
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        thresh = cv2.copyMakeBorder(thresh, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
        
        return thresh, scale, pad

    def run(self):
        try:
            ocr_langs = EASYOCR_LANG_MAP.get(self.source_lang, [self.source_lang])
            self.status.emit(f"Загрузка OCR ({ocr_langs})...")
            try:
                reader = get_reader(ocr_langs, use_gpu=self.use_gpu)
            except Exception as e:
                self.error.emit(f"OCR init fail: {e}")
                return
            self.debug.emit("OCR готов")

            if self.source_lang != self.target_lang:
                try:
                    self._translator = GoogleTranslator(source=self.source_lang, target=self.target_lang)
                except Exception as e:
                    self.debug.emit(f"Google init fail: {e}")
                try:
                    mm_src = MYMEMORY_LOCALE.get(self.source_lang, self.source_lang)
                    mm_tgt = MYMEMORY_LOCALE.get(self.target_lang, self.target_lang)
                    self._fallback = MyMemoryTranslator(source=mm_src, target=mm_tgt)
                except Exception as e:
                    self.debug.emit(f"MyMemory init fail: {e}")

            x0, y0, w_region, h_region = self.region_tuple
            monitor = {"top": y0, "left": x0, "width": w_region, "height": h_region}
            self.status.emit("Ожидание текста...")
            self._last_bypass = time.time()

            with mss.mss() as sct:
                last_blocks: List[TextBlock] = []
                last_seen = 0.0

                while not self._stop_event.is_set():
                    t0 = time.time()

                    need_bypass = (t0 - self._last_bypass > self.bypass_interval) or self._need_refresh.is_set()
                    if need_bypass:
                        self._last_bypass = t0
                        self._need_refresh.clear()

                    try:
                        img = self._capture(sct, monitor, need_bypass)
                        img_bgr = img[:, :, :3].copy()

                        processed_img, scale, pad = self._preprocess_image(img_bgr)
                        
                        results = reader.readtext(
                            processed_img, 
                            detail=1, 
                            paragraph=True,
                            mag_ratio=1.5,
                            canvas_size=2560
                        )
                        
                        combined_text_parts = []
                        min_x, min_y = w_region * scale, h_region * scale
                        max_x, max_y = 0, 0

                        for item in results:
                            if len(item) == 3:
                                bbox, text, prob = item
                            else:
                                bbox, text = item

                            if not text.strip():
                                continue

                            if not self._warned_lang and self.source_lang == 'en' and _has_cyrillic(text):
                                self.debug.emit("⚠ ВНИМАНИЕ: в тексте кириллица, но выбран 'en'. Смените на 'ru'")
                                self._warned_lang = True

                            combined_text_parts.append(text.strip())

                            try:
                                pts = np.array(bbox, dtype=np.int32).reshape(-1, 1, 2)
                                bx, by, bw, bh = cv2.boundingRect(pts)
                                min_x = min(min_x, bx)
                                min_y = min(min_y, by)
                                max_x = max(max_x, bx + bw)
                                max_y = max(max_y, by + bh)
                            except Exception:
                                pass

                        if combined_text_parts:
                            combined_text = "\n".join(combined_text_parts)
                            translated, is_failed = self._translate(combined_text)

                            # Делаем рамку чуть больше (на 5 пикселей), чтобы текст помещался
                            real_x = max(0, (min_x - pad) // scale - 5)
                            real_y = max(0, (min_y - pad) // scale - 5)
                            real_w = min(w_region - real_x, (max_x - min_x) // scale + 10)
                            real_h = min(h_region - real_y, (max_y - min_y) // scale + 10)

                            if real_w > 0 and real_h > 0:
                                bg = self._estimate_bg_color(img_bgr, real_x, real_y, real_w, real_h)
                                block = TextBlock((real_x, real_y, real_w, real_h), translated, combined_text, bg, failed=is_failed)
                                
                                last_blocks = [block]
                                last_seen = time.time()
                                self.blocks_ready.emit([block])
                                
                                status_msg = "Переведено" if not is_failed else "Ошибка API (лимит)"
                                self.status.emit(f"Статус: {status_msg}")
                        else:
                            if last_blocks and (time.time() - last_seen > self.HOLD_TIME):
                                last_blocks = []
                                self.blocks_ready.emit([])
                                self.status.emit("Ожидание текста...")

                        if self._stop_event.is_set():
                            break

                    except Exception as e:
                        self.error.emit(f"Loop: {e}")

                    elapsed = time.time() - t0
                    if elapsed < self.interval:
                        self._stop_event.wait(self.interval - elapsed)

        except Exception as e:
            self.error.emit(f"Critical: {e}")
        finally:
            self._is_running = False

    def _translate(self, text: str) -> Tuple[str, bool]:
        text = text.strip()
        if not text:
            return text, False

        norm_text = _norm_text(text)
        with self._cache_lock:
            if text in self._text_cache:
                return self._text_cache[text], False
                
            for cached_orig, cached_trans in self._text_cache.items():
                if difflib.SequenceMatcher(None, norm_text, _norm_text(cached_orig)).ratio() > 0.90:
                    return cached_trans, False

        if self.source_lang == self.target_lang:
            return text, False

        result = None
        is_failed = False

        # 1. Пробуем Google
        if self._translator and time.time() > self._google_dead_until:
            try:
                res = self._translator.translate(text)
                if res and res.strip():
                    result = res.strip()
            except Exception as e:
                err = str(e)
                if "429" in err or "Too Many Requests" in err:
                    self._google_dead_until = time.time() + 300  # Блок на 5 минут
                    self.debug.emit("Google: 429 Лимит (пауза 5 мин)")
                else:
                    self.debug.emit(f"Google fail: {err[:40]}")

        # 2. Пробуем MyMemory
        if result is None and self._fallback and time.time() > self._mymemory_dead_until:
            try:
                res = self._fallback.translate(text)
                if res and res.strip():
                    result = res.strip()
            except Exception as e:
                err = str(e)
                if "429" in err or "LIMIT" in err.upper() or "QUOTA" in err.upper():
                    self._mymemory_dead_until = time.time() + 120  # Блок на 2 минуты
                    self.debug.emit("MyMemory: Лимит (пауза 2 мин)")
                else:
                    self.debug.emit(f"MyMemory fail: {err[:40]}")

        # Если оба API недоступны
        if result is None:
            result = text
            is_failed = True
            self.debug.emit(f"✗ API недоступно: '{text[:30]}'")
        else:
            self.debug.emit(f"✓ '{text[:30]}...' → '{result[:30]}...'")

        with self._cache_lock:
            if len(self._text_cache) > 1000:
                self._text_cache.clear()
            self._text_cache[text] = result

        return result, is_failed

    @staticmethod
    def _estimate_bg_color(img_bgr, x, y, w, h):
        H, W = img_bgr.shape[:2]
        # Берем цвет по 4 углам рамки. Углы почти всегда содержат только фон.
        pts = [
            (x + 2, y + 2),
            (x + w - 3, y + 2),
            (x + 2, y + h - 3),
            (x + w - 3, y + h - 3)
        ]
        samples = []
        for px, py in pts:
            if 0 <= px < W and 0 <= py < H:
                samples.append(img_bgr[py, px])
        
        if not samples:
            return (0, 0, 0)
            
        arr = np.array(samples)
        med = np.median(arr, axis=0).astype(int)
        return (int(med[0]), int(med[1]), int(med[2]))

    def stop(self):
        self._stop_event.set()
        self._is_running = False


# ======================================================================
# 4. Главное окно
# ======================================================================
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Screen Translator — Anti-Ban Pro")
        self.resize(540, 500)
        self.region: Optional[QRect] = None
        self.worker: Optional[TranslationWorker] = None
        self.selector: Optional[RegionSelector] = None
        self.overlay = TranslationOverlay()
        self.overlay.hide()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()

        btn = QPushButton("Выделить область на экране")
        btn.clicked.connect(self.select_region)
        layout.addWidget(btn)

        ll = QHBoxLayout()
        ll.addWidget(QLabel("Исходный:"))
        self.src_combo = QComboBox()
        self.src_combo.addItems(["en", "ru", "ja", "zh", "ko", "de", "fr", "es"])
        ll.addWidget(self.src_combo)
        ll.addWidget(QLabel("Целевой:"))
        self.tgt_combo = QComboBox()
        self.tgt_combo.addItems(["ru", "en", "ja", "zh", "ko", "de", "fr", "es"])
        self.tgt_combo.setCurrentText("ru")
        ll.addWidget(self.tgt_combo)
        layout.addLayout(ll)

        il = QHBoxLayout()
        il.addWidget(QLabel("Интервал OCR:"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.5, 10.0)
        self.interval_spin.setSingleStep(0.1)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setValue(1.0)
        il.addWidget(self.interval_spin)

        il.addWidget(QLabel("Bypass:"))
        self.bypass_spin = QDoubleSpinBox()
        self.bypass_spin.setRange(1.0, 60.0)
        self.bypass_spin.setDecimals(0)
        self.bypass_spin.setValue(4.0)
        il.addWidget(self.bypass_spin)
        layout.addLayout(il)

        self.gpu_check = QCheckBox("GPU (CUDA)")
        layout.addWidget(self.gpu_check)

        bl = QHBoxLayout()
        self.btn_start = QPushButton("Запустить перевод")
        self.btn_start.clicked.connect(self.start_translation)
        self.btn_stop = QPushButton("Остановить")
        self.btn_stop.clicked.connect(self.stop_translation)
        self.btn_stop.setEnabled(False)
        self.btn_refresh = QPushButton("🔄 Пере-распознать")
        self.btn_refresh.clicked.connect(self.manual_refresh)
        self.btn_refresh.setEnabled(False)
        bl.addWidget(self.btn_start)
        bl.addWidget(self.btn_stop)
        bl.addWidget(self.btn_refresh)
        layout.addLayout(bl)

        self.status_label = QLabel("Область не выбрана")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(220)
        self.log_box.setStyleSheet("font-family: Consolas, monospace; font-size: 10px;")
        layout.addWidget(self.log_box)

        self.setLayout(layout)

    def log(self, msg):
        self.log_box.append(msg)
        doc = self.log_box.document()
        if doc.blockCount() > 200:
            cur = self.log_box.textCursor()
            cur.movePosition(cur.Start)
            cur.movePosition(cur.Down, cur.KeepAnchor, doc.blockCount() - 160)
            cur.removeSelectedText()
        print(msg)

    def select_region(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Внимание", "Остановите перевод перед изменением области.")
            return
        self.status_label.setText("Выделите область мышью (Esc — отмена)...")
        self.selector = RegionSelector()
        self.selector.region_selected.connect(self.on_region_selected)
        self.selector.cancelled.connect(self.on_region_cancelled)

    def on_region_selected(self, rect):
        self.region = rect
        self.overlay.set_region(rect)
        self.overlay.update_blocks([])
        self.overlay.hide()
        self.status_label.setText(f"Область: x={rect.x()}, y={rect.y()}, w={rect.width()}, h={rect.height()}")
        self.log(f"Region set: {rect.width()}x{rect.height()}")

    def on_region_cancelled(self):
        self.status_label.setText("Выделение отменено")

    def start_translation(self):
        if not self.region:
            QMessageBox.warning(self, "Внимание", "Сначала выделите область.")
            return
        if self.worker and self.worker.isRunning():
            return

        src = self.src_combo.currentText()
        tgt = self.tgt_combo.currentText()
        interval = float(self.interval_spin.value())
        gpu = self.gpu_check.isChecked()
        bypass = float(self.bypass_spin.value())

        self.log(f"=== START {src}→{tgt} ===")

        try:
            self.worker = TranslationWorker(self.region, src, tgt, interval, gpu, bypass)
            self.worker.blocks_ready.connect(self.on_blocks_ready)
            self.worker.error.connect(self.on_error)
            self.worker.status.connect(self.on_status)
            self.worker.debug.connect(self.log)
            self.worker.finished.connect(self.on_finished)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать: {e}")
            self.worker = None
            return

        self.overlay.show()
        self.overlay.raise_()

        if HAS_WIN32:
            try:
                hwnd = int(self.overlay.winId())
                self.worker.set_overlay_hwnd(hwnd)
            except Exception:
                pass

        self.worker.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.status_label.setText("Запуск...")

    def manual_refresh(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_refresh()

    def stop_translation(self):
        if self.worker and self.worker.isRunning():
            self.btn_stop.setEnabled(False)
            self.btn_refresh.setEnabled(False)
            self.btn_start.setEnabled(False)
            self.status_label.setText("Остановка...")
            self.worker.stop()
        else:
            self._cleanup()

    def _cleanup(self):
        self.worker = None
        self.overlay.update_blocks([])
        self.overlay.hide()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self.status_label.setText("Перевод остановлен")
        self.log("=== STOP ===")

    def on_blocks_ready(self, blocks):
        self.overlay.update_blocks(blocks)
        self.overlay.raise_()

    def on_error(self, msg):
        self.log(f"[ERROR] {msg}")
        self.status_label.setText(f"Ошибка: {msg}")

    def on_status(self, msg):
        self.status_label.setText(msg)

    def on_finished(self):
        self._cleanup()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            if not self.worker.wait(3000):
                self.worker.terminate()
                self.worker.wait(1000)
        self.overlay.close()
        event.accept()


if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_Use96Dpi, True)
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())