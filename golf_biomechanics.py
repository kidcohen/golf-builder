"""Body movement analysis: how the golfer's motion produces the club delivery.

This is the first link in the chain. The body moves the club, the club strikes
the ball, and the ball flies:

    golf_biomechanics  ->  golf_impact  ->  golf_physics
    body movement          club delivery     ball flight

The models here are the standard ones used in golf exercise science, applied
to whatever a 3D capture system, pressure plate or wearable sensor reports.

KINEMATIC SEQUENCE
    An efficient swing accelerates from the ground up: pelvis first, then
    thorax, then lead arm, then club, each peaking after the one before it.
    Sequence breaks - an arm peaking before the thorax, say - are the classic
    signature of a swing that is fast but leaks speed.

SPEED GAIN
    Each segment should be meaningfully faster than the one below it. The
    ratios say how well energy is being handed up the chain.

X-FACTOR
    Separation between thorax and pelvis rotation at the top, and how much it
    increases as the downswing starts - the stretch. It loads the trunk
    musculature; too little is a loss of power, and a great deal of it without
    the mobility to support it is a common injury pattern.

GROUND REACTION
    Vertical force and where pressure sits under the feet. Force pushed into
    the ground early, and pressure moved to the lead side before impact, is
    what the sequence above is built on.

POSTURE
    Early extension, loss of side bend and sway are the compensations that
    show up as inconsistent strike location, which is why they connect
    directly to the impact model's gear-effect terms.

NORMS, honestly stated
    The reference bands below are published tour and low-handicap averages.
    They are named constants so a coach or a golfer with their own baseline
    can replace them. Nothing here is a medical or diagnostic instrument: it
    describes movement, it does not assess injury.
"""

from dataclasses import dataclass

# --- Kinematic sequence --------------------------------------------------
# Peak angular speeds, degrees per second, for a full-speed driver swing.
SEQUENCE_SEGMENTS = ("pelvis", "thorax", "lead_arm", "club")
TYPICAL_PEAK_SPEED_DPS = {
    "pelvis": 550.0,
    "thorax": 800.0,
    "lead_arm": 1250.0,
    "club": 2400.0,
}
# Each segment should peak this long after the one below it.
TYPICAL_SEQUENCE_GAP_MS = 25.0
MIN_HEALTHY_SEQUENCE_GAP_MS = 10.0

# Each segment should be at least this much faster than the one below it.
MIN_SPEED_GAIN_RATIO = 1.15

# --- X-factor ------------------------------------------------------------
TYPICAL_X_FACTOR_DEG = 50.0
TYPICAL_X_FACTOR_STRETCH_DEG = 5.0

# --- Tempo ---------------------------------------------------------------
TYPICAL_TEMPO_RATIO = 3.0  # backswing time to downswing time

# --- Ground reaction -----------------------------------------------------
TYPICAL_PEAK_VERTICAL_FORCE_BW = 1.7  # multiples of body weight
TYPICAL_LEAD_PRESSURE_AT_IMPACT_PCT = 85.0

# --- Posture -------------------------------------------------------------
EARLY_EXTENSION_THRESHOLD_MM = 40.0  # pelvis moving toward the ball in transition
EXCESS_SWAY_MM = 60.0
MIN_SIDE_BEND_AT_IMPACT_DEG = 20.0


@dataclass(frozen=True)
class SegmentPeak:
    name: str
    speed_dps: float
    time_ms: float  # relative to impact; negative is before impact


def segment_peaks(body_metrics):
    """Pull the reported segment peaks into order of when they occurred."""
    peaks = []
    for segment in SEQUENCE_SEGMENTS:
        speed = body_metrics.get(f"{segment}_peak_speed_dps")
        time_ms = body_metrics.get(f"{segment}_peak_time_ms")
        if speed is None or time_ms is None:
            continue
        peaks.append(SegmentPeak(segment, speed, time_ms))
    return sorted(peaks, key=lambda peak: peak.time_ms)


def kinematic_sequence(body_metrics):
    """Did the swing accelerate from the ground up, and by how much?"""
    peaks = segment_peaks(body_metrics)
    if len(peaks) < 2:
        return {}

    order = [peak.name for peak in peaks]
    expected = [s for s in SEQUENCE_SEGMENTS if s in order]

    gaps = {
        f"{later.name}_after_{earlier.name}_ms": later.time_ms - earlier.time_ms
        for earlier, later in zip(peaks, peaks[1:])
    }
    tight = [name for name, gap in gaps.items() if abs(gap) < MIN_HEALTHY_SEQUENCE_GAP_MS]

    return {
        "sequence_order": order,
        "is_proximal_to_distal": order == expected,
        "sequence_gaps_ms": gaps,
        "segments_peaking_together": tight,
    }


def speed_gain(body_metrics):
    """How much faster each segment is than the one below it."""
    speeds = {
        segment: body_metrics.get(f"{segment}_peak_speed_dps")
        for segment in SEQUENCE_SEGMENTS
    }
    reported = [s for s in SEQUENCE_SEGMENTS if speeds.get(s)]

    ratios = {}
    weak = []
    for lower, upper in zip(reported, reported[1:]):
        ratio = speeds[upper] / speeds[lower]
        ratios[f"{upper}_over_{lower}"] = ratio
        if ratio < MIN_SPEED_GAIN_RATIO:
            weak.append(f"{upper}_over_{lower}")

    return {"speed_gain_ratios": ratios, "weak_transfers": weak} if ratios else {}


