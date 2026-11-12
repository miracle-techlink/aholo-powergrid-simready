"""Module entry point; prefer simready ingestion."""

from simready.ingestion.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
