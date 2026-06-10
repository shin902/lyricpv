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
