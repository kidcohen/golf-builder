"""Read shots from a launch monitor's export.

Nobody is going to type twenty metrics before every swing. Monitors already
write the numbers out - as JSON or CSV, per shot or per session - so this
module turns whatever they wrote into the metric blocks the simulator expects.

Two problems to solve:

FIELD NAMES
    Every device names things differently: "Ball Speed", "ball_speed_mph",
    "BallSpeed (mph)", "Club Head Speed", "AoA". Keys are normalised - case,
    punctuation, units in brackets and unit suffixes all stripped - and looked
    up in an alias table. Unrecognised keys are kept in `extra` rather than
    discarded, because a metric this module has not heard of is still the
    golfer's data.

UNITS
    Speeds arriving in m/s or km/h are converted to mph. Anything without a
    recognisable unit is taken at face value, which is the common case, since
    most monitors export mph already.
"""

import csv
import json
import re
from pathlib import Path

MPH_PER_MPS = 2.236936
MPH_PER_KMH = 0.621371

# canonical field -> the block it belongs to
CLUB_FIELDS = (
    "club_speed_mph",
    "attack_angle_deg",
    "dynamic_loft_deg",
    "club_path_deg",
    "swing_direction_deg",
    "face_angle_deg",
    "face_angle_to_path_deg",
    "closure_rate_deg_per_sec",
    "impact_offset_toe_in",
    "impact_offset_high_in",
    "dynamic_lie_deg",
)
BALL_FIELDS = (
    "ball_speed_mph",
    "launch_angle_deg",
    "launch_direction_deg",
    "back_spin_rpm",
    "side_spin_rpm",
    "rifle_spin_rpm",
    "spin_axis_deg",
)
BODY_FIELDS = (
    "pelvis_peak_speed_dps",
    "pelvis_peak_time_ms",
    "thorax_peak_speed_dps",
    "thorax_peak_time_ms",
    "lead_arm_peak_speed_dps",
    "lead_arm_peak_time_ms",
    "club_peak_speed_dps",
    "club_peak_time_ms",
    "pelvis_rotation_at_top_deg",
    "thorax_rotation_at_top_deg",
    "x_factor_stretch_deg",
    "backswing_time_ms",
    "downswing_time_ms",
    "peak_vertical_force_bw",
    "peak_vertical_force_time_ms",
    "lead_pressure_at_impact_pct",
    "pelvis_thrust_mm",
    "pelvis_sway_mm",
    "side_bend_at_impact_deg",
    "head_movement_mm",
)
CONTEXT_FIELDS = ("club", "lie_type")

