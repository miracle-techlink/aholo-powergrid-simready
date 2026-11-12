"""Aholo HTTPS upload client; credentials stay on the API origin only."""

import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urlparse


class Client:
    def __init__(self):
        import requests

        key = os.environ.get("AHOLO_API_KEY", "").strip()
        if not key:
            raise ValueError("Set AHOLO_API_KEY in the worker environment")
        self.base = "https://api.aholo3d.cn"
        self.s = requests.Session()
        self.s.headers["Authorization"] = key
        # Upload tokens use a separate session: never forward Authorization.
        self.upload_session = requests.Session()

    @staticmethod
    def _data(response):
        response.raise_for_status()
        value = response.json()
        if str(value.get("c")) != "0":
            raise RuntimeError("Aholo upload service rejected the request")
        return value["d"]

    def upload(self, path):
        path = Path(path)
        size = path.stat().st_size
        checksum = hashlib.md5()  # Service upload integrity contract, not security.
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        response = self.s.get(f"{self.base}/asset/v1/token", timeout=30)
        response.raise_for_status()
        token = response.json()
        domain = token["globalDomain"].rstrip("/")
        parsed = urlparse(domain)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Upload service returned an invalid HTTPS origin")
        headers = {"ous-token-v2": token["ousToken"]}
        block_size = int(token.get("blockSize", 5 << 20))
        if block_size <= 0 or block_size > 256 * 1024 * 1024:
            raise ValueError("Invalid upload block size")
        if size <= block_size:
            with path.open("rb") as stream:
                data = self._data(
                    self.upload_session.post(
                        f"{domain}/ous/api/v2/single/upload",
                        headers=headers,
                        files={"file": (path.name, stream, "application/octet-stream")},
                        data={"md5": checksum.hexdigest()},
                        timeout=600,
                    )
                )
        else:
            data = self._data(
                self.upload_session.post(
                    f"{domain}/ous/api/v2/block/upload/init",
                    headers=headers,
                    params={
                        "md5": checksum.hexdigest(),
                        "blocks": (size + block_size - 1) // block_size,
                        "size": size,
                        "name": path.name,
                    },
                    timeout=60,
                )
            )
            with path.open("rb") as stream:
                block = 0
                while chunk := stream.read(block_size):
                    block += 1
                    self._data(
                        self.upload_session.post(
                            f"{domain}/ous/api/v2/block/upload/part",
                            headers=headers,
                            files={
                                "file": (
                                    f"{path.name}.part{block}",
                                    chunk,
                                    "application/octet-stream",
                                )
                            },
                            data={"block": block},
                            timeout=600,
                        )
                    )
        for _ in range(90):
            state = self._data(
                self.upload_session.get(
                    f"{domain}/ous/api/v2/upload/status",
                    headers=headers,
                    params={"taskId": data["obsTaskId"]},
                    timeout=30,
                )
            )
            if state.get("url"):
                return state["url"]
            if str(state.get("status")).lower() in ("4", "failed"):
                raise RuntimeError("Aholo upload failed")
            time.sleep(2)
        raise TimeoutError("Aholo upload status deadline exhausted")
