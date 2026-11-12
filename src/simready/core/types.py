"""Boundary types; external JSON is validated by its owning domain schema."""

from os import PathLike as OsPathLike
from typing import Any, TypeAlias

PathLike: TypeAlias = str | OsPathLike[str]
JsonObject: TypeAlias = dict[str, Any]
