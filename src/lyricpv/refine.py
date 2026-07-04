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

from .schema import Phrase

logger = logging.getLogger(__name__)

# whisperx が日本語の既定にしている CTC モデル。再現性のため明示的に指定し
# meta.json に記録する (whisperx 側の既定変更に黙って追従しない)
DEFAULT_ALIGN_MODEL = "jonatasgrosman/wav2vec2-large-xlsr-53-japanese"

# 行窓の探索パディング。LRC の行時刻自体がこれ以上ずれている場合は補正しきれない
DEFAULT_PAD_MS = 400

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

# wav2vec2 (16kHz / stride 320) の 1 フレーム長。実測がこれ以下の文字は、
# CTC が音声上の居場所を見つけられないまま出力だけ強制された「潰れ」
# (弱く歌われた「〜だろう」の「う」や、縮退パスの中間文字) なので採らない
_ONE_FRAME_MS = 25


class RefineError(RuntimeError):
    """強制アラインメント補正に失敗したときに送出される。"""


@dataclass(frozen=True)
class RefineParams:
    """補正の調整パラメータ。CLI の --refine-* フラグから上書きできる。

    既定値は実データ (Reply 38 行) の実測分布から決めたもの (#3)。
    棄却系の閾値を緩めると補正される行は増えるが、縮退アラインメントが
    すり抜けて表示が乱れるリスクも増える。
    """

    # CTC アラインメントモデル (HuggingFace ID)。meta.json に記録される
    model_name: str = DEFAULT_ALIGN_MODEL

    # 行窓の探索パディング (ms)。LRC の行時刻が全体的にずれている曲は広げる
    pad_ms: int = DEFAULT_PAD_MS

    # 行内の歌唱文字のうち実測が付いた割合がこれ未満の行は按分のまま残す
    min_match_ratio: float = 0.5

    # この文字スコア未満の実測は潰れとして捨てる。実データでは正当な実測は
    # 0.46 以上、潰れは 0.0〜0.07 に分布し明確に分離する (0.35〜0.75 の
    # 中間帯には正当な文字が多いため、安易に上げない)
    min_char_score: float = 0.35

    # 行中間 (行末の文字を除く) で許容する潰れ実測の数。これを超える行は
    # CTC パスの崩壊 (文字が数十 ms 間隔で流れる) とみなし按分のまま残す
    max_squashed_mid_chars: int = 1

    def __post_init__(self) -> None:
        if not self.model_name:
            raise ValueError("refine のモデル名 (model_name) が空です")
        if self.pad_ms < 0:
            raise ValueError(f"pad_ms は 0 以上で指定してください: {self.pad_ms}")
        if not 0.0 <= self.min_match_ratio <= 1.0:
            raise ValueError(f"min_match_ratio は 0〜1 で指定してください: {self.min_match_ratio}")
        if not 0.0 <= self.min_char_score <= 1.0:
            raise ValueError(f"min_char_score は 0〜1 で指定してください: {self.min_char_score}")
        if self.max_squashed_mid_chars < 0:
            raise ValueError(
                f"max_squashed_mid_chars は 0 以上で指定してください: {self.max_squashed_mid_chars}"
            )


@dataclass
class AlignedChar:
    """whisperx が返した 1 文字分の実測時刻。"""

    char: str
    start_ms: int
    end_ms: int
    score: float | None = None


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
        if _apply_char_times(
            phrase,
            chars,
            min_start=prev_start,
            window_start=window_start,
            next_start=next_start,
            params=params,
        ):
            _extend_tail(phrase, orig_end)
            refined += 1
        # 前の行の末尾文字 (補間された括弧等) がこの行の頭を追い越すと、
        # 次の行の途中に「現在の文字」として割り込むため切り詰める
        if prev_phrase is not None:
            _clamp_tail(prev_phrase, phrase.start_time)
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


def _is_squashed(a: AlignedChar, min_score: float) -> bool:
    """CTC が音声上の居場所を見つけられないまま潰した実測かどうか。"""
    if a.end_ms - a.start_ms <= _ONE_FRAME_MS:
        return True
    return a.score is not None and a.score < min_score


