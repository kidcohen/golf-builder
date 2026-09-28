"""What the weather leaves lying on the course.

Conditions do more than change how the air and the turf behave. Rain collects
in the low ground; wind strips leaves off the trees and brings small branches
down across the fairway. Find your ball in one of those and the next shot is
not the shot you would have had from clean grass.

WHERE THINGS ARE
    Features are placed deterministically from the ground position, not drawn
    fresh each time they are asked for. A puddle you can see from the tee has
    to still be there when you walk up to it, and has to be in the same place
    for your playing partner. The same course in the same weather always
    reproduces the same course.

HOW MUCH THERE IS
    Coverage comes from the weather. Wetness fills hollows; wind strews
    debris, with branches far rarer than leaves. In fair, still weather the
    course is clean and none of this costs anything.
"""

import hashlib
from dataclasses import dataclass

# Features are placed per cell of ground, in yards. About a stride across:
# small enough that a puddle is something you can be unlucky with, large
# enough that it is not a coin flip per inch.
CELL_YARDS = 3.0

# --- How much the weather leaves behind -----------------------------------
# Standing water needs real rain. Even a soaked course is mostly playable
# grass, so this tops out well short of everywhere.
MAX_CASUAL_WATER_COVERAGE = 0.18
WETNESS_FOR_STANDING_WATER = 0.55  # below this it is soft, not flooded

# Leaves need wind to strip them. A gale leaves a mess.
WIND_MPH_FOR_MAX_DEBRIS = 35.0
MAX_LEAF_COVERAGE = 0.22
# Branches come down far less often than leaves come off.
BRANCH_SHARE_OF_DEBRIS = 0.08
WIND_MPH_FOR_BRANCHES = 18.0


@dataclass(frozen=True)
class SurfaceFeature:
    """Something the ball can finish in, and what it costs to play from."""

    kind: str
    lie_type: str
    # A shot played from here keeps this fraction of its ball speed, and this
    # fraction of its spin - wet grass and leaves both come between club and
    # ball, which costs speed and costs far more spin.
    speed_retained: float = 1.0
    spin_retained: float = 1.0
    # Landing in it kills the run-out: a ball pitching into standing water
    # stops where it lands rather than releasing.
    stops_roll: bool = False

    @property
    def is_clear(self):
        return self.kind == "clear"


CLEAR = SurfaceFeature("clear", None)
CASUAL_WATER = SurfaceFeature(
    "casual_water", "Casual water", speed_retained=0.72, spin_retained=0.45,
    stops_roll=True,
)
LEAVES = SurfaceFeature(
    "leaves", "Leaves", speed_retained=0.88, spin_retained=0.55,
)
BRANCH = SurfaceFeature(
    "branch", "Against a branch", speed_retained=0.70, spin_retained=0.80,
)


def casual_water_coverage(wetness):
    """How much of the course is under standing water, 0 to 1.

    Soft ground is not flooded ground: nothing stands until the course is wet
    enough to stop taking it.
    """
    if wetness is None or wetness <= WETNESS_FOR_STANDING_WATER:
        return 0.0
    over = (wetness - WETNESS_FOR_STANDING_WATER) / (1.0 - WETNESS_FOR_STANDING_WATER)
    return MAX_CASUAL_WATER_COVERAGE * min(1.0, over)


def debris_coverage(wind_speed_mph):
    """How much of the course has leaves and branches across it, 0 to 1."""
    if not wind_speed_mph or wind_speed_mph <= 0:
        return 0.0
    return MAX_LEAF_COVERAGE * min(1.0, wind_speed_mph / WIND_MPH_FOR_MAX_DEBRIS)


def wind_speed_of(conditions):
    """Wind speed in mph from a conditions object's vector."""
    wind = getattr(conditions, "wind_vector_mph", None) or (0.0, 0.0, 0.0)
    return (wind[0] ** 2 + wind[2] ** 2) ** 0.5


