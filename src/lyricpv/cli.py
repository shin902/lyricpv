"""コマンドラインインターフェース。

使い方:
    lyricpv analyze <YouTube URL または音声ファイル> [-o 出力先] [--lyrics 歌詞ファイル]
    lyricpv serve [--port 8000]   # WebUI (要: uv sync --extra webui)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lyricpv", description="文字PV生成 SDK のオフライン解析ツール")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="楽曲を解析して TextAlive 互換 JSON を生成する")
    p_analyze.add_argument(
        "source", nargs="?", default=None,
        help="YouTube URL または音声ファイルのパス (省略時は対話入力)",
    )
    p_analyze.add_argument(
        "-i", "--interactive", action="store_true",
        help="タイトル/アーティストの確認と歌詞検索結果のレビューを対話で行う",
    )
    p_analyze.add_argument("-o", "--out-dir", default=None, help="出力ディレクトリ (既定: data/songs/<名前>)")
    p_analyze.add_argument("--title", default=None, help="タイトルの上書き")
    p_analyze.add_argument("--artist", default=None, help="アーティスト名の上書き")
    p_analyze.add_argument("--lyrics", default=None, help="歌詞ファイル (LRC またはプレーンテキスト)")
    p_analyze.add_argument("--vocaloid", action="store_true", help="歌詞検索で NetEase を優先する")
    p_analyze.add_argument("--model", default="htdemucs", help="分離モデル (htdemucs / htdemucs_ft)")
    p_analyze.add_argument("--device", default=None, choices=["mps", "cuda", "cpu"], help="計算デバイス (既定: 自動)")
    p_analyze.add_argument("--skip-separation", action="store_true", help="音源分離を省略する (高速・低品質)")

    p_serve = sub.add_parser("serve", help="WebUI (解析フロントエンド) を起動する")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--data-dir", default=None, help="解析結果ディレクトリ (既定: data/songs)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "analyze":
        return _cmd_analyze(args)
    if args.command == "serve":
        return _cmd_serve(args)
    return 2


def _cmd_analyze(args: argparse.Namespace) -> int:
    from .fetch import FetchError, check_external_tools, extract_youtube_id, is_url
    from .pipeline import PipelineOptions, run
    from .separate import SeparationError

    missing = check_external_tools()
    if missing:
        print(f"必要な外部コマンドが見つかりません: {', '.join(missing)} (brew install ffmpeg)", file=sys.stderr)
        return 1

    # source 省略時は対話モードとみなす。
    interactive = args.interactive or args.source is None
    if interactive and not sys.stdin.isatty():
        print("対話モードには端末 (TTY) が必要です。--title/--artist/--lyrics で指定してください。", file=sys.stderr)
        return 1

    source = args.source
    if source is None:
        source = input("YouTube URL または音声ファイルのパス: ").strip()
        if not source:
            print("ソースが指定されていません。", file=sys.stderr)
            return 1

    lyrics_text = None
    if args.lyrics:
        lyrics_text = Path(args.lyrics).read_text(encoding="utf-8")

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        if is_url(source):
            # 動画 ID で曲ごとに分ける (固定名だと2曲目が1曲目を上書きする)
            stem = extract_youtube_id(source) or "youtube"
        else:
            stem = Path(source).stem
        out_dir = Path("data/songs") / stem

    options = PipelineOptions(
        title=args.title,
        artist=args.artist,
        lyrics_text=lyrics_text,
        vocaloid=args.vocaloid,
        separation_model=args.model,
        device=args.device,
        skip_separation=args.skip_separation,
    )

    def progress(stage: str, message: str) -> None:
        print(f"  [{stage}] {message}", file=sys.stderr)

    # 対話コールバック (対話モードかつユーザー供給歌詞でないときだけ有効化)
    on_metadata = _make_metadata_callback() if interactive else None
    on_lyrics_review = (
        _make_lyrics_review_callback() if interactive and lyrics_text is None else None
    )

    try:
        result = run(
            source,
            out_dir,
            options=options,
            progress=progress,
            on_metadata=on_metadata,
            on_lyrics_review=on_lyrics_review,
        )
    except (FetchError, SeparationError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    print(f"完了: {result.json_path} (歌詞 Tier: {result.lyrics_tier}, デバイス: {result.device_used})")
    return 0


def _prompt(label: str, default: str | None = None) -> str:
    """1 行の対話入力。Enter で default を採用する。"""
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{label}{suffix}: ").strip()
    except EOFError:
        return default or ""
    return raw or (default or "")


def _make_metadata_callback():
    """取得後に title/artist を確認・修正させるコールバックを返す。"""

    def on_metadata(title: str, artist: str) -> tuple[str, str]:
        print("検出したメタ情報（Enter で採用 / 入力で上書き）:")
        new_title = _prompt("  タイトル", title)
        new_artist = _prompt("  アーティスト", artist)
        return new_title, new_artist

    return on_metadata


def _make_lyrics_review_callback():
    """歌詞検索結果の冒頭を見せて 採用/スキップ/再検索 を選ばせるコールバックを返す。"""
    from .pipeline import LyricsDecision

    def on_lyrics_review(title, artist, lines, tier) -> "LyricsDecision":
        preview = [ln.text for ln in lines[:4] if ln.text]
        if preview:
            print(f"  歌詞が見つかりました (Tier: {tier})")
            print("--- 歌詞の冒頭 ---")
            for text in preview:
                print(f"  {text}")
            print("------------------")
            ans = input("この歌詞でよい？ [Y/n=歌詞なしで続行/r=再検索]: ").strip().lower()
        else:
            print(f"  歌詞が見つかりませんでした (Tier: {tier})")
            ans = input("歌詞なしで続行？ [Y/r=再検索]: ").strip().lower()

        if ans in ("r", "retry", "再検索"):
            print("  再検索する title/artist を入力してください:")
            new_title = _prompt("  タイトル", title)
            new_artist = _prompt("  アーティスト", artist)
            return LyricsDecision("retry", new_title, new_artist)
        if ans in ("n", "no") and preview:
            return LyricsDecision("skip", title, artist)
        return LyricsDecision("accept", title, artist)

    return on_lyrics_review


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("WebUI には extra が必要です: uv sync --extra webui", file=sys.stderr)
        return 1

    if args.data_dir:
        import os

        os.environ["LYRICPV_DATA_DIR"] = args.data_dir
    uvicorn.run("lyricpv.webui.app:create_app", host=args.host, port=args.port, factory=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
