"""Inherited cancellation pipes for cooperating, nested process owners.

Every process owner keeps a separate process group. Closing a private pipe asks
the next owner to unwind its children and receipts before a bounded hard kill.
No PID search or external process tree is used.
"""

import fcntl
import os
import select
import stat
import subprocess

CANCEL_FD = "SIMREADY_CANCEL_FD"
ACK_FD = "SIMREADY_CANCEL_ACK_FD"
RESERVED = frozenset({CANCEL_FD, ACK_FD})


def _descriptor(name, mode):
    try:
        fd_text, inode_text = os.environ[name].split(":")
        fd, inode = int(fd_text), int(inode_text)
        status = os.fstat(fd)
        if (
            fd < 3
            or not stat.S_ISFIFO(status.st_mode)
            or status.st_ino != inode
            or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != mode
        ):
            raise ValueError
        return fd
    except (KeyError, OSError, ValueError) as error:
        raise ValueError(
            f"Invalid inherited cancellation descriptor: {name}"
        ) from error


def parent_cancellation(*, enabled=True):
    """Acknowledge cooperation before spawning any separately grouped child."""
    if not enabled or not (RESERVED & os.environ.keys()):
        return None
    reader, acknowledgement = (
        _descriptor(CANCEL_FD, os.O_RDONLY),
        _descriptor(ACK_FD, os.O_WRONLY),
    )
    # Repeated calls need not fill the pipe if the parent is busy. One byte is
    # enough to request the documented cleanup grace from the immediate owner.
    os.set_blocking(acknowledgement, False)
    try:
        os.write(acknowledgement, b"1")
    except (BlockingIOError, BrokenPipeError):
        pass
    return reader


def parent_cancelled(reader):
    return reader is not None and bool(select.select([reader], [], [], 0)[0])


class ChildScope:
    def __init__(self):
        self.reader, self.writer = os.pipe()
        self.ack_reader, self.ack_writer = os.pipe()

    @property
    def pass_fds(self):
        return self.reader, self.ack_writer

    def environment(self, values):
        return {
            **values,
            CANCEL_FD: f"{self.reader}:{os.fstat(self.reader).st_ino}",
            ACK_FD: f"{self.ack_writer}:{os.fstat(self.ack_writer).st_ino}",
        }

    def spawned(self):
        # Only the child holds these ends. In particular no descendant inherits
        # the parent's write end, so parent death reliably produces EOF.
        for name in ("reader", "ack_writer"):
            os.close(getattr(self, name))
            setattr(self, name, None)

    def request_stop(self, process, grace_s):
        if self.writer is not None:
            os.close(self.writer)
            self.writer = None
        if process.poll() is not None:
            return
        if select.select([self.ack_reader], [], [], 0)[0]:
            if os.read(self.ack_reader, 1):
                try:
                    process.wait(timeout=grace_s)
                except subprocess.TimeoutExpired:
                    pass

    def close(self):
        for name in ("reader", "writer", "ack_reader", "ack_writer"):
            descriptor = getattr(self, name)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)
