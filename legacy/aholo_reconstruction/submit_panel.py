"""Compatibility entry point; use simready ingestion aholo instead."""

from simready.ingestion.aholo_reconstruction import main, run

__all__ = ["main", "run"]

if __name__ == "__main__":
    raise SystemExit(main())
