"""Exercise upload transport boundaries without sending data to the service."""

from simready.ingestion.reconstruction.aholo_client import Client


def test_upload_token_does_not_forward_api_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("AHOLO_API_KEY", "test-only-credential")
    sessions = []

    class Response:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    class Session:
        def __init__(self):
            self.headers = {}
            sessions.append(self)

        def get(self, url, **kwargs):
            if url.endswith("/asset/v1/token"):
                assert self.headers["Authorization"] == "test-only-credential"
                return Response(
                    {
                        "globalDomain": "https://upload.example",
                        "ousToken": "scoped-upload",
                        "blockSize": 1024,
                    }
                )
            assert "Authorization" not in self.headers
            assert kwargs["headers"] == {"ous-token-v2": "scoped-upload"}
            return Response({"c": 0, "d": {"url": "https://upload.example/video"}})

        def post(self, url, **kwargs):
            assert url == "https://upload.example/ous/api/v2/single/upload"
            assert "Authorization" not in self.headers
            assert kwargs["files"]["file"][1].read() == b"fixture video"
            return Response({"c": "0", "d": {"obsTaskId": "task-1"}})

    monkeypatch.setattr("requests.Session", Session)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fixture video")
    assert Client().upload(video) == "https://upload.example/video"
    assert len(sessions) == 2
