"""⑤' 強制アラインメント補正 (opt-in) — whisperx で word/char 時刻を実測に置き換える。

モーラ按分 (lyrics/align.py) は行内の時刻を文字の重み比で推定するだけなので、
メリスマ・ロングトーン・タメのある歌唱では文字単位の時刻がずれる (#3, #6)。
特に T2 (行 LRC) では行内が完全に推定値であり、サビでの体感ズレの主因になる。

whisperx の日本語 CTC アラインメント (wav2vec2) を分離ボーカルに掛け、
既存フレーズ窓 (±pad_ms に広げる) の中で文字レベルの実測時刻に置き換える。

- 行 (フレーズ) の窓は既存値 (LRC または按分) を信頼して探索範囲にする
- 認識できなかった行・文字は按分値のまま残す (全置換ではなく上書き補正)
- 行末は補正前の行末まで余韻として伸ばす (表示が次行まで残る按分の挙動を踏襲)
- モデルのダウンロードと推論が重いため既定 OFF (CLI の --refine-align)

依存は任意 extra: ``uv sync --extra refine``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .refine_logic import (
    DEFAULT_ALIGN_MODEL,
    DEFAULT_PAD_MS,
    AlignedChar,
    RefineParams,
    apply_char_times,
    clamp_tail,
    extend_tail,
)
from .schema import Phrase

__all__ = [
    "DEFAULT_ALIGN_MODEL",
    "DEFAULT_PAD_MS",
    "AlignedChar",
    "RefineParams",
    "RefineError",
    "RefineResult",
    "refine_phrases",
]

logger = logging.getLogger(__name__)


class RefineError(RuntimeError):
    """強制アラインメント補正に失敗したときに送出される。"""


@dataclass
class RefineResult:
    refined_count: int  # 実測時刻に置き換えられた行数
    total: int
    model: str


def refine_phrases(
    phrases: list[Phrase],
    vocals_path: str | Path,
    *,
    params: RefineParams | None = None,
) -> RefineResult:
    """phrases の word/char 時刻を whisperx の実測値で上書きする (in place)。

    行単位で独立に補正し、マッチ率が低い行・縮退した行は按分値のまま残すため、
    間奏の誤検出や歌詞テキストと歌唱の不一致 (ラララ等) に対して安全側に倒れる。
    whisperx/wav2vec2 の MPS 実行は不安定なため、常に CPU で実行する。
    """
    whisperx = _import_whisperx()
    dev = "cpu"
    params = params or RefineParams()

    if not phrases:
        return RefineResult(refined_count=0, total=0, model=params.model_name)

    try:
        audio = whisperx.load_audio(str(vocals_path))
        model, metadata = whisperx.load_align_model(
            language_code="ja", device=dev, model_name=params.model_name
        )
        segments = [
            {
                "text": p.text,
                "start": max(0.0, (p.start_time - params.pad_ms) / 1000),
                "end": (p.end_time + params.pad_ms) / 1000,
            }
            for p in phrases
        ]
        result = whisperx.align(segments, model, metadata, audio, dev, return_char_alignments=True)
    except Exception as e:  # モデル取得失敗・推論エラー等は依存側の例外型が不定
        raise RefineError(f"強制アラインメントに失敗しました: {e}") from e

    aligned_segments = result.get("segments", [])
    if len(aligned_segments) != len(phrases):
        logger.warning(
            "whisperx の返り行数 (%d) が入力行数 (%d) と一致しません。末尾の行が未補正のまま残ります",
            len(aligned_segments),
            len(phrases),
        )
    refined = 0
    prev_start = 0
    prev_phrase: Phrase | None = None
    for i, (phrase, seg) in enumerate(zip(phrases, aligned_segments)):
        chars = _parse_chars(seg)
        window_start = max(0, phrase.start_time - params.pad_ms)
        # この時点で次行は未補正なので、安定した境界 (LRC/按分の行頭) になる
        next_start = phrases[i + 1].start_time if i + 1 < len(phrases) else None
        orig_end = phrase.end_time
        if apply_char_times(
            phrase,
            chars,
            min_start=prev_start,
            window_start=window_start,
            next_start=next_start,
            params=params,
        ):
            extend_tail(phrase, orig_end)
            refined += 1
        # 前の行の末尾文字 (補間された括弧等) がこの行の頭を追い越すと、
        # 次の行の途中に「現在の文字」として割り込むため切り詰める
        if prev_phrase is not None:
            clamp_tail(prev_phrase, phrase.start_time)
        prev_start = phrase.start_time
        prev_phrase = phrase
    return RefineResult(refined_count=refined, total=len(phrases), model=params.model_name)


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
            AlignedChar(
                char=c.get("char", ""),
                start_ms=int(start * 1000),
                end_ms=int(end * 1000),
                score=c.get("score"),
            )
        )
    return chars
