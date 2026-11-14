"""Compatibility alias for :mod:`simready.geometry.depth_projection`.

This module owns no implementation. Sharing the canonical module object keeps
legacy monkeypatches and imported functions on the same module globals.
"""

import sys

from simready.geometry import depth_projection as _implementation

sys.modules[__name__] = _implementation
