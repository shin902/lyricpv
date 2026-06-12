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


# song_id として許容する文字クラス。\w は Unicode モードで漢字・かなを含むが、
# 対象範囲を明示するため日本語の文字クラスも併記する (々・〆 等の範囲外の記号は
# slugify() により _ に置換される)。webui/app.py の _SONG_ID_RE もこの定数を
# 使い、slugify() の出力形式と検証パターンが乖離しないようにする。
SONG_ID_CHARS = r"\w\-ぁ-んァ-ヶ一-龠ー"


def slugify(text: str) -> str:
    """タイトル等から出力ディレクトリ名を作る。"""
    text = unicodedata.normalize("NFKC", text).strip()
    text = re.sub(rf"[^{SONG_ID_CHARS}]+", "_", text)
    return text.strip("_")[:60] or "song"


class JobManager:
    # ジョブ辞書が無制限に増えないよう、完了済みジョブの保持上限を設ける
    MAX_JOBS = 100

    def __init__(self, data_dir: str | Path, max_concurrency: int = 1):
        self.data_dir = Path(data_dir)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Demucs はメモリ・GPU 負荷が大きいため既定で 1 ジョブずつ実行する
        self._slots = threading.Semaphore(max_concurrency)

    def submit(self, source: str, options: PipelineOptions) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], source=source)
        with self._lock:
            self._jobs[job.id] = job
            self._prune_locked()
        thread = threading.Thread(
            target=self._run, args=(job, options), name=f"lyricpv-job-{job.id}", daemon=True
        )
        thread.start()
        return job

    def _prune_locked(self) -> None:
        """完了/失敗ジョブを古い順に間引き、保持件数を MAX_JOBS 以下に保つ。

        呼び出し側で self._lock を保持していること。
        pending/running のジョブは間引かないため、それらだけで MAX_JOBS を
        超えて滞留している間は一時的に上限を超えうる
        (ローカルツール用途の同時実行数を想定した設計)。
        """
        if len(self._jobs) <= self.MAX_JOBS:
            return
        for job_id, job in list(self._jobs.items()):
            if len(self._jobs) <= self.MAX_JOBS:
                break
            if job.status in ("done", "error"):
                del self._jobs[job_id]

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
            job.message = "他のジョブの完了を待っています"
        with self._slots:
            with job.lock:
                job.status = "running"
                job.message = ""
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
        """既存の解析結果を壊さないよう、重複時は連番を付ける。

        exists() チェックと mkdir の間のレース (TOCTOU) を避けるため、
        mkdir(exist_ok=False) の成功でディレクトリを確保する。
        """
        candidate = self.data_dir / song_id
        n = 2
        while True:
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                return candidate
            except FileExistsError:
                candidate = self.data_dir / f"{song_id}-{n}"
                n += 1
