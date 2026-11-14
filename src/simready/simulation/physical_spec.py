"""Compatibility alias for :mod:`simready.assets.physics.physical_spec`.

This module owns no implementation. Sharing the canonical module object keeps
legacy monkeypatches and imported functions on the same module globals.
"""

import sys

from simready.assets.physics import physical_spec as _implementation

sys.modules[__name__] = _implementation
