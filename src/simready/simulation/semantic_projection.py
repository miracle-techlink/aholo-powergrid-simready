"""Compatibility alias for :mod:`simready.assets.association.semantic_projection`.

This module owns no implementation. Sharing the canonical module object keeps
legacy monkeypatches and imported functions on the same module globals.
"""

import sys

from simready.assets.association import semantic_projection as _implementation

sys.modules[__name__] = _implementation
