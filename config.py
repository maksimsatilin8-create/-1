import sys

# --- DPI awareness (до QApplication) ---
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
    win32gui = win32con = None
    HAS_WIN32 = False


# --- Языки ---
SUPPORTED_LANGS = ["en", "ru", "ja", "zh", "ko", "de", "fr", "es"]

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


# --- OCR / capture ---
DEFAULT_INTERVAL     = 1.0
DEFAULT_BYPASS       = 8.0
DEFAULT_CANVAS_SIZE  = 1920
DEFAULT_SCALE        = 2
DEFAULT_PAD          = 20
FRAME_DIFF_THRESHOLD = 3.0
MAX_INPAINT_PIXELS   = 500 * 500
HOLD_TIME            = 1.5
DEFAULT_OCR_GPU      = False


# --- Тема ---
DARK_THEME_QSS = """
QWidget {
    background-color: #1e1e1e; color: #e0e0e0;
    font-family: "Segoe UI", "Inter", "Arial", sans-serif; font-size: 12px;
}
QPushButton {
    background-color: #2d2d2d; border: 1px solid #3c3c3c;
    border-radius: 6px; padding: 6px 14px; color: #e0e0e0; min-height: 22px;
}
QPushButton:hover  { background-color: #383838; border-color: #4a90e2; }
QPushButton:pressed{ background-color: #4a90e2; color: #ffffff; }
QPushButton:disabled{ color: #666; background-color: #252525; border-color: #2d2d2d; }
QPushButton#primary { background-color: #4a90e2; color: #fff; border: none; }
QPushButton#primary:hover    { background-color: #5aa0f2; }
QPushButton#primary:disabled { background-color: #2a4a6a; color: #888; }
QPushButton#danger  { background-color: #c0392b; color: #fff; border: none; }
QPushButton#danger:hover     { background-color: #d0443a; }
QPushButton#danger:disabled  { background-color: #5c2a24; color: #888; }
QComboBox, QDoubleSpinBox, QTextEdit, QLineEdit {
    background-color: #252525; border: 1px solid #3c3c3c;
    border-radius: 4px; padding: 4px 8px; color: #e0e0e0;
}
QComboBox:hover, QDoubleSpinBox:hover { border-color: #4a90e2; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background-color: #252525; selection-background-color: #4a90e2;
    color: #e0e0e0;
}
QTextEdit { font-family: Consolas, Menlo, monospace; font-size: 10px; }
QCheckBox  { background: transparent; spacing: 6px; }
QCheckBox::indicator {
    width: 14px; height: 14px; border: 1px solid #3c3c3c;
    border-radius: 3px; background-color: #252525;
}
QCheckBox::indicator:checked { background-color: #4a90e2; border-color: #4a90e2; }
QLabel { background: transparent; }
QLabel#status { color: #4a90e2; font-weight: 600; }
"""