# Alias -> canonical. Keys here are already normalised.
ALIASES = {
    # club delivery
    "club_speed": "club_speed_mph",
    "clubhead_speed": "club_speed_mph",
    "club_head_speed": "club_speed_mph",
    "chs": "club_speed_mph",
    "attack_angle": "attack_angle_deg",
    "angle_of_attack": "attack_angle_deg",
    "aoa": "attack_angle_deg",
    "dynamic_loft": "dynamic_loft_deg",
    "loft_at_impact": "dynamic_loft_deg",
    "club_path": "club_path_deg",
    "path": "club_path_deg",
    "swing_direction": "swing_direction_deg",
    "swing_plane_direction": "swing_direction_deg",
    "face_angle": "face_angle_deg",
    "face_to_path": "face_angle_to_path_deg",
    "face_angle_to_path": "face_angle_to_path_deg",
    "closure_rate": "closure_rate_deg_per_sec",
    "face_closure_rate": "closure_rate_deg_per_sec",
    "impact_offset_toe": "impact_offset_toe_in",
    "impact_toe": "impact_offset_toe_in",
    "horizontal_impact_offset": "impact_offset_toe_in",
    "impact_location_horizontal": "impact_offset_toe_in",
    "impact_offset_high": "impact_offset_high_in",
    "impact_height": "impact_offset_high_in",
    "vertical_impact_offset": "impact_offset_high_in",
    "impact_location_vertical": "impact_offset_high_in",
    "dynamic_lie": "dynamic_lie_deg",
    "lie_at_impact": "dynamic_lie_deg",
    # ball launch
    "ball_speed": "ball_speed_mph",
    "ball_velocity": "ball_speed_mph",
    "launch_angle": "launch_angle_deg",
    "vertical_launch_angle": "launch_angle_deg",
    "vla": "launch_angle_deg",
    "launch_direction": "launch_direction_deg",
    "horizontal_launch_angle": "launch_direction_deg",
    "hla": "launch_direction_deg",
    "azimuth": "launch_direction_deg",
    "back_spin": "back_spin_rpm",
    "backspin": "back_spin_rpm",
    "spin_rate": "back_spin_rpm",
    "side_spin": "side_spin_rpm",
    "sidespin": "side_spin_rpm",
    "spin_axis": "spin_axis_deg",
    "spin_axis_tilt": "spin_axis_deg",
    "axis_tilt": "spin_axis_deg",
    "rifle_spin": "rifle_spin_rpm",
    # body movement
    "pelvis_peak_speed": "pelvis_peak_speed_dps",
    "hip_peak_speed": "pelvis_peak_speed_dps",
    "pelvis_peak_time": "pelvis_peak_time_ms",
    "hip_peak_time": "pelvis_peak_time_ms",
    "thorax_peak_speed": "thorax_peak_speed_dps",
    "chest_peak_speed": "thorax_peak_speed_dps",
    "thorax_peak_time": "thorax_peak_time_ms",
    "chest_peak_time": "thorax_peak_time_ms",
    "lead_arm_peak_speed": "lead_arm_peak_speed_dps",
    "arm_peak_speed": "lead_arm_peak_speed_dps",
    "lead_arm_peak_time": "lead_arm_peak_time_ms",
    "arm_peak_time": "lead_arm_peak_time_ms",
    "club_peak_speed": "club_peak_speed_dps",
    "club_peak_time": "club_peak_time_ms",
    "pelvis_rotation_at_top": "pelvis_rotation_at_top_deg",
    "hip_rotation_at_top": "pelvis_rotation_at_top_deg",
    "thorax_rotation_at_top": "thorax_rotation_at_top_deg",
    "shoulder_rotation_at_top": "thorax_rotation_at_top_deg",
    "x_factor_stretch": "x_factor_stretch_deg",
    "backswing_time": "backswing_time_ms",
    "downswing_time": "downswing_time_ms",
    "peak_vertical_force": "peak_vertical_force_bw",
    "peak_vertical_force_time": "peak_vertical_force_time_ms",
    "lead_pressure_at_impact": "lead_pressure_at_impact_pct",
    "lead_pressure": "lead_pressure_at_impact_pct",
    "pelvis_thrust": "pelvis_thrust_mm",
    "early_extension": "pelvis_thrust_mm",
    "pelvis_sway": "pelvis_sway_mm",
    "sway": "pelvis_sway_mm",
    "side_bend_at_impact": "side_bend_at_impact_deg",
    "side_bend": "side_bend_at_impact_deg",
    "head_movement": "head_movement_mm",
    # context
    "club": "club",
    "club_name": "club",
    "club_type": "club",
    "lie": "lie_type",
    "lie_type": "lie_type",
    "surface": "lie_type",
}

# Speed units a field may declare in brackets. Only unambiguous spellings are
# listed: a bare "ms" means milliseconds far more often than metres per second.
SPEED_UNIT_FACTORS = {
    "mph": 1.0,
    "mp_h": 1.0,
    "mps": MPH_PER_MPS,
    "m_s": MPH_PER_MPS,
    "metres_per_second": MPH_PER_MPS,
    "meters_per_second": MPH_PER_MPS,
    "kmh": MPH_PER_KMH,
    "km_h": MPH_PER_KMH,
    "kph": MPH_PER_KMH,
}

# Unit suffixes stripped from keys before alias lookup, paired with the factor
# needed to bring the value into the canonical unit.
UNIT_SUFFIXES = (
    ("_mph", 1.0),
    ("_mps", MPH_PER_MPS),
    ("_m_s", MPH_PER_MPS),
    ("_kmh", MPH_PER_KMH),
    ("_km_h", MPH_PER_KMH),
    ("_kph", MPH_PER_KMH),
    ("_deg", 1.0),
    ("_degrees", 1.0),
    ("_rpm", 1.0),
    ("_ms", 1.0),
    ("_msec", 1.0),
    ("_in", 1.0),
    ("_inches", 1.0),
    ("_mm", 1.0),
    ("_pct", 1.0),
    ("_percent", 1.0),
    ("_bw", 1.0),
    ("_dps", 1.0),
    ("_deg_per_sec", 1.0),
    ("_deg_s", 1.0),
)

ALL_FIELDS = set(CLUB_FIELDS) | set(BALL_FIELDS) | set(BODY_FIELDS) | set(CONTEXT_FIELDS)


def normalise_key(raw_key):
    """Reduce a monitor's field name to a comparable form.

    "Ball Speed (mph)" and "BallSpeed_MPH" both become "ball_speed".
    """
    key = str(raw_key).strip().lower()
    key = re.sub(r"\([^)]*\)", " ", key)  # drop units in brackets
    key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return key


