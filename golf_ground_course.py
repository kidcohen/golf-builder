"""Turn the crawled ground in courses_ground.json into playable holes.

That file is the deliverable of the gathering work: for each course modelled
here, a real centreline that bends where the hole bends, real bunkers and water
where the map has them, the height of the ground along the way, and - where a
metre survey covers the course - which way the green falls. This reads one hole
out of it as the library's Hole, marked surveyed so the shaping is kept as
measured rather than filled in around a guess.

Two things are deliberately not asserted from the survey:

  A green tilt steeper than a green can hold. Averaged over the pin it is a
  false front or a green perched on a slope - the plane was fitted through
  ground that is not the putting surface - so the tilt is left for the library
  to shape the way it shapes a guide-only green.

  A green the map did not carry, which comes through without a radius and is
  given the default rather than a measured one.
"""
import json
import math
from pathlib import Path

from golf_course import Hole
from golf_terrain import (
    BUNKER,
    FLAT,
    GreenContour,
    Hazard,
    HoleLayout,
    TREES,
    WATER,
    Waypoint,
)

GROUND_FILE = Path(__file__).parent / "courses_ground.json"

_FEATURE = {"bunker": BUNKER, "water": WATER, "trees": TREES}

# A surveyed green is used only where its fitted tilt is one a green can hold.
# Above this the plane was fitted through a false front, not the putting
# surface, so the tilt is not claimed. Kept under the proof's own ceiling of
# six per cent so a used green can never be one the proof would then refuse.
GREEN_TILT_CAP = 0.05


def load_ground(path=GROUND_FILE):
    """Every gathered course by name."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {course["name"]: course for course in data["courses"]}


def _scaled_to_length(waypoints, wanted):
    """The same shape, walked exactly the card's distance.

    The gathered centreline is stretched onto the card already, but only to
    within a twentieth of a yard - close enough for the data file, not close
    enough for a card that is checked hole for hole. Scaling down the hole only,
    the same way the gather does, so a bend keeps its width while its length is
    made exact.
    """
    def walked(factor):
        return sum(math.hypot((b.x - a.x) * factor, b.z - a.z)
                   for a, b in zip(waypoints, waypoints[1:]))
    got = walked(1.0)
    if not got or not wanted:
        return waypoints
    factor = wanted / got
    for _ in range(40):
        here = walked(factor)
        if abs(here - wanted) < 1e-7:
            break
        factor *= wanted / here
    return tuple(Waypoint(w.x * factor, w.z, w.height_yards) for w in waypoints)


def hole_from_ground(gh, guide=None):
    """One crawled hole as a playable Hole, marked surveyed.

    The centreline carries its own heights, so the waypoints are the shape and
    the climb together; the hazards are placed where the map put them; and the
    green is the one the survey measured only where that is a green a ball sits
    on.

    What a survey does not measure is taken from the guide-built hole it
    replaces: a green's pace, the out of bounds down one side, and the exact
    card length. A map has the sea and the sand but not the speed of a green,
    the wall behind the Road Hole, or the last inch of a yardage.
    """
    waypoints = _scaled_to_length(
        tuple(Waypoint(x, z, height)
              for (x, z), height in zip(gh["centreline"], gh["heights_yards"])),
        gh["length_yards"],
    )
    hazards = tuple(
        Hazard(_FEATURE[thing["kind"]], thing["along"], thing["across"],
               thing["radius_yards"])
        for thing in gh["hazards"] if thing["kind"] in _FEATURE
    )

    guide_green = guide.layout.green if guide else None
    pace = guide_green.stimp if guide_green else 10.0
    survey = gh.get("green") or {}
    green = (
        GreenContour(survey["falls_towards_deg"], survey["fall_per_yard"], pace)
        if survey.get("trustworthy")
        and survey.get("fall_per_yard", 1.0) <= GREEN_TILT_CAP
        else FLAT
    )
    radius = (gh.get("green_target_radius_yards")
              or gh.get("green_radius_yards") or 20.0)
    width = (gh.get("fairway_width_yards")
             or (guide.layout.fairway_width_yards if guide else 40.0))
    layout = HoleLayout(
        waypoints=waypoints,
        fairway_width_yards=width,
        green_radius_yards=radius,
        green=green,
        hazards=hazards,
        out_of_bounds_yards=guide.layout.out_of_bounds_yards if guide else None,
        out_of_bounds_side=guide.layout.out_of_bounds_side if guide else 0,
        surveyed=True,
    )
    return Hole(gh["number"], gh["length_yards"], gh["par"],
                stroke_index=gh["stroke_index"], layout=layout)
