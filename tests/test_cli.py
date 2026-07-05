"""CLI のエラーハンドリングのテスト。"""

from pathlib import Path

import lyricpv.cli as cli_mod
import lyricpv.fetch as fetch_mod
import lyricpv.pipeline as pipeline_mod
from lyricpv.config import load_song_config
from lyricpv.fetch import FetchError
from lyricpv.separate import SeparationError


def test_analyze_reports_fetch_error_without_traceback(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    def fake_run(*args, **kwargs):
        raise FetchError("ダウンロードに失敗しました")

    monkeypatch.setattr(pipeline_mod, "run", fake_run)

    code = cli_mod.main(["analyze", "song.wav", "-o", str(tmp_path / "out")])

    assert code == 1
    captured = capsys.readouterr()
    assert "ダウンロードに失敗しました" in captured.err
    assert "Traceback" not in captured.err


def test_analyze_reports_separation_error_without_traceback(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    def fake_run(*args, **kwargs):
        raise SeparationError("音源分離に失敗しました")

    monkeypatch.setattr(pipeline_mod, "run", fake_run)

    code = cli_mod.main(["analyze", "song.wav", "-o", str(tmp_path / "out")])

    assert code == 1
    captured = capsys.readouterr()
    assert "音源分離に失敗しました" in captured.err
    assert "Traceback" not in captured.err


def test_analyze_passes_tuning_flags_to_pipeline(monkeypatch, tmp_path):
    """--refine-* / --karaoke-model / --dereverb-model が PipelineOptions に届く。"""
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])
    captured = {}

    def fake_run(source, out_dir, *, options, **kwargs):
        captured["options"] = options
        return _DummyResult()

    monkeypatch.setattr(pipeline_mod, "run", fake_run)

    code = cli_mod.main(
        [
            "analyze",
            "song.wav",
            "-o",
            str(tmp_path / "out"),
            "--refine-align",
            "--refine-model",
            "my/align-model",
            "--refine-pad",
            "600",
            "--refine-min-match",
            "0.4",
            "--refine-min-score",
            "0.5",
            "--refine-max-squashed",
            "2",
            "--enhance-vocals",
            "--karaoke-model",
            "my_karaoke.ckpt",
            "--dereverb-model",
            "none",
        ]
    )

    assert code == 0
    opt = captured["options"]
    rp = opt.refine_params
    assert rp.model_name == "my/align-model"
    assert rp.pad_ms == 600
    assert rp.min_match_ratio == 0.4
    assert rp.min_char_score == 0.5
    assert rp.max_squashed_mid_chars == 2
    assert opt.enhance_karaoke_model == "my_karaoke.ckpt"
    assert opt.enhance_dereverb_model is None  # 'none' で段をスキップ


