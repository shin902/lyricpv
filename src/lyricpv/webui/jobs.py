"""解析ジョブのインメモリ管理。

1 曲の解析は数十秒〜数分かかるため、HTTP リクエストとは切り離した
ワーカースレッドで実行し、進捗をポーリングで返す。
ローカルツール想定のため永続化はしない (プロセス再起動でジョブ履歴は消えるが、
解析結果そのものは data ディレクトリに残る)。
"""

from __future__ import annotations

import re
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..pipeline import PipelineOptions, run


@dataclass
class Job:
    id: str
    source: str
    status: str = "pending"  # pending | running | done | error
    stage: str = ""
    message: str = ""
    error: str | None = None
    song_id: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "source": self.source,
                "status": self.status,
                "stage": self.stage,
                "message": self.message,
                "error": self.error,
                "songId": self.song_id,
            }


def slugify(text: str) -> str:
    """タイトル等から出力ディレクトリ名を作る。"""
    text = unicodedata.normalize("NFKC", text).strip()
    text = re.sub(r"[^\w\-ぁ-んァ-ヶ一-龠ー]+", "_", text)
    return text.strip("_")[:60] or "song"


class JobManager:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, source: str, options: PipelineOptions) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], source=source)
        with self._lock:
            self._jobs[job.id] = job
        thread = threading.Thread(
            target=self._run, args=(job, options), name=f"lyricpv-job-{job.id}", daemon=True
        )
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [j.snapshot() for j in jobs]

    def _run(self, job: Job, options: PipelineOptions) -> None:
        def progress(stage: str, message: str) -> None:
            with job.lock:
                job.stage = stage
                job.message = message

        with job.lock:
            job.status = "running"
        try:
            song_id = slugify(options.title or Path(job.source).stem or "song")
            out_dir = self._unique_dir(song_id)
            result = run(job.source, out_dir, options=options, progress=progress)
            with job.lock:
                job.status = "done"
                job.song_id = out_dir.name
                job.message = f"完了 (歌詞 Tier: {result.lyrics_tier}, デバイス: {result.device_used})"
        except Exception as e:  # ジョブの失敗は API 経由でユーザーに見せる
            with job.lock:
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}"

    def _unique_dir(self, song_id: str) -> Path:
        """既存の解析結果を壊さないよう、重複時は連番を付ける。"""
        candidate = self.data_dir / song_id
        n = 2
        while candidate.exists():
            candidate = self.data_dir / f"{song_id}-{n}"
            n += 1
        return candidate
