"""Single-host, bounded DAG execution. No GPU libraries required."""

from .runner import run

__all__ = ["run"]
