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

# 実測が付かない行頭・行末の文字 (括弧・句読点等が多い) に与える既定の長さ。
# 行端アンカーで補間すると「...」等が間奏まで引き伸ばされるため定数で置く
_TYPICAL_CHAR_MS = 150

# 行頭の実測がこの距離まで探索窓の先頭に張り付いていたら、縮退アラインメント
# (窓内にその行の歌唱が見つからずパスが端に潰れた状態) とみなして棄却する
_EDGE_EPS_MS = 30

# 行末の最後の実測文字に許す最大長。CTC は行末の無音・間奏を最後の文字の
# 終了時刻に吸収させる (実データで 4.3 秒の「ど」を観測)。ロングトーンは
# 残したいので 0 にはせず、無音吸収だけを切り詰める
_MAX_LAST_CHAR_MS = 2000


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
    prev_phrase: Phrase | None = None
    for phrase, seg in zip(phrases, aligned_segments):
        chars = _parse_chars(seg)
        window_start = max(0, phrase.start_time - pad_ms)
        if _apply_char_times(phrase, chars, min_start=prev_start, window_start=window_start):
            refined += 1
        # 前の行の末尾文字 (補間された括弧等) がこの行の頭を追い越すと、
        # SDK の二分探索の前提 (flat 配列が時刻昇順) が壊れるため切り詰める
        if prev_phrase is not None:
            _clamp_tail(prev_phrase, phrase.start_time)
        prev_start = phrase.start_time
        prev_phrase = phrase
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
    phrase: Phrase,
    aligned: list[AlignedChar],
    *,
    min_start: int = 0,
    window_start: int | None = None,
) -> bool:
    """フレーズ内の char 時刻を実測値で上書きする。成功したら True。

    - 文字の並びを先頭から突き合わせ、一致した文字に実測時刻を入れる
    - 実測が付かなかった文字は前後の確定点の間に均等配置する
      (行頭・行末の run は _TYPICAL_CHAR_MS で短く置き、引き伸ばさない)
    - マッチ率が _MIN_MATCH_RATIO 未満、補正後の行頭が前の行 (min_start) より
      前に出る、または実測が探索窓の先頭 (window_start) に張り付いている
      (縮退アラインメント) 場合は、何も変更せず False を返す (按分値のまま)
    """
    flat = [c for w in phrase.words for c in w.chars]
    if not flat or not aligned:
        return False

    # 先頭からの単調マッチ (CTC の出力は時間順なので前方探索のみで足りる)。
    # 認識されなかった文字があっても探索位置 j は進めず、後続のマッチを保つ。
    # 句読点・記号 (歌われない文字) は whisperx が補間時刻を付けて返すことが
    # あるが信用できないため実測としては採らず、後段の補間ルールで配置する
    times: list[tuple[int, int] | None] = [None] * len(flat)
    n_sung = sum(1 for c in flat if c.char.isalnum())
    if n_sung == 0:
        return False
    j = 0
    matched = 0
    for i, c in enumerate(flat):
        if not c.char.isalnum():
            continue
        k = j
        while k < len(aligned) and aligned[k].char != c.char:
            k += 1
        if k < len(aligned):
            times[i] = (aligned[k].start_ms, aligned[k].end_ms)
            matched += 1
            j = k + 1

    if matched < max(1, int(n_sung * _MIN_MATCH_RATIO)):
        return False

    first_matched = next(t for t in times if t)
    if window_start is not None and first_matched[0] <= window_start + _EDGE_EPS_MS:
        return False

    # CTC は行末の無音を最後の文字の終了時刻に吸収させるため上限で切り詰める
    i_last = max(i for i, t in enumerate(times) if t)
    s_last, e_last = times[i_last]
    times[i_last] = (s_last, min(e_last, s_last + _MAX_LAST_CHAR_MS))

    filled = _fill_gaps(times)
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


def _fill_gaps(times: list[tuple[int, int] | None]) -> list[tuple[int, int]]:
    """実測が付かなかった文字を、前後の確定点の間へ均等配置する。

    行頭・行末の run は片側にしか確定点がないため、_TYPICAL_CHAR_MS の固定長で
    確定点に隣接させる。行の按分端をアンカーにすると「...」「）」等の
    時刻が付かない文字が間奏まで引き伸ばされてしまう (実測データで最大 4 秒)。
    """
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
        run = j - i
        if i == 0 and j < n:  # 行頭: 最初の確定点の直前に置く
            prev_end = max(0, result[j][0] - run * _TYPICAL_CHAR_MS)
            next_start = result[j][0]
        elif j == n and i > 0:  # 行末: 最後の確定点の直後に置く
            prev_end = result[i - 1][1]
            next_start = prev_end + run * _TYPICAL_CHAR_MS
        else:  # 中間: 前後の確定点の間
            prev_end = result[i - 1][1]
            next_start = result[j][0]
        next_start = max(next_start, prev_end + run)  # 1 文字最低 1ms
        step = (next_start - prev_end) / run
        for k in range(i, j):
            s = int(prev_end + (k - i) * step)
            e = int(prev_end + (k - i + 1) * step)
            result[k] = (s, max(e, s + 1))
        i = j
    return result  # type: ignore[return-value]


def _clamp_tail(prev_phrase: Phrase, cur_start: int) -> None:
    """前の行のうち cur_start を追い越した文字・単語の開始時刻を切り詰める。

    契約A の利用側 (SDK) は全フレーズをフラット化した char/word 配列を
    開始時刻の昇順とみなして二分探索するため、行間の重なりは許しても
    開始時刻の逆行は作らない。
    """
    for w in prev_phrase.words:
        for c in w.chars:
            if c.start_time > cur_start:
                c.start_time = cur_start
                c.end_time = max(c.end_time, c.start_time + 1)
        if w.start_time > cur_start:
            w.start_time = cur_start
            w.end_time = max(w.end_time, w.start_time + 1)


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