def test_analyze_rejects_invalid_refine_params(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    code = cli_mod.main(
        [
            "analyze",
            "song.wav",
            "-o",
            str(tmp_path / "out"),
            "--refine-min-score",
            "1.5",
        ]
    )

    assert code == 1
    captured = capsys.readouterr()
    assert "min_char_score" in captured.err
    assert "Traceback" not in captured.err


def test_analyze_writes_song_toml_after_success(monkeypatch, tmp_path):
    """解析成功後、有効設定が out_dir/song.toml に書き出される。"""
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    def fake_run(source, out_dir, *, options, **kwargs):
        return _DummyResult()

    monkeypatch.setattr(pipeline_mod, "run", fake_run)

    out_dir = tmp_path / "out"
    code = cli_mod.main(
        [
            "analyze",
            "song.wav",
            "-o",
            str(out_dir),
            "--refine-align",
            "--refine-pad",
            "600",
        ]
    )

    assert code == 0
    saved = load_song_config(out_dir)
    # ローカル source は絶対パスの文字列に正規化されて保存される
    assert saved.source is not None
    assert isinstance(saved.source, str)
    assert Path(saved.source).is_absolute()
    assert Path(saved.source).name == "song.wav"
    # pipeline で確定した title/artist が song.toml に残る
    assert saved.title == "Test Title"
    assert saved.artist == "Test Artist"
    assert saved.refine.enabled is True
    assert saved.refine.pad_ms == 600
    # 触れていないパラメータは既定値のため書き出されない
    assert saved.refine.min_match_ratio is None


def test_analyze_saves_finalized_metadata_not_options(monkeypatch, tmp_path):
    """対話確認で title/artist が変更された場合、pipeline から返った確定値が
    song.toml に残る (options の pre-run 値ではない)。
    """
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    def fake_run(source, out_dir, *, options, **kwargs):
        # options には pre-run 値が残っているが、pipeline は修正後の値を返す
        assert options.title == "CLI Title"  # options は pre-run 値
        return _DummyResult()

    monkeypatch.setattr(pipeline_mod, "run", fake_run)

    out_dir = tmp_path / "out"
    code = cli_mod.main(
        [
            "analyze",
            "song.wav",
            "-o",
            str(out_dir),
            "--title",
            "CLI Title",
            "--artist",
            "CLI Artist",
        ]
    )

    assert code == 0
    saved = load_song_config(out_dir)
    # options の値ではなく、pipeline result の確定値が記録される
    assert saved.title == "Test Title"
    assert saved.artist == "Test Artist"


def test_analyze_reanalyzes_from_directory_with_song_toml(monkeypatch, tmp_path):
    """song.toml を含むディレクトリを source に指定すると、そこから再解析する。"""
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    out_dir = tmp_path / "mysong"
    out_dir.mkdir()
    (out_dir / "song.toml").write_text(
        """
source = "song.wav"

[refine]
enabled = true
pad_ms = 600
""",
        encoding="utf-8",
    )

    captured = {}

    def fake_run(source, out_dir_arg, *, options, **kwargs):
        captured["source"] = source
        captured["out_dir"] = out_dir_arg
        captured["options"] = options
        return _DummyResult()

    monkeypatch.setattr(pipeline_mod, "run", fake_run)

    code = cli_mod.main(["analyze", str(out_dir)])

    assert code == 0
    assert captured["source"] == "song.wav"
    assert captured["out_dir"] == out_dir
    assert captured["options"].refine_align is True
    assert captured["options"].refine_params.pad_ms == 600


def test_analyze_reanalyze_with_out_dir_copies_lyrics_file(monkeypatch, tmp_path):
    """song.toml から再解析しつつ -o で別の出力先を指定した場合も、歌詞ファイルを
    出力先へコピーして再現性を保つ (次回 out_dir から再解析できるようにする)。"""
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    mysong = tmp_path / "mysong"
    mysong.mkdir()
    lyrics_content = "[00:01.00]てすと歌詞\n"
    (mysong / "lyrics.lrc").write_text(lyrics_content, encoding="utf-8")
    (mysong / "song.toml").write_text(
        """
source = "song.wav"
lyrics_file = "lyrics.lrc"
""",
        encoding="utf-8",
    )

    other_dir = tmp_path / "other_dir"

    def fake_run(source, out_dir_arg, *, options, **kwargs):
        return _DummyResult()

    monkeypatch.setattr(pipeline_mod, "run", fake_run)

    code = cli_mod.main(["analyze", str(mysong), "-o", str(other_dir)])

    assert code == 0
    copied = other_dir / "lyrics.lrc"
    assert copied.is_file()
    assert copied.read_text(encoding="utf-8") == lyrics_content
    assert load_song_config(other_dir).lyrics_file == "lyrics.lrc"


def test_analyze_with_lyrics_option_preserves_original_extension(monkeypatch, tmp_path):
    """--lyrics で渡した外部歌詞ファイルは、拡張子を保ったまま out_dir にコピーされる
    (プレーンテキスト歌詞を lyrics.lrc という誤った名前で保存しないため)。"""
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    lyrics_content = "てすと歌詞\n"
    lyrics_path = tmp_path / "lyrics.txt"
    lyrics_path.write_text(lyrics_content, encoding="utf-8")

    def fake_run(source, out_dir_arg, *, options, **kwargs):
        out_dir_arg.mkdir(parents=True, exist_ok=True)
        return _DummyResult()

    monkeypatch.setattr(pipeline_mod, "run", fake_run)

    out_dir = tmp_path / "out"
    code = cli_mod.main(
        ["analyze", "song.wav", "-o", str(out_dir), "--lyrics", str(lyrics_path)]
    )

    assert code == 0
    copied = out_dir / "lyrics.txt"
    assert copied.is_file()
    assert copied.read_text(encoding="utf-8") == lyrics_content
    assert load_song_config(out_dir).lyrics_file == "lyrics.txt"


def test_analyze_directory_without_source_in_toml_errors(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    out_dir = tmp_path / "mysong"
    out_dir.mkdir()
    (out_dir / "song.toml").write_text('title = "曲名"\n', encoding="utf-8")

    code = cli_mod.main(["analyze", str(out_dir)])

    assert code == 1
    assert "source" in capsys.readouterr().err


def test_analyze_directory_without_song_toml_errors(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    empty_dir = tmp_path / "mysong"
    empty_dir.mkdir()

    code = cli_mod.main(["analyze", str(empty_dir)])

    assert code == 1
    assert "song.toml" in capsys.readouterr().err


def test_analyze_cli_flag_overrides_song_toml(monkeypatch, tmp_path):
    """ディレクトリ再解析でも CLI フラグが song.toml の値より優先される。"""
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])

    out_dir = tmp_path / "mysong"
    out_dir.mkdir()
    (out_dir / "song.toml").write_text(
        """
source = "song.wav"

[refine]
enabled = true
pad_ms = 400
""",
        encoding="utf-8",
    )

    captured = {}

    def fake_run(source, out_dir_arg, *, options, **kwargs):
        captured["options"] = options
        return _DummyResult()

    monkeypatch.setattr(pipeline_mod, "run", fake_run)

    code = cli_mod.main(["analyze", str(out_dir), "--refine-pad", "900"])

    assert code == 0
    assert captured["options"].refine_params.pad_ms == 900


def _feed_input(monkeypatch, answers):
    """input() に answers を順に返させる。"""
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))


