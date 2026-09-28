"""Impact model: how the club was delivered determines how the ball leaves.

Used when a golfer's equipment reports club delivery but not ball launch. When
the monitor reports both, the measured numbers win - no model beats
measurement.

THE METRIC SET
--------------
Club delivery, as a launch monitor reports it:

    club_speed_mph            how fast the head is moving
    attack_angle_deg          + is ascending
    dynamic_loft_deg          loft actually presented at impact
    swing_direction_deg       direction of the swing plane, + is right
    club_path_deg             path of the head through impact, + is in-to-out
    face_angle_deg            where the face points, absolute, + is open
    face_angle_to_path_deg    the same thing relative to the path
    closure_rate_deg_per_sec  how fast the face is rotating through impact
    impact_offset_toe_in      + is toward the toe
    impact_offset_high_in     + is above centre
    dynamic_lie_deg           lie angle at impact, + is toe-down

Ball launch, either measured or produced here:

    ball_speed_mph  launch_angle_deg  launch_direction_deg
    back_spin_rpm  side_spin_rpm  rifle_spin_rpm  spin_axis_deg

Swing direction and club path are related but not the same: path is the head's
travel, swing direction is the plane it travels on, and a descending or
ascending blow tilts one relative to the other. Monitors report both, so both
are accepted; either can stand in for the other when only one arrives.

Spin axis is accepted as an input, because that is what monitors report.
Sidespin is a derived convenience figure, not a measurement.

CALIBRATION, honestly stated
----------------------------
Ball speed is calibrated: the smash-to-spin-loft relationship was fitted
through tour reference points for driver, 7-iron and pitching wedge and lands
within 3% across the bag.

Launch angle and spin rate are NOT calibrated. They depend on dynamic loft per
club, for which reliable published values were not available, and comparison
against tour ball data showed launch running high and driver spin badly high
while iron spin was close. A driver genuinely spins less per degree of spin
loft than an iron does, so any honest model needs a per-club term.

That term is deliberately left as data rather than a guess: every shot now
stores club delivery alongside measured ball launch, so these can be fitted to
the individual golfer from their own history. Until they are, anything marked
provisional below should be treated as a placeholder.
"""

import math
from collections import defaultdict

# --- Smash factor - CALIBRATED -------------------------------------------
SMASH_AT_ZERO_SPIN_LOFT = 1.598
SMASH_LOST_PER_DEGREE_SPIN_LOFT = 0.00859
MAX_SMASH_FACTOR = 1.50  # a conforming face cannot beat this
MIN_SMASH_FACTOR = 0.60
SMASH_LOST_PER_SQUARE_INCH_OFF_CENTRE = 0.25  # half an inch costs about 6%

# --- Launch --------------------------------------------------------------
LAUNCH_FRACTION_OF_DYNAMIC_LOFT = 0.82
# A strike above centre launches higher, the same gear effect that takes spin
# off it.
LAUNCH_PER_INCH_HIGH = 4.0
START_LINE_FACE_WEIGHT = 0.85  # the ball starts close to where the face points

# --- Spin ----------------------------------------------------------------
BACK_SPIN_PER_MPH = 160.0
MAX_BACK_SPIN_RPM = 14000.0
BACK_SPIN_PER_INCH_HIGH = -900.0  # a high strike spins less

