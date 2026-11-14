"""Compatibility alias for :mod:`simready.assets.compilation.scene_preparation`.

This module owns no implementation. Sharing the canonical module object keeps
legacy monkeypatches and imported functions on the same module globals.
"""

import sys

from simready.assets.compilation import scene_preparation as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation
