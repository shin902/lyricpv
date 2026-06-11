"""歌詞アライメント — 行/単語の時刻を phrase→word→char 階層へ按分する。

中核はモーラ按分 (要件定義 9 章の安全網方式):
- 行レベル時刻 (T2) → 行窓内で単語をモーラ数比で配分し、char へ細分化
- 逐字時刻 (T1) → 単語時刻はそのまま使い、char のみ細分化
- 時刻なし (T3/T4) → 声量エンベロープから歌唱区間を推定し、行をモーラ数比で配分

音声モデル非依存・GPU 不要のため、ボカロ等の domain shift の影響を受けない。
メリスマ/ロングトーンには按分誤差が残るため、自動生成値は「叩き台」であり
手動上書き (ランタイム SDK の applyOverrides) で補正できるようにしている。
"""

from __future__ import annotations

from ..schema import AmplitudePoint, Char, Phrase, Word
from .lrc import LyricLine, is_synced, is_word_synced
from .morph import MorphWord, analyze_line
from .mora import SMALL_KANA, is_kana

# 1 モーラあたりの歌唱時間の上限 (これ以上は間奏とみなし行を切り上げる)
_MAX_MS_PER_MORA = 500
_LINE_TAIL_MARGIN_MS = 800

# 逐字 (T1) の最終単語など、次の時刻が無い場合の長さ推定に使う
# 1 モーラあたりの典型的な歌唱時間。_MAX_MS_PER_MORA (間奏とみなす上限) を
# そのまま使うと 5 モーラ語が 2.5 秒に伸びてしまうため別定数にする。
_TYPICAL_MS_PER_MORA = 180

# char 按分の重み: 促音・撥音・長音は短く発音される (小書き仮名は mora.SMALL_KANA)
_SHORT_KANA = set("ッっーンん")


def align(
    lines: list[LyricLine],
    duration_ms: int,
    amplitude: list[AmplitudePoint] | None = None,
) -> list[Phrase]:
    """歌詞行リストから契約A の phrases (phrase→word→char) を構築する。"""
    lines = [ln for ln in lines if ln.text.strip()]
    if not lines:
        return []

    if is_synced(lines):
        timed = [ln for ln in lines if ln.start_ms is not None]
        if is_word_synced(lines):
            return _align_word_synced(timed, duration_ms)
        return _align_line_synced(timed, duration_ms)
    return _align_plain(lines, duration_ms, amplitude)


def _align_line_synced(lines: list[LyricLine], duration_ms: int) -> list[Phrase]:
    """T2: 行時刻あり。行窓内を単語のモーラ数比で按分する。"""
    phrases: list[Phrase] = []
    for i, line in enumerate(lines):
        start = line.start_ms
        next_start = lines[i + 1].start_ms if i + 1 < len(lines) else duration_ms
        morphs = analyze_line(line.text)
        if not morphs:
            continue
        total_mora = sum(m.mora_count for m in morphs)
        cap = start + total_mora * _MAX_MS_PER_MORA + _LINE_TAIL_MARGIN_MS
        # 最低表示時間 200ms を確保するための下限。次行が 200ms 未満の間隔で
        # 始まる場合、この行の end が次行の start を超えて重なることがあるが、
        # 実害は小さく (validate() も startTime 昇順のみ検証) 許容している。
        end = max(start + 200, min(next_start, cap, duration_ms))
        phrases.append(_build_phrase(line.text, morphs, start, end))
    return phrases


def _align_word_synced(lines: list[LyricLine], duration_ms: int) -> list[Phrase]:
    """T1: 逐字時刻あり。単語時刻は実測値を使い char のみ按分する。"""
    phrases: list[Phrase] = []
    for i, line in enumerate(lines):
        next_line_start = lines[i + 1].start_ms if i + 1 < len(lines) else duration_ms
        words: list[Word] = []
        for j, tw in enumerate(line.words):
            w_start = tw.start_ms
            w_end = line.words[j + 1].start_ms if j + 1 < len(line.words) else None
            if w_end is None:
                morphs = analyze_line(tw.text)
                moras = sum(m.mora_count for m in morphs) or len(tw.text)
                w_end = min(next_line_start, w_start + moras * _TYPICAL_MS_PER_MORA)
            w_end = max(w_start + 1, w_end)
            pos = _first_pos(tw.text)
            words.append(
                Word(
                    start_time=w_start,
                    end_time=w_end,
                    text=tw.text,
                    pos=pos,
                    chars=_distribute_chars(tw.text, w_start, w_end),
                )
            )
        if not words:
            continue
        phrases.append(
            Phrase(
                start_time=words[0].start_time,
                end_time=words[-1].end_time,
                text=line.text,
                words=words,
            )
        )
    return phrases


