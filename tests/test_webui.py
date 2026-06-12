"""WebUI (解析フロントエンド) の API テスト。

合成 WAV + ユーザー供給 LRC で、投入 → 進捗 → 契約A JSON 取得まで
実サーバー相当 (TestClient) で確認する。ネットワーク・GPU 不使用。
"""

import time

import pytest

fastapi = pytest.importorskip("fastapi", reason="webui extra が必要 (uv sync --extra webui)")

from fastapi.testclient import TestClient  # noqa: E402

from lyricpv.schema import validate  # noqa: E402
from lyricpv.webui.app import create_app  # noqa: E402

LRC = "[00:01.00] 夜に駆ける\n[00:04.00] 君の声\n"


@pytest.fixture()
def client(tmp_path):
    app = create_app(data_dir=tmp_path / "songs")
    with TestClient(app) as c:
        yield c


def _wait_for_job(client, job_id: str, timeout_s: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.3)
    pytest.fail("ジョブがタイムアウトしました")


def test_index_serves_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "lyricpv" in res.text


def test_analyze_flow(client, synth_wav_path):
    res = client.post(
        "/api/songs",
        json={
            "source": str(synth_wav_path),
            "title": "合成テスト曲",
            "artist": "lyricpv",
            "lyrics": LRC,
            "skipSeparation": True,
        },
    )
    assert res.status_code == 202
    job_id = res.json()["jobId"]

    job = _wait_for_job(client, job_id)
    assert job["status"] == "done", job.get("error")
    assert job["songId"]

    # 一覧に出る
    songs = client.get("/api/songs").json()
    assert any(s["songId"] == job["songId"] for s in songs)
    meta = next(s for s in songs if s["songId"] == job["songId"])
    assert meta["lyricsTier"] == "T2"

    # 契約A JSON が取得でき、スキーマ検証を通る
    res = client.get(f"/api/songs/{job['songId']}/lyric_data.json")
    assert res.status_code == 200
    data = res.json()
    validate(data)
    assert data["phrases"][0]["text"] == "夜に駆ける"


def test_analyze_rejects_missing_file(client):
    res = client.post("/api/songs", json={"source": "/no/such/file.wav"})
    assert res.status_code == 422


def test_analyze_rejects_unknown_model(client, synth_wav_path):
    res = client.post(
        "/api/songs",
        json={"source": str(synth_wav_path), "model": "not-a-real-model"},
    )
    assert res.status_code == 422


def test_job_not_found(client):
    assert client.get("/api/jobs/deadbeef").status_code == 404


def test_lyric_data_rejects_path_traversal(client):
    assert client.get("/api/songs/..%2F..%2Fetc/lyric_data.json").status_code == 404


def test_lyric_data_not_found(client):
    assert client.get("/api/songs/nonexistent/lyric_data.json").status_code == 404
