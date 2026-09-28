"""What a golfer's stored shots say about them.

Everything gets recorded and, until now, nothing was ever read back. This
turns a pile of saved shots into the things a golfer actually wants to know:
what they hit each club, how consistent they are with it, whether anything is
improving, and what their own data says the impact model should be doing
differently.

Pure functions over a list of shot rows. Nothing here touches a database, so
it can be pointed at a live history, an export, or a handful of made-up shots
in a test.
"""

from statistics import mean, pstdev

from golf_clubs import describe_group, group_shots
from golf_impact import fit_launch_trims

# Fewer shots than this and a trend is noise with a direction.
MIN_SHOTS_FOR_TREND = 10

CLUB_FIELDS = (
    "club_speed_mph", "attack_angle_deg", "dynamic_loft_deg", "club_path_deg",
    "swing_direction_deg", "face_angle_deg", "closure_rate_deg_per_sec",
    "impact_offset_toe_in", "impact_offset_high_in", "dynamic_lie_deg",
)
BALL_FIELDS = (
    "ball_speed_mph", "launch_angle_deg", "launch_direction_deg",
    "back_spin_rpm", "side_spin_rpm", "spin_axis_deg",
)


def _numbers(shots, field):
    return [shot[field] for shot in shots if shot.get(field) is not None]


def _average(shots, field):
    values = _numbers(shots, field)
    return mean(values) if values else None


def dispersion(shots):
    """How scattered the shots are, side to side, in yards.

    Consistency is what most golfers are actually chasing, and it is invisible
    in an average - two golfers with the same mean can be very different
    players.
    """
    sides = _numbers(shots, "side_yards")
    if len(sides) < 2:
        return None
    return pstdev(sides)


def measured_pairs(shots):
    """Shots where the monitor measured the ball, not where we modelled it.

    Fitting the model to its own output would only ever confirm it, so shots
    whose ball numbers we supplied are excluded.
    """
    pairs = []
    for shot in shots:
        modelled = set(shot.get("modelled_ball_fields") or ())
        club = {f: shot[f] for f in CLUB_FIELDS if shot.get(f) is not None}
        ball = {
            f: shot[f] for f in BALL_FIELDS
            if shot.get(f) is not None and f not in modelled
        }
        if club and ball:
            pairs.append((club, ball))
    return pairs


# Which way is better, for the things worth trending.
HIGHER_IS_BETTER = {"smash_factor": True, "carry_yards": True,
                    "side_dispersion_yards": False}


def trend(shots, field):
    """Whether a number is moving, comparing the older half to the newer.

    Shots are assumed to arrive oldest first, and must be shots with the same
    club. Trending a mixed bag measures the order somebody practised in, not
    their golf: driver first and wedges last reads as a collapse.
    """
    values = _numbers(shots, field)
    if len(values) < MIN_SHOTS_FOR_TREND:
        return None

    middle = len(values) // 2
    earlier, later = mean(values[:middle]), mean(values[middle:])
    return _moved(earlier, later, field)


def dispersion_trend(shots):
    """Whether the golfer is getting tighter or looser, side to side."""
    sides = _numbers(shots, "side_yards")
    if len(sides) < MIN_SHOTS_FOR_TREND:
        return None

    middle = len(sides) // 2
    earlier, later = pstdev(sides[:middle]), pstdev(sides[middle:])
    return _moved(earlier, later, "side_dispersion_yards")


def _moved(earlier, later, field):
    better_is_up = HIGHER_IS_BETTER.get(field, True)
    change = later - earlier
    return {
        "earlier": earlier,
        "later": later,
        "change": change,
        "improving": change > 0 if better_is_up else change < 0,
    }


def summarise_group(group, shots):
    """A group of shots, with the things a golfer asks about it."""
    summary = dict(group)
    summary["side_dispersion_yards"] = dispersion(shots)
    for field in ("smash_factor", "spin_loft_deg", "apex_yards", "back_spin_rpm"):
        value = _average(shots, field)
        if value is not None:
            summary[field] = value

    # Trends belong here, within one club, rather than across the bag.
    trends = {}
    for field in ("smash_factor", "carry_yards"):
        moved = trend(shots, field)
        if moved:
            trends[field] = moved
    tightening = dispersion_trend(shots)
    if tightening:
        trends["side_dispersion_yards"] = tightening
    if trends:
        summary["trends"] = trends
    return summary


def fit_from_history(shots):
    """Trims fitted from a golfer's own measured shots, or nothing.

    Kept separate from the full report because a game start wants only this,
    and should not pay for grouping and trends to get it.
    """
    return fit_launch_trims(measured_pairs(shots or []))


def build_report(shots):
    """Everything the stored shots support saying."""
    shots = [shot for shot in shots or [] if shot]
    if not shots:
        return {"shots": 0, "groups": [], "trims": {}, "trends": {}}

    groups = group_shots(shots)
    detailed = []
    for group in groups:
        low, high = group["delivered_loft_range_deg"]
        members = [
            shot for shot in shots
            if shot.get("dynamic_loft_deg") is not None
            and low <= shot["dynamic_loft_deg"] <= high
        ]
        detailed.append(summarise_group(group, members))

    return {
        "shots": len(shots),
        "groups": detailed,
        # What this golfer's own shots say the model should be doing.
        "trims": fit_from_history(shots),
    }


def describe_report(report):
    """The report as lines a golfer can read."""
    if not report or not report.get("shots"):
        return ["No shots recorded yet."]

    lines = [f"{report['shots']} shots recorded.", ""]

    for group in report["groups"]:
        lines.append("  " + describe_group(group))
        detail = []
        if group.get("side_dispersion_yards") is not None:
            detail.append(f"dispersion {group['side_dispersion_yards']:.0f} yd")
        if group.get("smash_factor") is not None:
            detail.append(f"smash {group['smash_factor']:.2f}")
        if group.get("back_spin_rpm") is not None:
            detail.append(f"spin {group['back_spin_rpm']:.0f} rpm")
        if detail:
            lines.append("      " + ", ".join(detail))

        for field, moved in (group.get("trends") or {}).items():
            verdict = "better" if moved["improving"] else "worse"
            lines.append(
                f"      {field.replace('_', ' ')}: {moved['earlier']:.2f}"
                f" -> {moved['later']:.2f} ({verdict})"
            )

    trims = report.get("trims") or {}
    if trims:
        lines.append("")
        lines.append("  Fitted from your own measured shots:")
        for name, value in trims.items():
            lines.append(f"    {name.replace('_', ' ')} {value:.3f}")
    else:
        lines.append("")
        lines.append("  Not enough measured shots yet to fit the model to you.")

    return lines
