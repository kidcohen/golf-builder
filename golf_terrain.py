"""The shape of a hole: where it goes, what it is made of, and what is in the way.

A hole used to be a length. That is enough to tell a golfer how far to hit it
and nothing else, which is why every hole played the same - two courses could
only differ in how far apart their numbers were.

Here a hole is a centreline of waypoints from tee to pin, each with a height.
Everything a designer does follows from those two things:

  A dogleg is a waypoint that is not on the line between tee and green. Aiming
  at the flag from the tee of a hole that turns is how you find the trees.

  Elevation is the height on each waypoint. The ball flight already knows what
  thinner air and a longer drop do to a shot, so a green fifty feet above the
  tee costs a club without anything here having to say so.

  Trouble is a hazard placed on the ground, and the corridor of fairway either
  side of the centreline. Miss the corridor and you are in rough; miss it badly
  and you are in whatever the designer put out there.

What the ball finishes in is a SurfaceFeature, the same thing the weather uses
in golf_surface - a bunker and a puddle are both "something you have to play out
of", and they cost a shot in the same terms. Designed ground answers to a plan
rather than to the rain, which is the only real difference between them.
"""

import math
from dataclasses import dataclass, field

from golf_surface import SurfaceFeature

# --- What a designer can put on the ground -----------------------------------
#
# Speed and spin retained are what a shot played from it keeps. The numbers say
# how much of the shot the lie takes away: sand takes a great deal, a good lie
# on the fairway takes nothing.

FAIRWAY = SurfaceFeature("fairway", "Fairway")
TEE_GROUND = SurfaceFeature("tee", "Tee")
GREEN = SurfaceFeature("green", "Putting")
FRINGE = SurfaceFeature("fringe", "Green-side", speed_retained=0.97, spin_retained=0.92)
ROUGH = SurfaceFeature("rough", "Rough", speed_retained=0.88, spin_retained=0.62)
DEEP_ROUGH = SurfaceFeature(
    "deep_rough", "Deep rough", speed_retained=0.72, spin_retained=0.40,
    stops_roll=True,
)
BUNKER = SurfaceFeature(
    "bunker", "Sand", speed_retained=0.80, spin_retained=0.85, stops_roll=True,
)
TREES = SurfaceFeature(
    "trees", "Under the trees", speed_retained=0.75, spin_retained=0.80,
)

# Trouble you do not play out of. These cost a stroke and send the ball
# somewhere else, which is the rules of golf rather than a lie at all.
WATER = SurfaceFeature(
    "water", "Water hazard", speed_retained=0.0, spin_retained=0.0, stops_roll=True,
)
OUT_OF_BOUNDS = SurfaceFeature(
    "out_of_bounds", "Out of bounds", speed_retained=0.0, spin_retained=0.0,
    stops_roll=True,
)

# A ball in these is not played from where it lies. Water is a stroke and a drop
# at the margin; out of bounds is stroke and distance, played again from where
# the last one was struck.
PENALTY_FEATURES = {WATER.kind: 1, OUT_OF_BOUNDS.kind: 1}
REPLAYED_FROM_LAST_SHOT = frozenset({OUT_OF_BOUNDS.kind})


def penalty_for(feature):
    """Strokes added for finishing here, before the ball is put back in play."""
    return PENALTY_FEATURES.get(getattr(feature, "kind", None), 0)


def is_playable(feature):
    """Whether the ball is played from where it finished."""
    return penalty_for(feature) == 0


@dataclass(frozen=True)
class Waypoint:
    """A point on the centreline, and how high the ground is there.

    Heights are yards above the tee, so a downhill hole carries negatives.
    """

    x: float
    z: float
    height_yards: float = 0.0


@dataclass(frozen=True)
class Hazard:
    """Something the designer put there, in a circle of a given size.

    Circles rather than outlines on purpose. A bunker is not round, but a golfer
    is either in it or not, and an outline buys nothing a radius does not except
    a great deal of arithmetic on every shot.
    """

    feature: SurfaceFeature
    x: float
    z: float
    radius_yards: float

    def holds(self, x, z):
        return math.hypot(x - self.x, z - self.z) <= self.radius_yards


