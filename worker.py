import time
import threading
from typing import List, Optional, Tuple

import cv2
import numpy as np
import mss
from PyQt5 import QtCore
from PyQt5.QtCore import pyqtSignal

from config import (
    EASYOCR_LANG_MAP, HAS_WIN32, win32gui, win32con,
    FRAME_DIFF_THRESHOLD, HOLD_TIME, DEFAULT_CANVAS_SIZE,
)
from models import TextBlock
from ocr_engine import (
    get_reader, preprocess_image, inpaint_text,
    estimate_bg_color, make_small_gray, readtext,
)
from translator import TranslateService, has_cyrillic


class TranslationWorker(QtCore.QThread):
    blocks_ready = pyqtSignal(list, object)   # (List[TextBlock], (h,w,bytes)|None)
    error        = pyqtSignal(str)
    status       = pyqtSignal(str)
    debug        = pyqtSignal(str)

    HOLD_TIME = HOLD_TIME

    def __init__(self, region, source_lang, target_lang,
                 interval: float = 1.0, use_gpu: bool = False,
                 bypass_interval: float = 8.0,
                 use_inpaint: bool = True,
                 canvas_size: int = DEFAULT_CANVAS_SIZE):
        super().__init__()
        self.region_tuple = (region.x(), region.y(),
                             region.width(), region.height())
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.interval = max(0.5, float(interval))
        self.use_gpu = use_gpu
        self.bypass_interval = max(2.0, float(bypass_interval))
        self.use_inpaint = use_inpaint
        self.canvas_size = canvas_size

        self._stop_event = threading.Event()
        self._force_ocr = False

        self._overlay_hwnd: Optional[int] = None
        self._capture_excluded = False

        self._translator = TranslateService(source_lang, target_lang,
                                            debug_cb=self.debug.emit)
        self._warned_lang = False
        self._last_bypass = 0.0

    def set_overlay_hwnd(self, hwnd: int):
        self._overlay_hwnd = hwnd
        self._capture_excluded = self._try_exclude_from_capture(hwnd)
        if self._capture_excluded:
            self.debug.emit("✓ Capture exclusion активна (без мерцания)")
        elif HAS_WIN32:
            self.debug.emit("… capture exclusion недоступна — fallback hide/show")

    @staticmethod
    def _try_exclude_from_capture(hwnd: int) -> bool:
        if not HAS_WIN32:
            return False
        try:
            import ctypes
            WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Win10 2004+
            return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
                hwnd, WDA_EXCLUDEFROMCAPTURE))
        except Exception:
            return False

    def request_refresh(self):
        self._translator.refresh()
        self._force_ocr = True
        self.debug.emit("🔄 Полный refresh")

    # ------------- capture -------------
    def _capture(self, sct, monitor) -> Optional[np.ndarray]:
        if self._capture_excluded:
            try:
                return np.array(sct.grab(monitor))
            except Exception as e:
                self.debug.emit(f"Capture fail: {e}")
                return None
        # fallback: hide/show (мерцание)
        if not (self._overlay_hwnd and HAS_WIN32):
            return None
        img = None
        try:
            win32gui.ShowWindow(self._overlay_hwnd, win32con.SW_HIDE)
            time.sleep(0.020)
            img = np.array(sct.grab(monitor))
        except Exception as e:
            self.debug.emit(f"Capture fail: {e}")
        finally:
            try:
                win32gui.ShowWindow(self._overlay_hwnd, win32con.SW_SHOWNA)
            except Exception:
                pass
        return img

    def _deep_capture(self, sct, monitor) -> Optional[np.ndarray]:
        """Редкий «глубокий» захват — оверлей скрыт подольше, чтобы нижнее окно перерисовалось."""
        if not (self._overlay_hwnd and HAS_WIN32):
            return self._capture(sct, monitor)
        img = None
        try:
            win32gui.ShowWindow(self._overlay_hwnd, win32con.SW_HIDE)
            time.sleep(0.15)
            img = np.array(sct.grab(monitor))
        except Exception as e:
            self.debug.emit(f"Deep capture fail: {e}")
        finally:
            try:
                win32gui.ShowWindow(self._overlay_hwnd, win32con.SW_SHOWNA)
            except Exception:
                pass
        return img

    # ------------- main loop -------------
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

            x0, y0, wr, hr = self.region_tuple
            monitor = {"top": y0, "left": x0, "width": wr, "height": hr}
            self.status.emit("Ожидание текста...")

            prev_small: Optional[np.ndarray] = None
            last_miss_time = 0.0
            last_blocks: List[TextBlock] = []
            last_clean_bg = None
            self._last_bypass = time.time()

            with mss.mss() as sct:
                while not self._stop_event.is_set():
                    t0 = time.time()

                    # Deep refresh раз в bypass_interval
                    deep = (time.time() - self._last_bypass) >= self.bypass_interval
                    if deep:
                        self._last_bypass = time.time()
                        img = self._deep_capture(sct, monitor)
                        force_for_deep = True
                    else:
                        img = self._capture(sct, monitor)
                        force_for_deep = False

                    if img is None:
                        self._stop_event.wait(self.interval)
                        continue

                    img_bgr = np.ascontiguousarray(img[:, :, :3])
                    small = make_small_gray(img_bgr)

                    frame_changed = (
                        prev_small is None or
                        cv2.absdiff(prev_small, small).mean() >= FRAME_DIFF_THRESHOLD
                    )
                    retry_active = (last_miss_time > 0 and
                                    time.time() - last_miss_time < self.HOLD_TIME)
                    should_ocr = (self._force_ocr or frame_changed or
                                  retry_active or force_for_deep)

                    if should_ocr:
                        self._force_ocr = False
                        prev_small = small
                        try:
                            found, blocks, clean_bg = self._ocr_and_translate(
                                reader, img_bgr, wr, hr)
                            if found:
                                last_miss_time = 0.0
                                last_blocks = blocks
                                last_clean_bg = clean_bg
                                self.blocks_ready.emit(blocks, clean_bg)
                                is_failed = any(b.failed for b in blocks)
                                self.status.emit(
                                    "Ошибка API (лимит)" if is_failed else "Переведено")
                            else:
                                if last_miss_time == 0:
                                    last_miss_time = time.time()
                        except Exception as e:
                            self.error.emit(f"Loop: {e}")

                    # Hold-timer (единая точка очистки)
                    if (last_blocks and last_miss_time > 0 and
                            time.time() - last_miss_time > self.HOLD_TIME):
                        last_blocks = []
                        last_clean_bg = None
                        last_miss_time = 0.0
                        self.blocks_ready.emit([], None)
                        self.status.emit("Ожидание текста...")

                    elapsed = time.time() - t0
                    if elapsed < self.interval:
                        self._stop_event.wait(self.interval - elapsed)

        except Exception as e:
            self.error.emit(f"Critical: {e}")

    def _ocr_and_translate(self, reader, img_bgr, wr, hr):
        processed, scale, pad = preprocess_image(img_bgr)
        results = readtext(reader, processed, canvas_size=self.canvas_size)

        parts, per_box = [], []
        min_x = min_y = 10**9
        max_x = max_y = -1

        for item in results:
            bbox, text = (item[0], item[1]) if len(item) >= 2 else (None, None)
            if not text or not text.strip():
                continue

            if (not self._warned_lang and self.source_lang == "en"
                    and has_cyrillic(text)):
                self.debug.emit("⚠ Кириллица в тексте, но выбран 'en'")
                self._warned_lang = True

            parts.append(text.strip())
            try:
                pts = np.array(bbox, dtype=np.int32).reshape(-1, 1, 2)
                bx, by, bw, bh = cv2.boundingRect(pts)
                min_x, min_y = min(min_x, bx), min(min_y, by)
                max_x, max_y = max(max_x, bx + bw), max(max_y, by + bh)

                ox = max(0, (bx - pad) // scale)
                oy = max(0, (by - pad) // scale)
                ow = min(wr - ox, bw // scale + 2)
                oh = min(hr - oy, bh // scale + 2)
                if ow > 0 and oh > 0:
                    per_box.append((ox, oy, ow, oh))
            except Exception:
                continue

        if not parts:
            return False, [], None

        combined = "\n".join(parts)
        translated, is_failed = self._translator.translate(combined)

        real_x = max(0, (min_x - pad) // scale - 5)
        real_y = max(0, (min_y - pad) // scale - 5)
        real_w = min(wr - real_x, (max_x - min_x) // scale + 10)
        real_h = min(hr - real_y, (max_y - min_y) // scale + 10)
        if real_w <= 0 or real_h <= 0:
            return False, [], None

        bg = estimate_bg_color(img_bgr, real_x, real_y, real_w, real_h)
        block = TextBlock((real_x, real_y, real_w, real_h),
                          translated, combined, bg, failed=is_failed)

        clean_bg = None
        if self.use_inpaint:
            clean = inpaint_text(img_bgr, per_box)
            if clean is not None:
                h, w = clean.shape[:2]
                clean_bg = (h, w, clean.tobytes())

        return True, [block], clean_bg

    def stop(self):
        self._stop_event.set()