"""契約A スキーマの検証・入出力のテスト。"""

import pytest

from lyricpv.schema import (
    AmplitudePoint,
    Beat,
    Char,
    LyricData,
    Phrase,
    SchemaError,
    SongMeta,
    SongSource,
    Word,
    from_dict,
    validate,
)


def _sample() -> LyricData:
    return LyricData(
        song=SongMeta(
            title="テスト曲",
            artist="テスト歌手",
            duration_ms=180_000,
            source=SongSource(type="youtube", id="abc123xyz_-", offset_ms=120),
        ),
        phrases=[
            Phrase(
                start_time=1000,
                end_time=3000,
                text="こんにちは",
                words=[
                    Word(
                        start_time=1000,
                        end_time=3000,
                        text="こんにちは",
                        pos="感動詞",
                        chars=[
                            Char(start_time=1000 + i * 400, end_time=1400 + i * 400, char=c)
                            for i, c in enumerate("こんにちは")
                        ],
                    )
                ],
            )
        ],
        beats=[Beat(start_time=0, position=1), Beat(start_time=500, position=2)],
        amplitude=[AmplitudePoint(time=0, value=0.0), AmplitudePoint(time=50, value=0.5)],
    )


def test_round_trip(tmp_path):
    data = _sample()
    path = tmp_path / "lyric_data.json"
    data.save(path)
    loaded = LyricData.load(path)
    assert loaded.song.title == "テスト曲"
    assert loaded.song.source.offset_ms == 120
    assert loaded.phrases[0].words[0].chars[2].char == "に"
    assert loaded.beats[1].position == 2
    assert loaded.amplitude[1].value == 0.5


def test_to_dict_uses_textalive_keys():
    d = _sample().to_dict()
    assert "durationMs" in d["song"]
    assert "offsetMs" in d["song"]["source"]
    assert "startTime" in d["phrases"][0]
    assert "valenceArousal" in d


def test_validate_rejects_missing_song():
    with pytest.raises(SchemaError):
        validate({"phrases": []})


def test_validate_rejects_reversed_times():
    d = _sample().to_dict()
    d["phrases"][0]["endTime"] = 0
    with pytest.raises(SchemaError):
        validate(d)


def test_validate_rejects_unsorted_beats():
    d = _sample().to_dict()
    d["beats"] = [{"startTime": 500, "position": 1}, {"startTime": 0, "position": 2}]
    with pytest.raises(SchemaError):
        validate(d)


def test_from_dict_rejects_zero_duration():
    d = _sample().to_dict()
    d["song"]["durationMs"] = 0
    with pytest.raises(SchemaError):
        from_dict(d)
