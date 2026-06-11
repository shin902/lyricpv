"""パイプライン統合の E2E テスト (ネットワーク・GPU 不使用)。

ローカルの合成 WAV + ユーザー供給 LRC で、契約A JSON が生成・検証
できることを確認する。音源分離はモデルダウンロードを伴うため省略し、
分離込みの実行は tests/test_separate.py (環境変数ゲート) に分ける。
"""

import json

from lyricpv.pipeline import LyricsDecision, PipelineOptions, run
from lyricpv.schema import LyricData

LRC = """[00:01.00] 夜に駆ける
[00:04.00] 君の声が聞こえる
"""


def test_pipeline_with_local_file_and_lrc(synth_wav_path, tmp_path):
    out_dir = tmp_path / "out"
    events: list[tuple[str, str]] = []

    result = run(
        str(synth_wav_path),
        out_dir,
        options=PipelineOptions(
            title="合成テスト曲",
            artist="lyricpv",
            lyrics_text=LRC,
            skip_separation=True,
        ),
        progress=lambda stage, msg: events.append((stage, msg)),
    )

    # 契約A JSON が生成され、再読込で検証を通る
    assert result.json_path.exists()
    data = LyricData.load(result.json_path)
    assert data.song.title == "合成テスト曲"
    assert data.song.duration_ms > 10_000

    # 歌詞は行 LRC (T2) としてフレーズ化されている
    assert result.lyrics_tier == "T2"
    assert len(data.phrases) == 2
    assert data.phrases[0].text == "夜に駆ける"
    assert data.phrases[0].words[0].chars

    # 楽曲地図も載っている
    assert data.beats
    assert data.chords
    assert data.amplitude

    # メタ情報と進捗コールバック
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["lyricsTier"] == "T2"
    stages = [s for s, _ in events]
    assert stages.index("fetch") < stages.index("music_map") < stages.index("save")


def test_partial_word_sync_reports_tier_t2(synth_wav_path, tmp_path):
    # 1 行目のみ逐字タグがあり 2 行目は行タイミングのみの LRC。
    # align() は is_word_synced (全行が逐字) でないため T2 経路を取るので、
    # meta.json / lyrics_tier も T1 ではなく T2 と整合させる
    lrc = (
        "[00:01.00] <00:01.00> 夜に <00:02.00> 駆ける\n"
        "[00:04.00] 君の声が聞こえる\n"
    )

    result = run(
        str(synth_wav_path),
        tmp_path / "out",
        options=PipelineOptions(lyrics_text=lrc, skip_separation=True),
    )

    assert result.lyrics_tier == "T2"
    meta = json.loads((result.out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["lyricsTier"] == "T2"


def test_pipeline_without_lyrics_yields_empty_phrases(synth_wav_path, tmp_path, monkeypatch):
    # 歌詞検索をネットワークに出さずに T4 を再現する
    import lyricpv.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "fetch_lyrics", lambda *a, **k: (None, "T4"))

    result = run(
        str(synth_wav_path),
        tmp_path / "out",
        options=PipelineOptions(skip_separation=True),
    )
    assert result.lyrics_tier == "T4"
    assert result.data.phrases == []
    assert result.data.beats  # 楽曲地図のみの JSON になる


def test_on_metadata_overrides_title_artist(synth_wav_path, tmp_path):
    # 取得後の確認コールバックで title/artist を差し替えられる
    def on_metadata(title, artist):
        return "Remember", "yuigot"

    result = run(
        str(synth_wav_path),
        tmp_path / "out",
        options=PipelineOptions(
            title="装飾だらけのタイトル", artist="チャンネル名", lyrics_text=LRC,
            skip_separation=True,
        ),
        on_metadata=on_metadata,
    )
    assert result.data.song.title == "Remember"
    assert result.data.song.artist == "yuigot"


def test_lyrics_review_retry_then_accept(synth_wav_path, tmp_path, monkeypatch):
    # 最初の title では歌詞が見つからず、再検索で title を直すとヒットする状況を再現
    import lyricpv.pipeline as pipeline_mod

    def fake_fetch(title, artist, *, vocaloid=False):
        if title == "Remember":
            return "[00:01.00] 見つかった歌詞\n", "T2"
        return None, "T4"

    monkeypatch.setattr(pipeline_mod, "fetch_lyrics", fake_fetch)

    seen = []

    def on_review(title, artist, lines, tier):
        seen.append((title, tier))
        if tier == "T4":
            return LyricsDecision("retry", "Remember", "yuigot")
        return LyricsDecision("accept", title, artist)

    result = run(
        str(synth_wav_path),
        tmp_path / "out",
        options=PipelineOptions(title="装飾タイトル", skip_separation=True),
        on_lyrics_review=on_review,
    )

    # 1 回目 T4 → 再検索 → 2 回目 T2 で採用
    assert [tier for _, tier in seen] == ["T4", "T2"]
    assert result.lyrics_tier == "T2"
    # 再検索で直した title/artist が曲メタに反映される
    assert result.data.song.title == "Remember"
    assert result.data.song.artist == "yuigot"
    assert result.data.phrases and result.data.phrases[0].text == "見つかった歌詞"


def test_lyrics_review_skip_yields_t4(synth_wav_path, tmp_path, monkeypatch):
    # レビューで「歌詞なしで続行」を選ぶと T4 (フレーズ空) になる
    import lyricpv.pipeline as pipeline_mod

    monkeypatch.setattr(
        pipeline_mod, "fetch_lyrics", lambda *a, **k: ("[00:01.00] 不要\n", "T2")
    )

    result = run(
        str(synth_wav_path),
        tmp_path / "out",
        options=PipelineOptions(skip_separation=True),
        on_lyrics_review=lambda *a: LyricsDecision("skip", a[0], a[1]),
    )
    assert result.lyrics_tier == "T4"
    assert result.data.phrases == []


def test_lyrics_review_skipped_for_user_supplied_lyrics(synth_wav_path, tmp_path):
    # ユーザー供給歌詞があるときはレビューコールバックを呼ばない
    def on_review(*args):
        raise AssertionError("ユーザー供給歌詞ではレビューを呼ばないこと")

    result = run(
        str(synth_wav_path),
        tmp_path / "out",
        options=PipelineOptions(lyrics_text=LRC, skip_separation=True),
        on_lyrics_review=on_review,
    )
    assert result.lyrics_tier == "T2"
