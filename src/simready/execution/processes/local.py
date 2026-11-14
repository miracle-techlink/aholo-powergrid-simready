"""Owned foreground processes; never kill unrelated jobs or invoke a shell."""

import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from simready.core.validation import number
from simready.execution.processes.cancellation import (
    ChildScope,
    parent_cancellation,
    parent_cancelled,
)
from simready.execution.resources.gpu_capability import delegated_capability


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    seconds: float


class ProcessTimeoutError(TimeoutError):
    def __init__(self, timeout, seconds):
        super().__init__(f"Worker exceeded {timeout} seconds")
        self.seconds = seconds


class ProcessCancelledError(RuntimeError):
    def __init__(self, seconds):
        super().__init__("Worker cancelled by its owner")
        self.seconds = seconds


def run_process(
    argv,
    *,
    cwd,
    timeout,
    stdout,
    stderr,
    env=None,
    input_text=None,
    pass_fds=(),
    cancel_event=None,
    cancellation_grace_s=20,
    inherit_cancellation=True,
):
    """Run an owned process group; optional cancellation is polled within 0.1 s.

    Cooperating nested owners receive a private cancellation pipe and at most
    cancellation_grace_s to unwind their groups and receipts before hard kill.
    Noncooperating children are killed immediately. OS-level uninterruptible IO
    can delay the final reap. Deliberately detached, unmanaged sessions are outside
    this contract. Cleanup commands may explicitly ignore inherited cancellation.
    """
    if number(timeout, "timeout") <= 0:
        raise ValueError("timeout must be positive")
    number(cancellation_grace_s, "cancellation_grace_s")
    inherited = parent_cancellation(enabled=inherit_cancellation)
    start = time.monotonic()
    if parent_cancelled(inherited) or (
        cancel_event is not None and cancel_event.is_set()
    ):
        raise ProcessCancelledError(0.0)
    for path in (stdout, stderr):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    scope = ChildScope()
    process = None
    try:
        with (
            delegated_capability(
                dict(os.environ) if env is None else env, pass_fds
            ) as (child_env, child_fds),
            Path(stdout).open("wb") as out,
            Path(stderr).open("wb") as err,
            selectors.DefaultSelector() as writer,
        ):
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=scope.environment(child_env),
                stdout=out,
                stderr=err,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=tuple(set(child_fds) | set(scope.pass_fds)),
            )
            scope.spawned()
            try:
                # Retrying communicate(None) after a short timeout stops feeding
                # buffered input on Python 3.10/3.12. Own the pipe offset instead;
                # nonblocking writes keep cancellation responsive under backpressure.
                payload = memoryview(
                    input_text.encode() if input_text is not None else b""
                )
                offset = 0
                if process.stdin is not None:
                    os.set_blocking(process.stdin.fileno(), False)
                    if payload:
                        writer.register(process.stdin, selectors.EVENT_WRITE)
                    else:
                        process.stdin.close()
                while True:
                    if parent_cancelled(inherited) or (
                        cancel_event is not None and cancel_event.is_set()
                    ):
                        raise ProcessCancelledError(time.monotonic() - start)
                    remaining = timeout - (time.monotonic() - start)
                    if remaining <= 0:
                        raise ProcessTimeoutError(timeout, time.monotonic() - start)
                    interval = (
                        remaining
                        if cancel_event is None and inherited is None
                        else min(remaining, 0.1)
                    )
                    if writer.get_map():
                        if writer.select(interval):
                            try:
                                offset += os.write(
                                    process.stdin.fileno(),
                                    payload[offset : offset + 65536],
                                )
                            except BlockingIOError:
                                continue
                            except BrokenPipeError:
                                writer.unregister(process.stdin)
                                process.stdin.close()
                            else:
                                if offset == len(payload):
                                    writer.unregister(process.stdin)
                                    process.stdin.close()
                        continue
                    try:
                        process.wait(timeout=interval)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                if parent_cancelled(inherited) or (
                    cancel_event is not None and cancel_event.is_set()
                ):
                    raise ProcessCancelledError(time.monotonic() - start)
                return ProcessResult(process.returncode, time.monotonic() - start)
            finally:
                try:
                    if process.stdin is not None:
                        process.stdin.close()
                    scope.request_stop(process, cancellation_grace_s)
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
    finally:
        scope.close()
