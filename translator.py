import re
import time
import difflib
import threading
from typing import Dict, Tuple, Callable, Optional

# Таймаут по умолчанию для requests (deep-translator)
import requests as _requests
_orig_req = _requests.Session.request
def _req_t(self, method, url, **kw):
    kw.setdefault("timeout", 5)
    return _orig_req(self, method, url, **kw)
_requests.Session.request = _req_t

from deep_translator import GoogleTranslator, MyMemoryTranslator
from config import MYMEMORY_LOCALE


def _norm_text(s: str) -> str:
    return " ".join(s.strip().lower().split())


def has_cyrillic(s: str) -> bool:
    return bool(re.search("[а-яА-Я]", s))


class TranslateService:
    def __init__(self, source: str, target: str,
                 debug_cb: Optional[Callable[[str], None]] = None):
        self.source = source
        self.target = target
        self.debug = debug_cb or (lambda *_: None)

        self._lock = threading.Lock()
        self._cache: Dict[str, str] = {}
        self._google_dead_until = 0.0
        self._mymemory_dead_until = 0.0

        self._google = None
        self._mymemory = None

        if source != target:
            try:
                self._google = GoogleTranslator(source=source, target=target)
            except Exception as e:
                self.debug(f"Google init fail: {e}")
            try:
                mm_src = MYMEMORY_LOCALE.get(source, source)
                mm_tgt = MYMEMORY_LOCALE.get(target, target)
                self._mymemory = MyMemoryTranslator(source=mm_src, target=mm_tgt)
            except Exception as e:
                self.debug(f"MyMemory init fail: {e}")

    def refresh(self) -> None:
        with self._lock:
            self._cache.clear()
        self.debug("🔄 Кэш переводов очищен")

    def translate(self, text: str) -> Tuple[str, bool]:
        text = text.strip()
        if not text:
            return text, False
        if self.source == self.target:
            return text, False

        norm = _norm_text(text)
        with self._lock:
            if text in self._cache:
                return self._cache[text], False
            for orig, tr in self._cache.items():
                if difflib.SequenceMatcher(None, norm, _norm_text(orig)).ratio() > 0.90:
                    return tr, False

        result = self._try_google(text) or self._try_mymemory(text)
        is_failed = result is None
        if is_failed:
            result = text
            self.debug(f"✗ API недоступно: '{text[:30]}'")
        else:
            self.debug(f"✓ '{text[:30]}...' → '{result[:30]}...'")
            with self._lock:
                if len(self._cache) > 1000:
                    self._cache.clear()
                self._cache[text] = result
        return result, is_failed

    def _try_google(self, text: str) -> Optional[str]:
        if not self._google or time.time() <= self._google_dead_until:
            return None
        try:
            res = self._google.translate(text)
            if res and res.strip():
                return res.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "Too Many Requests" in err:
                self._google_dead_until = time.time() + 300
                self.debug("Google: 429 (пауза 5 мин)")
            else:
                self.debug(f"Google fail: {err[:40]}")
        return None

    def _try_mymemory(self, text: str) -> Optional[str]:
        if not self._mymemory or time.time() <= self._mymemory_dead_until:
            return None
        try:
            res = self._mymemory.translate(text)
            if res and res.strip():
                return res.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "LIMIT" in err.upper() or "QUOTA" in err.upper():
                self._mymemory_dead_until = time.time() + 120
                self.debug("MyMemory: лимит (пауза 2 мин)")
            else:
                self.debug(f"MyMemory fail: {err[:40]}")
        return None