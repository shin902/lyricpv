"""形態素解析 — fugashi (MeCab + unidic-lite) による単語分割と読みの取得。

pyopenjtalk の g2p の代わりに UniDic の仮名読みを使う。
ビルド依存が無く macOS で確実に動き、モーラ按分には十分な精度。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .mora import count_mora, is_kana


@dataclass
class MorphWord:
    surface: str
    pos: str  # 例: "名詞"
    reading: str  # カタカナ読み (取得不能時は surface のまま)
    mora_count: int


@lru_cache(maxsize=1)
def _tagger():
    from fugashi import Tagger

    return Tagger()


def analyze_line(text: str) -> list[MorphWord]:
    """1 行を単語に分割し、品詞と読み (モーラ数) を付与する。"""
    words: list[MorphWord] = []
    for token in _tagger()(text):
        surface = token.surface
        if not surface.strip():
            continue
        feature = token.feature
        pos = getattr(feature, "pos1", None) or "*"
        reading = getattr(feature, "kana", None) or getattr(feature, "pron", None)
        if not reading or reading == "*":
            reading = surface
        words.append(
            MorphWord(
                surface=surface,
                pos=pos if pos != "*" else "記号",
                reading=reading,
                mora_count=max(1, _estimate_mora(surface, reading)),
            )
        )
    return words


def _estimate_mora(surface: str, reading: str) -> int:
    """読みからモーラ数を求める。読みが仮名でない場合は文字数で代用する。"""
    if any(is_kana(c) for c in reading):
        return count_mora(reading)
    return len(surface.strip())
