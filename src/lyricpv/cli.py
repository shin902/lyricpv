"""コマンドラインインターフェース。

使い方:
    lyricpv analyze <YouTube URL または音声ファイル> [-o 出力先] [--lyrics 歌詞ファイル]
    lyricpv serve [--port 8000]
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
    p_analyze.add_argument("source", help="YouTube URL または音声ファイルのパス")
    p_analyze.add_argument("-o", "--out-dir", default=None, help="出力ディレクトリ (既定: data/songs/<名前>)")
    p_analyze.add_argument("--title", default=None, help="タイトルの上書き")
    p_analyze.add_argument("--artist", default=None, help="アーティスト名の上書き")
    p_analyze.add_argument("--lyrics", default=None, help="歌詞ファイル (LRC またはプレーンテキスト)")
    p_analyze.add_argument("--vocaloid", action="store_true", help="歌詞検索で NetEase を優先する")
    p_analyze.add_argument("--model", default="htdemucs", help="分離モデル (htdemucs / htdemucs_ft)")
    p_analyze.add_argument("--device", default=None, choices=["mps", "cuda", "cpu"], help="計算デバイス (既定: 自動)")
    p_analyze.add_argument("--skip-separation", action="store_true", help="音源分離を省略する (高速・低品質)")

    p_serve = sub.add_parser("serve", help="WebUI (解析ツール) を起動する")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "analyze":
        return _cmd_analyze(args)
    if args.command == "serve":
        return _cmd_serve(args)
    return 2


def _cmd_analyze(args: argparse.Namespace) -> int:
    from .pipeline import PipelineOptions, run

    lyrics_text = None
    if args.lyrics:
        lyrics_text = Path(args.lyrics).read_text(encoding="utf-8")

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        stem = Path(args.source).stem if not args.source.startswith("http") else "youtube"
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

    result = run(args.source, out_dir, options=options, progress=progress)
    print(f"完了: {result.json_path} (歌詞 Tier: {result.lyrics_tier}, デバイス: {result.device_used})")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("lyricpv.webui.app:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