@dataclass(frozen=True)
class GreenContour:
    """Which way a green falls, and how hard.

    One plane rather than a surface of humps and hollows. A green that tilts is
    most of what putting is: it decides where the ball finishes when it stops
    being struck and starts being carried, and it is the difference between a
    putt aimed at the hole and a putt aimed at a point three feet outside it.
    Humps are a refinement; a tilt is the foundation.

    `falls_towards_deg` is measured in the hole's own frame - 0 is towards the
    pin, 90 is to the right of that line. `fall_per_yard` is the gradient, so
    0.02 is a green losing about seven inches over ten yards.


    `stimp` is the stimpmeter reading, the feet a ball runs off a standard
    ramp. Around 8 is an ordinary members' green, 10 quick, 12 or 13 the sort
    cut for a championship. It matters as much as the tilt does: the same slope
    reads far more break at 12 than at 8, because a slower-moving ball spends
    longer on the hill and gravity has longer to work on it.
    """

    falls_towards_deg: float = 0.0
    fall_per_yard: float = 0.0
    stimp: float = 10.0

    @property
    def is_flat(self):
        return abs(self.fall_per_yard) < 1e-9

    @property
    def pace(self):
        """How this green compares with the one the break was calibrated on."""
        return max(float(self.stimp), 1.0) / BASELINE_STIMP

    def downhill(self):
        """The direction water would run, as a unit vector in x and z."""
        angle = math.radians(self.falls_towards_deg)
        return math.cos(angle), math.sin(angle)


FLAT = GreenContour()


@dataclass(frozen=True)
class HoleLayout:
    """Where a hole goes and what is on it."""

    waypoints: tuple
    fairway_width_yards: float = 40.0
    green_radius_yards: float = 20.0
    green: GreenContour = FLAT
    hazards: tuple = ()
    # Past this far off the centreline there is no course left. None leaves the
    # hole open, which is how a links hole with nothing but dunes either side
    # actually plays.
    out_of_bounds_yards: float = None
    # Which side the boundary is on: 0 for both, +1 for the right only, -1 for
    # the left. Most out of bounds is one-sided - a wall, a road, a garden -
    # and treating it as a ring around the hole turns a bad miss one way into
    # a lost ball either way. The Road Hole has a hotel down the right and
    # nothing at all down the left, and modelling that as both cost the hole
    # every time a golfer who sprays it played there.
    out_of_bounds_side: int = 0
    # Whether this hole's shape was read off a survey - the crawled ground, its
    # real centreline, hazards and heights - rather than described from a guide.
    # A surveyed hole is left as it was measured: the gap-filling that gives
    # every other hole a plausible tilt and a hill does not paint over ground
    # that is already known.
    surveyed: bool = False

    def __post_init__(self):
        if len(self.waypoints) < 2:
            raise ValueError("a hole runs from a tee to a pin, so at least two points")
        if self.fairway_width_yards <= 0:
            raise ValueError("fairway_width_yards must be positive")
        if self.green_radius_yards <= 0:
            raise ValueError("green_radius_yards must be positive")

    @property
    def tee(self):
        return self.waypoints[0]

    @property
    def pin(self):
        return self.waypoints[-1]

    @property
    def length_yards(self):
        """Along the centreline, which is the way the hole is walked.

        A dogleg measured tee to green flatters itself - the card says how far
        it plays, not how far the green is as the crow flies.
        """
        return sum(
            math.hypot(b.x - a.x, b.z - a.z)
            for a, b in zip(self.waypoints, self.waypoints[1:])
        )

    @property
    def elevation_change_yards(self):
        """Green above tee, in yards. Negative runs downhill."""
        return self.pin.height_yards - self.tee.height_yards

    @property
    def doglegs(self):
        """Whether the hole bends enough that the flag is not the line."""
        return self.turn_degrees > 5.0

    @property
    def turn_degrees(self):
        """How far the hole turns, adding up every bend along it."""
        total = 0.0
        for a, b, c in zip(self.waypoints, self.waypoints[1:], self.waypoints[2:]):
            first = math.atan2(b.z - a.z, b.x - a.x)
            second = math.atan2(c.z - b.z, c.x - b.x)
            turn = math.degrees(second - first)
            total += abs((turn + 180.0) % 360.0 - 180.0)
        return total


# How much rough there is either side of the fairway before the ground stops
# being worth playing from. Beyond the second band a course has usually stopped
# being a course, but that is for the layout to say rather than this.
ROUGH_BAND_YARDS = 12.0
FRINGE_SHARE_OF_GREEN = 1.25  # the apron, as a multiple of the green's radius

# The shortest a hole can be and still be one, per shot allowed to reach the
# green. Kept here because two things need to agree about it: whatever builds a
# hole, and whatever checks one. They did not - a tee could be walked far
# enough up a hole to leave sixty yards, which the proof would then reject as
# not golf, so a course could be built that the gate refused. One number now,
# in the module both of them already depend on.
SHORTEST_PLAYABLE_YARDS = 90.0


