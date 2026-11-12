"""Compatibility entry point; use python -m simready.ingestion."""

from simready.ingestion.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
