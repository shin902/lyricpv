"""CLI のエラーハンドリングのテスト。"""

import lyricpv.cli as cli_mod
import lyricpv.fetch as fetch_mod
import lyricpv.pipeline as pipeline_mod
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

    code = cli_mod.main([
        "analyze", "song.wav", "-o", str(tmp_path / "out"),
        "--refine-align", "--refine-model", "my/align-model",
        "--refine-pad", "600", "--refine-min-match", "0.4",
        "--refine-min-score", "0.5", "--refine-max-squashed", "2",
        "--enhance-vocals", "--karaoke-model", "my_karaoke.ckpt",
        "--dereverb-model", "none",
    ])

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

    code = cli_mod.main([
        "analyze", "song.wav", "-o", str(tmp_path / "out"), "--refine-min-score", "1.5",
    ])

    assert code == 1
    captured = capsys.readouterr()
    assert "min_char_score" in captured.err
    assert "Traceback" not in captured.err


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


class _DummyLine:
    def __init__(self, text):
        self.text = text