# --- Club properties -----------------------------------------------------
# Clubs are not just lofts on a stick. A driver has a deep, low centre of
# gravity, a large curved face and shallow grooves; a wedge has a small flat
# face, sharp grooves and its mass close behind the ball. So the same spin
# loft produces very different spin and launch depending on what is being
# swung, and that is physics, not bookkeeping.
#
# The correction is sampled at the delivered loft of each tour reference shot
# and interpolated between them. Delivered loft is a measurement, so nothing
# has to be named, typed or bucketed - and because it interpolates rather than
# steps, two near-identical deliveries can never fall either side of a line
# and get different answers.
SPIN_CORRECTION_BY_LOFT = (
    (13.5, 0.582),
    (16.0, 0.659),
    (22.0, 0.840),
    (26.0, 0.977),
    (33.0, 1.057),
    (38.0, 1.027),
)
LAUNCH_CORRECTION_BY_LOFT = (
    (13.5, 1.000),
    (16.0, 0.730),
    (22.0, 0.820),
    (26.0, 0.800),
    (33.0, 0.780),
    (38.0, 0.800),
)
SPIN_AXIS_PER_DEGREE_FACE_TO_PATH = 3.6
SPIN_AXIS_PER_INCH_TOE = -22.0  # gear effect: a toe strike draws
MAX_SPIN_AXIS_DEG = 60.0
RIFLE_SPIN_PER_DEGREE_DYNAMIC_LIE = 30.0

# Below this many shots, a fitted trim is noise rather than a fit.
MIN_SHOTS_TO_FIT = 5
NEUTRAL_TRIM = 1.0

# --- Swing plane ---------------------------------------------------------
# How far club path shifts from swing direction per degree of attack angle.
PATH_SHIFT_PER_DEGREE_ATTACK = 0.67

# --- Closure rate --------------------------------------------------------
# The face keeps rotating while the ball is on it. Contact lasts under half a
# millisecond, so the effect on face angle is real but small - a fast 400
# deg/sec closure moves it under a fifth of a degree. Its larger role is
# diagnostic, and it amplifies gear effect on an off-centre strike, where the
# face is already rotating about the ball.
CONTACT_TIME_SEC = 0.00045
TYPICAL_CLOSURE_RATE_DEG_PER_SEC = 250.0
GEAR_EFFECT_CLOSURE_SENSITIVITY = 0.35


def spin_loft(dynamic_loft_deg, attack_angle_deg):
    """The angle between where the face points and where the club is moving."""
    return dynamic_loft_deg - attack_angle_deg


def path_from_swing_direction(swing_direction_deg, attack_angle_deg):
    """Club path implied by the swing plane and where on the arc you struck it.

    The head travels an inclined circle. Struck before the low point - a
    descending blow - it is still moving outward, so the path sits right of the
    swing direction for a right-hander. Struck after it, on the way up, the
    path has already begun working left.
    """
    return swing_direction_deg - PATH_SHIFT_PER_DEGREE_ATTACK * attack_angle_deg


def resolve_path_and_face(club_metrics):
    """Work out club path and absolute face angle from whatever was reported.

    Monitors differ: some report swing direction and face angle, some report
    club path and face-to-path. Any consistent subset resolves to the same
    pair, which is what the rest of the model needs.
    """
    path = club_metrics.get("club_path_deg")
    swing_direction = club_metrics.get("swing_direction_deg")
    if path is None:
        if swing_direction is not None:
            path = path_from_swing_direction(
                swing_direction, club_metrics.get("attack_angle_deg", 0.0)
            )
        else:
            path = 0.0

    face = club_metrics.get("face_angle_deg")
    if face is None:
        face_to_path = club_metrics.get("face_angle_to_path_deg")
        face = path + face_to_path if face_to_path is not None else path

    return path, face


def face_at_separation(face_angle_deg, closure_rate_deg_per_sec=None):
    """Face angle by the time the ball leaves it.

    The face is still rotating during contact, so the ball sees a slightly
    more closed face than the one measured at first touch.
    """
    if closure_rate_deg_per_sec is None:
        return face_angle_deg
    return face_angle_deg - closure_rate_deg_per_sec * CONTACT_TIME_SEC


def smash_factor(spin_loft_deg, strike_offset_inches=0.0):
    """Ball speed as a multiple of club speed. CALIBRATED."""
    smash = SMASH_AT_ZERO_SPIN_LOFT - SMASH_LOST_PER_DEGREE_SPIN_LOFT * spin_loft_deg
    smash *= max(0.0, 1.0 - SMASH_LOST_PER_SQUARE_INCH_OFF_CENTRE * strike_offset_inches**2)
    return max(MIN_SMASH_FACTOR, min(smash, MAX_SMASH_FACTOR))