def _apply_char_times(
    phrase: Phrase,
    aligned: list[AlignedChar],
    *,
    min_start: int = 0,
    window_start: int | None = None,
    next_start: int | None = None,
    params: RefineParams | None = None,
) -> bool:
    """フレーズ内の char 時刻を実測値で上書きする。成功したら True。

    - 文字の並びを先頭から突き合わせ、一致した文字に実測時刻を入れる
    - 実測が付かなかった文字は前後の確定点の間に均等配置する
      (行頭・行末の run は _TYPICAL_CHAR_MS で短く置き、引き伸ばさない)
    - 次の場合は何も変更せず False を返す (按分値のまま):
      マッチ率が min_match_ratio 未満 / 行中間の潰れ実測が
      max_squashed_mid_chars 超 / 補正後の行頭が前の行 (min_start) より前 /
      実測が探索窓の先頭 (window_start) に張り付いている (縮退) /
      次行の頭 (next_start) を追い越す文字が半数を超える
    """
    params = params or RefineParams()
    flat = [c for w in phrase.words for c in w.chars]
    if not flat or not aligned:
        return False

    # 先頭からの単調マッチ (CTC の出力は時間順なので前方探索のみで足りる)。
    # 認識されなかった文字があっても探索位置 j は進めず、後続のマッチを保つ。
    # 句読点・記号 (歌われない文字) は whisperx が補間時刻を付けて返すことが
    # あるが信用できないため実測としては採らず、後段の補間ルールで配置する。
    # 潰れ実測 (_is_squashed) も同様に採らないが、パス上の位置は消費する
    times: list[tuple[int, int] | None] = [None] * len(flat)
    sung_idx = [i for i, c in enumerate(flat) if c.char.isalnum()]
    n_sung = len(sung_idx)
    if n_sung == 0:
        return False
    last_sung = sung_idx[-1]
    j = 0
    matched = 0
    squashed_mid = 0
    for i, c in enumerate(flat):
        if not c.char.isalnum():
            continue
        k = j
        while k < len(aligned) and aligned[k].char != c.char:
            k += 1
        if k < len(aligned):
            a = aligned[k]
            j = k + 1
            if _is_squashed(a, params.min_char_score):
                if i != last_sung:
                    squashed_mid += 1
                continue
            times[i] = (a.start_ms, a.end_ms)
            matched += 1

    # 行中間の潰れが多い行は CTC パスの崩壊 (一部の文字を数十 ms 間隔で
    # 埋めただけの状態)。残った実測も信用できないため行ごと棄却する。
    # 長い行ほど潰れが出やすいため、歌唱文字数に比例した閾値を使う
    squash_limit = max(params.max_squashed_mid_chars, n_sung // 6)
    if squashed_mid > squash_limit:
        logger.debug(
            "棄却 [squashed_mid=%d > %d (n_sung=%d)]: %s",
            squashed_mid,
            squash_limit,
            n_sung,
            phrase.text,
        )
        return False
    min_needed = max(1, int(n_sung * params.min_match_ratio))
    if matched < min_needed:
        logger.debug(
            "棄却 [match=%d/%d < %d (%.0f%%)]: %s",
            matched,
            n_sung,
            min_needed,
            params.min_match_ratio * 100,
            phrase.text,
        )
        return False

    first_matched = next(t for t in times if t)
    if window_start is not None and first_matched[0] <= window_start + _EDGE_EPS_MS:
        logger.debug(
            "棄却 [edge_eps: first=%dms <= window_start+eps=%dms]: %s",
            first_matched[0],
            window_start + _EDGE_EPS_MS,
            phrase.text,
        )
        return False

    # CTC は行末の無音を最後の文字の終了時刻に吸収させるため上限で切り詰める
    i_last = max(i for i, t in enumerate(times) if t)
    s_last, e_last = times[i_last]
    times[i_last] = (s_last, min(e_last, s_last + _MAX_LAST_CHAR_MS))

    filled = _fill_gaps(times)
    filled = _enforce_monotonic(filled)
    if filled[0][0] < min_start:
        logger.debug(
            "棄却 [min_start: filled[0]=%dms < min_start=%dms]: %s",
            filled[0][0],
            min_start,
            phrase.text,
        )
        return False

    # 次行の頭を追い越す文字は next_start に clamp する。半数超が追い越す行は
    # CTC パスが行窓からずれている (コーラス重なり等) ため棄却する。
    # しきい値は固定 (n_filled // 2) で、行ごとに調整可能なパラメータは持たない
    if next_start is not None:
        crossing = sum(1 for s, _ in filled if s > next_start)
        n_filled = len(filled)
        if crossing > n_filled // 2:
            logger.debug(
                "棄却 [crossing=%d > %d (半数超), next_start=%dms]: %s",
                crossing,
                n_filled // 2,
                next_start,
                phrase.text,
            )
            return False
        if crossing > 0:
            logger.debug(
                "clamp [crossing=%d, next_start=%dms]: %s",
                crossing,
                next_start,
                phrase.text,
            )
            for ci in range(n_filled):
                s, e = filled[ci]
                if s > next_start:
                    filled[ci] = (next_start, max(next_start + 1, e))

    logger.debug(
        "補正 [match=%d/%d, squashed_mid=%d]: %s",
        matched,
        n_sung,
        squashed_mid,
        phrase.text,
    )
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


def _extend_tail(phrase: Phrase, orig_end: int) -> None:
    """実測で縮んだ行末を、補正前の行末 (T2 では次行の頭) まで余韻として伸ばす。

    SDK の currentChar/currentPhrase は endTime を過ぎると null を返すため、
    行末を実測の歌い終わりちょうどで切ると、次の行まで歌詞が消えて
    「ぶっつり切れた」表示になる。按分は行末文字の end を行窓の端まで
    伸ばしており、その表示挙動を踏襲する。開始時刻には触れない。
    """
    if orig_end <= phrase.end_time:
        return
    last_word = phrase.words[-1]
    if last_word.chars:
        last_word.chars[-1].end_time = orig_end
    last_word.end_time = orig_end
    phrase.end_time = orig_end


def _clamp_tail(prev_phrase: Phrase, cur_start: int) -> None:
    """前の行のうち cur_start を追い越した文字・単語の開始時刻を切り詰める。

    SDK は flat 配列を読み込み時に startTime で再ソートするため逆行で壊れは
    しないが、前の行の補間文字 (括弧等) が次の行の途中に「現在の文字」として
    割り込むと表示がちらつく。開始時刻を次行の頭に揃えて割り込みを防ぐ。
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
