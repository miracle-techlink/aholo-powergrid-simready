"""Mock the provider boundary; never submit a real cloud reconstruction."""

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.execution.remote_polling import poll_existing_job
from simready.ingestion import aholo_reconstruction as worker


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video fixture")
    return path


@pytest.fixture
def fake_cloud(monkeypatch):
    requests = pytest.importorskip("requests")
    calls = {"client": 0, "upload": 0, "post": 0, "download": []}

    class Response:
        def __init__(self, value=None, content=b"downloaded PLY"):
            self.value, self.content = value, content

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, chunk_size):
            yield self.content

    class Client:
        def __init__(self):
            calls["client"] += 1
            self.s = self
            self.base = "https://api.example.invalid"

        def upload(self, path):
            calls["upload"] += 1
            return "https://upload.example.invalid/video"

        def post(self, url, **kwargs):
            calls["post"] += 1
            assert url.endswith("/world/v1/reconstructions")
            if calls.get("post_error"):
                raise requests.Timeout("ambiguous POST outcome")
            return Response({"worldId": "paid-once"})

    def download(url, **kwargs):
        calls["download"].append(url)
        if url.endswith("spz") and calls.get("spz_error"):
            raise requests.ConnectionError("download interrupted")
        return Response(content=url.encode())

    def poll(*args, **kwargs):
        urls = {"plyPath": "https://assets.example.invalid/ply"}
        if calls.get("spz"):
            urls["spzPath"] = "https://assets.example.invalid/spz"
        return {"status": "SUCCEEDED", "assets": {"splats": {"urls": urls}}}

    monkeypatch.setattr("simready.ingestion.aholo_client.Client", Client)
    monkeypatch.setattr(worker, "poll_existing_job", poll)
    monkeypatch.setattr(requests, "get", download)
    return calls


def test_default_never_constructs_client_uploads_or_creates_output(
    tmp_path, video, fake_cloud
):
    out = tmp_path / "job"
    with pytest.raises(PermissionError, match="--submit"):
        worker.run(video, out, None, 10)
    assert not out.exists()
    assert fake_cloud["client"] == fake_cloud["post"] == fake_cloud["upload"] == 0


def test_explicit_submit_once_and_receipt_resume_reuses_download(
    tmp_path, video, fake_cloud
):
    out = tmp_path / "job"
    first = worker.run(video, out, None, 10, allow_submit=True)
    second = worker.run(video, out, None, 10)
    assert first == second
    assert first["world_id"] == "paid-once"
    assert fake_cloud["post"] == fake_cloud["upload"] == 1
    assert len(fake_cloud["download"]) == 1
    assert read_json(out / "receipt.json")["video_sha256"] == digest(video)


def test_ambiguous_submit_keeps_intent_and_blocks_another_paid_post(
    tmp_path, video, fake_cloud
):
    requests = pytest.importorskip("requests")
    out = tmp_path / "job"
    fake_cloud["post_error"] = True
    with pytest.raises(requests.Timeout):
        worker.run(video, out, None, 10, allow_submit=True)
    assert (out / "submit_intent.json").exists()
    assert not (out / "receipt.json").exists()
    with pytest.raises(RuntimeError, match="outcome unknown"):
        worker.run(video, out, None, 10, allow_submit=True)
    assert fake_cloud["post"] == fake_cloud["upload"] == fake_cloud["client"] == 1


def test_receipt_source_mismatch_fails_before_credentials_or_requests(
    tmp_path, video, fake_cloud
):
    out = tmp_path / "job"
    write_json(out / "receipt.json", {"world_id": "prior", "video_sha256": "wrong"})
    with pytest.raises(ValueError, match="video differs"):
        worker.run(video, out, None, 10, allow_submit=True)
    assert fake_cloud["client"] == 0


def test_second_asset_failure_retains_first_verified_asset_for_resume(
    tmp_path, video, fake_cloud
):
    requests = pytest.importorskip("requests")
    out = tmp_path / "job"
    fake_cloud.update(spz=True, spz_error=True)
    with pytest.raises(requests.ConnectionError):
        worker.run(video, out, None, 10, allow_submit=True)
    assert read_json(out / "assets.json")["assets"][0]["path"] == "splats.ply"
    fake_cloud["spz_error"] = False
    result = worker.run(video, out, None, 10)
    assert [record["path"] for record in result["assets"]] == [
        "splats.ply",
        "splats.spz",
    ]
    assert fake_cloud["download"].count("https://assets.example.invalid/ply") == 1
    assert fake_cloud["post"] == 1


@pytest.mark.parametrize("submit", [False, True])
def test_module_supervision_preserves_explicit_submission_flag(
    tmp_path, video, monkeypatch, submit
):
    calls = []

    def supervise(argv, **kwargs):
        calls.append(argv)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(worker, "supervise_process", supervise)
    result = worker.main(
        [
            "--video",
            str(video),
            "--out",
            str(tmp_path / "job"),
            "--supervise",
            *(["--submit"] if submit else []),
        ]
    )
    assert result == 0
    assert ("--submit" in calls[0]) is submit


def test_ssl_retry_resumes_receipt_and_reuses_verified_download(tmp_path, monkeypatch):
    pytest.importorskip("requests")
    from simready.ingestion import aholo_reconstruction as submit_panel

    video = tmp_path / "source.mp4"
    video.write_bytes(b"video fixture")
    out = tmp_path / "job"
    out.mkdir()
    write_json(
        out / "receipt.json", {"world_id": "original", "video_sha256": digest(video)}
    )
    ply = out / "splats.ply"
    ply.write_bytes(b"existing verified asset")
    write_json(
        out / "assets.json",
        {
            "world_id": "original",
            "assets": [
                {
                    "path": "splats.ply",
                    "sha256": digest(ply),
                    "bytes": ply.stat().st_size,
                }
            ],
        },
    )
    client = tmp_path / "client.py"
    client.write_text(
        "import requests\n"
        "class Client:\n"
        "    def __init__(self): self.s=self;self.base='https://example.invalid';self.calls=0\n"
        "    def upload(self,*a,**k): raise AssertionError('Must not upload or submit')\n"
        "    def post(self,*a,**k): raise AssertionError('Must not resubmit')\n"
        "    def get(self,url,timeout):\n"
        "        assert url.endswith('/world/v1/original')\n"
        "        self.calls+=1\n"
        "        if self.calls<3: raise requests.exceptions.SSLError('EOF')\n"
        "        return self\n"
        "    status_code=200\n"
        "    def raise_for_status(self): pass\n"
        "    def json(self): return {'status':'SUCCEEDED','assets':{'splats':{'urls':{'plyPath':'https://example.invalid/private-signature'}}}}\n"
    )
    monkeypatch.setattr(
        submit_panel,
        "poll_existing_job",
        lambda *a, **k: poll_existing_job(*a, **k, interval=0.001),
    )
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: pytest.fail("Verified asset must not be downloaded again"),
    )
    submit_panel.run(video, out, client, timeout=10)
    assert read_json(out / "status.json")["status"] == "SUCCEEDED"
    assert (out / "poll_events.jsonl").read_text().count("TRANSPORT_RETRY") == 2
    assert not (out / "submit_intent.json").exists()
