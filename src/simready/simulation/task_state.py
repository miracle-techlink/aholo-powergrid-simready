"""Feedback-driven task sequencing, independent of simulators and robot SDKs.

Observations must come from an adapter, not from a task's desired state.
This software supervisor is not a certified emergency-stop controller.
"""

import math
from dataclasses import asdict, dataclass
from enum import Enum


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


class Phase(str, Enum):
    APPROACH = "approach"
    SETTLE = "settle"
    REACH = "reach"
    PRESS = "press"
    RETRACT = "retract"
    SUCCEEDED = "succeeded"
    FAULT = "fault"


@dataclass(frozen=True)
class Observation:
    sequence: int
    timestamp: float  # adapter's monotonic clock, seconds
    at_dock: bool = False
    stable: bool = False
    arm_at_prepress: bool = False
    contact: bool = False
    button_travel_m: float = 0.0
    button_on: bool = False
    retracted: bool = False
    fell: bool = False
    penetration: bool = False
    emergency_stop: bool = False


@dataclass(frozen=True)
class MissionConfig:
    observation_timeout_s: float = 0.25
    phase_timeout_s: float = 30.0
    stable_dwell_s: float = 0.5
    press_dwell_s: float = 0.10
    press_threshold_m: float = 0.005
    max_button_travel_m: float = 0.03

    def __post_init__(self):
        if any(not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("Mission thresholds must be finite and positive")
        if self.max_button_travel_m <= self.press_threshold_m:
            raise ValueError("Maximum travel must exceed press threshold")


class Mission:
    """One mission per instance; faults latch and require a new session to reset."""

    def __init__(self, config: MissionConfig, start_time: float):
        if not finite_number(start_time):
            raise ValueError("Invalid monotonic start time")
        self.config = config
        self.phase = Phase.APPROACH
        self.phase_started = start_time
        self.last_now = start_time
        self.last_sequence = -1
        self.last_timestamp = None
        self.stable_since = None
        self.press_since = None
        self.press_contact_observed = False
        self.button_state_changed = False
        self.initial_button_on = None
        self.reason = None
        self.transitions = []

    def _transition(self, phase, now, reason=None):
        self.transitions.append(
            dict(
                timestamp=now,
                previous=self.phase.value,
                phase=phase.value,
                reason=reason,
            )
        )
        self.phase, self.phase_started = phase, now
        self.reason = reason

    def fault(self, now, reason):
        if self.phase != Phase.FAULT:
            self._transition(Phase.FAULT, now, reason)
        return self.phase

    def tick(self, obs: Observation | None, now: float):
        if self.phase == Phase.FAULT:
            return self.phase
        if not finite_number(now) or now < self.last_now:
            return self.fault(self.last_now, "clock_invalid")
        self.last_now = now
        if obs is None:
            last = (
                self.last_timestamp
                if self.last_timestamp is not None
                else self.phase_started
            )
            if now - last > self.config.observation_timeout_s:
                return self.fault(now, "observation_timeout")
            return self.phase
        if (
            type(obs.sequence) is not int
            or obs.sequence <= self.last_sequence
            or not finite_number(obs.timestamp)
            or obs.timestamp > now
            or now - obs.timestamp > self.config.observation_timeout_s
            or (
                self.last_timestamp is not None
                and (
                    obs.timestamp <= self.last_timestamp
                    or obs.timestamp - self.last_timestamp
                    > self.config.observation_timeout_s
                )
            )
        ):
            return self.fault(now, "observation_invalid_or_stale")
        bools = (
            obs.at_dock,
            obs.stable,
            obs.arm_at_prepress,
            obs.contact,
            obs.button_on,
            obs.retracted,
            obs.fell,
            obs.penetration,
            obs.emergency_stop,
        )
        if any(type(v) is not bool for v in bools):
            return self.fault(now, "observation_boolean_invalid")
        if (
            not finite_number(obs.button_travel_m)
            or obs.button_travel_m < -1e-4
            or obs.button_travel_m > self.config.max_button_travel_m
        ):
            return self.fault(now, "button_travel_invalid")
        self.last_sequence, self.last_timestamp = obs.sequence, obs.timestamp
        if obs.emergency_stop or obs.fell or obs.penetration:
            return self.fault(
                now,
                "emergency_stop"
                if obs.emergency_stop
                else "fall"
                if obs.fell
                else "penetration",
            )
        if self.phase == Phase.SUCCEEDED:
            return self.phase
        if self.initial_button_on is None:
            self.initial_button_on = obs.button_on
        if now - self.phase_started > self.config.phase_timeout_s:
            return self.fault(now, "phase_timeout:" + self.phase.value)
        if self.phase in (Phase.REACH, Phase.PRESS, Phase.RETRACT) and not (
            obs.at_dock and obs.stable
        ):
            return self.fault(now, "base_unstable_during_manipulation")
        if (
            self.phase in (Phase.APPROACH, Phase.SETTLE, Phase.REACH)
            and obs.button_on != self.initial_button_on
        ):
            return self.fault(now, "button_changed_before_press")
        if self.phase == Phase.APPROACH:
            if obs.at_dock:
                self._transition(Phase.SETTLE, now)
        elif self.phase == Phase.SETTLE:
            if not obs.at_dock:
                self.stable_since = None
                self._transition(Phase.APPROACH, now)
            elif not obs.stable:
                self.stable_since = None
            else:
                if self.stable_since is None:
                    self.stable_since = obs.timestamp
                if obs.timestamp - self.stable_since >= self.config.stable_dwell_s:
                    self._transition(Phase.REACH, now)
        elif self.phase == Phase.REACH:
            if obs.arm_at_prepress:
                self._transition(Phase.PRESS, now)
        elif self.phase == Phase.PRESS:
            if obs.button_on != self.initial_button_on and not (
                obs.contact and obs.button_travel_m >= self.config.press_threshold_m
            ):
                return self.fault(now, "button_changed_without_valid_press")
            valid_press = (
                obs.contact
                and obs.button_travel_m >= self.config.press_threshold_m
                and obs.button_on != self.initial_button_on
            )
            if valid_press:
                if self.press_since is None:
                    self.press_since = obs.timestamp
                if obs.timestamp - self.press_since >= self.config.press_dwell_s:
                    self.press_contact_observed = True
                    self.button_state_changed = True
                    self._transition(Phase.RETRACT, now)
            else:
                self.press_since = None
        elif self.phase == Phase.RETRACT and obs.retracted:
            if obs.button_on == self.initial_button_on:
                return self.fault(now, "button_state_reverted")
            self._transition(Phase.SUCCEEDED, now)
        return self.phase

    def result(self):
        return dict(
            phase=self.phase.value,
            success=self.phase == Phase.SUCCEEDED,
            reason=self.reason,
            press_contact_observed=self.press_contact_observed,
            button_state_changed=self.button_state_changed,
            transitions=self.transitions,
        )