def interpolate_by_loft(curve, dynamic_loft_deg):
    """Read a correction curve at a delivered loft, straight-lining between
    its samples and holding flat beyond the ends.

    Interpolating rather than bucketing is the point: the correction moves
    smoothly with the measurement, so there is no boundary for two similar
    swings to fall either side of.
    """
    if dynamic_loft_deg is None:
        return 1.0
    if dynamic_loft_deg <= curve[0][0]:
        return curve[0][1]
    if dynamic_loft_deg >= curve[-1][0]:
        return curve[-1][1]

    for (low_loft, low_value), (high_loft, high_value) in zip(curve, curve[1:]):
        if low_loft <= dynamic_loft_deg <= high_loft:
            span = high_loft - low_loft
            position = (dynamic_loft_deg - low_loft) / span
            return low_value + position * (high_value - low_value)
    return 1.0


def launch_angle(dynamic_loft_deg, attack_angle_deg, strike_high_offset_inches=0.0):
    """Vertical launch. Mostly loft, shifted by whether the club is descending.

    Corrected for what the club actually is, read off its delivered loft.
    """
    base = (
        LAUNCH_FRACTION_OF_DYNAMIC_LOFT * dynamic_loft_deg
        + (1.0 - LAUNCH_FRACTION_OF_DYNAMIC_LOFT) * attack_angle_deg
    )
    correction = interpolate_by_loft(LAUNCH_CORRECTION_BY_LOFT, dynamic_loft_deg)
    return base * correction + LAUNCH_PER_INCH_HIGH * strike_high_offset_inches


def launch_direction(face_angle_deg, club_path_deg):
    """Horizontal start line. The ball starts close to where the face points."""
    return (
        START_LINE_FACE_WEIGHT * face_angle_deg
        + (1.0 - START_LINE_FACE_WEIGHT) * club_path_deg
    )


def back_spin(club_speed_mph, spin_loft_deg, strike_high_offset_inches=0.0,
              dynamic_loft_deg=None):
    """Backspin rate. Rises with speed and spin loft; a high strike reduces it.

    Corrected for what the club actually is - a driver spins far less than a
    wedge at the same spin loft - read off its delivered loft.
    """
    spin = BACK_SPIN_PER_MPH * club_speed_mph * math.sin(math.radians(max(0.0, spin_loft_deg)))
    spin *= interpolate_by_loft(SPIN_CORRECTION_BY_LOFT, dynamic_loft_deg)
    spin += BACK_SPIN_PER_INCH_HIGH * strike_high_offset_inches
    return max(0.0, min(spin, MAX_BACK_SPIN_RPM))


def spin_axis(face_angle_deg, club_path_deg, strike_toe_offset_inches=0.0,
              closure_rate_deg_per_sec=None):
    """Tilt of the spin axis in degrees. Positive tilts right - a fade or slice.

    Curvature comes from the face-to-path relationship. Gear effect from an
    off-centre strike then pulls it the other way, more strongly when the face
    is rotating quickly through impact.
    """
    tilt = SPIN_AXIS_PER_DEGREE_FACE_TO_PATH * (face_angle_deg - club_path_deg)

    gear = SPIN_AXIS_PER_INCH_TOE * strike_toe_offset_inches
    if closure_rate_deg_per_sec is not None and TYPICAL_CLOSURE_RATE_DEG_PER_SEC > 0:
        excess = closure_rate_deg_per_sec / TYPICAL_CLOSURE_RATE_DEG_PER_SEC - 1.0
        gear *= 1.0 + GEAR_EFFECT_CLOSURE_SENSITIVITY * excess
    tilt += gear

    return max(-MAX_SPIN_AXIS_DEG, min(tilt, MAX_SPIN_AXIS_DEG))