def straight_hole(length_yards, **kwargs):
    """A hole that goes where it looks like it goes, flat, fairway either side.

    What every hole was before there was anything to say otherwise, and still
    what a hole is when a course does not describe its shape.
    """
    return HoleLayout(
        waypoints=(Waypoint(0.0, 0.0, 0.0), Waypoint(float(length_yards), 0.0, 0.0)),
        **kwargs,
    )


def _project_onto_segment(x, z, start, end):
    """Where this point falls on a segment: how far along, and how far off.

    `along` is clamped to the segment, so a ball past the end of one segment
    measures from its end rather than off the end of the line it is not on.
    """
    run_x, run_z = end.x - start.x, end.z - start.z
    span = math.hypot(run_x, run_z)
    if span == 0.0:
        return 0.0, math.hypot(x - start.x, z - start.z), 0.0

    share = ((x - start.x) * run_x + (z - start.z) * run_z) / (span * span)
    clamped = min(max(share, 0.0), 1.0)
    foot_x = start.x + run_x * clamped
    foot_z = start.z + run_z * clamped
    return clamped * span, math.hypot(x - foot_x, z - foot_z), span


def station_of(layout, x, z):
    """How far along the hole this position is, and how far off the line.

    The nearest point on the centreline wins. On a hole that turns back on
    itself that is the only sensible reading - a ball is where it is, not where
    the walk to it has got to.
    """
    where, offset, _ = _station_and_side(layout, x, z)
    return where, offset


def _station_and_side(layout, x, z):
    """As above, plus which side of the line the ball is: +1 right, -1 left."""
    best = None
    travelled = 0.0
    for start, end in zip(layout.waypoints, layout.waypoints[1:]):
        along, offset, span = _project_onto_segment(x, z, start, end)
        if best is None or offset < best[1]:
            run_x, run_z = end.x - start.x, end.z - start.z
            across = run_x * (z - start.z) - run_z * (x - start.x)
            best = (travelled + along, offset, 1 if across >= 0 else -1)
        travelled += span
    return best


def height_at(layout, x, z):
    """Ground height under a position, read along the centreline.

    The hole is treated as a ribbon that rises and falls with its middle, so a
    ball off to the side is as high as the fairway beside it. A real course
    tilts sideways too; that is a refinement, not a foundation.
    """
    where, _ = station_of(layout, x, z)
    travelled = 0.0
    for start, end in zip(layout.waypoints, layout.waypoints[1:]):
        span = math.hypot(end.x - start.x, end.z - start.z)
        if where <= travelled + span or span == 0.0:
            share = 0.0 if span == 0.0 else (where - travelled) / span
            share = min(max(share, 0.0), 1.0)
            return start.height_yards + (end.height_yards - start.height_yards) * share
        travelled += span
    return layout.pin.height_yards


def distance_to_pin(layout, x, z):
    """Straight to the flag, which is what a golfer is playing to."""
    return math.hypot(layout.pin.x - x, layout.pin.z - z)


def aim_from(layout, x, z):
    """What to aim at from here: the flag, or the corner if the hole turns.

    Aiming at the green through the trees is a decision a golfer can make, but
    it should not be the one the simulator makes for them by default.
    """
    where, _ = station_of(layout, x, z)
    travelled = 0.0
    for start, end in zip(layout.waypoints, layout.waypoints[1:]):
        span = math.hypot(end.x - start.x, end.z - start.z)
        travelled += span
        if travelled > where + 1.0:  # the first point still ahead of the ball
            return end
    return layout.pin


def ground_at(layout, x, z):
    """What the ball is sitting in, by the design of the hole.

    Order matters: a bunker cut into the middle of a green is a bunker, and a
    hazard the designer put there beats whatever the ground would otherwise be.
    """
    for hazard in layout.hazards:
        if hazard.holds(x, z):
            return hazard.feature

    to_pin = distance_to_pin(layout, x, z)
    if to_pin <= layout.green_radius_yards:
        return GREEN
    if to_pin <= layout.green_radius_yards * FRINGE_SHARE_OF_GREEN:
        return FRINGE

    where, offset, side = _station_and_side(layout, x, z)
    if layout.out_of_bounds_yards is not None and offset > layout.out_of_bounds_yards:
        # Only out if the boundary is on the side the ball went.
        if layout.out_of_bounds_side in (0, side):
            return OUT_OF_BOUNDS

    half = layout.fairway_width_yards / 2.0
    if offset <= half and 0.0 < where < layout.length_yards:
        return FAIRWAY
    if offset <= half + ROUGH_BAND_YARDS:
        return ROUGH
    return DEEP_ROUGH


