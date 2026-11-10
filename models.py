from dataclasses import dataclass
from typing import Tuple


@dataclass
class TextBlock:
    bbox: Tuple[int, int, int, int]
    text: str
    original: str
    bg_color: Tuple[int, int, int]
    failed: bool = False