def side_spin_from_axis(back_spin_rpm, spin_axis_deg):
    """Convert a tilted spin axis into the sidespin figure the engine takes."""
    return back_spin_rpm * math.tan(math.radians(spin_axis_deg))


def spin_axis_from_side_spin(back_spin_rpm, side_spin_rpm):
    """The inverse, for monitors that report sidespin instead of an axis."""
    if not back_spin_rpm:
        return 0.0
    return math.degrees(math.atan2(side_spin_rpm, back_spin_rpm))


def derive_ball_launch(club_metrics, spin_trim=NEUTRAL_TRIM, launch_trim=NEUTRAL_TRIM):
    """Produce ball launch metrics from how the club was delivered.

    Every reported delivery metric is used. Absent optional ones - strike
    location, closure rate, dynamic lie - are treated as a centred, level
    strike with a typical release.

    Nothing here knows or cares which club was swung. The ball responds to how
    the face was delivered, and the delivery is measured. `spin_trim` and
    `launch_trim` personalise the model to one golfer's own shots; see
    fit_launch_trims.
    """
    club_speed = club_metrics.get("club_speed_mph", 0.0)
    attack_angle = club_metrics.get("attack_angle_deg", 0.0)
    dynamic_loft = club_metrics.get("dynamic_loft_deg", 0.0)
    closure_rate = club_metrics.get("closure_rate_deg_per_sec")
    toe_offset = club_metrics.get("impact_offset_toe_in", 0.0)
    high_offset = club_metrics.get("impact_offset_high_in", 0.0)
    dynamic_lie = club_metrics.get("dynamic_lie_deg", 0.0)

    path, face = resolve_path_and_face(club_metrics)
    face = face_at_separation(face, closure_rate)

    loft_over_attack = spin_loft(dynamic_loft, attack_angle)
    strike_offset = math.hypot(toe_offset, high_offset)

    smash = smash_factor(loft_over_attack, strike_offset)
    spin = back_spin(club_speed, loft_over_attack, high_offset, dynamic_loft) * spin_trim
    axis = spin_axis(face, path, toe_offset, closure_rate)

    return {
        "ball_speed_mph": club_speed * smash,
        "launch_angle_deg": launch_angle(dynamic_loft, attack_angle, high_offset) * launch_trim,
        "launch_direction_deg": launch_direction(face, path),
        "back_spin_rpm": spin,
        "side_spin_rpm": side_spin_from_axis(spin, axis),
        "rifle_spin_rpm": RIFLE_SPIN_PER_DEGREE_DYNAMIC_LIE * dynamic_lie,
        "spin_axis_deg": axis,
        "smash_factor": smash,
        "spin_loft_deg": loft_over_attack,
        "face_to_path_deg": face - path,
        "derived": True,
    }


def fit_launch_trims(shots, minimum_shots=MIN_SHOTS_TO_FIT):
    """Fit spin and launch trims from a golfer's own measured shots.

    `shots` is an iterable of (club_metrics, measured_ball_metrics). Only
    shots where the monitor measured the value being fitted contribute, and
    too few of them yields no trim rather than one taken from noise.

    A single trim each, not one per club: the reference data shows spin and
    launch are explained by spin loft, so a golfer who spins it more than the
    model expects does so across their bag. Anything genuinely club-specific
    would show up as scatter here rather than being assumed into existence.
    """
    ratios = defaultdict(list)

    for club_metrics, measured in shots:
        if not club_metrics or not measured:
            continue
        # Model with trims off, so the ratio is the trim rather than a trim on
        # top of one.
        modelled = derive_ball_launch(club_metrics, spin_trim=1.0, launch_trim=1.0)

        for name, field in (("spin_trim", "back_spin_rpm"),
                            ("launch_trim", "launch_angle_deg")):
            actual = measured.get(field)
            predicted = modelled.get(field)
            if actual is None or not predicted:
                continue
            ratios[name].append(actual / predicted)

    return {
        name: sum(values) / len(values)
        for name, values in ratios.items()
        if len(values) >= minimum_shots
    }


