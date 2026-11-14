"""Historical Gaussian model, detection and repair algorithms with CPU regressions.

The retired execution loop is available through the commit linked in legacy/README.md.
"""

__version__ = "1.0.0"

from .model_io import GaussianModel, load_model, save_model, model_digest  # noqa: F401
