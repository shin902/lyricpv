"""song.toml (Configuration as Data) の読み書き・合成ロジックのテスト。"""

import pytest

from lyricpv.config import (
    ConfigError,
    EnhanceConfig,
    RefineConfig,
    SeparationConfig,
    SongConfig,
    effective_config_from_options,
    load_song_config,
    merge_configs,
    save_song_config,
    to_pipeline_options,
)
from lyricpv.pipeline import PipelineOptions
from lyricpv.refine import RefineParams


def test_load_song_config_reads_all_sections(tmp_path):
    (tmp_path / "song.toml").write_text(
        """
source = "https://www.youtube.com/watch?v=abc"
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
karaoke_model = "kmodel.ckpt"
dereverb_model = "none"

[refine]
enabled = true
pad_ms = 600
min_match_ratio = 0.4
min_char_score = 0.5
max_squashed_mid_chars = 2
""",
        encoding="utf-8",
    )

    config = load_song_config(tmp_path)

    assert config.source == "https://www.youtube.com/watch?v=abc"
    assert config.title == "曲名"
    assert config.artist == "アーティスト"
    assert config.vocaloid is True
    assert config.lyrics_file == "lyrics.lrc"
    assert config.separation == SeparationConfig(model="htdemucs_ft", device="mps", skip=False)
    assert config.enhance == EnhanceConfig(
        enabled=True, karaoke_model="kmodel.ckpt", dereverb_model="none"
    )
    assert config.refine == RefineConfig(
        enabled=True, pad_ms=600, min_match_ratio=0.4, min_char_score=0.5, max_squashed_mid_chars=2
    )


def test_load_song_config_accepts_path_to_file_directly(tmp_path):
    path = tmp_path / "song.toml"
    path.write_text('source = "song.wav"\n', encoding="utf-8")

    config = load_song_config(path)

    assert config.source == "song.wav"


def test_load_song_config_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_song_config(tmp_path)


