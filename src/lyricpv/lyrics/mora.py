"""モーラ分割 — カタカナ読みをモーラ列に分割する。

日本語の発話リズムの単位であるモーラは、char タイミングの按分の重みになる
(要件定義 9 章「モーラ按分を安全網に」)。

規則:
- 拗音・小書き母音 (ャュョァィゥェォヮ) は直前の文字と 1 モーラを成す
- 促音 (ッ)・撥音 (ン)・長音 (ー) はそれぞれ独立した 1 モーラ
"""

from __future__ import annotations

# 直前の文字と 1 モーラを成す小書き仮名 (align.py の char 按分重みでも使う)
SMALL_KANA = frozenset("ャュョァィゥェォヮゃゅょぁぃぅぇぉゎ")

_KATAKANA_RANGE = (0x30A0, 0x30FF)
_HIRAGANA_RANGE = (0x3040, 0x309F)


def is_kana(char: str) -> bool:
    cp = ord(char)
    return (
        _KATAKANA_RANGE[0] <= cp <= _KATAKANA_RANGE[1]
        or _HIRAGANA_RANGE[0] <= cp <= _HIRAGANA_RANGE[1]
    )


def split_mora(reading: str) -> list[str]:
    """カタカナ (またはひらがな) の読みをモーラ列に分割する。

    かな以外の文字 (英数等) は 1 文字 1 モーラ扱い。空白は無視する。
    """
    moras: list[str] = []
    for ch in reading:
        if ch.isspace():
            continue
        if ch in SMALL_KANA and moras and is_kana(moras[-1][-1]):
            moras[-1] += ch
        else:
            moras.append(ch)
    return moras


def count_mora(reading: str) -> int:
    return len(split_mora(reading))
