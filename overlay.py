from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, QRect, QTimer
from PyQt5.QtGui import (QPainter, QColor, QFont, QPen,
                         QFontMetrics, QImage)
from PyQt5.QtWidgets import QWidget

from config import HAS_WIN32, win32gui, win32con
from models import TextBlock

try:
    import ctypes
except ImportError:
    ctypes = None


def _pick_text_color(bg_bgr):
    b, g, r = bg_bgr
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return QColor(0, 0, 0) if lum > 128 else QColor(255, 255, 255)


class TranslationOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint |
                            Qt.WindowStaysOnTopHint |
                            Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.blocks: List[TextBlock] = []
        self.bg_image: Optional[QImage] = None
        self.region_rect: Optional[QRect] = None
        self.base_font = QFont("Arial")
        self._click_through_done = False
        self._font_cache: Dict = {}

    # ------------- Win32 / capture exclusion -------------
    def showEvent(self, event):
        super().showEvent(event)
        if HAS_WIN32 and not self._click_through_done:
            QTimer.singleShot(0, self._apply_win32_styles)

    def _apply_win32_styles(self):
        try:
            hwnd = int(self.winId())
            ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                hwnd, win32con.GWL_EXSTYLE,
                ex | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT |
                win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE,
            )
            self._click_through_done = True
        except Exception as e:
            print(f"[Overlay] style fail: {e}")

    # ------------- state -------------
    def set_region(self, rect: QRect):
        self.region_rect = rect
        self.setGeometry(rect)
        self.update()

    def update_content(self, blocks: List[TextBlock], bg_image: Optional[QImage]):
        self.blocks = blocks
        self.bg_image = bg_image
        self.update()

    # ------------- paint -------------
    def paintEvent(self, event):
        if not self.region_rect:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        if self.bg_image is not None and not self.bg_image.isNull():
            p.drawImage(0, 0, self.bg_image)
        else:
            for b in self.blocks:
                x, y, w, h = b.bbox
                bg = QColor(b.bg_color[2], b.bg_color[1], b.bg_color[0])
                p.fillRect(x, y, w, h, bg)

        for b in self.blocks:
            x, y, w, h = b.bbox
            p.setPen(_pick_text_color(b.bg_color))
            p.setFont(self._fit_font(b.text, w, h))
            p.drawText(x, y, w, h,
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap,
                       b.text)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 50, 50, 255), 2, Qt.DashLine)
                     if b.failed else QPen(QColor(0, 220, 0, 200), 2))
            p.drawRect(x, y, w, h)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 200, 255, 200), 2))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))

    # ------------- font -------------
    def _fit_font(self, text: str, w: int, h: int) -> QFont:
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

    def _compute_size(self, text: str, w: int, h: int) -> int:
        max_w = max(w - 4, 10)
        max_h = max(h - 4, 10)
        lo, hi, best = 6, min(48, max_h), 6
        while lo <= hi:
            mid = (lo + hi) // 2
            f = QFont(self.base_font)
            f.setPixelSize(mid)
            fm = QFontMetrics(f)
            br = fm.boundingRect(0, 0, max_w, 10000, Qt.TextWordWrap, text)
            if br.width() <= max_w and br.height() <= max_h:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        return best