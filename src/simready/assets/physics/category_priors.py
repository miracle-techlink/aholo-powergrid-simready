"""Versioned simulation contact presets, never visual material measurements."""

from copy import deepcopy
from typing import Any

PRIOR_VERSION = "category-contact-presets-v1"

# These intentionally broad hypotheses are useful for testing assignment and
# sensitivity. Category alone does not identify surface finish or a contact pair.
_GROUPS = (
    (("floor", "wall", "ceiling"), (0.8, 0.6, 0.0)),
    (("cabinet", "door", "appliance", "lamp", "button", "handle"), (0.5, 0.4, 0.05)),
    (("table", "chair", "shelf", "bed", "countertop"), (0.6, 0.45, 0.05)),
    (("window", "sink", "toilet"), (0.4, 0.3, 0.05)),
    (("sofa",), (0.9, 0.7, 0.05)),
)


def category_priors() -> dict[str, dict[str, Any]]:
    """Return independent, editable static hypotheses with SI provenance.

    No mass, density, stiffness, material identity or actual movability is
    inferred. Unknown classes have no fallback: callers must skip or supply a
    documented preset. Intervals are exploration ranges, not confidence bounds.
    """
    values = {}
    for categories, numbers in _GROUPS:
        contact = dict(
            zip(
                ("static_friction", "dynamic_friction", "restitution"),
                numbers,
                strict=True,
            )
        )
        for category in categories:
            values[category] = {
                "motion_type": "static",
                "contact": dict(contact),
                "provenance": {
                    f"contact.{key}": {
                        "source_kind": "category_prior",
                        "source": f"{PRIOR_VERSION}/{category}; unmeasured simulation preset",
                        "unit": "1",
                        "interval": [0.0, 1.0 if key == "restitution" else 1.5],
                    }
                    for key in contact
                },
            }
    return deepcopy(values)
