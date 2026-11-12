"""Measured-contact button state adapter; independent of Isaac and controllers.

The adapter never moves a button or robot. Feed joint-parent-frame displacement
and force from the configured contact pair, not desired positions or a timeline.
Press/release rules match the physically verified 2026-11-11 fixture. Fault/reset
transitions remain declarations until a separate event source is implemented.
"""

import math
from copy import deepcopy
from dataclasses import dataclass


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class ButtonParameters:
    sample_dt_s: float
    max_travel_m: float
    press_depth_m: float
    release_depth_m: float
    press_force_N: float
    release_force_N: float
    debounce_s: float = 0.04
    release_debounce_s: float = 0.04

    def __post_init__(self):
        if any(not finite(v) or v < 0 for v in vars(self).values()):
            raise ValueError("Button parameters must be finite and nonnegative")
        if not (
            0 < self.sample_dt_s <= self.debounce_s
            and self.sample_dt_s <= self.release_debounce_s
        ):
            raise ValueError("Invalid sampling/debounce interval")
        if not 0 <= self.release_depth_m < self.press_depth_m < self.max_travel_m:
            raise ValueError("Require release < press < maximum travel")
        if not 0 <= self.release_force_N < self.press_force_N:
            raise ValueError("Require release force < press force")

    def mission_config(self, **overrides):
        """Use this component's travel contract instead of unrelated defaults."""
        from simready.simulation.task_state import MissionConfig

        config = MissionConfig(
            **{
                "press_threshold_m": self.press_depth_m,
                "max_button_travel_m": self.max_travel_m,
                **overrides,
            }
        )
        self.check_mission_config(config)
        return config

    def check_mission_config(self, config):
        if config.max_button_travel_m < self.max_travel_m:
            raise ValueError(
                "Mission maximum travel is smaller than the button joint travel"
            )
        if config.press_threshold_m > self.press_depth_m:
            raise ValueError(
                "Mission press threshold exceeds the component trigger threshold"
            )


@dataclass(frozen=True)
class ButtonSample:
    sequence: int
    timestamp_s: float
    button_prim: str
    counterpart_prim: str
    depth_m: float
    contact_force_N: float


