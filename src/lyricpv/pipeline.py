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
from typing import Callable

from . import music_map
from .fetch import FetchResult, fetch_youtube, import_file
from .lyrics.align import align
from .lyrics.fetch import fetch_lyrics
from .lyrics.lrc import LyricLine, parse_lrc
from .schema import LyricData, SongMeta, SongSource
from .separate import separate

logger = logging.getLogger(__name__)

LYRIC_DATA_FILENAME = "lyric_data.json"
META_FILENAME = "meta.json"

ProgressCallback = Callable[[str, str], None]

_STAGES = ("fetch", "separate", "music_map", "lyrics", "align", "save")


@dataclass
class PipelineOptions:
    title: str | None = None  # 自動取得値の上書き
    artist: str | None = None
    lyrics_text: str | None = None  # ユーザー供給の歌詞 (LRC またはプレーン = T3)
    vocaloid: bool = False  # 歌詞検索で NetEase を優先する
    separation_model: str = "htdemucs"
    device: str | None = None  # None = 自動 (MPS 優先)
    skip_separation: bool = False  # テスト・高速試行用


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
) -> PipelineResult:
    """1 曲分のフル解析を実行する。

    Args:
        source: YouTube URL または ローカル音声ファイルのパス。
        out_dir: 解析結果の出力ディレクトリ。
        options: 解析オプション。
        progress: ``(stage, message)`` を受け取る進捗コールバック。
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

    # ② 分離 (MPS)
    vocals_path = None
    device_used = "none"
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

    # ③ 楽曲地図
    report("music_map", "ビート・構造・コード・声量を解析しています")
    mm = music_map.analyze(fetched.wav_path, vocals_path)

    # ④ 歌詞取得
    report("lyrics", "歌詞を取得しています")
    lines, tier = _get_lyrics(title, artist, options)
    report("lyrics", f"歌詞 Tier: {tier}")

    # ⑤ 整合 (モーラ按分)
    report("align", "歌詞タイミングを按分しています")
    phrases = align(lines, fetched.duration_ms, mm.amplitude)

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
    if source.startswith(("http://", "https://")):
        return fetch_youtube(source, out_dir)
    return import_file(source, out_dir, title=options.title, artist=options.artist)


def _get_lyrics(
    title: str, artist: str, options: PipelineOptions
) -> tuple[list[LyricLine], str]:
    """歌詞行と Tier を決める。ユーザー供給テキストがあれば優先する。"""
    if options.lyrics_text:
        lines = parse_lrc(options.lyrics_text)
        if any(ln.start_ms is not None for ln in lines):
            tier = "T1" if any(ln.words for ln in lines) else "T2"
        else:
            tier = "T3"
        return lines, tier

    lrc, tier = fetch_lyrics(title, artist, vocaloid=options.vocaloid)
    if lrc is None:
        return [], tier
    return parse_lrc(lrc), tier
