"""LRC / 逐字 (enhanced) LRC のパーサ。

対応フォーマット:
- 行タグ: ``[mm:ss.xx] 歌詞`` (行レベル同期 = Tier 2)
- 逐字タグ: ``[mm:ss.xx] <mm:ss.xx> 単語 <mm:ss.xx> 単語`` (word レベル = Tier 1)

タグなしの行はプレーンテキスト行 (時刻 None) として返す。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_LINE_TAG = re.compile(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_WORD_TAG = re.compile(r"<(\d+):(\d{1,2})(?:[.:](\d{1,3}))?>")
_META_TAG = re.compile(r"^\[(ar|ti|al|by|offset|la|length|re|ve|tool|id):.*\]$", re.IGNORECASE)


@dataclass
class TimedWord:
    start_ms: int
    text: str


@dataclass
class LyricLine:
    text: str
    start_ms: int | None = None
    words: list[TimedWord] = field(default_factory=list)  # 逐字 LRC の場合のみ


def _tag_to_ms(m: re.Match) -> int:
    minutes, seconds = int(m.group(1)), int(m.group(2))
    frac = m.group(3) or "0"
    # 2 桁は 1/100 秒、3 桁は ms
    ms = int(frac) * (10 if len(frac) == 2 else 100 if len(frac) == 1 else 1)
    return (minutes * 60 + seconds) * 1000 + ms


def parse_lrc(text: str) -> list[LyricLine]:
    """LRC テキストをパースし、時刻昇順の歌詞行リストを返す。"""
    lines: list[LyricLine] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or _META_TAG.match(raw):
            continue

        tags = list(_LINE_TAG.finditer(raw))
        if not tags or tags[0].start() != 0:
            if not raw.startswith("["):
                lines.append(LyricLine(text=raw))
            continue

        # 先頭に複数タグが連なる場合 ([00:10.0][00:50.0]繰り返し行) に対応
        pos = 0
        starts: list[int] = []
        while pos < len(raw):
            m = _LINE_TAG.match(raw, pos)
            if not m:
                break
            starts.append(_tag_to_ms(m))
            pos = m.end()
        body = raw[pos:].strip()

        words = _parse_word_tags(body)
        plain = _WORD_TAG.sub("", body).strip()
        plain = re.sub(r"\s+", " ", plain)
        if not plain:
            continue
        for start in starts:
            lines.append(LyricLine(text=plain, start_ms=start, words=list(words)))

    timed = [ln for ln in lines if ln.start_ms is not None]
    plain_lines = [ln for ln in lines if ln.start_ms is None]
    timed.sort(key=lambda ln: ln.start_ms)
    return timed + plain_lines


def _parse_word_tags(body: str) -> list[TimedWord]:
    tags = list(_WORD_TAG.finditer(body))
    if not tags:
        return []
    words: list[TimedWord] = []
    for i, m in enumerate(tags):
        end = tags[i + 1].start() if i + 1 < len(tags) else len(body)
        chunk = body[m.end():end].strip()
        if chunk:
            words.append(TimedWord(start_ms=_tag_to_ms(m), text=chunk))
    return words


def is_synced(lines: list[LyricLine]) -> bool:
    """行レベル以上の時刻が付いているか。"""
    return any(ln.start_ms is not None for ln in lines)


def is_word_synced(lines: list[LyricLine]) -> bool:
    """逐字 (word レベル) の時刻が付いているか。"""
    timed = [ln for ln in lines if ln.start_ms is not None]
    return bool(timed) and all(len(ln.words) > 0 for ln in timed)
