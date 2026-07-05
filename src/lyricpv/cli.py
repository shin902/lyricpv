"""コマンドラインインターフェース。

使い方:
    lyricpv analyze <YouTube URL または音声ファイル> [-o 出力先] [--lyrics 歌詞ファイル]
    lyricpv serve [--port 8000]   # WebUI (要: uv sync --extra webui)
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lyricpv", description="文字PV生成 SDK のオフライン解析ツール"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="楽曲を解析して TextAlive 互換 JSON を生成する")
    p_analyze.add_argument(
        "source",
        nargs="?",
        default=None,
        help=(
            "YouTube URL / 音声ファイルのパス / song.toml を含む出力ディレクトリ "
            "(省略時は対話入力)"
        ),
    )
    p_analyze.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="タイトル/アーティストの確認と歌詞検索結果のレビューを対話で行う",
    )
    p_analyze.add_argument(
        "-o", "--out-dir", default=None, help="出力ディレクトリ (既定: data/songs/<名前>)"
    )
    p_analyze.add_argument("--title", default=None, help="タイトルの上書き")
    p_analyze.add_argument("--artist", default=None, help="アーティスト名の上書き")
    p_analyze.add_argument(
        "--lyrics", default=None, help="歌詞ファイル (LRC またはプレーンテキスト)"
    )
    p_analyze.add_argument("--vocaloid", action="store_true", help="歌詞検索で NetEase を優先する")
    p_analyze.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="分離モデル (htdemucs / htdemucs_ft) (既定: htdemucs)",
    )
    p_analyze.add_argument(
        "--device", default=None, choices=["mps", "cuda", "cpu"], help="計算デバイス (既定: 自動)"
    )
    p_analyze.add_argument(
        "--skip-separation", action="store_true", help="音源分離を省略する (高速・低品質)"
    )
    p_analyze.add_argument(
        "--enhance-vocals",
        action="store_true",
        help="分離ボーカルにハモリ・残響除去を掛ける (要: uv sync --extra enhance)",
    )
    p_analyze.add_argument(
        "--karaoke-model",
        default=None,
        metavar="MODEL",
        help="enhance 1 段目 (ハモリ除去) のモデルファイル名。'none' でスキップ "
        "(一覧: uv run audio-separator --list_models)",
    )
    p_analyze.add_argument(
        "--dereverb-model",
        default=None,
        metavar="MODEL",
        help="enhance 2 段目 (残響除去) のモデルファイル名。'none' でスキップ",
    )
    p_analyze.add_argument(
        "--refine-align",
        action="store_true",
        help="強制アラインメントで word/char 時刻を実測値に補正する (要: uv sync --extra refine)",
    )
    p_analyze.add_argument(
        "--refine-model",
        default=None,
        metavar="MODEL",
        help="refine の CTC アラインメントモデル (HuggingFace ID)",
    )
    p_analyze.add_argument(
        "--refine-pad",
        type=int,
        default=None,
        metavar="MS",
        help="refine の行窓の探索パディング。LRC が全体的にずれている曲は広げる (既定: 400)",
    )
    p_analyze.add_argument(
        "--refine-min-match",
        type=float,
        default=None,
        metavar="X",
        help="行を補正する最低マッチ率 0〜1 (既定: 0.5)",
    )
    p_analyze.add_argument(
        "--refine-min-score",
        type=float,
        default=None,
        metavar="X",
        help="この文字スコア未満の実測は潰れとして捨てる 0〜1 (既定: 0.35)",
    )
    p_analyze.add_argument(
        "--refine-max-squashed",
        type=int,
        default=None,
        metavar="N",
        help="行中間で許容する潰れ実測の数。超えた行は按分のまま (既定: 1)",
    )

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
    from .config import (
        SONG_TOML_FILENAME,
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
    from .enhance import EnhanceError
    from .fetch import FetchError, check_external_tools, extract_youtube_id, is_url
    from .pipeline import run
    from .refine import RefineError
    from .separate import SeparationError

    missing = check_external_tools()
    if missing:
        print(
            f"必要な外部コマンドが見つかりません: {', '.join(missing)} (brew install ffmpeg)",
            file=sys.stderr,
        )
        return 1

    # source 省略時は対話モードとみなす。
    interactive = args.interactive or args.source is None
    if interactive and not sys.stdin.isatty():
        print(
            "対話モードには端末 (TTY) が必要です。source を指定し -i を外して実行してください。",
            file=sys.stderr,
        )
        return 1

    source = args.source
    if source is None:
        source = _prompt("YouTube URL または音声ファイルのパス")
        if not source:
            print("ソースが指定されていません。", file=sys.stderr)
            return 1

    # source が song.toml (を含むディレクトリ) を指す場合、そこから設定を読み込んで
    # 再解析する (`lyricpv analyze data/songs/mysong/`)。
    toml_dir: Path | None = None
    toml_config: SongConfig | None = None
    if not is_url(source):
        candidate = Path(source)
        toml_path = candidate / SONG_TOML_FILENAME if candidate.is_dir() else candidate
        if candidate.is_dir() and not toml_path.is_file():
            print(
                f"エラー: ディレクトリが指定されましたが song.toml がありません: {candidate}",
                file=sys.stderr,
            )
            return 1
        if toml_path.name == SONG_TOML_FILENAME and toml_path.is_file():
            try:
                toml_config = load_song_config(toml_path)
            except ConfigError as e:
                print(f"エラー: {e}", file=sys.stderr)
                return 1
            if not toml_config.source:
                print(f"エラー: {toml_path} に source が指定されていません。", file=sys.stderr)
                return 1
            toml_dir = toml_path.parent
            source = toml_config.source

    lyrics_text = None
    if args.lyrics:
        lyrics_text = Path(args.lyrics).read_text(encoding="utf-8")

    if args.out_dir:
        out_dir = Path(args.out_dir)
    elif toml_dir is not None:
        out_dir = toml_dir
    else:
        if is_url(source):
            # 動画 ID で曲ごとに分ける (固定名だと2曲目が1曲目を上書きする)
            stem = extract_youtube_id(source) or "youtube"
        else:
            stem = Path(source).stem
        out_dir = Path("data/songs") / stem

    # CLI フラグは指定されたものだけ song.toml を上書きする (None = 未指定)。
    # store_true な opt-in フラグ (vocaloid 等) は False が既定のため、指定時
    # (True) のみ上書き対象にする (未指定と明示的無効化を区別できないため)。
    cli_config = SongConfig(
        title=args.title,
        artist=args.artist,
        vocaloid=True if args.vocaloid else None,
        separation=SeparationConfig(
            model=args.model,
            device=args.device,
            skip=True if args.skip_separation else None,
        ),
        enhance=EnhanceConfig(
            enabled=True if args.enhance_vocals else None,
            karaoke_model=args.karaoke_model,
            dereverb_model=args.dereverb_model,
        ),
        refine=RefineConfig(
            enabled=True if args.refine_align else None,
            model=args.refine_model,
            pad_ms=args.refine_pad,
            min_match_ratio=args.refine_min_match,
            min_char_score=args.refine_min_score,
            max_squashed_mid_chars=args.refine_max_squashed,
        ),
    )
    effective = merge_configs(toml_config or SongConfig(), cli_config)

    try:
        options = to_pipeline_options(
            effective, base_dir=toml_dir or out_dir, lyrics_text=lyrics_text
        )
    except ConfigError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

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
    except (FetchError, SeparationError, EnhanceError, RefineError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    # 再現性のため、実際に使った有効設定を out_dir/song.toml に書き出す。
    # 次回から `lyricpv analyze <out_dir>` で同条件の再解析ができる。
    lyrics_file_to_save = effective.lyrics_file
    if args.lyrics:
        # 外部歌詞ファイルは out_dir にコピーして再現性を閉じる
        # (拡張子は元ファイルのものを維持し、ファイル名と実体の食い違いを防ぐ)
        src = Path(args.lyrics).resolve()
        lyrics_filename = f"lyrics{src.suffix or '.txt'}"
        dst = (out_dir / lyrics_filename).resolve()
        if src != dst:
            shutil.copyfile(src, dst)
        lyrics_file_to_save = lyrics_filename
    elif toml_dir is not None and effective.lyrics_file is not None:
        # song.toml から再解析しつつ -o で別の出力先を指定した場合、歌詞ファイルの
        # 参照が toml_dir 基準のままだと out_dir 側から解決できなくなる。
        # 同じ相対パス名で out_dir にもコピーし、再現性を保つ。
        src = (toml_dir / effective.lyrics_file).resolve()
        dst = (out_dir / effective.lyrics_file).resolve()
        if src != dst:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    saved_config = effective_config_from_options(
        options,
        source=source,
        lyrics_file=lyrics_file_to_save,
        title=result.meta.get("title"),
        artist=result.meta.get("artist"),
    )
    save_song_config(out_dir / SONG_TOML_FILENAME, saved_config)

    print(
        f"完了: {result.json_path} (歌詞 Tier: {result.lyrics_tier}, デバイス: {result.device_used})"
    )
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
