"""Group a golfer's shots by how the club was delivered, learned from their data.

A golfer wants to know what they average "with this club". The temptation is to
answer it by defining clubs up front - driver under 16 degrees of delivered
loft, mid iron between 24 and 30, and so on - and stamping a label on every
shot as it is played.

That is the same mistake as putting a club term in the ball flight model. The
boundaries are invented, they create discontinuities where two near-identical
swings land on opposite sides of a line, and they write an assumption into the
golfer's record where a measurement should be. It also assumes every golfer
carries the same bag, delivers it the same way, and never changes it.

So nothing is labelled when a shot is stored. Shots keep their measurements,
and grouping happens afterwards, over the shots themselves: sort by delivered
loft, and split where the golfer's own data separates. Fourteen clubs produce
fourteen clusters; a half set produces a half set; a golfer who has just
changed their irons produces whatever they now actually hit.

Groups have no names. A group is described by what it measures - "40 shots
around 26 degrees of delivered loft at 90 mph, averaging 172 yards" - which is
both language-neutral and the thing the golfer was really asking about.
"""

from statistics import median

# Fewer shots than this and any split is noise rather than a bag.
MIN_SHOTS_TO_GROUP = 8

# A gap this many times the typical gap between neighbouring shots is taken as
# the space between two clubs rather than spread within one.
SEPARATION_FACTOR = 2.5

# ...but only if it is also a real difference in loft, so that a tight cluster
# of near-identical shots is not split on rounding.
MIN_SEPARATION_DEG = 2.5

GROUPING_FIELD = "dynamic_loft_deg"


def _usable(shots):
    return [shot for shot in shots if shot.get(GROUPING_FIELD) is not None]


def _summarise(members):
    """Describe a group by what it measures, never by a name."""
    lofts = [shot[GROUPING_FIELD] for shot in members]

    summary = {
        "shots": len(members),
        "delivered_loft_deg": sum(lofts) / len(lofts),
        "delivered_loft_range_deg": (min(lofts), max(lofts)),
    }
    for field, name in (
        ("club_speed_mph", "club_speed_mph"),
        ("carry_yards", "carry_yards"),
        ("total_yards", "total_yards"),
        ("back_spin_rpm", "back_spin_rpm"),
        ("smash_factor", "smash_factor"),
    ):
        values = [shot[field] for shot in members if shot.get(field) is not None]
        if values:
            summary[name] = sum(values) / len(values)
    return summary


def group_shots(shots, separation_factor=SEPARATION_FACTOR,
                minimum_shots=MIN_SHOTS_TO_GROUP):
    """Split a golfer's shots into the clubs their own data shows.

    Returns groups ordered by delivered loft, each a summary of its members.
    Too little history returns a single group: a bag cannot be inferred from a
    handful of swings, and pretending otherwise would invent the boundaries
    this module exists to avoid.
    """
    usable = _usable(shots)
    if not usable:
        return []

    ordered = sorted(usable, key=lambda shot: shot[GROUPING_FIELD])
    if len(ordered) < minimum_shots:
        return [_summarise(ordered)]

    lofts = [shot[GROUPING_FIELD] for shot in ordered]
    gaps = [later - earlier for earlier, later in zip(lofts, lofts[1:])]
    typical_gap = median(gaps) or 0.0
    threshold = max(typical_gap * separation_factor, MIN_SEPARATION_DEG)

    groups, current = [], [ordered[0]]
    for gap, shot in zip(gaps, ordered[1:]):
        if gap > threshold:
            groups.append(current)
            current = []
        current.append(shot)
    groups.append(current)

    return [_summarise(members) for members in groups]


def nearest_group(groups, delivered_loft_deg):
    """The group a delivery belongs to, by proximity rather than by a boundary.

    Used to place one shot against a golfer's history - "this was one of your
    26 degree shots, and it went 12 yards shorter than they usually do".
    """
    if not groups or delivered_loft_deg is None:
        return None
    return min(groups, key=lambda g: abs(g["delivered_loft_deg"] - delivered_loft_deg))


def describe_group(group):
    """A plain description of a group, in numbers rather than club names."""
    if not group:
        return "no shots"

    parts = [f"{group['shots']} shots around {group['delivered_loft_deg']:.0f}deg loft"]
    if "club_speed_mph" in group:
        parts.append(f"{group['club_speed_mph']:.0f} mph")
    if "carry_yards" in group:
        parts.append(f"carrying {group['carry_yards']:.0f} yd")
    return ", ".join(parts)