def x_factor(body_metrics):
    """Trunk separation at the top, and how much it stretches in transition."""
    pelvis = body_metrics.get("pelvis_rotation_at_top_deg")
    thorax = body_metrics.get("thorax_rotation_at_top_deg")
    readings = {}

    if pelvis is not None and thorax is not None:
        separation = thorax - pelvis
        readings["x_factor_deg"] = separation
        readings["x_factor_vs_typical"] = separation / TYPICAL_X_FACTOR_DEG

    stretch = body_metrics.get("x_factor_stretch_deg")
    if stretch is not None:
        readings["x_factor_stretch_deg"] = stretch
        readings["stretches_in_transition"] = stretch > 0

    return readings


def tempo(body_metrics):
    """Backswing time against downswing time. The classic ratio is about 3:1."""
    backswing = body_metrics.get("backswing_time_ms")
    downswing = body_metrics.get("downswing_time_ms")
    if not backswing or not downswing:
        return {}
    ratio = backswing / downswing
    return {
        "tempo_ratio": ratio,
        "tempo_vs_typical": ratio / TYPICAL_TEMPO_RATIO,
        "total_swing_time_ms": backswing + downswing,
    }


def ground_reaction(body_metrics):
    """What the golfer pushed into the ground, and where their pressure went."""
    readings = {}

    peak_force = body_metrics.get("peak_vertical_force_bw")
    if peak_force is not None:
        readings["peak_vertical_force_bw"] = peak_force
        readings["force_vs_typical"] = peak_force / TYPICAL_PEAK_VERTICAL_FORCE_BW

    lead_pressure = body_metrics.get("lead_pressure_at_impact_pct")
    if lead_pressure is not None:
        readings["lead_pressure_at_impact_pct"] = lead_pressure
        readings["pressure_shifted_to_lead_side"] = (
            lead_pressure >= TYPICAL_LEAD_PRESSURE_AT_IMPACT_PCT * 0.8
        )

    peak_force_time = body_metrics.get("peak_vertical_force_time_ms")
    if peak_force_time is not None:
        # Force should peak before impact, not at it - late force is speed
        # that never reached the club.
        readings["peak_force_time_ms"] = peak_force_time
        readings["force_peaks_before_impact"] = peak_force_time < 0

    return readings


def posture(body_metrics):
    """Compensations that show up as inconsistent strike."""
    readings = {}

    thrust = body_metrics.get("pelvis_thrust_mm")
    if thrust is not None:
        readings["pelvis_thrust_mm"] = thrust
        readings["early_extension"] = thrust > EARLY_EXTENSION_THRESHOLD_MM

    sway = body_metrics.get("pelvis_sway_mm")
    if sway is not None:
        readings["pelvis_sway_mm"] = sway
        readings["excessive_sway"] = abs(sway) > EXCESS_SWAY_MM

    side_bend = body_metrics.get("side_bend_at_impact_deg")
    if side_bend is not None:
        readings["side_bend_at_impact_deg"] = side_bend
        readings["loses_side_bend"] = side_bend < MIN_SIDE_BEND_AT_IMPACT_DEG

    head_movement = body_metrics.get("head_movement_mm")
    if head_movement is not None:
        readings["head_movement_mm"] = head_movement

    return readings


def analyse_body(body_metrics):
    """Every reading the reported body movement supports.

    Returns an empty dict when nothing was reported, so a golfer without body
    tracking is unaffected.
    """
    if not body_metrics:
        return {}

    readings = {}
    for part in (kinematic_sequence, speed_gain, x_factor, tempo,
                 ground_reaction, posture):
        readings.update(part(body_metrics))
    return readings


def describe_body(body_metrics):
    """Plain-language notes on the movement, in the style of the shot feedback."""
    readings = analyse_body(body_metrics)
    if not readings:
        return []

    notes = ["--- Body Movement ---"]

    order = readings.get("sequence_order")
    if order:
        if readings.get("is_proximal_to_distal"):
            notes.append(f"  Sequence: {' -> '.join(order)} (ground up, as it should be)")
        else:
            notes.append(f"  Sequence: {' -> '.join(order)} - out of order.")
            notes.append("    Speed is leaking: a segment is peaking before the one below it.")
    for name in readings.get("segments_peaking_together", []):
        notes.append(f"    {name.replace('_', ' ')} is unusually tight; little separation there.")

    for name in readings.get("weak_transfers", []):
        upper, lower = name.split("_over_")
        notes.append(f"  Weak transfer from {lower} to {upper}: little speed is being gained.")

    if "x_factor_deg" in readings:
        separation = readings["x_factor_deg"]
        notes.append(f"  X-factor: {separation:.0f}deg at the top (typical {TYPICAL_X_FACTOR_DEG:.0f}).")
        if readings.get("stretches_in_transition") is False:
            notes.append("    No stretch in transition; the trunk is not being loaded.")

    if "tempo_ratio" in readings:
        notes.append(
            f"  Tempo: {readings['tempo_ratio']:.1f}:1 backswing to downswing "
            f"(classic {TYPICAL_TEMPO_RATIO:.0f}:1)."
        )

    if readings.get("force_peaks_before_impact") is False:
        notes.append("  Ground force peaks at or after impact; it is arriving too late to help.")
    if readings.get("pressure_shifted_to_lead_side") is False:
        notes.append("  Pressure is still on the trail side at impact.")

    if readings.get("early_extension"):
        notes.append(f"  Early extension: pelvis moves {readings['pelvis_thrust_mm']:.0f}mm toward the ball.")
    if readings.get("excessive_sway"):
        notes.append(f"  Sway: {readings['pelvis_sway_mm']:.0f}mm of lateral movement.")
    if readings.get("loses_side_bend"):
        notes.append("  Side bend is lost through impact, which tends to move the strike around the face.")

    return notes
