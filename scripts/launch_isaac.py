"""Compatibility entry for the packaged Isaac launcher."""

from simready.execution.workers.isaac_launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
