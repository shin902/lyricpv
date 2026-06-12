"""⑤ パイプライン統合 — 取得→分離→楽曲地図→歌詞整合→契約A JSON。

1 曲につき 1 回のオフライン処理。出力ディレクトリ構成:

    <out_dir>/
      master.wav          可逆 WAV マスター (44.1kHz/ステレオ)
      vocals.wav          分離ボーカル
      accompaniment.wav   伴奏
      lyric_data.json     契約A (TextAlive 互換 JSON)
      meta.json           解析メタ情報 (使用デバイス・歌詞 Tier 等)

音源ファイルは解析処理内に留め、配布物に含めないこと (要件定義 2 章)。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NamedTuple

from . import music_map
from .enhance import enhance_vocals
from .fetch import FetchResult, fetch_youtube, import_file, is_url
from .lyrics.align import align
from .lyrics.fetch import fetch_lyrics
from .lyrics.lrc import LyricLine, is_word_synced, parse_lrc
from .schema import LyricData, SongMeta, SongSource
from .separate import separate

logger = logging.getLogger(__name__)

LYRIC_DATA_FILENAME = "lyric_data.json"
META_FILENAME = "meta.json"

ProgressCallback = Callable[[str, str], None]

# 取得後に自動検出した (title, artist) を受け取り、上書き後の (title, artist) を返す。
# CLI の対話モードでユーザーに確認・修正させるために使う。
MetadataCallback = Callable[[str, str], "tuple[str, str]"]


class LyricsDecision(NamedTuple):
    """歌詞検索結果のレビュー判定。

    - action="accept": この歌詞を採用する
    - action="skip":   歌詞なし (T4) で続行する
    - action="retry":  title/artist を差し替えて再検索する
    """

    action: str  # "accept" | "skip" | "retry"
    title: str
    artist: str


# 検索した (title, artist, 行, tier) を受け取り、採用/スキップ/再検索を返す。
LyricsReviewCallback = Callable[
    [str, str, "list[LyricLine]", str], LyricsDecision
]


@dataclass
class PipelineOptions:
    title: str | None = None  # 自動取得値の上書き
    artist: str | None = None
    lyrics_text: str | None = None  # ユーザー供給の歌詞 (LRC またはプレーン = T3)
    vocaloid: bool = False  # 歌詞検索で NetEase を優先する
    separation_model: str = "htdemucs"
    device: str | None = None  # None = 自動 (MPS 優先)
    skip_separation: bool = False  # テスト・高速試行用
    # 分離ボーカルにハモリ除去・残響除去を掛ける (#3)。重い処理かつ
    # audio-separator (extra: enhance) が必要なため既定 OFF
    enhance_vocals: bool = False


@dataclass
class PipelineResult:
    out_dir: Path
    json_path: Path
    data: LyricData
    lyrics_tier: str
    device_used: str
    meta: dict = field(default_factory=dict)


def run(
    source: str,
    out_dir: str | Path,
    *,
    options: PipelineOptions | None = None,
    progress: ProgressCallback | None = None,
    on_metadata: MetadataCallback | None = None,
    on_lyrics_review: LyricsReviewCallback | None = None,
) -> PipelineResult:
    """1 曲分のフル解析を実行する。

    Args:
        source: YouTube URL または ローカル音声ファイルのパス。
        out_dir: 解析結果の出力ディレクトリ。
        options: 解析オプション。
        progress: ``(stage, message)`` を受け取る進捗コールバック。
        on_metadata: 取得後に ``(title, artist)`` を受け取り上書き後の値を返す
            コールバック。CLI 対話モードでの確認・修正に使う (既定 None=確認なし)。
        on_lyrics_review: 歌詞検索結果をレビューして採用/スキップ/再検索を決める
            コールバック (既定 None=最初の検索結果をそのまま採用)。
    """
    options = options or PipelineOptions()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def report(stage: str, message: str) -> None:
        logger.info("[%s] %s", stage, message)
        if progress:
            progress(stage, message)

    # ① 取得
    report("fetch", "音源を取得しています")
    fetched = _fetch(source, out_dir, options)
    title = options.title or fetched.title
    artist = options.artist or fetched.artist

    # ①' メタ情報の対話確認 (任意)
    # YouTube の title/artist は装飾やチャンネル名混入で歌詞検索を外しやすいため、
    # 重い分離処理に入る前にここで確認・修正できるようにする。
    if on_metadata is not None:
        title, artist = on_metadata(title, artist)

    # ② 歌詞取得 (対話レビューがあれば確認・再検索ループ)
    # 分離より先に解決することで、対話を前半に集約しユーザーが以降を放置できる。
    report("lyrics", "歌詞を取得しています")
    lines, tier, title, artist = _resolve_lyrics(
        title, artist, options, on_lyrics_review, report
    )
    report("lyrics", f"歌詞 Tier: {tier}")

    # ③ 分離 (MPS)
    vocals_path = None
    device_used = "none"
    enhance_models: list[str] = []
    if not options.skip_separation:
        report("separate", f"音源分離を実行しています (モデル: {options.separation_model})")
        sep = separate(
            fetched.wav_path,
            out_dir,
            model_name=options.separation_model,
            device=options.device,
        )
        vocals_path = sep.vocals_path
        device_used = sep.device_used
        report("separate", f"分離完了 (デバイス: {device_used})")

        # ③' ボーカル強化 (opt-in): ハモリ・残響を除去し歌唱区間推定を安定させる (#3)
        if options.enhance_vocals:
            report("enhance", "ボーカル強化 (ハモリ・残響除去) を実行しています")
            enhanced = enhance_vocals(vocals_path, out_dir)
            vocals_path = enhanced.vocals_path
            enhance_models = enhanced.models_used
            report("enhance", f"強化完了 (モデル: {', '.join(enhance_models)})")
    elif options.enhance_vocals:
        report("enhance", "skip_separation のためボーカル強化をスキップします")

    # ④ 楽曲地図
    report("music_map", "ビート・構造・コード・声量を解析しています")
    mm = music_map.analyze(fetched.wav_path, vocals_path)

    # ⑤ 整合 (モーラ按分)
    # 声量 (amplitude) ではなくオンセットゲート済みの歌唱活動度を渡す。
    # エコー・ハモリの余韻で歌唱区間が膨らむのを抑えるため (#3)
    report("align", "歌詞タイミングを按分しています")
    phrases = align(lines, fetched.duration_ms, mm.vocal_activity or mm.amplitude)

    # ⑥ 契約A JSON へ規格化
    report("save", "TextAlive 互換 JSON を書き出しています")
    data = LyricData(
        song=SongMeta(
            title=title,
            artist=artist,
            duration_ms=fetched.duration_ms,
            source=SongSource(type=fetched.source_type, id=fetched.source_id),
        ),
        phrases=phrases,
        beats=mm.beats,
        chords=mm.chords,
        segments=mm.segments,
        amplitude=mm.amplitude,
        valence_arousal=mm.valence_arousal,
    )
    json_path = out_dir / LYRIC_DATA_FILENAME
    data.save(json_path)

    meta = {
        "title": title,
        "artist": artist,
        "durationMs": fetched.duration_ms,
        "sourceType": fetched.source_type,
        "sourceId": fetched.source_id,
        "lyricsTier": tier,
        "deviceUsed": device_used,
        "tempoBpm": round(mm.tempo_bpm, 1),
        "separationModel": None if options.skip_separation else options.separation_model,
        "enhanceModels": enhance_models or None,
    }
    (out_dir / META_FILENAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    return PipelineResult(
        out_dir=out_dir,
        json_path=json_path,
        data=data,
        lyrics_tier=tier,
        device_used=device_used,
        meta=meta,
    )


def _fetch(source: str, out_dir: Path, options: PipelineOptions) -> FetchResult:
    if is_url(source):
        return fetch_youtube(source, out_dir)
    return import_file(source, out_dir, title=options.title, artist=options.artist)


def _resolve_lyrics(
    title: str,
    artist: str,
    options: PipelineOptions,
    on_review: LyricsReviewCallback | None,
    report: ProgressCallback,
) -> tuple[list[LyricLine], str, str, str]:
    """歌詞を解決し ``(行, tier, title, artist)`` を返す。

    ``on_review`` があり、かつユーザー供給歌詞でない場合は、検索結果を
    レビューさせ「再検索」なら title/artist を差し替えて検索し直す。
    再検索で title/artist が変わると曲メタにも反映するため、確定値も返す。
    """
    # ユーザー供給歌詞は検索しないのでレビューループ対象外
    if options.lyrics_text or on_review is None:
        lines, tier = _get_lyrics(title, artist, options)
        return lines, tier, title, artist

    while True:
        lines, tier = _get_lyrics(title, artist, options)
        decision = on_review(title, artist, lines, tier)
        if decision.action == "retry":
            title, artist = decision.title, decision.artist
            report("lyrics", f"歌詞を再検索しています ({title} / {artist})")
            continue
        if decision.action == "skip":
            return [], "T4", title, artist
        return lines, tier, title, artist


def _get_lyrics(
    title: str, artist: str, options: PipelineOptions
) -> tuple[list[LyricLine], str]:
    """歌詞行と Tier を決める。ユーザー供給テキストがあれば優先する。"""
    if options.lyrics_text:
        lines = parse_lrc(options.lyrics_text)
        if any(ln.start_ms is not None for ln in lines):
            # align() の経路選択 (is_word_synced) と同じ基準で Tier を判定する。
            # 一部の行のみ逐字タグを持つ LRC は align() 側で T2 経路になるため、
            # ここで T1 と報告すると meta.json と実際の整合が食い違う。
            tier = "T1" if is_word_synced(lines) else "T2"
        else:
            tier = "T3"
        return lines, tier

    lrc, tier = fetch_lyrics(title, artist, vocaloid=options.vocaloid)
    if lrc is None:
        return [], tier
    return parse_lrc(lrc), tier
