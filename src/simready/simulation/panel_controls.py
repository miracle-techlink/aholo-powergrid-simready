"""Manifest-bound press and latch adapter, with explicit reset and no pose writes.

The inherited off/on signal is a diagnostic counter. It does not represent a
real switchgear command. Consumers use ``latched`` separately for the physical
hold state, and supply measured displacement/paired contact samples.
"""

from simready.simulation.button import ButtonParameters, ContactButton, finite


class PanelControl:
    def __init__(self, spec, counterpart_prim, write_joint_target):
        self.spec = spec
        self.button = ContactButton(
            spec["id"],
            spec["semantic_definition"],
            ButtonParameters(
                0.002,
                spec["stroke_m"],
                spec["press_depth_m"],
                spec["release_depth_m"],
                spec["press_force_N"],
                spec["release_force_N"],
            ),
            spec["cap_prim"],
            counterpart_prim,
        )
        self.write_joint_target = write_joint_target
        self.latched = False
        self.last_sample = None

    def observe(self, sample):
        result = self.button.observe(sample)
        self.last_sample = sample
        if result["event"] and self.spec["requires_latch"]:
            target = self.spec["side"] * self.spec["stroke_m"] * 0.85
            self.write_joint_target(target)
            self.latched = True
        result["latched"] = self.latched
        result["device_function_verified"] = False
        return result

    def reset_latch(self):
        if not self.spec["requires_latch"]:
            raise ValueError("This control does not implement a latch")
        if not self.latched:
            return False
        if (
            self.last_sample is None
            or self.last_sample.contact_force_N > self.spec["release_force_N"]
        ):
            raise ValueError("Withdraw the contact counterpart before requesting reset")
        # A reset commands the spring rest target; the physics solver must
        # still return the cap and the contact adapter must observe release.
        self.write_joint_target(0.0)
        self.latched = False
        return True


class RotaryControl:
    """Joint-angle/contact events in the authored Y-axis convention.

    This observes a physical handle; it never writes a pose, torque, or device
    command. Positive/negative describe joint angle, not electrical meaning.
    """

    def __init__(self, asset_key, counterpart_prim, dt=0.002):
        self.asset_key = asset_key
        self.counterpart_prim = counterpart_prim
        self.dt = dt
        self.state = "neutral"
        self.last_sequence = -1
        self.last_time = None
        self.count = 0
        self.candidate = None

    def observe(self, sequence, timestamp_s, angle_deg, contact_N, counterpart_prim):
        if counterpart_prim != self.counterpart_prim:
            raise ValueError("Wrong contact counterpart")
        if (
            not all(finite(x) for x in [timestamp_s, angle_deg, contact_N])
            or contact_N < 0
            or abs(angle_deg) > 91.0
        ):
            raise ValueError("Invalid measured rotary sample")
        if (
            type(sequence) is not int
            or sequence <= self.last_sequence
            or (self.last_time is not None and timestamp_s <= self.last_time)
        ):
            raise ValueError("Stale rotary sample")
        if self.last_time is not None and (
            sequence != self.last_sequence + 1
            or abs(timestamp_s - self.last_time - self.dt) > self.dt * 0.5
        ):
            self.count = 0
            self.candidate = None
        self.last_sequence = sequence
        self.last_time = timestamp_s
        target = None
        if self.state == "neutral" and contact_N > 0.01 and abs(angle_deg) > 30.0:
            target = "positive" if angle_deg > 0 else "negative"
        elif self.state != "neutral" and abs(angle_deg) < 5.0 and contact_N < 0.01:
            target = "neutral"
        if target != self.candidate:
            self.count = 0
            self.candidate = target
        self.count = self.count + 1 if target else 0
        event = None
        if target and self.count * self.dt >= 0.04:
            previous = self.state
            self.state = target
            self.count = 0
            self.candidate = None
            event = dict(
                asset_key=self.asset_key,
                sequence=sequence,
                t=timestamp_s,
                event=previous + "->" + target,
                cause="measured_joint_angle_and_paired_contact",
                angle_deg=angle_deg,
                contact_N=contact_N,
            )
        return dict(state=self.state, event=event, device_function_verified=False)
