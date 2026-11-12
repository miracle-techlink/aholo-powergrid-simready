"""Compatibility entry point; use python -m simready_bridge.ingest."""

from simready_bridge.ingest.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