def bracketed_unit(raw_key):
    """The unit a field declares in brackets, normalised. "" when there is none.

    Read before the brackets are stripped, or "Ball Speed (m/s)" and
    "Ball Speed (mph)" become indistinguishable.
    """
    found = re.findall(r"\(([^)]*)\)", str(raw_key).lower())
    if not found:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", found[-1]).strip("_")


def resolve_field(raw_key):
    """Map a monitor field name to (canonical_name, conversion_factor).

    Returns (None, 1.0) when the field is not recognised.
    """
    key = normalise_key(raw_key)
    factor = SPEED_UNIT_FACTORS.get(bracketed_unit(raw_key), 1.0)

    if key in ALL_FIELDS:
        return key, factor
    if key in ALIASES:
        return ALIASES[key], factor

    # Some monitors carry the unit in the field name instead of in brackets.
    for suffix, suffix_factor in UNIT_SUFFIXES:
        if key.endswith(suffix):
            trimmed = key[: -len(suffix)]
            # "club_speed_mps" trims to "club_speed", whose canonical name
            # already carries its own unit suffix.
            for candidate in (trimmed, ALIASES.get(trimmed), f"{trimmed}_mph"):
                if candidate and candidate in ALL_FIELDS:
                    return candidate, suffix_factor
    return None, 1.0


def coerce_number(value):
    """Return a float when the reading is numeric, else None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_shot(reading):
    """Turn one monitor reading into the metric blocks the simulator expects.

    Unrecognised fields are kept under `extra` in shot_context, so nothing the
    golfer measured is thrown away just because this module has not heard of it.
    """
    club, ball, body, context, extra = {}, {}, {}, {}, {}

    for raw_key, raw_value in (reading or {}).items():
        canonical, factor = resolve_field(raw_key)
        if canonical is None:
            extra[normalise_key(raw_key)] = raw_value
            continue

        if canonical in CONTEXT_FIELDS:
            if raw_value not in (None, ""):
                context[canonical] = str(raw_value)
            continue

        number = coerce_number(raw_value)
        if number is None:
            continue
        number *= factor

        if canonical in CLUB_FIELDS:
            club[canonical] = number
        elif canonical in BALL_FIELDS:
            ball[canonical] = number
        elif canonical in BODY_FIELDS:
            body[canonical] = number

    metrics = {}
    if club:
        metrics["club_metrics"] = club
    if ball:
        metrics["ball_launch_metrics"] = ball
    if body:
        metrics["body_metrics"] = body
    if extra:
        context["extra"] = extra
    if context:
        metrics["shot_context"] = context
    return metrics


def parse_json(text):
    """Parse a monitor's JSON export. One shot, or a list, or {"shots": [...]}."""
    data = json.loads(text)
    if isinstance(data, dict):
        for key in ("shots", "data", "records"):
            if isinstance(data.get(key), list):
                return [parse_shot(row) for row in data[key]]
        return [parse_shot(data)]
    if isinstance(data, list):
        return [parse_shot(row) for row in data]
    raise ValueError("unrecognised JSON shape")


def parse_csv(text):
    """Parse a monitor's CSV export - one shot per row, headers in the first."""
    rows = list(csv.DictReader(text.splitlines()))
    return [parse_shot(row) for row in rows if any(v not in (None, "") for v in row.values())]


def read_shots(path):
    """Read every shot from one export file."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        return parse_csv(text)
    return parse_json(text)


class MonitorFolder:
    """Watches a folder a monitor writes shots into.

    Polled rather than event-driven: monitors write files at the pace a human
    hits balls, so a poll each time round the loop is ample, and it avoids a
    platform-specific filesystem-watching dependency.
    """

    SUFFIXES = (".json", ".csv")

    def __init__(self, directory, seen=None):
        self.directory = Path(directory)
        self.seen = set(seen or ())

    def mark_existing_as_seen(self):
        """Ignore whatever is already there, so only new swings are played."""
        for path in self._candidates():
            self.seen.add(path.name)
        return self

    def _candidates(self):
        if not self.directory.is_dir():
            return []
        return sorted(
            (p for p in self.directory.iterdir()
             if p.is_file() and p.suffix.lower() in self.SUFFIXES),
            key=lambda p: p.name,
        )

    def poll(self):
        """Return metrics for every shot written since the last poll."""
        fresh = []
        for path in self._candidates():
            if path.name in self.seen:
                continue
            self.seen.add(path.name)
            try:
                fresh.extend(read_shots(path))
            except (OSError, ValueError, csv.Error):
                # A half-written file will be complete by the next poll, but it
                # has been marked seen, so skip it rather than retry forever.
                continue
        return fresh
