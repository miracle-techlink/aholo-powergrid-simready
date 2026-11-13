"""Resumable Aholo reconstruction with an included HTTPS client.

No requests on import. A submit intent without receipt blocks paid resubmission.
Existing-job GET/resume is integration-tested; new paid submissions are not smoke-tested.
"""

import argparse
import fcntl
import importlib.util
import math
import os
import sys
import time
from pathlib import Path

from simready.core.artifacts import digest, read_json, write_json
from simready.execution.process_supervision import supervise_process
from simready.execution.remote_polling import RetryablePollError, poll_existing_job


def _fetch_status(client, world_id, remaining):
    import requests

    try:
        response = client.s.get(
            f"{client.base}/world/v1/{world_id}", timeout=min(30, remaining)
        )
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise RetryablePollError("Retryable HTTP status")
        response.raise_for_status()
        return response.json()
    except (requests.Timeout, requests.ConnectionError) as error:
        raise RetryablePollError("Transient status transport failure") from error


def run(video, out, client_file, timeout, *, allow_submit=False):
    import requests

    video, out = Path(video).resolve(), Path(out).resolve()
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Finite positive timeout required")
    if type(allow_submit) is not bool:
        raise ValueError("allow_submit must be an explicit boolean")
    source_hash = digest(video)
    receipt, intent = out / "receipt.json", out / "submit_intent.json"
    if not receipt.exists():
        if intent.exists():
            raise RuntimeError(
                "Submission outcome unknown. Recover the server receipt before retrying; no automatic resubmission."
            )
        if not allow_submit:
            raise PermissionError("New Aholo reconstruction requires explicit --submit")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "job.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Recheck under the lock: a disappearing receipt must never turn a
        # resume-only invocation into an upload or paid submission.
        resuming = receipt.exists()
        if resuming:
            job = read_json(receipt)
            if job["video_sha256"] != source_hash:
                raise ValueError("Resume video differs from the submitted input")
            world_id = job["world_id"]
            if not isinstance(world_id, str) or not world_id:
                raise ValueError("Existing receipt has no valid world ID")
        else:
            if intent.exists():
                raise RuntimeError(
                    "Submission outcome unknown. Recover the server receipt before retrying; no automatic resubmission."
                )
            if not allow_submit:
                raise PermissionError(
                    "New Aholo reconstruction requires explicit --submit"
                )
        if client_file is None:
            from simready.ingestion.aholo_client import Client

            client = Client()
        else:
            spec = importlib.util.spec_from_file_location(
                "simready_external_aholo_client", client_file
            )
            if spec is None or spec.loader is None:
                raise ValueError("Trusted client file could not be loaded")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            client = module.Client()
        if not resuming:
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
        timeout = min(
            timeout,
            float(os.environ.get("SIMREADY_JOB_DEADLINE_UNIX_S", time.time() + timeout))
            - time.time(),
        )
        if timeout <= 0:
            raise TimeoutError("Job deadline exhausted; receipt retained")
        job = poll_existing_job(
            lambda remaining: _fetch_status(client, world_id, remaining),
            job_id=world_id,
            directory=out,
            timeout=timeout,
        )
        write_json(out / "status.json", {"world_id": world_id, "status": job["status"]})
        urls = ((job.get("assets") or {}).get("splats") or {}).get("urls") or {}
        if not urls.get("plyPath"):
            raise ValueError("Successful job lacks a PLY asset")
        assets = []
        previous = (
            read_json(out / "assets.json") if (out / "assets.json").exists() else {}
        )
        if previous and previous["world_id"] != world_id:
            raise ValueError("Asset receipt belongs to a different job")
        verified = {item["path"]: item for item in previous.get("assets", [])}
        for key, filename in [("plyPath", "splats.ply"), ("spzPath", "splats.spz")]:
            if not urls.get(key):
                continue
            target, partial = out / filename, out / (filename + ".partial")
            old = verified.get(filename)
            if (
                old
                and target.is_file()
                and target.stat().st_size == old["bytes"]
                and digest(target) == old["sha256"]
            ):
                assets.append(old)
                continue
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
            # A later download failure must not discard an already verified PLY.
            write_json(
                out / "assets.json",
                {"world_id": world_id, "assets": assets, "cloud_cost_usd": None},
            )
        write_json(
            out / "assets.json",
            {"world_id": world_id, "assets": assets, "cloud_cost_usd": None},
        )
        return {"world_id": world_id, "video_sha256": source_hash, "assets": assets}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--client",
        type=Path,
        help="Optional trusted Client override; default uses AHOLO_API_KEY",
    )
    parser.add_argument("--poll-timeout", type=float, default=7200)
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Authorize one new cloud reconstruction if no receipt exists; resume never resubmits",
    )
    parser.add_argument(
        "--supervise",
        action="store_true",
        help="Restart owned monitor on transport exit or crash, retaining the same receipt",
    )
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args(argv)
    if args.supervise:
        report = supervise_process(
            [
                sys.executable,
                "-m",
                "simready.ingestion.aholo_reconstruction",
                "--video",
                str(args.video.resolve()),
                "--out",
                str(args.out.resolve()),
                *(["--client", str(args.client.resolve())] if args.client else []),
                *(["--submit"] if args.submit else []),
                "--poll-timeout",
                str(args.poll_timeout),
            ],
            cwd=Path.cwd(),
            directory=args.out / f"supervisor_{time.time_ns()}",
            timeout=args.poll_timeout,
            max_attempts=args.max_attempts,
        )
        return 0 if report["status"] == "SUCCESS" else 2
    import requests

    try:
        run(
            args.video,
            args.out,
            args.client,
            args.poll_timeout,
            allow_submit=args.submit,
        )
    except (requests.Timeout, requests.ConnectionError):
        # No raw exception text: signed download URLs may contain credentials.
        print("Transport interrupted; resume the persisted receipt", file=sys.stderr)
        return 75
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else None
        print(f"HTTP failure (status {status}); receipt retained", file=sys.stderr)
        return 75 if status == 429 or status is not None and 500 <= status < 600 else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
