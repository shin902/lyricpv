"""Configuration as Data — 曲ごとの解析設定を song.toml で再現可能にする。

現状 (#4 以前) は曲ごとの調整が CLI フラグ (--refine-pad 600 等) の組み合わせ
としてしか存在せず、再解析のたびに打ち直しが必要で再現性がなかった。

本モジュールは ``PipelineOptions`` (pipeline.py) に対応する ``SongConfig`` を
song.toml として読み書きし、CLI フラグとの合成 (優先順位: CLI > song.toml >
既定値) を提供する。解析成功後に有効設定を out_dir/song.toml へ書き出せば、
次回から ``lyricpv analyze <out_dir>`` だけで同条件の再解析ができる。

song.toml スキーマ例::

    source = "https://www.youtube.com/watch?v=..."
    title = "曲名"
    artist = "アーティスト"
    vocaloid = true
    lyrics_file = "lyrics.lrc"

    [separation]
    model = "htdemucs_ft"
    device = "mps"
    skip = false

    [enhance]
    enabled = true
    karaoke_model = "..."   # "none" でその段をスキップ
    dereverb_model = "..."

    [refine]
    enabled = true
    pad_ms = 600
    min_match_ratio = 0.5
    min_char_score = 0.35
    max_squashed_mid_chars = 1
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .enhance import DEFAULT_DEREVERB_MODEL, DEFAULT_KARAOKE_MODEL
from .pipeline import PipelineOptions
from .refine import RefineParams
from .separate import DEFAULT_MODEL as DEFAULT_SEPARATION_MODEL

SONG_TOML_FILENAME = "song.toml"

_TOP_LEVEL_KEYS = {"source", "title", "artist", "vocaloid", "lyrics_file", "separation", "enhance", "refine"}
_VALID_DEVICES = {"cpu", "mps", "cuda"}
_SEPARATION_KEYS = {"model", "device", "skip"}
_ENHANCE_KEYS = {"enabled", "karaoke_model", "dereverb_model"}
_REFINE_KEYS = {"enabled", "model", "pad_ms", "min_match_ratio", "min_char_score", "max_squashed_mid_chars"}

# 「変更していない」ことの判定基準。RefineParams() の既定値をそのまま使う
_DEFAULT_REFINE = RefineParams()


class ConfigError(ValueError):
    """song.toml の読み込み・合成に失敗したときに送出される。"""


@dataclass
class SeparationConfig:
    model: str | None = None
    device: str | None = None
    skip: bool | None = None


@dataclass
class EnhanceConfig:
    enabled: bool | None = None
    karaoke_model: str | None = None  # "none" 文字列でその段をスキップ
    dereverb_model: str | None = None


@dataclass
class RefineConfig:
    enabled: bool | None = None
    model: str | None = None
    pad_ms: int | None = None
    min_match_ratio: float | None = None
    min_char_score: float | None = None
    max_squashed_mid_chars: int | None = None


@dataclass
class SongConfig:
    """song.toml の内容。全フィールド省略可 (None = 未指定)。"""

    source: str | None = None
    title: str | None = None
    artist: str | None = None
    vocaloid: bool | None = None
    lyrics_file: str | None = None  # song.toml のあるディレクトリからの相対パス
    separation: SeparationConfig = field(default_factory=SeparationConfig)
    enhance: EnhanceConfig = field(default_factory=EnhanceConfig)
    refine: RefineConfig = field(default_factory=RefineConfig)


def _validate_scalar(raw: dict[str, Any], expected: dict[str, type], section: str) -> None:
    """Validate that each non-None scalar value matches its expected TOML type.

    TOML bool is a strict subtype that must not be accepted where int/str is expected
    and vice-versa.  For ``float`` fields, ``int`` values are also accepted (application
    policy for ratio fields) and normalized to ``float`` so the runtime value matches
    the type annotation.
    """
    for key, value in raw.items():
        if value is None:
            continue
        exp = expected.get(key)
        if exp is None:
            continue  # unknown-key check is handled elsewhere
        if exp is bool:
            if not isinstance(value, bool):
                raise ConfigError(
                    f"[{section}] {key} はブール値で指定してください (現在: {type(value).__name__})"
                )
        elif exp is str:
            if not isinstance(value, str):
                raise ConfigError(
                    f"[{section}] {key} は文字列で指定してください (現在: {type(value).__name__})"
                )
        elif exp is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(
                    f"[{section}] {key} は整数で指定してください (現在: {type(value).__name__})"
                )
        elif exp is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(
                    f"[{section}] {key} は数値で指定してください (現在: {type(value).__name__})"
                )
            if isinstance(value, int):
                raw[key] = float(value)


# ── per-section type schemas for _validate_scalar ──────────────────────────
_TOP_LEVEL_SCHEMA: dict[str, type] = {
    "source": str,
    "title": str,
    "artist": str,
    "vocaloid": bool,
    "lyrics_file": str,
}
_SEPARATION_SCHEMA: dict[str, type] = {
    "model": str,
    "device": str,
    "skip": bool,
}
_ENHANCE_SCHEMA: dict[str, type] = {
    "enabled": bool,
    "karaoke_model": str,
    "dereverb_model": str,
}
_REFINE_SCHEMA: dict[str, type] = {
    "enabled": bool,
    "model": str,
    "pad_ms": int,
    "min_match_ratio": float,
    "min_char_score": float,
    "max_squashed_mid_chars": int,
}


def _check_unknown(raw: dict[str, Any], allowed: set[str], section: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(
            f"song.toml の [{section}] に未知のキーがあります: {', '.join(sorted(unknown))} "
            f"(有効なキー: {', '.join(sorted(allowed))})"
        )


def load_song_config(path: str | Path) -> SongConfig:
    """song.toml を読み込む。``path`` はディレクトリでもファイルでもよい。

    未知のキーはタイポの黙殺を防ぐため ConfigError で拒否する。
    """
    path = Path(path)
    if path.is_dir():
        path = path / SONG_TOML_FILENAME
    if not path.is_file():
        raise ConfigError(f"song.toml が見つかりません: {path}")

    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"song.toml の構文が不正です ({path}): {e}") from e

    _check_unknown(raw, _TOP_LEVEL_KEYS, "top-level")
    separation_raw = raw.get("separation", {})
    enhance_raw = raw.get("enhance", {})
    refine_raw = raw.get("refine", {})
    if not isinstance(separation_raw, dict):
        raise ConfigError("song.toml の separation はテーブルで指定してください")
    if not isinstance(enhance_raw, dict):
        raise ConfigError("song.toml の enhance はテーブルで指定してください")
    if not isinstance(refine_raw, dict):
        raise ConfigError("song.toml の refine はテーブルで指定してください")
    _check_unknown(separation_raw, _SEPARATION_KEYS, "separation")
    _check_unknown(enhance_raw, _ENHANCE_KEYS, "enhance")
    _check_unknown(refine_raw, _REFINE_KEYS, "refine")

    # Validate scalar types before constructing dataclasses
    _validate_scalar(raw, _TOP_LEVEL_SCHEMA, "top-level")
    _validate_scalar(separation_raw, _SEPARATION_SCHEMA, "separation")
    _validate_scalar(enhance_raw, _ENHANCE_SCHEMA, "enhance")
    _validate_scalar(refine_raw, _REFINE_SCHEMA, "refine")

    # Validate device value (type check above only ensures it's a string)
    device_val = separation_raw.get("device")
    if device_val is not None and device_val not in _VALID_DEVICES:
        raise ConfigError(
            f"[separation] device は {_VALID_DEVICES!r} のいずれかを指定してください (現在: {device_val!r})"
        )

    return SongConfig(
        source=raw.get("source"),
        title=raw.get("title"),
        artist=raw.get("artist"),
        vocaloid=raw.get("vocaloid"),
        lyrics_file=raw.get("lyrics_file"),
        separation=SeparationConfig(**separation_raw),
        enhance=EnhanceConfig(**enhance_raw),
        refine=RefineConfig(**refine_raw),
    )


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        _ESC = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t",
                "\n": "\\n", "\f": "\\f", "\r": "\\r"}
        escaped = "".join(
            _ESC[c] if c in _ESC
            else (f"\\u{ord(c):04x}" if ord(c) < 0x20 or ord(c) == 0x7F else c)
            for c in value
        )
        return f'"{escaped}"'
    raise ConfigError(f"TOML にシリアライズできない値です: {value!r}")


def save_song_config(path: str | Path, config: SongConfig) -> None:
    """有効設定を song.toml として書き出す。None のフィールドは省略する。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key in ("source", "title", "artist", "vocaloid", "lyrics_file"):
        value = getattr(config, key)
        if value is not None:
            lines.append(f"{key} = {_toml_scalar(value)}")

    for section_name, section in (
        ("separation", config.separation),
        ("enhance", config.enhance),
        ("refine", config.refine),
    ):
        set_fields = [
            (f.name, getattr(section, f.name))
            for f in fields(section)
            if getattr(section, f.name) is not None
        ]
        if not set_fields:
            continue
        lines.append("")
        lines.append(f"[{section_name}]")
        for name, value in set_fields:
            lines.append(f"{name} = {_toml_scalar(value)}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pick(base: Any, override: Any) -> Any:
    return override if override is not None else base


def merge_configs(base: SongConfig, override: SongConfig) -> SongConfig:
    """``override`` の非 None フィールドを優先して合成する (CLI > song.toml)。"""
    return SongConfig(
        source=_pick(base.source, override.source),
        title=_pick(base.title, override.title),
        artist=_pick(base.artist, override.artist),
        vocaloid=_pick(base.vocaloid, override.vocaloid),
        lyrics_file=_pick(base.lyrics_file, override.lyrics_file),
        separation=SeparationConfig(
            model=_pick(base.separation.model, override.separation.model),
            device=_pick(base.separation.device, override.separation.device),
            skip=_pick(base.separation.skip, override.separation.skip),
        ),
        enhance=EnhanceConfig(
            enabled=_pick(base.enhance.enabled, override.enhance.enabled),
            karaoke_model=_pick(base.enhance.karaoke_model, override.enhance.karaoke_model),
            dereverb_model=_pick(base.enhance.dereverb_model, override.enhance.dereverb_model),
        ),
        refine=RefineConfig(
            enabled=_pick(base.refine.enabled, override.refine.enabled),
            model=_pick(base.refine.model, override.refine.model),
            pad_ms=_pick(base.refine.pad_ms, override.refine.pad_ms),
            min_match_ratio=_pick(base.refine.min_match_ratio, override.refine.min_match_ratio),
            min_char_score=_pick(base.refine.min_char_score, override.refine.min_char_score),
            max_squashed_mid_chars=_pick(
                base.refine.max_squashed_mid_chars, override.refine.max_squashed_mid_chars
            ),
        ),
    )


def resolve_model_flag(value: str | None, default: str | None) -> str | None:
    """モデル指定を解決する。未指定 (None) なら既定値、'none' ならその段をスキップ。"""
    if value is None:
        return default
    if value.strip().lower() == "none":
        return None
    return value


def _unresolve_model_flag(value: str | None, default: str | None) -> str | None:
    """resolve_model_flag の逆変換。既定値と同じなら「未指定」として省略する。"""
    if value == default:
        return None
    if value is None:
        return "none"
    return value


def to_pipeline_options(
    config: SongConfig,
    *,
    base_dir: str | Path,
    lyrics_text: str | None = None,
) -> PipelineOptions:
    """合成済み ``SongConfig`` から ``PipelineOptions`` を組み立てる。

    ``lyrics_text`` が与えられればそれを最優先する (CLI の --lyrics で読み込んだ
    実テキスト)。無ければ ``config.lyrics_file`` を ``base_dir`` 基準で解決して読む。
    """
    text = lyrics_text
    if text is None and config.lyrics_file:
        lyrics_path = Path(base_dir) / config.lyrics_file
        if not lyrics_path.is_file():
            raise ConfigError(f"lyrics_file が見つかりません: {lyrics_path}")
        text = lyrics_path.read_text(encoding="utf-8")

    refine_kwargs = {
        k: v
        for k, v in {
            "model_name": config.refine.model,
            "pad_ms": config.refine.pad_ms,
            "min_match_ratio": config.refine.min_match_ratio,
            "min_char_score": config.refine.min_char_score,
            "max_squashed_mid_chars": config.refine.max_squashed_mid_chars,
        }.items()
        if v is not None
    }
    try:
        refine_params = RefineParams(**refine_kwargs)
    except ValueError as e:
        raise ConfigError(str(e)) from e

    return PipelineOptions(
        title=config.title,
        artist=config.artist,
        lyrics_text=text,
        vocaloid=bool(config.vocaloid),
        separation_model=config.separation.model or DEFAULT_SEPARATION_MODEL,
        device=config.separation.device,
        skip_separation=bool(config.separation.skip),
        enhance_vocals=bool(config.enhance.enabled),
        enhance_karaoke_model=resolve_model_flag(config.enhance.karaoke_model, DEFAULT_KARAOKE_MODEL),
        enhance_dereverb_model=resolve_model_flag(config.enhance.dereverb_model, DEFAULT_DEREVERB_MODEL),
        refine_align=bool(config.refine.enabled),
        refine_params=refine_params,
    )


def effective_config_from_options(
    options: PipelineOptions,
    *,
    source: str,
    lyrics_file: str | None,
    title: str | None = None,
    artist: str | None = None,
) -> SongConfig:
    """解析に実際に使った ``PipelineOptions`` から、既定値でない値だけを
    含む ``SongConfig`` を組み立てる (song.toml への書き出し用)。

    ``title`` / ``artist`` が与えられた場合は ``options`` の値を上書きする。
    対話確認や歌詞検索の再試行でメタ情報が変更された場合、pipeline 側で
    最終的な値を渡すことで song.toml に正しい値が残る。
    """
    rp = options.refine_params
    return SongConfig(
        source=source,
        title=title if title is not None else options.title,
        artist=artist if artist is not None else options.artist,
        vocaloid=options.vocaloid or None,
        lyrics_file=lyrics_file,
        separation=SeparationConfig(
            model=options.separation_model if options.separation_model != DEFAULT_SEPARATION_MODEL else None,
            device=options.device,
            skip=options.skip_separation or None,
        ),
        enhance=EnhanceConfig(
            enabled=options.enhance_vocals or None,
            karaoke_model=_unresolve_model_flag(options.enhance_karaoke_model, DEFAULT_KARAOKE_MODEL),
            dereverb_model=_unresolve_model_flag(options.enhance_dereverb_model, DEFAULT_DEREVERB_MODEL),
        ),
        refine=RefineConfig(
            enabled=options.refine_align or None,
            model=rp.model_name if rp.model_name != _DEFAULT_REFINE.model_name else None,
            pad_ms=rp.pad_ms if rp.pad_ms != _DEFAULT_REFINE.pad_ms else None,
            min_match_ratio=(
                rp.min_match_ratio if rp.min_match_ratio != _DEFAULT_REFINE.min_match_ratio else None
            ),
            min_char_score=(
                rp.min_char_score if rp.min_char_score != _DEFAULT_REFINE.min_char_score else None
            ),
            max_squashed_mid_chars=(
                rp.max_squashed_mid_chars
                if rp.max_squashed_mid_chars != _DEFAULT_REFINE.max_squashed_mid_chars
                else None
            ),
        ),
    )