class ContactButton:
    def __init__(
        self, asset_key, definition, parameters, button_prim, counterpart_prim
    ):
        if not isinstance(asset_key, str) or not asset_key:
            raise ValueError("A source-bound asset key is required")
        if not all(
            isinstance(p, str) and p.startswith("/")
            for p in (button_prim, counterpart_prim)
        ):
            raise ValueError("Contact pair paths must be absolute")
        if button_prim == counterpart_prim:
            raise ValueError("Button and counterpart must be different bodies")
        self.asset_key, self.parameters = asset_key, parameters
        self.button_prim, self.counterpart_prim = button_prim, counterpart_prim
        self.definition = deepcopy(definition)
        states = self.definition["states"]
        self.state = self.definition["initial"]
        if self.state not in states:
            raise ValueError("Initial state is undefined")
        for key, value in self.definition["transitions"].items():
            edge = key.split("->")
            if (
                len(edge) != 2
                or any(x not in states for x in edge)
                or not isinstance(value["action"], str)
            ):
                raise ValueError("Invalid semantic transition")
        for state in states:
            feedback = self.definition["visual_feedback"][state]
            color = feedback["color"]
            if len(color) != 3 or any(not finite(x) or not 0 <= x <= 1 for x in color):
                raise ValueError("Invalid state color")
            if not finite(feedback["emission"]) or feedback["emission"] < 0:
                raise ValueError("Invalid emission command")
            presses = [
                k
                for k, v in self.definition["transitions"].items()
                if k.split("->")[0] == state and v["action"] == "press"
            ]
            if len(presses) > 1:
                raise ValueError("Ambiguous press transition")
        self.armed = True
        self.press_count = self.release_count = 0
        self.press_steps = math.ceil(parameters.debounce_s / parameters.sample_dt_s)
        self.release_steps = math.ceil(
            parameters.release_debounce_s / parameters.sample_dt_s
        )
        self.last_sequence = -1
        self.last_timestamp = None

    def observe(self, sample):
        p = self.parameters
        if (
            type(sample.sequence) is not int
            or sample.sequence <= self.last_sequence
            or not finite(sample.timestamp_s)
            or sample.timestamp_s < 0
            or (
                self.last_timestamp is not None
                and sample.timestamp_s <= self.last_timestamp
            )
        ):
            raise ValueError("Duplicate, stale or invalid button sample")
        if (sample.button_prim, sample.counterpart_prim) != (
            self.button_prim,
            self.counterpart_prim,
        ):
            raise ValueError("Contact sample belongs to a different bound body pair")
        if (
            not finite(sample.depth_m)
            or not -1e-4 <= sample.depth_m <= p.max_travel_m + 1e-4
        ):
            raise ValueError(
                "Measured displacement exceeds the component travel contract"
            )
        if not finite(sample.contact_force_N) or sample.contact_force_N < 0:
            raise ValueError("Invalid measured contact force magnitude")
        if self.last_timestamp is not None:
            gap = sample.timestamp_s - self.last_timestamp
            if gap < p.sample_dt_s * 0.5:
                raise ValueError(
                    "Samples are faster than the declared physics sampling interval"
                )
            if sample.sequence != self.last_sequence + 1 or gap > p.sample_dt_s * 1.5:
                # Missing observations cannot count as continuous contact/release.
                self.press_count = self.release_count = 0
        event, rearmed = None, False
        if self.armed:
            self.press_count = (
                self.press_count + 1
                if (
                    sample.depth_m >= p.press_depth_m
                    and sample.contact_force_N >= p.press_force_N
                )
                else 0
            )
            if self.press_count >= self.press_steps:
                edges = [
                    k
                    for k, v in self.definition["transitions"].items()
                    if k.split("->")[0] == self.state and v["action"] == "press"
                ]
                if len(edges) != 1:
                    raise ValueError(
                        "No measured-press transition exists in the current state"
                    )
                previous, self.state = self.state, edges[0].split("->")[1]
                self.armed = False
                self.press_count = self.release_count = 0
                event = dict(
                    asset_key=self.asset_key,
                    sequence=sample.sequence,
                    t=sample.timestamp_s,
                    event=previous + "->" + self.state,
                    action="press",
                    cause="measured_contact_and_button_displacement",
                    press_depth_m=sample.depth_m,
                    contact_force_N=sample.contact_force_N,
                )
        else:
            self.release_count = (
                self.release_count + 1
                if (
                    sample.depth_m <= p.release_depth_m
                    and sample.contact_force_N <= p.release_force_N
                )
                else 0
            )
            if self.release_count >= self.release_steps:
                self.armed = True
                self.release_count = 0
                rearmed = True
        self.last_sequence, self.last_timestamp = sample.sequence, sample.timestamp_s
        return dict(
            state=self.state,
            armed=self.armed,
            event=event,
            rearmed=rearmed,
            binding=dict(
                asset_key=self.asset_key,
                button_prim=self.button_prim,
                counterpart_prim=self.counterpart_prim,
            ),
            press_count=self.press_count,
            release_count=self.release_count,
            feedback=deepcopy(self.definition["visual_feedback"][self.state]),
            # These three fields can populate Mission Observation. Docking,
            # base stability and arm reach/retraction need separate sensors.
            observation_fields=dict(
                contact=sample.contact_force_N >= p.press_force_N,
                button_travel_m=max(0.0, sample.depth_m),
                button_on=self.state == "on",
            ),
        )


def apply_usd_feedback(stage, button_prim, cap_prim, result):
    """Update only existing semantic/color attributes; never author body poses."""
    from pxr import Gf

    if result["binding"]["button_prim"] != button_prim or not cap_prim.startswith(
        button_prim + "/"
    ):
        raise ValueError("Feedback target does not match the observed button binding")
    button, cap = stage.GetPrimAtPath(button_prim), stage.GetPrimAtPath(cap_prim)
    if not button or not cap:
        raise ValueError("Missing button feedback prim")
    state = button.GetAttribute("substation:current_state")
    color = cap.GetAttribute("primvars:displayColor")
    emission = button.GetAttribute("substation:emission_command")
    if not state or not color or not emission:
        raise ValueError("Missing declared button feedback attributes")
    state.Set(result["state"])
    color.Set([Gf.Vec3f(*result["feedback"]["color"])])
    # This is a command attribute. An emitting material must bind it explicitly;
    # authoring this attribute alone does not prove visible emission.
    emission.Set(result["feedback"]["emission"])
    return dict(
        state=state.Get(), color=list(color.Get()[0]), emission_command=emission.Get()
    )