def _cell_value(position, salt):
    """A stable number in [0, 1) for a patch of ground.

    Deterministic across processes and machines - Python's own hash is salted
    per run, which would move every puddle on every restart.

    It has to be a real hash rather than a checksum. CRC32 was tried and is
    linear: neighbouring cells produced strongly correlated values, so puddles
    bunched together and whole lines of the course came out dry regardless of
    how much rain had fallen.
    """
    x = int(position[0] // CELL_YARDS)
    z = int(position[2] // CELL_YARDS)
    digest = hashlib.blake2b(f"{salt}:{x}:{z}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def feature_at(position, conditions, course_seed=""):
    """What is lying on the ground where the ball finished.

    The same position, weather and course always give the same answer.
    """
    wetness = getattr(conditions, "wetness", 0.0)
    water = casual_water_coverage(wetness)
    if water and _cell_value(position, f"{course_seed}|water") < water:
        return CASUAL_WATER

    wind = wind_speed_of(conditions)
    debris = debris_coverage(wind)
    if not debris:
        return CLEAR

    roll = _cell_value(position, f"{course_seed}|debris")
    if roll >= debris:
        return CLEAR
    # Within the debris, a small share of it is a branch rather than leaves.
    if wind >= WIND_MPH_FOR_BRANCHES and \
            _cell_value(position, f"{course_seed}|branch") < BRANCH_SHARE_OF_DEBRIS:
        return BRANCH
    return LEAVES


# --- What the ball meets on its way ---------------------------------------
# A patch of leaves takes this share of whatever roll the ball had left.
LEAF_ROLL_LOSS = 0.45
# A crosswind pushes a slowing ball. Small, but it is why a putt on an exposed
# green does not finish where you aimed it in a gale.
WIND_DRIFT_YARDS_PER_MPH_PER_100_YARDS = 0.35


def roll_out(landed, free_rest, conditions, course_seed=""):
    """Run the ball out from where it landed, over the ground as it really is.

    The engine works out where the ball would finish on open turf. The course
    then has its say: standing water pulls it up, a branch stops it, leaves
    take the pace off it, and a crosswind pushes it off line as it slows.

    Returns (resting position, what it met on the way).
    """
    total = ((free_rest[0] - landed[0]) ** 2 + (free_rest[2] - landed[2]) ** 2) ** 0.5
    if total <= 0:
        return landed, ()

    direction = ((free_rest[0] - landed[0]) / total, (free_rest[2] - landed[2]) / total)
    steps = max(1, int(total // CELL_YARDS))
    met = []

    travelled = total  # unimpeded, it runs the whole way
    walked = 0.0
    for step in range(1, steps + 1):
        reached = walked + CELL_YARDS
        remaining = total - reached
        candidate = (
            landed[0] + direction[0] * reached,
            0.0,
            landed[2] + direction[1] * reached,
        )
        feature = feature_at(candidate, conditions, course_seed)

        if feature.kind in ("casual_water", "branch"):
            met.append(feature.kind)  # it runs in, or up against it, and stops
            travelled = reached
            break
        if feature.kind == "leaves":
            met.append("leaves")
            # Leaves take the pace off: most of whatever roll was left goes.
            travelled = reached + max(0.0, remaining) * (1 - LEAF_ROLL_LOSS)
            break

        walked = reached

    travelled = min(travelled, total)
    rested = (
        landed[0] + direction[0] * travelled,
        0.0,
        landed[2] + direction[1] * travelled,
    )
    return _drift_on_the_wind(landed, rested, conditions, travelled), tuple(met)


def _drift_on_the_wind(landed, rested, conditions, travelled):
    """Push a rolling ball off line, the way a crosswind does."""
    wind = getattr(conditions, "wind_vector_mph", None) or (0.0, 0.0, 0.0)
    crosswind = wind[2]
    if not crosswind or travelled <= 0:
        return rested

    drift = crosswind * (travelled / 100.0) * WIND_DRIFT_YARDS_PER_MPH_PER_100_YARDS
    return (rested[0], 0.0, rested[2] + drift)


ENCOUNTER_NOTES = {
    "casual_water": "Ran into standing water and pulled up.",
    "branch": "Stopped against a fallen branch.",
    "leaves": "Ran into leaves, which took the pace off it.",
}


def describe_encounters(met):
    """What to tell the golfer about the run-out."""
    return [ENCOUNTER_NOTES[kind] for kind in met or () if kind in ENCOUNTER_NOTES]


def apply_lie_to_metrics(swing_metrics, feature):
    """Charge a shot for the lie it is played from.

    The golfer swings the same way; the ball comes off differently because
    there is grass, water or leaves between the face and the ball. So the
    delivery is untouched and the ball launch pays for it.
    """
    if feature is None or feature.is_clear:
        return swing_metrics

    metrics = dict(swing_metrics)
    ball = dict(metrics.get("ball_launch_metrics") or {})
    if ball:
        for field, factor in (
            ("ball_speed_mph", feature.speed_retained),
            ("back_spin_rpm", feature.spin_retained),
            ("side_spin_rpm", feature.spin_retained),
        ):
            if ball.get(field) is not None:
                ball[field] = ball[field] * factor
        metrics["ball_launch_metrics"] = ball

    context = dict(metrics.get("shot_context") or {})
    context["lie_type"] = feature.lie_type
    context["surface"] = feature.kind
    metrics["shot_context"] = context
    return metrics


def describe_surface(conditions, course_seed=""):
    """A line about what the weather has left on the course, or nothing."""
    water = casual_water_coverage(getattr(conditions, "wetness", 0.0))
    wind = wind_speed_of(conditions)
    debris = debris_coverage(wind)

    notes = []
    if water:
        notes.append(f"standing water across about {water:.0%} of the course")
    if debris:
        if wind >= WIND_MPH_FOR_BRANCHES:
            notes.append(f"leaves and fallen branches over about {debris:.0%}")
        else:
            notes.append(f"leaves over about {debris:.0%}")
    if not notes:
        return ""
    return "Course: " + ", ".join(notes) + "."
