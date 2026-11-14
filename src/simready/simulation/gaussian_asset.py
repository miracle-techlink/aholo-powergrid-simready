"""Compatibility alias for :mod:`simready.assets.appearance.gaussian_asset`.

This module owns no implementation. Sharing the canonical module object keeps
legacy monkeypatches and imported functions on the same module globals.
"""

import sys

from simready.assets.appearance import gaussian_asset as _implementation

sys.modules[__name__] = _implementation
