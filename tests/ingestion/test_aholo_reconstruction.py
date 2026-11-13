"""Mock the provider boundary; never submit a real cloud reconstruction."""

import pytest

from simready.core.artifacts import digest, read_json, write_json
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
