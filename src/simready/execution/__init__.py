"""Single-host, bounded DAG execution. No GPU libraries required."""

from simready.execution.task_graph import run_task_graph

__all__ = ["run_task_graph"]
