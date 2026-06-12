"""⑤' 強制アラインメント補正 (opt-in) — whisperx で word/char 時刻を実測に置き換える。

モーラ按分 (lyrics/align.py) は行内の時刻を文字の重み比で推定するだけなので、
メリスマ・ロングトーン・タメのある歌唱では文字単位の時刻がずれる (#3, #6)。
特に T2 (行 LRC) では行内が完全に推定値であり、サビでの体感ズレの主因になる。

whisperx の日本語 CTC アラインメント (wav2vec2) を分離ボーカルに掛け、
既存フレーズ窓 (±pad_ms に広げる) の中で文字レベルの実測時刻に置き換える。

- 行 (フレーズ) の窓は既存値 (LRC または按分) を信頼して探索範囲にする
- 認識できなかった行・文字は按分値のまま残す (全置換ではなく上書き補正)
- モデルのダウンロードと推論が重いため既定 OFF (CLI の --refine-align)

依存は任意 extra: ``uv sync --extra refine``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .schema import Phrase

logger = logging.getLogger(__name__)

# whisperx が日本語の既定にしている CTC モデル。再現性のため明示的に指定し
# meta.json に記録する (whisperx 側の既定変更に黙って追従しない)
DEFAULT_ALIGN_MODEL = "jonatasgrosman/wav2vec2-large-xlsr-53-japanese"

# 行窓の探索パディング。LRC の行時刻自体がこれ以上ずれている場合は補正しきれない
DEFAULT_PAD_MS = 400

# 行内の文字のうち、この割合以上に実測時刻が付かなければその行は按分値のまま残す
_MIN_MATCH_RATIO = 0.5


class RefineError(RuntimeError):
    """強制アラインメント補正に失敗したときに送出される。"""


@dataclass
class AlignedChar:
    """whisperx が返した 1 文字分の実測時刻。"""

    char: str
    start_ms: int
    end_ms: int


@dataclass
class RefineResult:
    refined_count: int  # 実測時刻に置き換えられた行数
    total: int
    model: str


def refine_phrases(
    phrases: list[Phrase],
    vocals_path: str | Path,
    *,
    device: str | None = None,
    model_name: str = DEFAULT_ALIGN_MODEL,
    pad_ms: int = DEFAULT_PAD_MS,
) -> RefineResult:
    """phrases の word/char 時刻を whisperx の実測値で上書きする (in place)。

    行単位で独立に補正し、マッチ率が低い行は按分値のまま残すため、
    間奏の誤検出や歌詞テキストと歌唱の不一致 (ラララ等) に対して安全側に倒れる。
    """
    whisperx = _import_whisperx()
    dev = device or "cpu"

    if not phrases:
        return RefineResult(refined_count=0, total=0, model=model_name)

    try:
        audio = whisperx.load_audio(str(vocals_path))
        model, metadata = whisperx.load_align_model(
            language_code="ja", device=dev, model_name=model_name
        )
        segments = [
            {
                "text": p.text,
                "start": max(0.0, (p.start_time - pad_ms) / 1000),
                "end": (p.end_time + pad_ms) / 1000,
            }
            for p in phrases
        ]
        result = whisperx.align(
            segments, model, metadata, audio, dev, return_char_alignments=True
        )
    except Exception as e:  # モデル取得失敗・推論エラー等は依存側の例外型が不定
        raise RefineError(f"強制アラインメントに失敗しました: {e}") from e

    aligned_segments = result.get("segments", [])
    refined = 0
    prev_start = 0
    for phrase, seg in zip(phrases, aligned_segments):
        chars = _parse_chars(seg)
        if _apply_char_times(phrase, chars, min_start=prev_start):
            refined += 1
        prev_start = phrase.start_time
    return RefineResult(refined_count=refined, total=len(phrases), model=model_name)


def _import_whisperx():
    try:
        import whisperx
    except ImportError as e:
        raise RefineError(
            "強制アラインメントには whisperx が必要です: uv sync --extra refine"
        ) from e
    return whisperx


def _parse_chars(segment: dict) -> list[AlignedChar]:
    """whisperx のセグメントから時刻付きの文字だけを取り出す。

    空白や認識不能文字には start/end が付かないことがあるため除外する。
    """
    chars: list[AlignedChar] = []
    for c in segment.get("chars", []):
        start, end = c.get("start"), c.get("end")
        if start is None or end is None:
            continue
        chars.append(
            AlignedChar(char=c.get("char", ""), start_ms=int(start * 1000), end_ms=int(end * 1000))
        )
    return chars


def _apply_char_times(
    phrase: Phrase, aligned: list[AlignedChar], *, min_start: int = 0
) -> bool:
    """フレーズ内の char 時刻を実測値で上書きする。成功したら True。

    - 文字の並びを先頭から突き合わせ、一致した文字に実測時刻を入れる
    - 実測が付かなかった文字は前後の確定点の間に均等配置する
    - マッチ率が _MIN_MATCH_RATIO 未満、または補正後の行頭が前の行 (min_start)
      より前に出る場合は、何も変更せず False を返す (按分値のまま)
    """
    flat = [c for w in phrase.words for c in w.chars]
    if not flat or not aligned:
        return False

    # 先頭からの単調マッチ (CTC の出力は時間順なので前方探索のみで足りる)。
    # 認識されなかった文字があっても探索位置 j は進めず、後続のマッチを保つ
    times: list[tuple[int, int] | None] = [None] * len(flat)
    j = 0
    matched = 0
    for i, c in enumerate(flat):
        k = j
        while k < len(aligned) and aligned[k].char != c.char:
            k += 1
        if k < len(aligned):
            times[i] = (aligned[k].start_ms, aligned[k].end_ms)
            matched += 1
            j = k + 1

    if matched < max(1, int(len(flat) * _MIN_MATCH_RATIO)):
        return False

    # 補間のアンカーは実測の極値も考慮する (パディングで行窓の外に出た実測を潰さない)
    span_start = min(phrase.start_time, min(t[0] for t in times if t))
    span_end = max(phrase.end_time, max(t[1] for t in times if t))
    filled = _fill_gaps(times, span_start, span_end)
    filled = _enforce_monotonic(filled)
    if filled[0][0] < min_start:
        return False

    k = 0
    for w in phrase.words:
        for c in w.chars:
            c.start_time, c.end_time = filled[k]
            k += 1
        w.start_time = w.chars[0].start_time
        w.end_time = w.chars[-1].end_time
    phrase.start_time = phrase.words[0].start_time
    phrase.end_time = phrase.words[-1].end_time
    return True


def _fill_gaps(
    times: list[tuple[int, int] | None], span_start: int, span_end: int
) -> list[tuple[int, int]]:
    """実測が付かなかった文字を、前後の確定点の間へ均等配置する。"""
    n = len(times)
    result = list(times)
    i = 0
    while i < n:
        if result[i] is not None:
            i += 1
            continue
        j = i
        while j < n and result[j] is None:
            j += 1
        prev_end = result[i - 1][1] if i > 0 else span_start
        next_start = result[j][0] if j < n else span_end
        next_start = max(next_start, prev_end + (j - i))  # 1 文字最低 1ms
        step = (next_start - prev_end) / (j - i)
        for k in range(i, j):
            s = int(prev_end + (k - i) * step)
            e = int(prev_end + (k - i + 1) * step)
            result[k] = (s, max(e, s + 1))
        i = j
    return result  # type: ignore[return-value]


def _enforce_monotonic(times: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """char 時刻列を非減少に揃える (丸めによる僅かな逆行を吸収する)。"""
    fixed: list[tuple[int, int]] = []
    prev_end = None
    for s, e in times:
        if prev_end is not None and s < prev_end:
            s = prev_end
        e = max(e, s + 1)
        fixed.append((s, e))
        prev_end = e
    return fixed
