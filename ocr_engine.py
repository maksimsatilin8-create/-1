import threading
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import easyocr

from config import (
    DEFAULT_SCALE, DEFAULT_PAD, DEFAULT_CANVAS_SIZE,
    MAX_INPAINT_PIXELS,
)

_reader_cache: Dict = {}
_reader_lock = threading.Lock()


def get_reader(ocr_langs, use_gpu: bool = False):
    key = (tuple(ocr_langs), use_gpu)
    with _reader_lock:
        if key not in _reader_cache:
            try:
                _reader_cache[key] = easyocr.Reader(list(ocr_langs), gpu=use_gpu)
            except Exception:
                _reader_cache[key] = easyocr.Reader(list(ocr_langs), gpu=False)
        return _reader_cache[key]


def preprocess_image(img_bgr: np.ndarray, scale: int = DEFAULT_SCALE,
                     pad: int = DEFAULT_PAD):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_CUBIC)
    if np.mean(gray) < 127:
        gray = cv2.bitwise_not(gray)
    _, thresh = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thresh = cv2.copyMakeBorder(thresh, pad, pad, pad, pad,
                                cv2.BORDER_CONSTANT, value=255)
    return thresh, scale, pad


def inpaint_text(img_bgr: np.ndarray,
                 boxes: List[Tuple[int, int, int, int]]) -> Optional[np.ndarray]:
    """Возвращает очищенный BGR либо None, если инпейнт нецелесообразен."""
    if not boxes:
        return None
    h, w = img_bgr.shape[:2]
    if w * h > MAX_INPAINT_PIXELS:
        return None
    mask = np.zeros((h, w), dtype=np.uint8)
    for (x, y, bw, bh) in boxes:
        x0 = max(0, x - 3); y0 = max(0, y - 3)
        x1 = min(w, x + bw + 3); y1 = min(h, y + bh + 3)
        if x1 > x0 and y1 > y0:
            cv2.rectangle(mask, (x0, y0), (x1, y1), 255, -1)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    try:
        return cv2.inpaint(img_bgr, mask, 3, cv2.INPAINT_TELEA)
    except Exception:
        return None


def estimate_bg_color(img_bgr: np.ndarray, x: int, y: int,
                      w: int, h: int) -> Tuple[int, int, int]:
    H, W = img_bgr.shape[:2]
    pts = [(x + 2, y + 2), (x + w - 3, y + 2),
           (x + 2, y + h - 3), (x + w - 3, y + h - 3)]
    samples = [img_bgr[py, px] for px, py in pts
               if 0 <= px < W and 0 <= py < H]
    if not samples:
        return (0, 0, 0)
    med = np.median(np.array(samples), axis=0).astype(int)
    return (int(med[0]), int(med[1]), int(med[2]))


def make_small_gray(img_bgr: np.ndarray, size: int = 64) -> np.ndarray:
    return cv2.resize(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY),
                      (size, size), interpolation=cv2.INTER_AREA)


def readtext(reader, processed_img: np.ndarray, canvas_size: int = DEFAULT_CANVAS_SIZE):
    return reader.readtext(processed_img, detail=1, paragraph=True,
                           mag_ratio=1.5, canvas_size=canvas_size)