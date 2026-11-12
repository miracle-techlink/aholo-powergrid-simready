"""Repository fixtures are explicit test inputs, never runtime imports."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