def test_interactive_prompts_for_source_and_wires_callbacks(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    _feed_input(monkeypatch, [str(tmp_path / "song.wav")])

    captured_kwargs = {}

    def fake_run(source, out_dir, **kwargs):
        captured_kwargs["source"] = source
        captured_kwargs.update(kwargs)
        return _DummyResult()

    monkeypatch.setattr(pipeline_mod, "run", fake_run)

    # source 省略 → 対話入力 + コールバックが有効化される
    code = cli_mod.main(["analyze"])

    assert code == 0
    assert captured_kwargs["source"] == str(tmp_path / "song.wav")
    assert captured_kwargs["on_metadata"] is not None
    assert captured_kwargs["on_lyrics_review"] is not None


def test_interactive_without_tty_errors(monkeypatch, capsys):
    monkeypatch.setattr(fetch_mod, "check_external_tools", lambda: [])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)

    code = cli_mod.main(["analyze", "-i", "song.wav"])

    assert code == 1
    assert "端末" in capsys.readouterr().err


def test_lyrics_review_callback_decisions(monkeypatch):
    on_review = cli_mod._make_lyrics_review_callback()
    lines = [_DummyLine("一行目"), _DummyLine("二行目")]

    # Enter → 採用
    _feed_input(monkeypatch, [""])
    assert on_review("t", "a", lines, "T2").action == "accept"

    # n → 歌詞なしで続行 (skip)
    _feed_input(monkeypatch, ["n"])
    assert on_review("t", "a", lines, "T2").action == "skip"

    # r → 再検索 (新しい title/artist を入力)
    _feed_input(monkeypatch, ["r", "Remember", "yuigot"])
    decision = on_review("t", "a", lines, "T2")
    assert decision.action == "retry"
    assert decision.title == "Remember"
    assert decision.artist == "yuigot"


class _DummyResult:
    json_path = "out/lyric_data.json"
    lyrics_tier = "T2"
    device_used = "cpu"
    meta = {"title": "Test Title", "artist": "Test Artist"}


class _DummyLine:
    def __init__(self, text):
        self.text = text