# How much a green's tilt takes hold of a rolling ball. Break grows with the
# square of the distance rolled: gravity pulls for the whole roll, and the ball
# is slowest and least able to hold its line at the end of it, which is why a
# long putt on the same slope breaks so much more than a short one.
BREAK_PER_GRADIENT_YARD = 0.55
# The same tilt along the line of the putt, lengthening it downhill and cutting
# it short uphill.
RUN_PER_GRADIENT = 6.0
# The green the numbers above were read against: quick, but not a championship
# cut. Everything else is measured relative to it.
BASELINE_STIMP = 10.0


def putt_break(layout, from_position, rest_position):
    """Where a putt actually finishes, once the green has had its say.

    Two things happen to a ball rolling across a tilted green. It is pulled
    sideways the whole way, more the longer it rolls, which is the break. And
    it is helped or held up along its own line, which is why the same stroke
    dies short uphill and runs past downhill.

    Struck rather than rolled shots are left alone. A ball still in the air is
    not being carried by anything.
    """
    green = layout.green
    if green.is_flat:
        return rest_position

    run_x = rest_position[0] - from_position[0]
    run_z = rest_position[2] - from_position[2]
    rolled = math.hypot(run_x, run_z)
    if rolled < 1e-6:
        return rest_position

    along_x, along_z = run_x / rolled, run_z / rolled
    fall_x, fall_z = green.downhill()
    gradient = green.fall_per_yard

    # Split the slope into the part along the putt and the part across it.
    downhill_share = (fall_x * along_x + fall_z * along_z) * gradient
    across_share = (fall_x * -along_z + fall_z * along_x) * gradient

    # A quick green takes more break out of the same slope, and gives up less
    # of the roll to friction, so both effects scale with its pace.
    pace = green.pace
    lengthened = rolled + downhill_share * RUN_PER_GRADIENT * rolled * pace
    lengthened = max(lengthened, 0.0)
    sideways = across_share * BREAK_PER_GRADIENT_YARD * rolled * rolled * pace

    return (
        from_position[0] + along_x * lengthened - along_z * sideways,
        0.0,
        from_position[2] + along_z * lengthened + along_x * sideways,
    )


def describe_layout(layout):
    """The hole as plain data, for something that has to draw it.

    Everything a client needs to put a hole on a screen and nothing it does
    not: where the ground goes and how high it is, how wide the fairway is,
    where the green is and which way it falls, and what trouble is where. No
    trajectories, no scores, no players - this is the ground, which does not
    change while the round is played.

    Distances are yards throughout, in the hole's own frame with the tee at the
    origin. Whatever draws this decides what a bunker looks like; the simulator
    only says there is one, how big, and where.
    """
    return {
        "centreline": [
            {"x": round(w.x, 2), "z": round(w.z, 2),
             "height_yards": round(w.height_yards, 2)}
            for w in layout.waypoints
        ],
        "length_yards": round(layout.length_yards, 1),
        "fairway_width_yards": layout.fairway_width_yards,
        "rough_band_yards": ROUGH_BAND_YARDS,
        "out_of_bounds_yards": layout.out_of_bounds_yards,
        "out_of_bounds_side": layout.out_of_bounds_side,
        "turn_degrees": round(layout.turn_degrees, 1),
        "elevation_change_yards": round(layout.elevation_change_yards, 2),
        "green": {
            "x": round(layout.pin.x, 2),
            "z": round(layout.pin.z, 2),
            "radius_yards": layout.green_radius_yards,
            "falls_towards_deg": round(layout.green.falls_towards_deg, 1),
            "fall_per_yard": round(layout.green.fall_per_yard, 4),
            "stimp": layout.green.stimp,
        },
        "hazards": [
            {"kind": h.feature.kind, "lie_type": h.feature.lie_type,
             "x": round(h.x, 2), "z": round(h.z, 2),
             "radius_yards": round(h.radius_yards, 1),
             "costs_a_stroke": penalty_for(h.feature) > 0}
            for h in layout.hazards
        ],
    }


def more_punishing(first, second):
    """The worse of two lies.

    Designed ground and weather both have a say in what a ball is sitting in -
    a bunker with an inch of water in it is not a bunker any more. Rather than
    rank one source above the other, whichever costs the shot more wins.
    """
    if first is None:
        return second
    if second is None:
        return first
    if penalty_for(second) != penalty_for(first):
        return second if penalty_for(second) > penalty_for(first) else first
    cost = lambda lie: (lie.speed_retained, lie.spin_retained)
    return second if cost(second) < cost(first) else first