def _align_plain(
    lines: list[LyricLine], duration_ms: int, amplitude: list[AmplitudePoint] | None
) -> list[Phrase]:
    """T3/T4: 時刻なし。歌唱区間全体に行をモーラ数比で按分する粗いドラフト。

    精度は低い前提であり、後段の手動補正の叩き台に位置づけられる。
    """
    span_start, span_end = _vocal_active_span(duration_ms, amplitude)

    # 形態素が空の行 (記号のみ等) は按分対象から除外する。weights に残すと
    # その分のスパンが割り当て先のないまま隙間として残ってしまう。
    entries = [(ln, ms) for ln in lines if (ms := analyze_line(ln.text))]
    if not entries:
        return []
    weights = [max(1, sum(m.mora_count for m in ms)) for _, ms in entries]
    total = sum(weights)
    span = span_end - span_start

    phrases: list[Phrase] = []
    cursor = span_start
    for (line, morphs), w in zip(entries, weights):
        line_span = span * w / total
        sing_ms = max(200, int(line_span * 0.85))  # 残り 15% は行間の息継ぎ
        phrases.append(_build_phrase(line.text, morphs, int(cursor), int(cursor) + sing_ms))
        cursor += line_span
    return phrases


def _vocal_active_span(
    duration_ms: int, amplitude: list[AmplitudePoint] | None, threshold: float = 0.15
) -> tuple[int, int]:
    """声量エンベロープから歌唱の開始・終了をざっくり推定する。"""
    if amplitude:
        active = [p.time for p in amplitude if p.value >= threshold]
        if len(active) >= 2:
            return active[0], min(active[-1] + 1000, duration_ms)
    return int(duration_ms * 0.1), int(duration_ms * 0.9)


def _build_phrase(text: str, morphs: list[MorphWord], start: int, end: int) -> Phrase:
    """行窓 [start, end] を単語のモーラ数比で分割し Word/Char を作る。"""
    total_mora = sum(m.mora_count for m in morphs)
    span = end - start

    words: list[Word] = []
    cursor = float(start)
    for m in morphs:
        w_span = span * m.mora_count / total_mora
        w_start, w_end = int(cursor), int(cursor + w_span)
        w_end = max(w_end, w_start + 1)
        words.append(
            Word(
                start_time=w_start,
                end_time=w_end,
                text=m.surface,
                pos=m.pos,
                chars=_distribute_chars(m.surface, w_start, w_end),
            )
        )
        cursor += w_span
    words[-1].end_time = end
    if words[-1].chars:
        words[-1].chars[-1].end_time = end
    return Phrase(start_time=start, end_time=end, text=text, words=words)


def _distribute_chars(surface: str, start: int, end: int) -> list[Char]:
    """単語窓内を文字へ按分する。

    小書き仮名 (ャ等) は直前の文字と同一モーラなので軽く、
    促音・撥音・長音 (ッンー) はやや短い重みにする。
    漢字等は読みとの対応付けが一意でないため均等割りとする。
    """
    chars = list(surface)
    if not chars:
        return []

    def weight(c: str) -> float:
        if c in SMALL_KANA:
            return 0.3
        if c in _SHORT_KANA:
            return 0.6
        return 1.0

    has_kana = any(is_kana(c) for c in chars)
    weights = [weight(c) if has_kana else 1.0 for c in chars]
    total = sum(weights)
    span = end - start

    result: list[Char] = []
    cursor = float(start)
    for c, w in zip(chars, weights):
        c_span = span * w / total
        c_start, c_end = int(cursor), int(cursor + c_span)
        result.append(Char(start_time=c_start, end_time=max(c_end, c_start + 1), char=c))
        cursor += c_span
    result[-1].end_time = end
    return result


def _first_pos(text: str) -> str:
    morphs = analyze_line(text)
    return morphs[0].pos if morphs else ""
