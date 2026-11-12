"""Inject the observed SSL EOF without submitting a paid reconstruction."""

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.execution.remote_polling import poll_existing_job


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
