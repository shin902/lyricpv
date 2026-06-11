"""JobManager のジョブ保持上限のテスト。"""

from lyricpv.webui.jobs import Job, JobManager


def test_prune_keeps_jobs_within_limit(tmp_path):
    manager = JobManager(tmp_path)
    for i in range(manager.MAX_JOBS + 10):
        job = Job(id=f"job-{i}", source="dummy", status="done")
        manager._jobs[job.id] = job

    with manager._lock:
        manager._prune_locked()

    assert len(manager._jobs) == manager.MAX_JOBS


def test_prune_does_not_remove_running_or_pending_jobs(tmp_path):
    manager = JobManager(tmp_path)
    for i in range(manager.MAX_JOBS + 10):
        status = "running" if i < 20 else "done"
        job = Job(id=f"job-{i}", source="dummy", status=status)
        manager._jobs[job.id] = job

    with manager._lock:
        manager._prune_locked()

    running = [j for j in manager._jobs.values() if j.status == "running"]
    assert len(running) == 20
    assert len(manager._jobs) == manager.MAX_JOBS
