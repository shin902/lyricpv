"""① 音源取得 — yt-dlp / ローカルファイル → 可逆 WAV マスター。

YouTube の素音声は Opus/AAC なので mp3 化は二重劣化になる。
bestaudio を取得して 44.1kHz/ステレオの可逆 WAV マスター 1 本を作り、
以降の全工程 (分離・楽曲地図・声量) はこのマスターを入力にする。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

MASTER_SAMPLE_RATE = 44_100
MASTER_FILENAME = "master.wav"


class FetchError(RuntimeError):
    """音源の取得・変換に失敗したときに送出される。"""


@dataclass
class FetchResult:
    wav_path: Path
    title: str
    artist: str
    duration_ms: int
    source_type: str  # "youtube" | "file"
    source_id: str


def _ffmpeg_to_master(src: Path, dst: Path) -> None:
    """任意の音声/動画ファイルを 44.1kHz ステレオ WAV に変換する。"""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vn", "-ac", "2", "-ar", str(MASTER_SAMPLE_RATE),
        "-c:a", "pcm_s16le", str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise FetchError(f"ffmpeg での WAV 変換に失敗しました: {proc.stderr[-500:]}")


def _probe_duration_ms(path: Path) -> int:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise FetchError(f"ffprobe で長さを取得できませんでした: {path}")
    return int(float(proc.stdout.strip()) * 1000)


def fetch_youtube(url: str, out_dir: str | Path) -> FetchResult:
    """YouTube から bestaudio を取得し WAV マスターを作る。"""
    import yt_dlp

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_template = str(out_dir / "source.%(ext)s")

    opts = {
        "format": "bestaudio/best",
        "outtmpl": raw_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise FetchError(f"YouTube からのダウンロードに失敗しました: {e}") from e

    raw_files = sorted(out_dir.glob("source.*"))
    raw_files = [p for p in raw_files if p.suffix != ".wav"]
    if not raw_files:
        raise FetchError("ダウンロードされた音声ファイルが見つかりません")
    raw = raw_files[0]

    wav_path = out_dir / MASTER_FILENAME
    _ffmpeg_to_master(raw, wav_path)
    raw.unlink()  # 解析用中間ファイルは残さない (再配布防止)

    return FetchResult(
        wav_path=wav_path,
        title=info.get("title", "不明なタイトル"),
        artist=info.get("artist") or info.get("uploader") or "不明なアーティスト",
        duration_ms=_probe_duration_ms(wav_path),
        source_type="youtube",
        source_id=info.get("id", ""),
    )


def import_file(path: str | Path, out_dir: str | Path, *,
                title: str | None = None, artist: str | None = None) -> FetchResult:
    """ローカルの音声ファイルから WAV マスターを作る。"""
    path = Path(path)
    if not path.exists():
        raise FetchError(f"ファイルが見つかりません: {path}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    wav_path = out_dir / MASTER_FILENAME
    if path.suffix.lower() == ".wav" and path != wav_path:
        # WAV でもサンプルレート統一のため必ず ffmpeg を通す
        _ffmpeg_to_master(path, wav_path)
    else:
        _ffmpeg_to_master(path, wav_path)

    return FetchResult(
        wav_path=wav_path,
        title=title or path.stem,
        artist=artist or "不明なアーティスト",
        duration_ms=_probe_duration_ms(wav_path),
        source_type="file",
        source_id=path.name,
    )


def extract_youtube_id(url: str) -> str | None:
    """URL から YouTube 動画 ID を抜き出す (失敗時 None)。"""
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def check_external_tools() -> list[str]:
    """必要な外部コマンドのうち欠けているものを返す。"""
    return [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
