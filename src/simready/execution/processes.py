"""Owned foreground processes; never kill unrelated jobs or invoke a shell."""

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    seconds: float


class ProcessTimeoutError(TimeoutError):
    def __init__(self, timeout, seconds):
        super().__init__(f"Worker exceeded {timeout} seconds")
        self.seconds = seconds


def run_process(
    argv, *, cwd, timeout, stdout, stderr, env=None, input_text=None, pass_fds=()
):
    start = time.monotonic()
    for path in (stdout, stderr):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(stdout).open("wb") as out, Path(stderr).open("wb") as err:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=out,
            stderr=err,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=pass_fds,
        )
        try:
            process.communicate(
                input_text.encode() if input_text is not None else None, timeout=timeout
            )
            return ProcessResult(process.returncode, time.monotonic() - start)
        except subprocess.TimeoutExpired as error:
            raise ProcessTimeoutError(timeout, time.monotonic() - start) from error
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
