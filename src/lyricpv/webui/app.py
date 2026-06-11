"""WebUI の FastAPI アプリケーション。

エンドポイント:
- ``GET  /``                              フロントページ (静的 HTML)
- ``POST /api/songs``                     解析ジョブ投入 → {jobId}
- ``GET  /api/jobs``                      ジョブ一覧
- ``GET  /api/jobs/{job_id}``             ジョブ進捗
- ``GET  /api/songs``                     解析済み楽曲一覧 (meta.json ベース)
- ``GET  /api/songs/{song_id}/lyric_data.json``  契約A JSON

データディレクトリは環境変数 ``LYRICPV_DATA_DIR`` (既定: ``data/songs``)。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from ..pipeline import LYRIC_DATA_FILENAME, META_FILENAME, PipelineOptions
from .jobs import JobManager

# \w は Unicode モードで漢字・かなを含むが、対象範囲を明示するため日本語の
# 文字クラスも併記する (々・〆 等の範囲外の記号は slugify() により _ に置換される)
_SONG_ID_RE = re.compile(r"^[\w\-ぁ-んァ-ヶ一-龠ー]+$")

_STATIC_DIR = Path(__file__).parent / "static"


class AnalyzeRequest(BaseModel):
    source: str = Field(description="YouTube URL またはサーバー上の音声ファイルパス")
    title: str | None = None
    artist: str | None = None
    lyrics: str | None = Field(default=None, description="LRC またはプレーン歌詞 (任意)")
    vocaloid: bool = False
    model: Literal["htdemucs", "htdemucs_ft"] = "htdemucs"
    skipSeparation: bool = False


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("LYRICPV_DATA_DIR", "data/songs"))
    data_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="lyricpv", description="文字PV生成 SDK — 解析フロントエンド")
    manager = JobManager(data_dir)
    app.state.jobs = manager

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.post("/api/songs", status_code=202)
    def analyze(req: AnalyzeRequest) -> dict:
        source = req.source.strip()
        if not source:
            raise HTTPException(422, "source を指定してください")
        if not source.startswith(("http://", "https://")) and not Path(source).exists():
            raise HTTPException(422, f"ファイルが見つかりません: {source}")
        options = PipelineOptions(
            title=req.title or None,
            artist=req.artist or None,
            lyrics_text=req.lyrics or None,
            vocaloid=req.vocaloid,
            separation_model=req.model,
            skip_separation=req.skipSeparation,
        )
        job = manager.submit(source, options)
        return {"jobId": job.id}

    @app.get("/api/jobs")
    def list_jobs() -> list[dict]:
        return manager.list()

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "ジョブが見つかりません")
        return job.snapshot()

    @app.get("/api/songs")
    def list_songs() -> list[dict]:
        songs = []
        for meta_path in sorted(data_dir.glob(f"*/{META_FILENAME}")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            meta["songId"] = meta_path.parent.name
            songs.append(meta)
        return songs

    @app.get("/api/songs/{song_id}/lyric_data.json")
    def lyric_data(song_id: str) -> FileResponse:
        if not _SONG_ID_RE.match(song_id):  # パストラバーサル防止
            raise HTTPException(404, "不正な楽曲IDです")
        path = data_dir / song_id / LYRIC_DATA_FILENAME
        if not path.exists():
            raise HTTPException(404, "解析データが見つかりません")
        return FileResponse(path, media_type="application/json")

    return app
