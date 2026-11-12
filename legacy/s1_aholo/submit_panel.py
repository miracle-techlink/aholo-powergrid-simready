"""Historical Aholo adapter requiring a separately supplied, trusted Client.

No requests on import. A submit intent without receipt blocks paid resubmission.
The external API contract still needs an integration test.
"""

import argparse
import fcntl
import importlib.util
from pathlib import Path
import time

from simready_bridge.artifacts import digest, read_json, write_json


def run(video, out, client_file, timeout):
    import requests

    video, out = Path(video).resolve(), Path(out).resolve()
    if timeout <= 0:
        raise ValueError("Positive timeout required")
    source_hash = digest(video)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "job.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        spec = importlib.util.spec_from_file_location(
            "simready_external_aholo_client", client_file
        )
        if spec is None or spec.loader is None:
            raise ValueError("Trusted client file could not be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        client = module.Client()
        receipt, intent = out / "receipt.json", out / "submit_intent.json"
        if receipt.exists():
            job = read_json(receipt)
            if job["video_sha256"] != source_hash:
                raise ValueError("Resume video differs from the submitted input")
            world_id = job["world_id"]
        else:
            if intent.exists():
                raise RuntimeError(
                    "Submission outcome unknown. Recover the server receipt before retrying; no automatic resubmission."
                )
            url = client.upload(str(video))
            if digest(video) != source_hash:
                raise ValueError("Video changed during upload")
            write_json(intent, {"video_sha256": source_hash, "started_at": time.time()})
            response = client.s.post(
                f"{client.base}/world/v1/reconstructions",
                json={
                    "name": video.stem,
                    "resources": [{"url": url, "type": "video"}],
                    "taskQuality": "normal",
                    "scene": "space",
                },
                timeout=60,
            )
            response.raise_for_status()
            world_id = response.json().get("worldId")
            if not isinstance(world_id, str) or not world_id:
                raise ValueError(
                    "Submission returned no valid worldId; intent retained"
                )
            write_json(
                receipt,
                {
                    "world_id": world_id,
                    "video_sha256": source_hash,
                    "cloud_cost_usd": None,
                    "submitted_at": time.time(),
                },
            )
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            job = client.get(world_id)
            status = str(job.get("status", "")).upper()
            write_json(
                out / "status.json",
                {
                    "world_id": world_id,
                    "status": status,
                    "progress": job.get("progress"),
                },
            )
            if status in ("FAILED", "FAIL", "CANCELLED", "CANCELED"):
                raise RuntimeError(f"Aholo task ended with {status}")
            if status in ("SUCCEEDED", "SUCCESS", "DONE"):
                break
            time.sleep(min(20, max(0, timeout - (time.monotonic() - started))))
        else:
            raise TimeoutError(
                "Aholo polling budget exhausted; receipt retained for resume"
            )
        urls = ((job.get("assets") or {}).get("splats") or {}).get("urls") or {}
        if not urls.get("plyPath"):
            raise ValueError("Successful job lacks a PLY asset")
        assets = []
        for key, filename in [("plyPath", "splats.ply"), ("spzPath", "splats.spz")]:
            if not urls.get(key):
                continue
            target, partial = out / filename, out / (filename + ".partial")
            # Signed asset downloads must not receive the API session's headers.
            with requests.get(urls[key], timeout=(30, 600), stream=True) as response:
                response.raise_for_status()
                with partial.open("wb") as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        stream.write(chunk)
            if not partial.stat().st_size:
                raise ValueError("Downloaded asset is empty")
            partial.replace(target)
            assets.append(
                {
                    "path": filename,
                    "sha256": digest(target),
                    "bytes": target.stat().st_size,
                }
            )
        write_json(
            out / "assets.json",
            {"world_id": world_id, "assets": assets, "cloud_cost_usd": None},
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--client",
        type=Path,
        required=True,
        help="Trusted scene_recon.py containing Client",
    )
    parser.add_argument("--poll-timeout", type=float, default=7200)
    args = parser.parse_args()
    run(args.video, args.out, args.client, args.poll_timeout)


if __name__ == "__main__":
    main()