def analyse_delivery(club_metrics):
    """Swing-quality readings derived from how the club was delivered.

    The ball itself only ever sees the face and the path - swing direction does
    not reach it except through the path. That does not make swing direction
    useless: compared against the path it tells you where your low point is,
    which is the thing a golfer can actually change.
    """
    attack_angle = club_metrics.get("attack_angle_deg", 0.0)
    dynamic_loft = club_metrics.get("dynamic_loft_deg", 0.0)
    path, face = resolve_path_and_face(club_metrics)
    swing_direction = club_metrics.get("swing_direction_deg")

    notes = {
        "spin_loft_deg": spin_loft(dynamic_loft, attack_angle),
        "face_to_path_deg": face - path,
        "club_path_deg": path,
        "face_angle_deg": face,
        "attack_angle_deg": attack_angle,
    }

    if swing_direction is not None:
        notes["swing_direction_deg"] = swing_direction
        notes["path_vs_swing_direction_deg"] = path - swing_direction
        implied = path_from_swing_direction(swing_direction, attack_angle)
        # Measured path further right than the plane implies means the strike
        # came earlier in the arc than the attack angle alone accounts for.
        notes["low_point_bias_deg"] = path - implied

    closure_rate = club_metrics.get("closure_rate_deg_per_sec")
    if closure_rate is not None:
        notes["closure_rate_deg_per_sec"] = closure_rate
        notes["closure_vs_typical"] = closure_rate / TYPICAL_CLOSURE_RATE_DEG_PER_SEC

    toe = club_metrics.get("impact_offset_toe_in")
    high = club_metrics.get("impact_offset_high_in")
    if toe is not None or high is not None:
        notes["strike_offset_in"] = math.hypot(toe or 0.0, high or 0.0)

    return notes


REQUIRED_BALL_FIELDS = (
    "ball_speed_mph",
    "launch_angle_deg",
    "launch_direction_deg",
    "back_spin_rpm",
    "side_spin_rpm",
    "rifle_spin_rpm",
)


def complete_ball_launch(swing_metrics, spin_trim=NEUTRAL_TRIM, launch_trim=NEUTRAL_TRIM):
    """Return ball launch, measured where available and modelled where not.

    Trims personalise the modelled half to one golfer; they have no effect on
    anything their equipment actually measured.

    Returns (ball_metrics, modelled_field_names) so a caller can tell the
    golfer which numbers were their own and which the simulator supplied.

    A monitor reporting spin axis but no sidespin has the sidespin computed
    from its own measurement rather than modelled, because that is arithmetic
    on measured data, not a guess.
    """
    measured = dict(swing_metrics.get("ball_launch_metrics") or {})
    club = swing_metrics.get("club_metrics") or {}

    # Spin axis is a measurement; sidespin derived from it still counts as
    # measured, so resolve that before deciding what is missing.
    if measured.get("side_spin_rpm") is None and measured.get("spin_axis_deg") is not None:
        measured["side_spin_rpm"] = side_spin_from_axis(
            measured.get("back_spin_rpm") or 0.0, measured["spin_axis_deg"]
        )
    elif measured.get("spin_axis_deg") is None and measured.get("side_spin_rpm") is not None:
        measured["spin_axis_deg"] = spin_axis_from_side_spin(
            measured.get("back_spin_rpm") or 0.0, measured["side_spin_rpm"]
        )

    missing = tuple(field for field in REQUIRED_BALL_FIELDS if measured.get(field) is None)
    if not missing or not club:
        return measured, missing

    modelled = derive_ball_launch(club, spin_trim=spin_trim, launch_trim=launch_trim)
    for field in missing:
        measured[field] = modelled[field]
    measured.setdefault("spin_axis_deg", modelled["spin_axis_deg"])
    return measured, missing