def test_load_song_config_rejects_unknown_top_level_key(tmp_path):
    (tmp_path / "song.toml").write_text('sorce = "typo.wav"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="sorce"):
        load_song_config(tmp_path)


def test_load_song_config_rejects_unknown_section_key(tmp_path):
    (tmp_path / "song.toml").write_text(
        """
[refine]
pad_m = 600
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="pad_m"):
        load_song_config(tmp_path)


def test_load_song_config_rejects_string_for_bool_field(tmp_path):
    """skip = \"false\" should raise ConfigError, not silently become truthy."""
    (tmp_path / "song.toml").write_text(
        """
[separation]
skip = "false"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="skip"):
        load_song_config(tmp_path)


def test_load_song_config_rejects_string_for_int_field(tmp_path):
    """pad_ms = \"600\" should raise ConfigError."""
    (tmp_path / "song.toml").write_text(
        """
[refine]
pad_ms = "600"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="pad_ms"):
        load_song_config(tmp_path)


def test_load_song_config_rejects_bool_for_int_field(tmp_path):
    """pad_ms = true should raise ConfigError (bool is not int in TOML)."""
    (tmp_path / "song.toml").write_text(
        """
[refine]
pad_ms = true
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="pad_ms"):
        load_song_config(tmp_path)


def test_load_song_config_rejects_bool_for_str_field(tmp_path):
    """model = true should raise ConfigError."""
    (tmp_path / "song.toml").write_text(
        """
[separation]
model = true
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="model"):
        load_song_config(tmp_path)


def test_load_song_config_rejects_int_for_bool_field(tmp_path):
    """vocaloid = 1 should raise ConfigError."""
    (tmp_path / "song.toml").write_text(
        """
vocaloid = 1
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="vocaloid"):
        load_song_config(tmp_path)


def test_load_song_config_rejects_float_for_int_field(tmp_path):
    """pad_ms = 600.0 should raise ConfigError."""
    (tmp_path / "song.toml").write_text(
        """
[refine]
pad_ms = 600.0
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="pad_ms"):
        load_song_config(tmp_path)


def test_load_song_config_accepts_int_for_float_field(tmp_path):
    """min_match_ratio = 1 should be accepted (int is valid for float field)."""
    (tmp_path / "song.toml").write_text(
        """
[refine]
min_match_ratio = 1
""",
        encoding="utf-8",
    )
    config = load_song_config(tmp_path)
    assert config.refine.min_match_ratio == 1.0  # normalized to float


def test_load_song_config_rejects_string_for_float_field(tmp_path):
    """min_char_score = \"0.5\" should raise ConfigError."""
    (tmp_path / "song.toml").write_text(
        """
[refine]
min_char_score = "0.5"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="min_char_score"):
        load_song_config(tmp_path)


def test_save_then_load_round_trip(tmp_path):
    config = SongConfig(
        source="song.wav",
        title="曲名",
        vocaloid=True,
        lyrics_file="lyrics.lrc",
        separation=SeparationConfig(model="htdemucs_ft"),
        enhance=EnhanceConfig(enabled=True, dereverb_model="none"),
        refine=RefineConfig(enabled=True, pad_ms=600, max_squashed_mid_chars=2),
    )

    save_song_config(tmp_path / "song.toml", config)
    loaded = load_song_config(tmp_path)

    assert loaded == config


def test_save_song_config_omits_none_fields(tmp_path):
    config = SongConfig(source="song.wav")
    save_song_config(tmp_path / "song.toml", config)

    text = (tmp_path / "song.toml").read_text(encoding="utf-8")

    assert "title" not in text
    assert "[separation]" not in text
    assert "[enhance]" not in text
    assert "[refine]" not in text


def test_merge_configs_cli_overrides_toml():
    base = SongConfig(
        title="toml タイトル",
        separation=SeparationConfig(model="htdemucs_ft"),
        refine=RefineConfig(enabled=True, pad_ms=400),
    )
    override = SongConfig(
        separation=SeparationConfig(model="htdemucs"),
        refine=RefineConfig(pad_ms=600),
    )

    merged = merge_configs(base, override)

    # CLI (override) が指定した値が勝つ
    assert merged.separation.model == "htdemucs"
    assert merged.refine.pad_ms == 600
    # CLI が触れていない値は toml の値を維持する
    assert merged.title == "toml タイトル"
    assert merged.refine.enabled is True


def test_to_pipeline_options_applies_defaults_when_unset():
    config = SongConfig()

    options = to_pipeline_options(config, base_dir="/tmp")

    assert options.separation_model == "htdemucs"
    assert options.vocaloid is False
    assert options.skip_separation is False
    assert options.refine_params == RefineParams()


def test_to_pipeline_options_resolves_lyrics_file_relative_to_base_dir(tmp_path):
    (tmp_path / "lyrics.lrc").write_text("[00:01.00] てすと\n", encoding="utf-8")
    config = SongConfig(lyrics_file="lyrics.lrc")

    options = to_pipeline_options(config, base_dir=tmp_path)

    assert options.lyrics_text == "[00:01.00] てすと\n"


def test_to_pipeline_options_cli_lyrics_text_overrides_lyrics_file(tmp_path):
    (tmp_path / "lyrics.lrc").write_text("[00:01.00] toml側\n", encoding="utf-8")
    config = SongConfig(lyrics_file="lyrics.lrc")

    options = to_pipeline_options(config, base_dir=tmp_path, lyrics_text="CLI側のテキスト")

    assert options.lyrics_text == "CLI側のテキスト"


def test_to_pipeline_options_missing_lyrics_file_raises(tmp_path):
    config = SongConfig(lyrics_file="missing.lrc")

    with pytest.raises(ConfigError):
        to_pipeline_options(config, base_dir=tmp_path)


def test_to_pipeline_options_rejects_invalid_refine_params():
    config = SongConfig(refine=RefineConfig(min_char_score=1.5))

    with pytest.raises(ConfigError):
        to_pipeline_options(config, base_dir="/tmp")


def test_effective_config_from_options_omits_defaults():
    options = PipelineOptions(skip_separation=True)

    config = effective_config_from_options(options, source="song.wav", lyrics_file=None)

    assert config.source == "song.wav"
    assert config.separation.model is None  # 既定値 htdemucs のまま
    assert config.separation.skip is True
    assert config.refine.enabled is None


def test_effective_config_from_options_round_trips_non_default_refine_params():
    options = PipelineOptions(
        refine_align=True,
        refine_params=RefineParams(pad_ms=600, max_squashed_mid_chars=2),
    )

    config = effective_config_from_options(options, source="song.wav", lyrics_file=None)

    assert config.refine.enabled is True
    assert config.refine.pad_ms == 600
    assert config.refine.max_squashed_mid_chars == 2
    assert config.refine.min_match_ratio is None  # 既定値のまま


def test_effective_config_from_options_title_artist_override():
    """対話確認や歌詞検索で変更された title/artist が options の値を上書きする。"""
    options = PipelineOptions(title="Original Title", artist="Original Artist")

    config = effective_config_from_options(
        options,
        source="song.wav",
        lyrics_file=None,
        title="Final Title",
        artist="Final Artist",
    )

    assert config.title == "Final Title"
    assert config.artist == "Final Artist"


def test_effective_config_from_options_title_artist_fallback():
    """title/artist 未指定の場合は options の値にフォールバックする。"""
    options = PipelineOptions(title="Original Title", artist="Original Artist")

    config = effective_config_from_options(options, source="song.wav", lyrics_file=None)

    assert config.title == "Original Title"
    assert config.artist == "Original Artist"
