"""Single-host, bounded DAG execution. No GPU libraries required."""

__all__ = ["run_task_graph"]


def __getattr__(name):
    if name == "run_task_graph":
        from simready.execution.tasks.graph import run_task_graph

        return run_task_graph
    raise AttributeError(name)
