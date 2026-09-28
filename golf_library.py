"""The courses. Each one a real place, played under a name of its own.

Every course here is modelled on one of the world's ranked hundred-and-more,
and none of them borrows its name. What is copied is the golf: the yardage,
the par, the way the holes bend and climb, the trouble and where it sits. What
is not copied is the badge on the flag, because a yardage is a fact and a name
is somebody's property.

A course is a character before it is a list of holes. A links runs along the
ground in the wind with the trouble at your feet; a parkland course is longer
in the air with the trouble at head height; a desert course has no rough at
all, only the bits that are course and the bits that are not. Those differences
are what make two courses of the same length play nothing alike, and they are
what the terrain model exists to hold.

Every course in here has been round with golf_courseproof before it was
committed. A course that cannot be finished does not ship.
"""

import math
from dataclasses import dataclass, replace
from hashlib import blake2b

from golf_course import Hole
from golf_contours import load_contours, researched_green
from golf_ground_course import hole_from_ground, load_ground
from golf_terrain import (
    SHORTEST_PLAYABLE_YARDS,
    BUNKER,
    DEEP_ROUGH,
    TREES,
    WATER,
    GreenContour,
    Hazard,
    HoleLayout,
    Waypoint,
)

# What is and is not claimed about these courses, said once rather than
# implied eighteen times per course.
#
# The yardages, the pars and the order of the holes are off published cards and
# are checked against them. The shaping is not: which way a hole bends, how
# much it climbs, where the sand sits and how the green falls follow published
# hole descriptions, course guides and photographs of the place rather than a
# survey of it. The intent is a hole that plays like the hole. It is not a
# measured copy, and the difference matters enough to write down - somebody
# comparing this against a real yardage book should know which half to trust.
TERRAIN_PROVENANCE = (
    "Yardages, pars and hole order off published cards. Shaping - bends, "
    "elevation, bunkering and green contour - is read off the crawled survey "
    "on the holes the map covers, and follows published hole descriptions "
    "rather than survey data on the holes it does not. Each course lists which "
    "of its holes are surveyed, so a yardage book can be compared against the "
    "half that is measured and the half that is described."
)

# What a course is called when nobody has picked a set of tees.
DEFAULT_TEES = "regular"

LINKS = "links"
PARKLAND = "parkland"
HEATHLAND = "heathland"
CLIFFTOP = "clifftop"
# Melbourne's sandbelt: neither links nor parkland. Sand under everything, so
# the ground is fast all year, the bunkers are enormous with faces cut sheer,
# and the greens run quicker than anywhere outside a major.
SANDBELT = "sandbelt"


# How each sort of ground is shaped where the card does not say.
#
# A card gives yardages and pars and nothing else. Left at that, every hole
# would have one bunker, a dead flat green and no elevation, and eighteen
# courses would look and play alike no matter what was written about them in
# prose. So each character carries what its ground does: how much its greens
# tilt and how quick they are, how much sand there is and how close in it sits,
# and how much the land moves.
#
# Everything here is filled in only where a hole says nothing. A hole given its
# own contour, bunkering or elevation keeps exactly what it was given - the
# famous ones are described hole by hole and must not be overwritten by a
# average of their own course.
SHAPING = {
    LINKS: dict(
        # Links greens run fast and tilt gently; the trouble is at your feet.
        fall=(0.012, 0.028), stimp=(9.0, 10.5), bunkers=(3, 5),
        bunker_reach=(0.55, 0.95), bunker_offset=(8, 20), roll=(0.0, 5.0),
    ),
    PARKLAND: dict(
        # Built greens, cut into slopes and quicker than the ground they sit on.
        fall=(0.025, 0.05), stimp=(11.5, 13.5), bunkers=(2, 4),
        bunker_reach=(0.6, 0.92), bunker_offset=(14, 26), roll=(0.0, 9.0),
    ),
    SANDBELT: dict(
        # Sand under everything, greens as quick as any in the world.
        fall=(0.025, 0.045), stimp=(12.5, 13.5), bunkers=(3, 6),
        bunker_reach=(0.55, 0.94), bunker_offset=(12, 22), roll=(0.0, 4.0),
    ),
    CLIFFTOP: dict(
        # Small greens on exposed ground that falls towards the water.
        fall=(0.02, 0.04), stimp=(10.5, 12.0), bunkers=(2, 4),
        bunker_reach=(0.6, 0.93), bunker_offset=(10, 20), roll=(0.0, 7.0),
    ),
    HEATHLAND: dict(
        fall=(0.018, 0.035), stimp=(10.5, 12.0), bunkers=(2, 4),
        bunker_reach=(0.6, 0.92), bunker_offset=(12, 22), roll=(0.0, 6.0),
    ),
}

# Green contours read off published guides for the holes no metre survey reaches
# - Scotland, Ireland, Australia. Loaded once here because dressed() reads it
# while the courses are being built. Empty or all-null until it is filled, and a
# no-op until then: a hole with no read contour is shaped by character as before.
_CONTOURS = load_contours()


def _spread(seed, salt, low, high):
    """A number between low and high, fixed by the seed rather than by chance.

    A course has to look the same every time it is opened, so none of the
    shaping may be random - it is a function of which hole of which course it
    belongs to.
    """
    stamp = f"{seed}|{salt}".encode("utf-8")
    share = int.from_bytes(blake2b(stamp, digest_size=2).digest(), "big") / 65535.0
    return low + (high - low) * share


def dressed(hole_, character, seed, contours=None):
    """Fill in the shaping this hole was not given, in the manner of its ground.

    Only the gaps. A green already contoured keeps its contour, sand already
    placed stays where it was put, and a hole that climbs goes on climbing.
    """
    look = SHAPING.get(character, SHAPING[PARKLAND])
    layout = hole_.layout
    key = f"{seed}|{hole_.number}"

    green = layout.green
    if green.is_flat:
        # Every green tilts. A green that does not is a table, and putting on it
        # is a formality rather than a read. A surveyed green is never flat, so
        # this only ever shapes a green nobody measured: use the researched
        # contour if one has been read off a guide, and otherwise the course's
        # character. Survey first, then research, then character.
        pace = (green.stimp if green.stimp != 10.0
                else _spread(key, "pace", *look["stimp"]))
        read = researched_green(
            _CONTOURS if contours is None else contours, seed, hole_.number, pace)
        green = read if read is not None else GreenContour(
            falls_towards_deg=_spread(key, "aspect", 0.0, 360.0),
            fall_per_yard=_spread(key, "fall", *look["fall"]),
            stimp=pace,
        )

    # Surveyed holes keep the sand and the hills they were measured with. The
    # gap-filling below exists for holes described from a guide, which arrive
    # with a bunker count and a climb still to invent; a hole read off the
    # ground has both already, and topping it up would bury real sand under
    # made-up sand and paint a hill over one that is really there.
    hazards = layout.hazards
    if not layout.surveyed:
        wanted = int(_spread(key, "count", *look["bunkers"]))
        for extra in range(len(hazards), max(wanted, len(hazards))):
            along = _spread(key, f"reach{extra}", *look["bunker_reach"])
            out = _spread(key, f"offset{extra}", *look["bunker_offset"])
            side = 1 if _spread(key, f"side{extra}", 0.0, 1.0) > 0.5 else -1
            size = _spread(key, f"size{extra}", 5.0, 9.0)
            hazards = hazards + (_place(layout.waypoints, BUNKER,
                                        along, out * side, size),)

    waypoints = layout.waypoints
    if not layout.surveyed and abs(layout.elevation_change_yards) < 0.5:
        # Land moves. Flat ground is a driving range, not a golf course.
        rise = _spread(key, "rise", -look["roll"][1], look["roll"][1])
        waypoints = tuple(
            Waypoint(w.x, w.z, rise * (i / max(len(waypoints) - 1, 1)))
            for i, w in enumerate(waypoints)
        )

    return replace(hole_, layout=replace(
        layout, green=green, hazards=hazards, waypoints=waypoints))


@dataclass(frozen=True)
class Tees:
    """One set of tees, and what the card says from them.

    A course is not one length. The members play a different golf course from
    the one the championship is played on - Pinehurst is 6307 yards and par 72
    from the white tees and 7548 off par 70 for the U.S. Open, on the same
    eighteen greens. Both are real, both are published, and a golfer should be
    able to pick.

    Only the card changes. The hole is the same hole: the same bends, the same
    sand, the same greens falling the same ways.
    """

    name: str
    card: tuple          # (par, yards) for each hole, in order
    source: str


# What counts as a hole is decided in one place, so a tee cannot be walked far
# enough up to build something the course proof will not accept as golf.
SHORTEST_HOLE_YARDS = SHORTEST_PLAYABLE_YARDS


def at_length(layout, wanted_yards):
    """The same hole played from a tee further back, or further forward.

    A course is lengthened by moving the tee, not by stretching the hole.
    Everything else stays exactly where it was built - the corner is still at
    the corner and the bunker that catches a good drive is still at the
    distance it was dug for, which is the whole reason a longer tee is harder.
    Scaling the hole instead would move the trouble along with the golfer and
    change nothing about how it plays.

    Moving forward, the tee walks up the hole and takes the ground behind it
    with it. Far enough forward and it passes the corner, and then the dogleg
    is simply gone - which is what a forward tee does to a hole that bends, and
    why the members' course and the championship course are not the same golf
    even where they share a green.
    """
    have = layout.length_yards
    if have <= 0 or abs(wanted_yards - have) < 0.5:
        return layout

    if wanted_yards > have:
        return _tee_back(layout, wanted_yards - have)
    # Walk the tee up the hole by exactly the difference, so what is left
    # behind it is exactly the length asked for.
    return _tee_forward(layout, have - max(wanted_yards, SHORTEST_HOLE_YARDS))


def _tee_back(layout, by_yards):
    """Straight back down the line the first shot is played along."""
    tee, next_point = layout.waypoints[0], layout.waypoints[1]
    run_x, run_z = next_point.x - tee.x, next_point.z - tee.z
    span = math.hypot(run_x, run_z)
    if span <= 0:
        return layout
    moved = Waypoint(tee.x - run_x / span * by_yards,
                     tee.z - run_z / span * by_yards,
                     tee.height_yards)
    return replace(layout, waypoints=(moved,) + layout.waypoints[1:])


def _tee_forward(layout, by_yards):
    """Up the hole, dropping any corner the tee has walked past.

    A tee in front of the bend is not playing a dogleg. Keeping the corner as
    a waypoint would have the golfer aiming at a point behind them.
    """
    points = list(layout.waypoints)
    remaining = by_yards
    while len(points) >= 2:
        run_x = points[1].x - points[0].x
        run_z = points[1].z - points[0].z
        span = math.hypot(run_x, run_z)
        if span <= 0:
            points.pop(0)
            continue

        if remaining < span:
            share = remaining / span
            moved = Waypoint(points[0].x + run_x * share,
                             points[0].z + run_z * share,
                             points[0].height_yards)
            return replace(layout, waypoints=(moved,) + tuple(points[1:]))

        # The tee has walked past this corner, so the corner is behind it and
        # the hole no longer bends there.
        remaining -= span
        points.pop(0)

    # Nothing left to stand on. The hole cannot be played this short, which
    # from_tees reports rather than this pretending otherwise.
    return layout


# The tees a course actually offers, as a share of its full length.
#
# Every club sets several, and they are not arbitrary: the back tees are the
# card the course is rated from, and each set forward takes roughly seven per
# cent out of it. These shares are the ones the game is generally laid out to.
#
# Read this before trusting them. The full card of each course here was read
# off a published scorecard hole by hole. These sets are NOT: they are worked
# out from that card, because hole-by-hole yardages for every tee are published
# almost nowhere reachable - club sites give the championship card and the
# course databases that hold the rest refuse automated access. A course whose
# other tees have been found is given them properly and keeps them; these are
# what the rest get in the meantime, and every one says so in its own source
# line so nobody mistakes one for the other.
STANDARD_TEES = (
    ("back", 0.93, "one set in from the full card"),
    ("middle", 0.86, "the members' card on most days"),
    ("forward", 0.78, "shorter again"),
    ("front", 0.70, "the shortest a course is generally set up to play"),
)


# Where one par stops and the next begins. Taken from what the course proof
# already refuses rather than invented here, so a derived card cannot describe
# a hole in terms the rest of this would call impossible.
SHORTEST_PAR_FIVE = 430.0
SHORTEST_PAR_FOUR = 240.0


def par_for(length_yards, published_par):
    """What a hole of this length is, once a tee has been moved up to it.

    Par was left alone at first, on the grounds that which hole a club turns
    into a par 4 is the club's decision and not mine to guess. The proof threw
    out a third of the tee sets for it: a par 5 shortened to four hundred yards
    is not a par 5, it is a par 4 with the wrong number on it, and every course
    with a reachable par 5 produced one. Clubs change par on forward tees for
    exactly this reason.

    So par follows length, by the same thresholds the proof uses to decide
    whether a card makes sense. It never goes up - a hole does not get harder
    for being shorter.
    """
    if published_par >= 5 and length_yards < SHORTEST_PAR_FIVE:
        return 4 if length_yards >= SHORTEST_PAR_FOUR else 3
    if published_par >= 4 and length_yards < SHORTEST_PAR_FOUR:
        return 3
    return published_par


def standard_tee_sets(course):
    """The usual sets of tees for a course that publishes only its full card."""
    built = []
    for name, share, about in STANDARD_TEES:
        card = []
        for hole in course.holes:
            yards = max(round(hole.length_yards * share),
                        int(SHORTEST_PLAYABLE_YARDS))
            card.append((par_for(yards, hole.par), yards))
        card = tuple(card)
        built.append(Tees(
            name=name,
            card=card,
            source=(
                f"DERIVED, not read off a card: {about}, at {share:.0%} of "
                f"{course.name}'s full length, floored so no hole falls below "
                f"{SHORTEST_PLAYABLE_YARDS:.0f} yards. The full card is "
                "researched; this set is arithmetic on it, and is here because "
                "hole-by-hole yardages for every tee are not published "
                "anywhere reachable."
            ),
        ))
    return tuple(built)


@dataclass(frozen=True)
class Course:
    """A course, and the honest note about where it came from.

    `card_source` says where the yardages and pars were read from, and
    `off_the_card` lists the holes actually taken from it. Anything not in
    that list was filled to match the course's published total, because not
    every club publishes a hole-by-hole card.

    That distinction is recorded rather than described, so it can be checked.
    A course claiming to be somewhere real ought to be able to show its
    working, and a hole nobody can source should say so instead of passing
    quietly as fact.
    """

    name: str
    after: str          # the real course this is modelled on
    location: str       # a real place, so the weather is the weather
    character: str
    holes: tuple
    # Required, and deliberately so. A course cannot be added to this library
    # without saying where its card came from, because the one time these were
    # written without that the yardages were mine rather than anybody's and
    # nothing in the code could tell the difference.
    card_source: str
    off_the_card: tuple
    # Other tees the same eighteen can be played from. The holes above are the
    # default set; these are alternatives a golfer may choose.
    tees: tuple = ()
    # Which holes were shaped off the crawled survey rather than a guide - real
    # centreline, sand, water and heights. Recorded the way off_the_card records
    # sourced yardages, so a course can show which half of its shaping is
    # measured and which is described.
    surveyed_holes: tuple = ()

    def tee_names(self):
        """Every set of tees this course can be played from, longest first.

        In order of length rather than the order they were written down. A
        golfer picking tees is picking how long a course they want to play, and
        a list that reads regular, championship, back is a list nobody can use -
        which is what Pinehurst gave, its researched members' card sitting
        above the championship one because it was declared first.
        """
        lengths = [(self.yards, DEFAULT_TEES)]
        lengths += [(float(sum(yards for _, yards in tees.card)), tees.name)
                    for tees in self.tees]
        return tuple(name for _, name in
                     sorted(lengths, key=lambda pair: -pair[0]))

    def from_tees(self, name):
        """The same course, played from another set of tees.

        The greens, the sand, the bends and the ground are untouched. Only the
        card changes, and each hole's tee moves to make its yardage right.
        """
        if not name or name == DEFAULT_TEES:
            return self
        chosen = next((t for t in self.tees if t.name.lower() == name.lower()), None)
        if chosen is None:
            raise ValueError(
                f"{self.name} has no {name!r} tees. It has: "
                + ", ".join(self.tee_names()))
        if len(chosen.card) != len(self.holes):
            raise ValueError(
                f"{self.name} {chosen.name} tees describe {len(chosen.card)} "
                f"holes, not {len(self.holes)}")

        played = tuple(
            replace(hole_, par=par, length_yards=yards,
                    layout=at_length(hole_.layout, yards))
            for hole_, (par, yards) in zip(self.holes, chosen.card)
        )

        # A tee that cannot be put where the card wants it is a fault in the
        # data, not something to approximate quietly. Moving a tee forward on a
        # hole that bends runs out of room at the corner, and the hole then
        # comes out at whatever length it could manage - the card saying one
        # thing and the ground being another, which is the exact failure every
        # other check here exists to prevent.
        adrift = [
            f"hole {built.number} wants {wanted:.0f} yards and can only be built "
            f"at {built.length_yards:.0f}"
            for built, (_, wanted) in zip(played, chosen.card)
            if abs(built.length_yards - wanted) >= 0.5
        ]
        if adrift:
            raise ValueError(
                f"{self.name} cannot be laid out from the {chosen.name} tees: "
                + "; ".join(adrift)
            )
        return replace(self, holes=played, card_source=chosen.source, tees=())

    def __post_init__(self):
        if not str(self.card_source).strip():
            raise ValueError(f"{self.name} does not say where its card came from")
        numbers = {hole.number for hole in self.holes}
        stray = set(self.off_the_card) - numbers
        if stray:
            raise ValueError(f"{self.name} sources holes it does not have: {sorted(stray)}")

        # Whatever the card did not describe, the ground supplies. Done here so
        # every course gets it rather than only the ones written most carefully,
        # and so a hole described in detail keeps every detail it was given.
        object.__setattr__(self, "holes", tuple(
            dressed(hole_, self.character, self.name) for hole_ in self.holes))

    @property
    def fully_sourced(self):
        """Whether every hole's yardage and par came from a published card."""
        return len(self.off_the_card) == len(self.holes)

    @property
    def par(self):
        return sum(hole.par for hole in self.holes)

    @property
    def yards(self):
        return sum(hole.length_yards for hole in self.holes)

    def __str__(self):
        return (f"{self.name} ({self.character}, after {self.after}) - "
                f"par {self.par}, {self.yards:.0f} yards")


def hole(number, par, yards, stroke_index, *, bend_deg=0.0, bend_at=0.55,
         rise_yards=0.0, width=40.0, green=18.0, falls=None, stimp=10.0,
         sand=(), water=(), trees=(), fenced=None, fenced_side=0):
    """One hole, described the way a course guide describes it.

    `bend_deg` turns the hole right (positive) or left, at `bend_at` of the way
    down it. `rise_yards` is how much the green sits above the tee. Trouble is
    given as fractions of the way down the hole and yards off the centreline,
    which is how it is actually described - "bunkers at 280 down the left".
    """
    waypoints = _shape(yards, bend_deg, bend_at, rise_yards)
    hazards = (
        tuple(_place(waypoints, BUNKER, *spec) for spec in sand)
        + tuple(_place(waypoints, WATER, *spec) for spec in water)
        + tuple(_place(waypoints, TREES, *spec) for spec in trees)
    )
    return Hole(number, yards, par, stroke_index=stroke_index, layout=HoleLayout(
        waypoints=waypoints,
        fairway_width_yards=width,
        green_radius_yards=green,
        green=GreenContour(falls[0], falls[1], stimp) if falls else GreenContour(stimp=stimp),
        hazards=hazards,
        out_of_bounds_yards=fenced,
        out_of_bounds_side=fenced_side,
    ))


def _shape(yards, bend_deg, bend_at, rise_yards):
    """Tee, corner if it turns, green - with the ground rising as it goes."""
    if abs(bend_deg) < 1.0:
        return (Waypoint(0.0, 0.0, 0.0), Waypoint(yards, 0.0, rise_yards))

    first = yards * bend_at
    second = yards - first
    turn = math.radians(bend_deg)
    corner = Waypoint(first, 0.0, rise_yards * bend_at)
    return (
        Waypoint(0.0, 0.0, 0.0),
        corner,
        Waypoint(first + second * math.cos(turn),
                 second * math.sin(turn),
                 rise_yards),
    )


def _place(waypoints, feature, down_the_hole, offset_yards, radius_yards):
    """Trouble described as a course guide would, put where it belongs."""
    spans = [
        math.hypot(b.x - a.x, b.z - a.z) for a, b in zip(waypoints, waypoints[1:])
    ]
    wanted = sum(spans) * down_the_hole
    travelled = 0.0
    for (start, end), span in zip(zip(waypoints, waypoints[1:]), spans):
        if travelled + span >= wanted or span == 0.0:
            share = 0.0 if span == 0.0 else (wanted - travelled) / span
            along_x = (end.x - start.x) / (span or 1.0)
            along_z = (end.z - start.z) / (span or 1.0)
            return Hazard(
                feature,
                start.x + (end.x - start.x) * share - along_z * offset_yards,
                start.z + (end.z - start.z) * share + along_x * offset_yards,
                radius_yards,
            )
        travelled += span
    last = waypoints[-1]
    return Hazard(feature, last.x, last.z + offset_yards, radius_yards)


# --- The courses -------------------------------------------------------------
#
# Ranked first in the world, and the hardest test in golf: sand and scrub
# between every fairway and every green, so there is no such thing as a bad lie
# here - there is fairway, and there is not being on it. Narrow, unforgiving,
# and not especially long.

PINE_HOLLOW = Course(
    name="Pine Hollow",
    after="Pine Valley Golf Club, New Jersey (ranked 1st)",
    location="Pine Valley, New Jersey",
    character=PARKLAND,
    holes=(
        hole(1, 4, 421, 11, width=30, sand=((0.62, 26, 14),),
             trees=((0.5, -34, 16),)),
        hole(2, 4, 368, 15, width=28, bend_deg=-8, sand=((0.7, -22, 12),)),
        hole(3, 3, 198, 13, width=26, green=15, falls=(120, 0.025),
             sand=((0.9, 20, 13), (0.9, -20, 13))),
        hole(4, 4, 499, 3, width=30, rise_yards=6, sand=((0.66, 24, 15),)),
        hole(5, 3, 238, 1, width=26, green=16, water=((0.55, 0, 20),)),
        hole(6, 4, 438, 7, width=28, bend_deg=7, sand=((0.72, 26, 12),)),
        hole(7, 5, 636, 9, width=32, sand=((0.55, 0, 26), (0.85, 22, 14))),
        hole(8, 4, 326, 17, width=26, green=15, sand=((0.78, -20, 12),)),
        hole(9, 4, 458, 5, width=30, rise_yards=8, sand=((0.7, 26, 14),)),
        hole(10, 3, 161, 18, width=26, green=14, falls=(90, 0.03),
             sand=((0.88, -16, 11),)),
        hole(11, 4, 397, 14, width=30, sand=((0.68, 24, 13),)),
        hole(12, 4, 358, 16, width=28, bend_deg=-10, trees=((0.55, -30, 15),)),
        hole(13, 4, 486, 4, width=32, rise_yards=5, sand=((0.7, 26, 15),)),
        hole(14, 3, 210, 10, width=26, green=15, water=((0.75, -18, 14),)),
        hole(15, 5, 615, 12, width=32, water=((0.42, 0, 22),),
             sand=((0.86, 22, 13),)),
        hole(16, 4, 475, 6, width=30, bend_deg=9, sand=((0.72, 26, 14),)),
        hole(17, 4, 414, 8, width=28, green=16, sand=((0.8, -20, 12),)),
        hole(18, 4, 483, 2, width=30, rise_yards=7, sand=((0.68, 26, 15),)),
    ),
    card_source=(
        "Pine Valley championship card - par 70, 7181 yards, all eighteen "
        "holes. An earlier version of this course used the club's 6699 yard "
        "back-tee total with most of the holes filled in; this is the full "
        "card. Stroke indexes derived by difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Second in the world, and nothing like the first: wide off the tee, no rough
# worth the name, and all of the defence in the greens - vast, fast and tilted
# so that being on the wrong side of the pin is worse than being off the green.
AZALEA_NATIONAL = Course(
    name="Azalea National",
    after="Augusta National Golf Club, Georgia (ranked 2nd)",
    location="Augusta, Georgia",
    character=PARKLAND,
    holes=(
        hole(1, 4, 445, 5, width=52, rise_yards=9, stimp=13, falls=(150, 0.03),
             sand=((0.72, 24, 15),)),
        hole(2, 5, 585, 11, width=56, rise_yards=-12, stimp=13, bend_deg=-8,
             falls=(200, 0.035), sand=((0.88, -22, 16),)),
        hole(3, 4, 350, 17, width=50, stimp=13, green=16, falls=(120, 0.04),
             sand=((0.62, -20, 13),)),
        hole(4, 3, 240, 3, width=46, stimp=13, green=17, falls=(160, 0.03),
             sand=((0.9, -18, 13),)),
        hole(5, 4, 495, 1, width=50, rise_yards=8, stimp=13, bend_deg=-7,
             falls=(140, 0.035), sand=((0.66, -24, 16),)),
        hole(6, 3, 180, 15, width=46, stimp=13, green=17, rise_yards=-9,
             falls=(210, 0.045)),
        hole(7, 4, 450, 9, width=44, stimp=13, green=15, falls=(180, 0.035),
             sand=((0.9, 18, 12), (0.9, -18, 12))),
        hole(8, 5, 570, 13, width=54, rise_yards=14, stimp=13, bend_deg=6,
             falls=(170, 0.03)),
        hole(9, 4, 460, 7, width=50, rise_yards=11, stimp=13, falls=(180, 0.045),
             sand=((0.85, -20, 14),)),
        hole(10, 4, 495, 2, width=52, rise_yards=-18, stimp=13, bend_deg=-9,
             falls=(150, 0.04), sand=((0.8, -22, 15),)),
        hole(11, 4, 520, 4, width=48, rise_yards=-10, stimp=13,
             falls=(200, 0.035), water=((0.94, -20, 14),)),
        hole(12, 3, 155, 16, width=44, stimp=13, green=14, falls=(190, 0.03),
             water=((0.72, 0, 15),)),
        hole(13, 5, 545, 14, width=52, stimp=13, bend_deg=-14,
             falls=(160, 0.04), water=((0.93, 0, 16),)),
        hole(14, 4, 440, 10, width=50, rise_yards=7, stimp=13, falls=(140, 0.05)),
        hole(15, 5, 550, 12, width=52, rise_yards=-8, stimp=13,
             falls=(180, 0.04), water=((0.9, 0, 15),)),
        hole(16, 3, 170, 18, width=46, stimp=13, green=16, falls=(220, 0.045),
             water=((0.72, -16, 14),)),
        hole(17, 4, 440, 8, width=48, rise_yards=6, stimp=13, falls=(160, 0.035),
             trees=((0.5, -26, 14),)),
        hole(18, 4, 465, 6, width=46, rise_yards=15, stimp=13, bend_deg=8,
             falls=(150, 0.035), sand=((0.62, -22, 15),)),
    ),
    card_source="Augusta National scorecard via Wikipedia - par 72, 7555 yards",
    off_the_card=tuple(range(1, 19)),
)

# The home of the game, and the one that plays least like anywhere else: vast
# shared fairways, almost no water, and pot bunkers that cost a stroke to get
# out of sideways. The wind is the course. Nothing here is fenced, because
# there is nothing to fence it from.
AULD_LINKS = Course(
    name="Auld Links",
    after="St Andrews Links, Old Course, Scotland (ranked 9th)",
    location="St Andrews, Scotland",
    character=LINKS,
    holes=(
        hole(1, 4, 376, 15, width=90, green=22, stimp=9),
        hole(2, 4, 453, 3, width=70, green=20, stimp=9, sand=((0.72, 18, 6),)),
        hole(3, 4, 397, 11, width=72, green=20, stimp=9, sand=((0.68, -16, 5),)),
        hole(4, 4, 480, 1, width=68, green=20, stimp=9, sand=((0.66, 20, 6),)),
        hole(5, 5, 568, 9, width=78, green=24, stimp=9, sand=((0.6, -18, 6),)),
        hole(6, 4, 412, 7, width=70, green=20, stimp=9, sand=((0.7, 18, 5),)),
        hole(7, 4, 371, 13, width=68, green=20, stimp=9, bend_deg=9,
             sand=((0.78, 16, 5),)),
        hole(8, 3, 175, 17, width=60, green=18, stimp=9, sand=((0.88, -14, 5),)),
        hole(9, 4, 352, 5, width=76, green=20, stimp=9),
        hole(10, 4, 386, 12, width=74, green=20, stimp=9),
        hole(11, 3, 174, 18, width=58, green=17, stimp=9, falls=(180, 0.035),
             sand=((0.86, 14, 5),)),
        hole(12, 4, 348, 16, width=70, green=20, stimp=9, sand=((0.62, 0, 5),)),
        hole(13, 4, 465, 2, width=68, green=20, stimp=9, sand=((0.64, -18, 6),)),
        hole(14, 5, 618, 4, width=72, green=22, stimp=9,
             sand=((0.55, 16, 7), (0.8, -14, 5))),
        hole(15, 4, 455, 8, width=70, green=20, stimp=9, sand=((0.7, 16, 5),)),
        hole(16, 4, 423, 10, width=66, green=20, stimp=9, sand=((0.68, -16, 5),)),
        # The Road Hole. The wall and the hotel are down the right and there is
        # nothing at all down the left, so the boundary is on one side only.
        hole(17, 4, 495, 6, width=58, green=18, stimp=9, bend_deg=7,
             sand=((0.92, -12, 4),), fenced=62, fenced_side=1),
        hole(18, 4, 357, 14, width=96, green=22, stimp=9),
    ),
    card_source="Old Course championship card via Wikipedia - par 72, 7305 yards",
    off_the_card=tuple(range(1, 19)),
)

# Third in the world. Blind drives over dunes, gorse to both sides, and
# bunkers with eyebrows of marram grass hanging over them. Small greens by
# links standards, and the narrowest driving of any of these.
SLIEVE_LINKS = Course(
    name="Slieve Links",
    after="Royal County Down Golf Club, Northern Ireland (ranked 3rd)",
    location="Newcastle, Northern Ireland",
    character=LINKS,
    holes=(
        hole(1, 5, 539, 15, width=52, green=17, stimp=9, sand=((0.6, -18, 7),)),
        hole(2, 4, 444, 7, width=46, green=16, stimp=9, bend_deg=-7,
             sand=((0.68, -16, 6),)),
        hole(3, 4, 475, 3, width=46, green=16, stimp=9, sand=((0.66, 18, 6),)),
        hole(4, 3, 229, 5, width=42, green=15, stimp=9, falls=(150, 0.03),
             sand=((0.9, -14, 6),)),
        hole(5, 4, 440, 9, width=46, green=16, stimp=9, rise_yards=6,
             sand=((0.7, 16, 6),)),
        hole(6, 4, 396, 13, width=48, green=17, stimp=9, sand=((0.72, -16, 6),)),
        hole(7, 3, 144, 17, width=40, green=14, stimp=9, sand=((0.88, 12, 5),)),
        hole(8, 4, 429, 11, width=46, green=16, stimp=9, bend_deg=8,
             sand=((0.7, 16, 6),)),
        hole(9, 4, 483, 1, width=44, green=16, stimp=9, rise_yards=-8,
             sand=((0.64, -18, 7),)),
        hole(10, 3, 196, 12, width=42, green=15, stimp=9, sand=((0.88, -14, 5),)),
        hole(11, 4, 442, 6, width=46, green=16, stimp=9, sand=((0.68, 16, 6),)),
        hole(12, 5, 525, 16, width=52, green=18, stimp=9, sand=((0.62, -16, 6),)),
        hole(13, 4, 446, 4, width=44, green=16, stimp=9, bend_deg=-8,
             sand=((0.7, -16, 6),)),
        hole(14, 3, 212, 10, width=42, green=15, stimp=9, falls=(120, 0.03),
             sand=((0.9, 14, 5),)),
        hole(15, 4, 468, 2, width=44, green=16, stimp=9, rise_yards=7,
             sand=((0.68, 18, 6),)),
        hole(16, 4, 337, 18, width=50, green=17, stimp=9, sand=((0.78, -14, 5),)),
        hole(17, 4, 433, 8, width=46, green=16, stimp=9, sand=((0.7, 16, 6),)),
        hole(18, 5, 548, 14, width=52, green=18, stimp=9, sand=((0.6, -16, 6),)),
    ),
    card_source=(
        "Royal County Down championship links card (blue tees) via Wikipedia - "
        "par 71, 7186 yards. Stroke indexes derived by difficulty; the club's "
        "own allotment is not published there."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Seventh, and the fairest links of the lot: two loops running in opposite
# directions so the wind never sits behind you for long, nothing blind, and
# every bit of the trouble in front of you where you can see it. The bunkers
# are the deepest here and cost a stroke to escape sideways.
GULLANE_HONOURS = Course(
    name="Gullane Honours",
    after="Muirfield, Honourable Company of Edinburgh Golfers (ranked 7th)",
    location="Gullane, Scotland",
    character=LINKS,
    holes=(
        hole(1, 4, 450, 5, width=54, green=18, stimp=10, sand=((0.7, 18, 5),)),
        hole(2, 4, 367, 17, width=56, green=18, stimp=10, sand=((0.76, -16, 5),)),
        hole(3, 4, 379, 9, width=54, green=18, stimp=10, bend_deg=7,
             sand=((0.74, 16, 5),)),
        hole(4, 3, 229, 3, width=46, green=16, stimp=10, falls=(140, 0.03),
             sand=((0.9, -14, 5),)),
        hole(5, 5, 561, 11, width=58, green=19, stimp=10, sand=((0.6, 18, 6),)),
        hole(6, 4, 469, 1, width=52, green=18, stimp=10, bend_deg=-8,
             sand=((0.68, -18, 5),)),
        hole(7, 3, 187, 13, width=46, green=16, stimp=10, sand=((0.88, 14, 5),)),
        hole(8, 4, 445, 7, width=52, green=18, stimp=10, sand=((0.7, 18, 6),)),
        hole(9, 5, 558, 15, width=56, green=19, stimp=10, sand=((0.64, -16, 5),),
             fenced=54),
        hole(10, 4, 472, 6, width=52, green=18, stimp=10, sand=((0.68, 18, 5),)),
        hole(11, 4, 389, 10, width=54, green=18, stimp=10, sand=((0.74, -16, 5),)),
        hole(12, 4, 382, 16, width=54, green=18, stimp=10, bend_deg=6,
             sand=((0.76, 16, 5),)),
        hole(13, 3, 193, 12, width=46, green=16, stimp=10, sand=((0.88, -14, 5),)),
        hole(14, 4, 478, 2, width=52, green=18, stimp=10, sand=((0.66, 18, 6),)),
        hole(15, 4, 447, 8, width=52, green=18, stimp=10, sand=((0.7, -18, 5),)),
        hole(16, 3, 188, 18, width=46, green=16, stimp=10, falls=(200, 0.03),
             sand=((0.88, 14, 5),)),
        hole(17, 5, 578, 14, width=56, green=19, stimp=10, bend_deg=-7,
             sand=((0.62, -18, 6),)),
        hole(18, 4, 473, 4, width=50, green=18, stimp=10, sand=((0.68, 18, 6),)),
    ),
    card_source=(
        "Muirfield championship card via Wikipedia - par 71, 7245 yards. "
        "Stroke indexes derived by difficulty; the club's own allotment is not "
        "published there."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Eighth. Links golf on Long Island: fescue to the waist off every fairway,
# greens perched up where the wind gets at them, and a course that has no
# water and does not need any.
SEBONACK_HILLS = Course(
    name="Sebonack Hills",
    after="Shinnecock Hills Golf Club, New York (ranked 8th)",
    location="Southampton, New York",
    character=LINKS,
    holes=(
        hole(1, 4, 399, 13, width=44, green=17, stimp=11, sand=((0.72, 16, 6),)),
        hole(2, 3, 252, 1, width=40, green=15, stimp=11, rise_yards=5,
             falls=(150, 0.03), sand=((0.9, -14, 6),)),
        hole(3, 4, 500, 3, width=44, green=17, stimp=11, bend_deg=-7,
             sand=((0.68, -16, 6),)),
        hole(4, 4, 475, 9, width=42, green=16, stimp=11, sand=((0.7, 16, 6),)),
        hole(5, 5, 589, 15, width=48, green=18, stimp=11, sand=((0.6, -16, 6),)),
        hole(6, 4, 491, 5, width=42, green=16, stimp=11, rise_yards=7,
             sand=((0.68, 18, 6),)),
        hole(7, 3, 189, 17, width=40, green=15, stimp=11, falls=(120, 0.035),
             sand=((0.88, 14, 6), (0.88, -14, 6))),
        hole(8, 4, 439, 11, width=44, green=17, stimp=11, sand=((0.72, -16, 6),)),
        hole(9, 4, 485, 7, width=42, green=16, stimp=11, rise_yards=8,
             sand=((0.68, 16, 6),)),
        hole(10, 4, 415, 8, width=44, green=17, stimp=11, sand=((0.72, 16, 6),)),
        hole(11, 3, 159, 18, width=40, green=14, stimp=11, falls=(200, 0.04),
             sand=((0.88, -12, 5),)),
        hole(12, 4, 469, 6, width=42, green=16, stimp=11, bend_deg=7,
             sand=((0.7, 16, 6),)),
        hole(13, 4, 374, 14, width=46, green=17, stimp=11, sand=((0.76, -14, 6),)),
        hole(14, 4, 519, 2, width=42, green=16, stimp=11, rise_yards=6,
             sand=((0.66, 18, 6),)),
        hole(15, 4, 409, 12, width=44, green=17, stimp=11, sand=((0.72, -16, 6),)),
        hole(16, 5, 616, 10, width=48, green=18, stimp=11, sand=((0.62, 16, 6),)),
        hole(17, 3, 175, 16, width=40, green=15, stimp=11, sand=((0.88, 14, 5),)),
        hole(18, 4, 485, 4, width=42, green=16, stimp=11, rise_yards=9,
             sand=((0.68, -18, 6),)),
    ),
    card_source=(
        "Shinnecock Hills card via Wikipedia - par 70, 7440 yards. Stroke "
        "indexes derived by difficulty; the club's own allotment is not "
        "published there."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Tenth, and the hardest par in America. No water, almost no trees, and none
# of it needed: ditches, furrowed sand, rough that swallows a ball whole, and
# greens quicker than anywhere else in the world - which is why the tilt on
# them matters more here than the length of anything.
FURROW_RIDGE = Course(
    name="Furrow Ridge",
    after="Oakmont Country Club, Pennsylvania (ranked 10th)",
    location="Oakmont, Pennsylvania",
    character=PARKLAND,
    holes=(
        hole(1, 4, 482, 5, width=40, green=16, stimp=14, rise_yards=-7,
             falls=(180, 0.04), sand=((0.68, 18, 7),)),
        hole(2, 4, 346, 17, width=40, green=15, stimp=14, falls=(160, 0.045),
             sand=((0.76, -16, 7),)),
        hole(3, 4, 467, 9, width=38, green=16, stimp=14, falls=(150, 0.035),
             sand=((0.55, 16, 9), (0.55, -16, 9))),  # the church pews
        hole(4, 5, 612, 13, width=42, green=17, stimp=14, falls=(170, 0.03),
             sand=((0.58, -18, 8),)),
        hole(5, 4, 410, 11, width=40, green=16, stimp=14, falls=(140, 0.04),
             sand=((0.74, 16, 7),)),
        hole(6, 3, 203, 15, width=38, green=15, stimp=14, falls=(200, 0.04),
             sand=((0.88, -14, 6),)),
        hole(7, 4, 487, 3, width=38, green=16, stimp=14, falls=(160, 0.035),
             sand=((0.68, 18, 7),)),
        hole(8, 3, 293, 1, width=38, green=16, stimp=14, falls=(190, 0.03),
             sand=((0.9, -16, 8),)),
        hole(9, 4, 475, 7, width=40, green=17, stimp=14, rise_yards=8,
             falls=(150, 0.045), sand=((0.7, 16, 7),)),
        hole(10, 4, 460, 8, width=40, green=16, stimp=14, falls=(170, 0.04),
             sand=((0.7, -18, 7),)),
        hole(11, 4, 398, 12, width=40, green=16, stimp=14, falls=(150, 0.04),
             sand=((0.74, 16, 7),)),
        hole(12, 5, 663, 10, width=44, green=17, stimp=14, falls=(180, 0.03),
             sand=((0.6, -18, 8),)),
        hole(13, 3, 186, 16, width=38, green=15, stimp=14, falls=(210, 0.045),
             sand=((0.88, 14, 6),)),
        hole(14, 4, 381, 14, width=40, green=16, stimp=14, falls=(160, 0.04),
             sand=((0.76, -16, 7),)),
        hole(15, 4, 509, 2, width=38, green=16, stimp=14, falls=(150, 0.035),
             sand=((0.66, 18, 8),)),
        hole(16, 3, 237, 6, width=38, green=15, stimp=14, falls=(180, 0.04),
             sand=((0.9, -14, 6),)),
        hole(17, 4, 317, 18, width=42, green=15, stimp=14, rise_yards=6,
             falls=(140, 0.05), sand=((0.82, 14, 7),)),
        hole(18, 4, 505, 4, width=38, green=16, stimp=14, falls=(160, 0.04),
             sand=((0.68, -18, 8),)),
    ),
    card_source=(
        "Oakmont championship card via Wikipedia - 7431 yards. The source says "
        "par 71 in its summary and 35+35 in its own nine totals; the hole-by-hole "
        "card sums to 70, which is the U.S. Open configuration it also mentions. "
        "The holes are followed rather than the summary. Stroke indexes derived "
        "by difficulty; the club's own allotment is not published there."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Sixth, and the shortest of the lot by four hundred yards - which tells you
# how little length has to do with it. Cypress and sand dunes for thirteen
# holes, then out onto the cliffs where three of them are played along and
# over the Pacific. The 16th asks for 233 yards across open ocean.
CYPRESS_HEAD = Course(
    name="Cypress Head",
    after="Cypress Point Club, California (ranked 6th)",
    location="Pebble Beach, California",
    character=CLIFFTOP,
    holes=(
        hole(1, 4, 420, 1, width=40, green=15, stimp=11, trees=((0.55, -26, 14),)),
        hole(2, 5, 549, 7, width=44, green=16, stimp=11, sand=((0.62, 18, 8),)),
        hole(3, 3, 156, 15, width=36, green=14, stimp=11, sand=((0.88, -14, 7),)),
        hole(4, 4, 383, 3, width=38, green=15, stimp=11, bend_deg=-8,
             trees=((0.6, -24, 14),)),
        hole(5, 5, 491, 13, width=42, green=16, stimp=11, rise_yards=6,
             trees=((0.5, 26, 15),)),
        hole(6, 5, 521, 9, width=42, green=16, stimp=11, sand=((0.66, -18, 8),)),
        hole(7, 3, 170, 11, width=36, green=14, stimp=11, falls=(140, 0.03),
             sand=((0.88, 14, 7),)),
        hole(8, 4, 369, 5, width=38, green=15, stimp=11, bend_deg=9,
             sand=((0.72, 16, 7),)),
        hole(9, 4, 289, 17, width=40, green=15, stimp=11, sand=((0.78, -14, 7),)),
        hole(10, 5, 476, 16, width=42, green=16, stimp=11, sand=((0.68, 16, 8),)),
        hole(11, 4, 440, 4, width=38, green=15, stimp=11, trees=((0.55, -26, 15),)),
        hole(12, 4, 404, 6, width=38, green=15, stimp=11, trees=((0.6, 24, 14),)),
        hole(13, 4, 394, 8, width=38, green=15, stimp=11, sand=((0.72, -16, 7),)),
        hole(14, 4, 393, 10, width=38, green=15, stimp=11, sand=((0.74, 16, 7),)),
        # Out onto the cliffs. Nothing between the tee and the green but ocean.
        hole(15, 3, 135, 18, width=34, green=13, stimp=11,
             water=((0.55, 0, 22),), sand=((0.9, -12, 6),)),
        hole(16, 3, 233, 2, width=34, green=14, stimp=11,
             water=((0.5, 0, 30),), sand=((0.92, 14, 7),)),
        hole(17, 4, 386, 12, width=36, green=15, stimp=11, bend_deg=-10,
             water=((0.45, -30, 20),)),
        hole(18, 4, 343, 14, width=40, green=15, stimp=11,
             trees=((0.6, 22, 14),)),
    ),
    card_source=(
        "Cypress Point championship card via Wikipedia - par 72, 6552 yards. "
        "Stroke indexes derived by difficulty; the club's own allotment is not "
        "published there."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Fourth, and the furthest north of them. Plateau greens perched above the
# fairway so a shot that pitches short falls back off, whins either side, and
# turf firm enough that everything runs. Short by modern standards and none
# the easier for it.
STRUIE_LINKS = Course(
    name="Struie Links",
    after="Royal Dornoch Golf Club, Championship course, Scotland (ranked 4th)",
    location="Dornoch, Scotland",
    character=LINKS,
    holes=(
        hole(1, 4, 333, 15, width=50, green=16, stimp=10, sand=((0.78, -14, 6),)),
        hole(2, 3, 184, 9, width=42, green=15, stimp=10, rise_yards=4,
             falls=(150, 0.035), sand=((0.88, 14, 6),)),
        hole(3, 4, 413, 7, width=48, green=16, stimp=10, rise_yards=4,
             sand=((0.72, 16, 6),)),
        hole(4, 4, 422, 5, width=48, green=16, stimp=10, rise_yards=5,
             sand=((0.7, -16, 6),)),
        hole(5, 4, 353, 11, width=50, green=16, stimp=10, sand=((0.76, 14, 6),)),
        hole(6, 3, 161, 17, width=42, green=15, stimp=10, rise_yards=4,
             falls=(120, 0.035), sand=((0.88, -12, 5),)),
        hole(7, 4, 485, 1, width=46, green=16, stimp=10, sand=((0.68, 16, 6),)),
        hole(8, 4, 482, 3, width=46, green=16, stimp=10, bend_deg=-7,
             sand=((0.7, -16, 6),)),
        hole(9, 5, 529, 13, width=52, green=17, stimp=10, sand=((0.62, 16, 6),)),
        hole(10, 3, 174, 18, width=42, green=15, stimp=10, falls=(180, 0.03),
             sand=((0.88, 12, 5),)),
        hole(11, 4, 446, 4, width=46, green=16, stimp=10, sand=((0.7, -16, 6),)),
        hole(12, 5, 560, 12, width=52, green=17, stimp=10, sand=((0.6, 16, 6),)),
        hole(13, 3, 180, 16, width=42, green=15, stimp=10, rise_yards=4,
             falls=(140, 0.035), sand=((0.88, -12, 5),)),
        hole(14, 4, 445, 6, width=46, green=16, stimp=10, sand=((0.7, 16, 6),)),
        hole(15, 4, 360, 14, width=50, green=16, stimp=10, sand=((0.76, -14, 6),)),
        hole(16, 4, 401, 10, width=48, green=16, stimp=10, sand=((0.72, 16, 6),)),
        hole(17, 4, 417, 8, width=48, green=16, stimp=10, bend_deg=8,
             sand=((0.72, -16, 6),)),
        hole(18, 4, 456, 2, width=46, green=16, stimp=10, sand=((0.68, 16, 6),)),
    ),
    card_source=(
        "Royal Dornoch Championship card from the club's own site - par 70. "
        "The club states 6799 yards; its own holes sum to 6801, and the holes "
        "are what is used. Stroke indexes derived by difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Fifth. Sand under the whole property, so it plays fast in any weather, with
# bunkers cut sheer into the fairway edges and greens as quick as any in the
# world. Barely six and a half thousand yards and it has never needed to be
# longer.
BLACKROCK_SANDS = Course(
    name="Blackrock Sands",
    after="Royal Melbourne Golf Club, West Course, Australia (ranked 5th)",
    location="Black Rock, Victoria, Australia",
    character=SANDBELT,
    holes=(
        hole(1, 4, 429, 1, width=46, green=16, stimp=13, falls=(160, 0.035),
             sand=((0.7, 18, 8),)),
        hole(2, 5, 491, 15, width=50, green=17, stimp=13, falls=(180, 0.03),
             sand=((0.66, -18, 8),)),
        hole(3, 4, 354, 9, width=48, green=16, stimp=13, falls=(140, 0.04),
             sand=((0.76, 16, 8),)),
        hole(4, 5, 507, 13, width=50, green=17, stimp=13, bend_deg=-8,
             falls=(190, 0.03), sand=((0.64, -18, 8),)),
        hole(5, 3, 176, 11, width=42, green=15, stimp=13, falls=(150, 0.04),
             sand=((0.88, 14, 7),)),
        hole(6, 4, 428, 3, width=46, green=16, stimp=13, bend_deg=9,
             falls=(170, 0.035), sand=((0.7, 18, 8),)),
        hole(7, 3, 148, 17, width=40, green=14, stimp=13, falls=(200, 0.04),
             sand=((0.9, -12, 7),)),
        hole(8, 4, 379, 7, width=48, green=16, stimp=13, falls=(150, 0.035),
             sand=((0.74, 16, 8),)),
        hole(9, 4, 416, 5, width=46, green=16, stimp=13, falls=(160, 0.04),
             sand=((0.72, -18, 8),)),
        hole(10, 4, 312, 16, width=50, green=15, stimp=13, falls=(130, 0.045),
             sand=((0.8, 14, 8),)),
        hole(11, 4, 455, 2, width=46, green=16, stimp=13, falls=(170, 0.035),
             sand=((0.68, 18, 8),)),
        hole(12, 5, 476, 14, width=50, green=17, stimp=13, falls=(180, 0.03),
             sand=((0.66, -18, 8),)),
        hole(13, 3, 147, 18, width=40, green=14, stimp=13, falls=(210, 0.04),
             sand=((0.9, 12, 7),)),
        hole(14, 4, 366, 10, width=48, green=16, stimp=13, falls=(140, 0.04),
             sand=((0.76, -16, 8),)),
        hole(15, 5, 477, 12, width=50, green=17, stimp=13, falls=(190, 0.03),
             sand=((0.66, 18, 8),)),
        hole(16, 3, 221, 4, width=42, green=15, stimp=13, falls=(160, 0.04),
             sand=((0.88, -14, 7),)),
        hole(17, 4, 439, 6, width=46, green=16, stimp=13, bend_deg=-7,
             falls=(170, 0.035), sand=((0.7, 18, 8),)),
        hole(18, 4, 433, 8, width=46, green=16, stimp=13, falls=(150, 0.04),
             sand=((0.7, -18, 8),)),
    ),
    card_source=(
        "Royal Melbourne West card (blue tees) from the club's own site - par "
        "72. The club states 6658 yards converted from 6077 metres; its own "
        "hole yardages sum to 6654, and the holes are what is used. Stroke "
        "indexes derived by difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Fifteenth, and the most photographed golf in the world. Seven holes run
# along the cliff edge with the Pacific down the right, the greens are the
# smallest of any course here, and the 7th is a hundred and nine yards
# straight out at the ocean.
STILLWATER_SHORE = Course(
    name="Stillwater Shore",
    after="Pebble Beach Golf Links, California (ranked 15th)",
    location="Pebble Beach, California",
    character=CLIFFTOP,
    holes=(
        hole(1, 4, 380, 11, width=44, green=13, stimp=11, trees=((0.6, -24, 13),)),
        hole(2, 4, 516, 3, width=44, green=13, stimp=11, sand=((0.7, 18, 7),)),
        hole(3, 4, 404, 7, width=44, green=13, stimp=11, bend_deg=-7,
             sand=((0.72, -16, 7),)),
        hole(4, 4, 331, 15, width=40, green=12, stimp=11,
             water=((0.85, 26, 16),)),  # the cliff edge starts here
        hole(5, 3, 195, 9, width=38, green=12, stimp=11, sand=((0.88, -14, 6),)),
        hole(6, 5, 523, 13, width=42, green=14, stimp=11, rise_yards=9,
             water=((0.7, 30, 20),)),
        hole(7, 3, 109, 17, width=32, green=11, stimp=11, rise_yards=-6,
             water=((0.75, 16, 14),), sand=((0.9, -10, 5),)),
        hole(8, 4, 428, 5, width=40, green=13, stimp=11,
             water=((0.55, 24, 20),)),  # the second shot over the chasm
        hole(9, 4, 526, 1, width=42, green=13, stimp=11, rise_yards=-7,
             water=((0.7, 30, 18),)),
        hole(10, 4, 495, 2, width=40, green=13, stimp=11,
             water=((0.7, 28, 18),)),
        hole(11, 4, 390, 14, width=44, green=13, stimp=11, sand=((0.74, -16, 7),)),
        hole(12, 3, 202, 8, width=38, green=12, stimp=11, sand=((0.88, 14, 6),)),
        hole(13, 4, 445, 4, width=42, green=13, stimp=11, bend_deg=8,
             sand=((0.7, 18, 7),)),
        hole(14, 5, 580, 16, width=44, green=14, stimp=11, rise_yards=10,
             sand=((0.88, -16, 8),)),
        hole(15, 4, 397, 12, width=44, green=13, stimp=11, sand=((0.74, 16, 7),)),
        hole(16, 4, 403, 10, width=42, green=13, stimp=11, sand=((0.72, -16, 7),)),
        hole(17, 3, 208, 6, width=36, green=12, stimp=11, falls=(150, 0.035),
             water=((0.85, 22, 14),), sand=((0.9, -12, 6),)),
        # Along the sea wall, turning left the whole way.
        hole(18, 5, 543, 18, width=42, green=13, stimp=11, bend_deg=-12,
             water=((0.6, -30, 22),)),
    ),
    card_source=(
        "Pebble Beach 2019 U.S. Open card via Wikipedia - par 71, 7075 yards. "
        "The 2nd plays as a par 4 in competition and is carried that way here. "
        "Stroke indexes derived by difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Twelfth. Dunes high enough to hide a fairway, out of bounds hard against the
# first two holes, and Calamity Corner at the 16th - 236 yards across a ravine
# with nothing to bail out into on the right.
DUNLUCE_HEAD = Course(
    name="Dunluce Head",
    after="Royal Portrush Golf Club, Dunluce Links, Northern Ireland (ranked 12th)",
    location="Portrush, Northern Ireland",
    character=LINKS,
    holes=(
        # Out of bounds down both sides, which is what this hole is known for.
        # Held wide enough that it is still finishable from the forward tees:
        # a boundary a golfer cannot get between is a hole nobody can play.
        hole(1, 4, 421, 7, width=44, green=16, stimp=10, sand=((0.72, 16, 6),),
             fenced=58),
        hole(2, 5, 574, 13, width=48, green=17, stimp=10, bend_deg=8,
             sand=((0.64, -18, 7),), fenced=46),
        hole(3, 3, 177, 17, width=40, green=15, stimp=10, sand=((0.88, 14, 6),)),
        hole(4, 4, 482, 1, width=44, green=16, stimp=10, sand=((0.68, -16, 6),)),
        hole(5, 4, 374, 15, width=48, green=16, stimp=10, rise_yards=-8,
             sand=((0.78, 14, 6),)),
        hole(6, 3, 194, 11, width=40, green=15, stimp=10, falls=(140, 0.035),
             sand=((0.88, -14, 6),)),
        hole(7, 5, 592, 9, width=48, green=17, stimp=10, sand=((0.6, 18, 7),)),
        hole(8, 4, 432, 3, width=44, green=16, stimp=10, bend_deg=-7,
             sand=((0.7, -16, 6),)),
        hole(9, 4, 432, 5, width=44, green=16, stimp=10, rise_yards=6,
             sand=((0.72, 16, 6),)),
        hole(10, 4, 447, 10, width=44, green=16, stimp=10, sand=((0.7, -16, 6),)),
        hole(11, 4, 474, 2, width=42, green=16, stimp=10, sand=((0.68, 18, 6),)),
        hole(12, 5, 532, 18, width=48, green=17, stimp=10, sand=((0.66, -16, 6),)),
        hole(13, 3, 194, 16, width=40, green=15, stimp=10, sand=((0.88, 14, 6),)),
        hole(14, 4, 473, 6, width=42, green=16, stimp=10, bend_deg=7,
             sand=((0.7, 16, 6),)),
        hole(15, 4, 426, 12, width=44, green=16, stimp=10, sand=((0.72, -16, 6),)),
        # Calamity Corner: a ravine the whole way, and no room right.
        hole(16, 3, 236, 8, width=36, green=15, stimp=10,
             water=((0.55, 20, 18),), sand=((0.9, -12, 6),)),
        hole(17, 4, 408, 14, width=46, green=16, stimp=10, rise_yards=-7,
             sand=((0.76, 14, 6),)),
        hole(18, 4, 474, 4, width=42, green=16, stimp=10, sand=((0.68, 18, 6),)),
    ),
    card_source=(
        "Royal Portrush Dunluce 2019 Open card via Wikipedia - par 71. The "
        "page states 7337 yards; its own holes sum to 7342, and the holes are "
        "what is used. Stroke indexes derived by difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Thirteenth, and proof that a course does not need length. Under seven
# thousand yards and it held a U.S. Open at level par. Tiny greens, rough that
# stands the ball up, sand with torn edges rather than raked lips, and a finish
# across an abandoned quarry. The 13th is a hundred and two yards.
QUARRY_EAST = Course(
    name="Quarry East",
    after="Merion Golf Club, East Course, Pennsylvania (ranked 13th)",
    location="Ardmore, Pennsylvania",
    character=PARKLAND,
    holes=(
        hole(1, 4, 340, 17, width=34, green=13, stimp=12, falls=(140, 0.035),
             sand=((0.76, -16, 8),)),
        hole(2, 5, 567, 13, width=38, green=14, stimp=12, bend_deg=7,
             sand=((0.64, 18, 8),)),
        hole(3, 3, 246, 3, width=34, green=13, stimp=12, falls=(160, 0.035),
             sand=((0.9, -14, 7),)),
        hole(4, 5, 640, 9, width=38, green=14, stimp=12, sand=((0.6, 18, 8),)),
        hole(5, 4, 499, 1, width=34, green=13, stimp=12, bend_deg=-8,
             water=((0.68, -22, 12),)),  # Baffling Brook
        hole(6, 4, 489, 5, width=34, green=13, stimp=12, rise_yards=6,
             sand=((0.7, 16, 8),)),
        hole(7, 4, 382, 11, width=36, green=13, stimp=12, sand=((0.74, -16, 8),)),
        hole(8, 4, 360, 15, width=36, green=13, stimp=12, sand=((0.76, 16, 8),)),
        hole(9, 3, 237, 7, width=34, green=13, stimp=12, falls=(180, 0.035),
             sand=((0.9, 14, 7),)),
        hole(10, 4, 301, 16, width=38, green=13, stimp=12, falls=(130, 0.04),
             sand=((0.8, -14, 8),)),
        hole(11, 4, 375, 14, width=36, green=13, stimp=12,
             water=((0.86, 18, 12),)),  # where the Grand Slam finished
        hole(12, 4, 386, 12, width=36, green=13, stimp=12, sand=((0.74, 16, 8),)),
        hole(13, 3, 102, 18, width=30, green=12, stimp=12, falls=(150, 0.04),
             sand=((0.9, 12, 7), (0.9, -12, 7))),
        hole(14, 4, 446, 6, width=34, green=13, stimp=12, sand=((0.7, -16, 8),)),
        hole(15, 4, 413, 10, width=36, green=13, stimp=12, sand=((0.74, 16, 8),)),
        # Into the quarry, and out over it three times.
        hole(16, 4, 440, 8, width=34, green=13, stimp=12,
             water=((0.6, 0, 22),), sand=((0.88, 14, 7),)),
        hole(17, 3, 243, 4, width=32, green=13, stimp=12, rise_yards=-5,
             water=((0.45, 0, 18),), falls=(170, 0.04)),
        hole(18, 4, 500, 2, width=34, green=13, stimp=12, rise_yards=7,
             water=((0.3, 0, 20),), sand=((0.82, -16, 8),)),
    ),
    card_source=(
        "Merion East 2013 U.S. Open card via Wikipedia - par 70, 6966 yards, "
        "out 3760 and in 3206, which sum correctly. An earlier attempt at this "
        "course was abandoned because the figures then extracted fell fifty "
        "yards short of their own total. Stroke indexes derived by difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Eighteenth. Eight holes run along the Ayrshire shore with the rock out to
# sea the whole way, and the 9th is played from a tee on the rocks below the
# lighthouse - 248 yards, a par 3, across the water. The wind decides
# everything else.
AILSA_SHORE = Course(
    name="Ailsa Shore",
    after="Trump Turnberry, Ailsa course, Scotland (ranked 18th)",
    location="Turnberry, Scotland",
    character=LINKS,
    holes=(
        hole(1, 4, 441, 7, width=46, green=16, stimp=10, sand=((0.7, 16, 6),)),
        hole(2, 4, 425, 9, width=46, green=16, stimp=10, sand=((0.72, -16, 6),)),
        hole(3, 4, 496, 1, width=44, green=16, stimp=10, sand=((0.68, 18, 6),)),
        # Out onto the shore.
        hole(4, 3, 194, 15, width=40, green=15, stimp=10, falls=(140, 0.035),
             water=((0.7, 22, 14),)),
        hole(5, 5, 531, 17, width=48, green=17, stimp=10, water=((0.6, 30, 18),)),
        hole(6, 3, 201, 11, width=40, green=15, stimp=10, sand=((0.88, -14, 6),)),
        hole(7, 5, 591, 13, width=48, green=17, stimp=10, bend_deg=-7,
             water=((0.55, 32, 20),)),
        hole(8, 4, 481, 5, width=44, green=16, stimp=10, water=((0.65, 28, 18),)),
        # Bruce's Castle: from the rocks under the lighthouse, over the sea.
        hole(9, 3, 248, 3, width=36, green=14, stimp=10, rise_yards=-6,
             water=((0.5, 0, 26),)),
        hole(10, 5, 565, 18, width=48, green=17, stimp=10, water=((0.55, 32, 20),)),
        hole(11, 3, 215, 12, width=40, green=15, stimp=10, falls=(160, 0.035),
             water=((0.7, 22, 14),)),
        hole(12, 4, 468, 8, width=44, green=16, stimp=10, sand=((0.7, -16, 6),)),
        hole(13, 4, 409, 14, width=46, green=16, stimp=10, sand=((0.74, 16, 6),)),
        hole(14, 5, 568, 16, width=48, green=17, stimp=10, sand=((0.62, -18, 7),)),
        hole(15, 3, 234, 10, width=40, green=15, stimp=10, sand=((0.88, 14, 6),)),
        hole(16, 4, 479, 6, width=44, green=16, stimp=10,
             water=((0.9, 0, 12),)),  # the burn short of the green
        hole(17, 4, 509, 2, width=44, green=16, stimp=10, rise_yards=7,
             sand=((0.68, 18, 6),)),
        hole(18, 4, 481, 4, width=44, green=16, stimp=10, sand=((0.7, -18, 6),)),
    ),
    card_source=(
        "Turnberry Ailsa black tees via GolfPass - par 71, 7536 yards, out "
        "3608 and in 3928, which sum correctly. Wikipedia's table for this "
        "course is corrupt: it gives the 17th as a 559 yard par 4 and the 7th "
        "as a 476 yard par 5, neither of which golf builds, and a back nine of "
        "4012 yards. This source gives 509 and 591 for those holes and nothing "
        "outside the game. Stroke indexes derived by difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Thirty-eighth on the list and widely held to be the hardest of all of them.
# No dunes to speak of and nothing pretty about it: out of bounds down the
# right, bunkers exactly where a good drive finishes, and a burn that crosses
# the last two holes five times between them.
BARRY_BURN = Course(
    name="Barry Burn",
    after="Carnoustie Golf Links, Championship course, Scotland (ranked 38th)",
    location="Carnoustie, Scotland",
    character=LINKS,
    holes=(
        hole(1, 4, 405, 11, width=46, green=16, stimp=10, sand=((0.72, 16, 6),),
             fenced=44),
        hole(2, 4, 459, 3, width=44, green=16, stimp=10, sand=((0.68, -18, 7),)),
        hole(3, 4, 355, 17, width=48, green=16, stimp=10,
             water=((0.8, 14, 8),)),
        hole(4, 4, 412, 5, width=46, green=16, stimp=10, sand=((0.72, 16, 6),)),
        hole(5, 4, 411, 7, width=46, green=16, stimp=10, sand=((0.72, -16, 6),)),
        hole(6, 5, 573, 13, width=46, green=17, stimp=10, sand=((0.6, 18, 7),),
             fenced=42),  # Hogan's Alley: out of bounds all the way down
        hole(7, 4, 409, 9, width=46, green=16, stimp=10, sand=((0.72, 16, 6),)),
        hole(8, 3, 183, 15, width=40, green=15, stimp=10, sand=((0.88, -14, 6),)),
        hole(9, 4, 474, 1, width=44, green=16, stimp=10, sand=((0.68, 18, 7),)),
        hole(10, 4, 465, 10, width=44, green=16, stimp=10, water=((0.85, -16, 9),)),
        hole(11, 4, 380, 14, width=48, green=16, stimp=10, sand=((0.76, 14, 6),)),
        hole(12, 4, 504, 2, width=44, green=16, stimp=10, sand=((0.66, 18, 7),)),
        hole(13, 3, 171, 16, width=40, green=15, stimp=10, sand=((0.88, 14, 6),)),
        # The Spectacles: two bunkers cut into the rise short of the green.
        hole(14, 5, 510, 18, width=46, green=17, stimp=10, rise_yards=6,
             sand=((0.78, 12, 7), (0.78, -12, 7))),
        hole(15, 4, 471, 8, width=44, green=16, stimp=10, sand=((0.7, -18, 7),)),
        hole(16, 3, 249, 6, width=40, green=15, stimp=10, falls=(150, 0.035),
             sand=((0.9, 14, 6),)),
        # The burn, back and forth across the last two.
        hole(17, 4, 460, 12, width=44, green=16, stimp=10,
             water=((0.45, 0, 12), (0.8, 0, 10))),
        hole(18, 4, 499, 4, width=44, green=16, stimp=10,
             water=((0.5, 0, 12), (0.92, 0, 10)), fenced=58),
    ),
    card_source=(
        "Carnoustie Championship card via Wikipedia - par 71, 7390 yards, out "
        "3681 and in 3709, which sum correctly. Stroke indexes derived by "
        "difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Twenty-third, and the fairest links in England for one reason: the fairways
# sit flat in the valleys between the dunes instead of running over them, so a
# good drive is not thrown twenty yards sideways by a slope nobody could see.
# The dunes frame the holes rather than swallowing them. Willow scrub either
# side of everything.
DUNE_VALLEYS = Course(
    name="Dune Valleys",
    after="Royal Birkdale Golf Club, England (ranked 23rd)",
    location="Southport, England",
    character=LINKS,
    holes=(
        hole(1, 4, 443, 7, width=48, green=16, stimp=10, bend_deg=-7,
             sand=((0.7, -16, 6),)),
        hole(2, 4, 416, 11, width=48, green=16, stimp=10, sand=((0.72, 16, 6),)),
        hole(3, 4, 444, 5, width=46, green=16, stimp=10, sand=((0.7, -16, 6),)),
        hole(4, 3, 215, 9, width=42, green=15, stimp=10, falls=(150, 0.035),
             sand=((0.88, 14, 6),)),
        hole(5, 4, 322, 15, width=50, green=16, stimp=10, sand=((0.8, -14, 6),)),
        hole(6, 4, 512, 1, width=46, green=16, stimp=10, bend_deg=8,
             sand=((0.66, 18, 7),)),
        hole(7, 3, 149, 17, width=40, green=14, stimp=10, sand=((0.9, -12, 5),)),
        hole(8, 4, 456, 3, width=46, green=16, stimp=10, sand=((0.68, 16, 6),)),
        hole(9, 4, 411, 13, width=48, green=16, stimp=10, sand=((0.74, -16, 6),)),
        hole(10, 4, 399, 12, width=48, green=16, stimp=10, sand=((0.74, 16, 6),)),
        hole(11, 4, 431, 8, width=46, green=16, stimp=10, sand=((0.72, -16, 6),)),
        hole(12, 3, 183, 18, width=40, green=15, stimp=10, sand=((0.88, 14, 6),)),
        hole(13, 4, 499, 4, width=44, green=16, stimp=10, sand=((0.68, -18, 7),)),
        hole(14, 5, 600, 10, width=48, green=17, stimp=10, sand=((0.6, 18, 7),)),
        hole(15, 3, 237, 6, width=42, green=15, stimp=10, falls=(170, 0.035),
             sand=((0.88, -14, 6),)),
        hole(16, 4, 390, 14, width=48, green=16, stimp=10, sand=((0.76, 16, 6),)),
        hole(17, 5, 563, 16, width=48, green=17, stimp=10, bend_deg=-7,
             sand=((0.62, -18, 7),)),
        hole(18, 4, 506, 2, width=44, green=16, stimp=10, sand=((0.68, 18, 7),)),
    ),
    card_source=(
        "Royal Birkdale black tees via Wikipedia - par 70, out 3368 and in "
        "3808. Both nines sum correctly to their own stated figures, but the "
        "stated total of 7156 is 20 short of the 7176 its holes come to. The "
        "holes are used. Stroke indexes derived by difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Twenty-fifth. Tree-lined to both sides of every hole, and all of the defence
# in greens that sit up above the fairway and fall away on every edge - miss
# one and the ball keeps going, so the recovery is played back up a slope to a
# surface running away from you. The U.S. Open here in 1974 was won at seven
# over and is still called the Massacre.
FOOTHILL_WEST = Course(
    name="Foothill West",
    after="Winged Foot Golf Club, West Course, New York (ranked 25th)",
    location="Mamaroneck, New York",
    character=PARKLAND,
    holes=(
        hole(1, 4, 451, 11, width=36, green=15, stimp=13, rise_yards=5,
             falls=(160, 0.045), trees=((0.5, -28, 15),)),
        hole(2, 4, 484, 7, width=36, green=15, stimp=13, rise_yards=6,
             falls=(150, 0.04), sand=((0.7, 18, 8),)),
        hole(3, 3, 243, 5, width=34, green=14, stimp=13, rise_yards=4,
             falls=(180, 0.045), sand=((0.9, -14, 7),)),
        hole(4, 4, 467, 9, width=36, green=15, stimp=13, bend_deg=-7,
             falls=(140, 0.04), trees=((0.55, -26, 15),)),
        hole(5, 4, 502, 1, width=34, green=15, stimp=13, rise_yards=5,
             falls=(170, 0.04), sand=((0.68, 18, 8),)),
        hole(6, 4, 321, 17, width=40, green=14, stimp=13, falls=(130, 0.05),
             sand=((0.8, -14, 8),)),
        hole(7, 3, 162, 15, width=34, green=14, stimp=13, rise_yards=4,
             falls=(200, 0.045), sand=((0.9, 12, 7),)),
        hole(8, 4, 490, 3, width=36, green=15, stimp=13, falls=(150, 0.04),
             trees=((0.5, 28, 15),)),
        hole(9, 5, 565, 13, width=40, green=16, stimp=13, rise_yards=7,
             falls=(160, 0.035), sand=((0.64, -18, 8),)),
        hole(10, 3, 214, 10, width=34, green=14, stimp=13, rise_yards=5,
             falls=(190, 0.05), sand=((0.88, 14, 7), (0.88, -14, 7))),
        hole(11, 4, 384, 18, width=38, green=15, stimp=13, falls=(140, 0.04),
             trees=((0.6, -26, 14),)),
        hole(12, 5, 633, 16, width=40, green=16, stimp=13, falls=(170, 0.035),
             sand=((0.6, 18, 8),)),
        hole(13, 3, 212, 14, width=34, green=14, stimp=13, falls=(210, 0.045),
             sand=((0.9, -14, 7),)),
        hole(14, 4, 452, 8, width=36, green=15, stimp=13, rise_yards=5,
             falls=(150, 0.045), sand=((0.7, 18, 8),)),
        hole(15, 4, 426, 12, width=38, green=15, stimp=13, falls=(160, 0.04),
             trees=((0.55, 26, 15),)),
        hole(16, 4, 498, 4, width=36, green=15, stimp=13, bend_deg=8,
             falls=(150, 0.04), sand=((0.68, -18, 8),)),
        hole(17, 4, 504, 2, width=34, green=15, stimp=13, falls=(170, 0.045),
             trees=((0.5, -28, 16),)),
        hole(18, 4, 469, 6, width=36, green=15, stimp=13, rise_yards=8,
             falls=(140, 0.045), sand=((0.7, 18, 8),)),
    ),
    card_source=(
        "Winged Foot West 2020 U.S. Open card via Wikipedia - par 70, 7477 "
        "yards, out 3685 and in 3792. Everything sums in every direction, "
        "which by this point in the library is worth noting. Stroke indexes "
        "derived by difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Thirty-ninth, and the opposite of Birkdale in every way that matters. The
# fairways run over the dunes rather than between them, so they heave and tilt
# and a good drive can be thrown thirty yards sideways by ground nobody could
# see from the tee. Blind shots, and the deepest bunker in championship golf
# cut into the face of a dune at the 4th.
SANDWICH_DUNES = Course(
    name="Sandwich Dunes",
    after="Royal St George's Golf Club, England (ranked 39th)",
    location="Sandwich, England",
    character=LINKS,
    holes=(
        hole(1, 4, 442, 7, width=40, green=16, stimp=10, sand=((0.7, -16, 7),)),
        hole(2, 4, 426, 9, width=40, green=16, stimp=10, sand=((0.72, 16, 7),)),
        hole(3, 3, 239, 3, width=36, green=15, stimp=10, falls=(150, 0.035),
             sand=((0.88, -14, 7),)),
        # The 4th: the deepest bunker in championship golf, in the dune face.
        hole(4, 4, 496, 1, width=38, green=16, stimp=10, rise_yards=6,
             sand=((0.55, 18, 11),)),
        hole(5, 4, 416, 11, width=42, green=16, stimp=10, sand=((0.74, -16, 7),)),
        hole(6, 3, 176, 17, width=36, green=15, stimp=10, sand=((0.88, 14, 6),)),
        hole(7, 5, 573, 15, width=44, green=17, stimp=10, bend_deg=-7,
             sand=((0.6, -18, 8),)),
        hole(8, 4, 457, 5, width=40, green=16, stimp=10, sand=((0.7, 16, 7),)),
        hole(9, 4, 410, 13, width=42, green=16, stimp=10, sand=((0.74, -16, 7),)),
        hole(10, 4, 412, 12, width=42, green=16, stimp=10, sand=((0.74, 16, 7),)),
        hole(11, 3, 242, 4, width=36, green=15, stimp=10, falls=(170, 0.035),
             sand=((0.88, -14, 7),)),
        hole(12, 4, 379, 14, width=44, green=16, stimp=10, sand=((0.78, 14, 6),)),
        hole(13, 4, 457, 6, width=40, green=16, stimp=10, sand=((0.7, -16, 7),)),
        hole(14, 5, 545, 16, width=44, green=17, stimp=10, sand=((0.62, 18, 8),),
             fenced=46, fenced_side=1),  # the Suez Canal, and out of bounds right
        hole(15, 4, 493, 2, width=38, green=16, stimp=10, sand=((0.68, -18, 8),)),
        hole(16, 3, 161, 18, width=36, green=15, stimp=10, sand=((0.9, 12, 6),)),
        hole(17, 4, 424, 10, width=42, green=16, stimp=10, sand=((0.74, 16, 7),)),
        hole(18, 4, 456, 8, width=40, green=16, stimp=10, sand=((0.7, -18, 7),)),
    ),
    card_source=(
        "Royal St George's championship card via Wikipedia - out 3635, in 3569, "
        "total 7204, all of which sum. The page prints the nine pars but not "
        "each hole's, and only one assignment fits: par 35 over nine holes "
        "forces one more par 3 than par 5, and the shortest holes take them. "
        "Derived rather than read, and unambiguous. Stroke indexes derived by "
        "difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Eighty-first, and built on the oldest idea in links golf: out along the shore
# with the wind behind and back into it, so the front nine flatters you and the
# back nine takes it all back. The 8th is a hundred and twenty-three yards to a
# green the size of a postage stamp with a drop on every side of it, and it has
# ruined more cards than the 623 yard 6th ever did.
POSTAGE_LINKS = Course(
    name="Postage Links",
    after="Royal Troon Golf Club, Old Course, Scotland (ranked 81st)",
    location="Troon, Scotland",
    character=LINKS,
    holes=(
        hole(1, 4, 366, 15, width=52, green=17, stimp=10, sand=((0.78, 16, 6),)),
        hole(2, 4, 389, 11, width=50, green=17, stimp=10, sand=((0.74, -16, 6),)),
        hole(3, 4, 376, 13, width=50, green=17, stimp=10, sand=((0.76, 16, 6),)),
        hole(4, 5, 599, 9, width=52, green=18, stimp=10, sand=((0.6, -18, 7),)),
        hole(5, 3, 220, 1, width=42, green=15, stimp=10, falls=(150, 0.035),
             sand=((0.88, 14, 6),)),
        hole(6, 5, 623, 5, width=52, green=18, stimp=10, sand=((0.58, 18, 7),)),
        hole(7, 4, 403, 7, width=48, green=16, stimp=10, bend_deg=8,
             sand=((0.74, 16, 6),)),
        # The Postage Stamp. Nowhere to miss it, and everywhere to miss it into.
        hole(8, 3, 123, 17, width=30, green=10, stimp=10, falls=(120, 0.04),
             sand=((0.86, 9, 5), (0.86, -9, 5), (1.0, 0, 5))),
        hole(9, 4, 440, 3, width=46, green=16, stimp=10, sand=((0.7, -16, 6),)),
        hole(10, 4, 450, 14, width=46, green=16, stimp=10, sand=((0.7, 16, 6),)),
        # The Railway. The line runs down the right the whole way and is out.
        hole(11, 4, 498, 4, width=42, green=15, stimp=10,
             sand=((0.68, -16, 6),), fenced=38, fenced_side=1),
        hole(12, 4, 451, 12, width=46, green=16, stimp=10, sand=((0.72, 16, 6),)),
        hole(13, 4, 473, 8, width=46, green=16, stimp=10, sand=((0.7, -16, 6),)),
        hole(14, 3, 200, 16, width=42, green=15, stimp=10, sand=((0.88, 14, 6),)),
        hole(15, 4, 502, 2, width=44, green=16, stimp=10, sand=((0.68, 18, 7),)),
        hole(16, 5, 572, 18, width=50, green=18, stimp=10, water=((0.62, 0, 10),)),
        hole(17, 3, 242, 6, width=42, green=15, stimp=10, falls=(170, 0.035),
             sand=((0.88, -14, 6),)),
        hole(18, 4, 458, 10, width=44, green=16, stimp=10, sand=((0.7, 16, 6),)),
    ),
    card_source=(
        "Royal Troon Old 2024 Open card via Wikipedia - par 71, 7385 yards, "
        "out 3539 and in 3846, all of which sum. Stroke indexes derived by "
        "difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
)

# Sixtieth. Sandhills rather than links or parkland: pine, wiregrass and sand
# where other courses have rough, and no water anywhere on it. The defence is
# entirely in the greens, which are crowned like upturned saucers - a shot
# landing anywhere but the middle is thrown off the side, and the recovery is
# played back up a slope from a bare sandy lie. Every green here falls hard,
# and each falls its own way.
WIREGRASS_NO_TWO = Course(
    name="Wiregrass No. 2",
    after="Pinehurst Resort, Course No. 2, North Carolina (ranked 60th)",
    location="Pinehurst, North Carolina",
    character=HEATHLAND,
    holes=(
        # Shaped from a published hole-by-hole guide rather than guessed at:
        # which way each hole bends, where the sand sits, how each green falls
        # and what the ground does. Where the guide says a green is crowned
        # side to side, or falls hard back to front, or has a false front, that
        # is what is here.
        hole(1, 4, 376, 7, width=44, green=15, stimp=12, falls=(30, 0.055),
             sand=((0.5, 15, 9), (0.5, -15, 9))),      # cross bunkers midway
        hole(2, 4, 411, 3, width=42, green=15, stimp=12, falls=(200, 0.055),
             bend_deg=6, sand=((0.7, -18, 9),)),       # angled front-left to back-right
        hole(3, 4, 330, 11, width=46, green=14, stimp=12, falls=(120, 0.06),
             sand=((0.7, 16, 8), (0.7, -16, 8))),      # bunkers flanking the landing
        hole(4, 4, 434, 1, width=42, green=15, stimp=12, rise_yards=-9,
             falls=(220, 0.05), sand=((0.68, 18, 10),)),   # downhill, pitching right
        hole(5, 5, 462, 13, width=46, green=16, stimp=12, rise_yards=6,
             falls=(90, 0.055), sand=((0.66, -18, 9),)),   # crowned side to side
        hole(6, 3, 178, 9, width=40, green=14, stimp=12, falls=(180, 0.06),
             sand=((0.88, 14, 8), (0.88, -14, 8)),
             trees=((0.5, 26, 12),)),                  # deep bunkers either side, pines
        hole(7, 4, 385, 5, width=44, green=15, stimp=12, bend_deg=22,
             falls=(160, 0.05), sand=((0.55, 14, 9),)),    # sharp dogleg right
        hole(8, 5, 440, 17, width=46, green=16, stimp=12, falls=(0, 0.05),
             sand=((0.68, 18, 9),)),                   # green tilts front to back
        hole(9, 3, 148, 15, width=38, green=13, stimp=12, falls=(315, 0.055),
             sand=((0.9, -12, 8),)),                   # rises front-right to back-left
        hole(10, 5, 455, 18, width=46, green=16, stimp=12, falls=(180, 0.05),
             sand=((0.66, 16, 9),)),                   # falls away at the back
        hole(11, 4, 375, 4, width=44, green=15, stimp=12, falls=(45, 0.055),
             sand=((0.74, -18, 9),)),                  # false edge back-left
        hole(12, 4, 360, 8, width=42, green=14, stimp=12, falls=(270, 0.055),
             sand=((0.76, 16, 9),)),                   # left falls away, narrow green
        hole(13, 4, 358, 10, width=44, green=15, stimp=12, rise_yards=5,
             falls=(180, 0.06), sand=((0.76, 16, 9),)),    # plateau, hard back to front
        hole(14, 4, 419, 2, width=46, green=15, stimp=12, falls=(200, 0.055),
             sand=((0.7, 18, 10), (0.7, -18, 10))),    # bunkers both sides
        hole(15, 3, 170, 12, width=40, green=14, stimp=12, falls=(180, 0.06),
             sand=((0.88, 14, 8),)),                   # pronounced false front
        hole(16, 5, 478, 16, width=46, green=17, stimp=12, falls=(190, 0.02),
             water=((0.55, 34, 14),)),                 # flattest green, pond out of play
        hole(17, 3, 162, 14, width=38, green=13, stimp=12, falls=(90, 0.045),
             sand=((0.9, 12, 8),)),                    # sits in a bowl
        hole(18, 4, 366, 6, width=44, green=15, stimp=12, bend_deg=7,
             falls=(180, 0.055), sand=((0.92, 0, 8),)),    # cross bunker short
    ),
    card_source=(
        "Pinehurst No. 2 white tees via Wikipedia - par 72, 6307 yards, out "
        "3164 and in 3143, which sum. The members' card. The championship tees "
        "the U.S. Open is played on are here too and can be chosen. Stroke "
        "indexes derived by difficulty."
    ),
    off_the_card=tuple(range(1, 19)),
    tees=(
        # The same eighteen greens, twelve hundred yards further back and two
        # shots tighter on par: the 2nd and the 16th stop being reachable par
        # 5s and become par 4s of over five hundred yards.
        Tees(
            name="championship",
            card=((4, 395), (4, 504), (4, 387), (4, 528), (5, 588), (3, 228),
                  (4, 426), (4, 488), (3, 184),
                  (5, 617), (4, 482), (4, 486), (4, 381), (4, 472), (3, 197),
                  (4, 530), (3, 207), (4, 448)),
            source=(
                "Pinehurst No. 2 as set up for the 2024 U.S. Open - par 70, "
                "7548 yards, out 3728 and in 3820, which sum."
            ),
        ),
    ),
)

def _with_every_tee(course):
    """A course with the usual sets of tees, keeping any it already has.

    A researched set is never replaced by a worked-out one. Pinehurst has its
    real championship card and keeps it; the derived sets fill in around it.

    They are worked out from the longest card the course has, not from whatever
    happens to be its default. For nineteen of these the two are the same. For
    Pinehurst they are not - its default is the members' card at 6307 yards and
    its championship card is 7548 - and deriving from the default gave it back
    tees shorter than its regular ones, which is a ladder with a rung upside
    down.
    """
    have = {tees.name for tees in course.tees}
    longest = max((course.from_tees(name) for name in course.tee_names()),
                  key=lambda played: played.yards)
    return replace(course, tees=course.tees + tuple(
        tees for tees in standard_tee_sets(longest) if tees.name not in have))


_GROUND = load_ground()

# Ground holes the proof golfer cannot finish, so the hole keeps its guide-built
# shape rather than shipping one no round can end on. Augusta's 15th and 16th
# are the water holes a consistent but ordinary golfer drowns on; Pine Valley's
# 18th and the Old Course 1st the same. Found once by golf_courseproof, and held
# to by test_a_course_can_be_played_round - a re-gather that breaks another hole
# fails that test rather than quietly shipping a course that cannot be finished.
GROUND_UNPLAYABLE = {
    "Pine Hollow": {18},
    "Azalea National": {15, 16},
    "Auld Links": {1},
    # A 574 yard par 5 that doglegs sharply: the proof golfer aims straight down
    # the tee-to-green line, through the corner rather than round it, and cannot
    # get home. Marginal enough that it finished under one miss sequence and not
    # another, which is why it survived the first pass and the tee-set proof
    # caught it.
    "Dunluce Head": {2},
}

# Holes the map covers but where it omits the one hazard the hole is built
# around, so the described hole is kept instead. The water holes are handled by
# the rule below - the map drops a creek and the rule sees it - but sand it does
# carry can still be the wrong sand: Oakmont's third is the Church Pews, one
# ridged complex at driving distance between two fairways, and the map has only
# the greenside bunkers, so the survey would quietly lose the thing the hole is
# famous for.
GROUND_KEEP_DESCRIBED = {
    "Furrow Ridge": {3},   # the Church Pews, not in the map at driving distance
}


def _off_the_ground(course):
    """The same course with every mapped hole rebuilt off the crawled survey.

    A hole the map has and the proof can finish is taken from the ground - its
    real bends, sand, water and heights. A hole the map lacks, one whose
    surveyed shape cannot be finished, or one the map strips of the hazard it is
    built around, keeps the guide-built hole it already had. Which holes came
    from the survey is written on the course.
    """
    entry = _GROUND.get(course.name)
    if not entry:
        return course
    found = {hole_["number"]: hole_ for hole_ in entry["holes"]}
    keep = (GROUND_UNPLAYABLE.get(course.name, set())
            | GROUND_KEEP_DESCRIBED.get(course.name, set()))

    holes, surveyed = [], []
    for built in course.holes:
        mapped = found.get(built.number)
        # A survey that has dropped a water hazard the described hole was given
        # is missing what makes the hole. The map does not carry every creek -
        # Rae's Creek is on Augusta's eleventh in OpenStreetMap and not on its
        # twelfth or thirteenth - and a par three over water with no water is
        # not the hole. Where that happens the described hole is kept.
        guide_water = any(z.feature is WATER for z in built.layout.hazards)
        survey_water = (mapped is not None
                        and any(t["kind"] == "water" for t in mapped["hazards"]))
        drops_water = guide_water and not survey_water
        if mapped is not None and built.number not in keep and not drops_water:
            holes.append(hole_from_ground(mapped, guide=built))
            surveyed.append(built.number)
        else:
            holes.append(built)
    return replace(course, holes=tuple(holes), surveyed_holes=tuple(surveyed))


COURSES = tuple(_with_every_tee(_off_the_ground(course)) for course in (
    PINE_HOLLOW, AZALEA_NATIONAL, SLIEVE_LINKS, STRUIE_LINKS,
    BLACKROCK_SANDS, CYPRESS_HEAD, GULLANE_HONOURS, SEBONACK_HILLS,
    AULD_LINKS, FURROW_RIDGE, DUNLUCE_HEAD, QUARRY_EAST,
    STILLWATER_SHORE, AILSA_SHORE, BARRY_BURN, DUNE_VALLEYS,
    FOOTHILL_WEST, SANDWICH_DUNES, POSTAGE_LINKS, WIREGRASS_NO_TWO))

# The names above are rebound to the courses with their tees on, so there is
# one Auld Links rather than two. Without this the name and the library hold
# different objects - one able to be played from five sets of tees and one not -
# and which you get depends on how you asked for it.
_BY_NAME = {course.name: course for course in COURSES}
PINE_HOLLOW = _BY_NAME["Pine Hollow"]
AZALEA_NATIONAL = _BY_NAME["Azalea National"]
SLIEVE_LINKS = _BY_NAME["Slieve Links"]
STRUIE_LINKS = _BY_NAME["Struie Links"]
BLACKROCK_SANDS = _BY_NAME["Blackrock Sands"]
CYPRESS_HEAD = _BY_NAME["Cypress Head"]
GULLANE_HONOURS = _BY_NAME["Gullane Honours"]
SEBONACK_HILLS = _BY_NAME["Sebonack Hills"]
AULD_LINKS = _BY_NAME["Auld Links"]
FURROW_RIDGE = _BY_NAME["Furrow Ridge"]
DUNLUCE_HEAD = _BY_NAME["Dunluce Head"]
QUARRY_EAST = _BY_NAME["Quarry East"]
STILLWATER_SHORE = _BY_NAME["Stillwater Shore"]
AILSA_SHORE = _BY_NAME["Ailsa Shore"]
BARRY_BURN = _BY_NAME["Barry Burn"]
DUNE_VALLEYS = _BY_NAME["Dune Valleys"]
FOOTHILL_WEST = _BY_NAME["Foothill West"]
SANDWICH_DUNES = _BY_NAME["Sandwich Dunes"]
POSTAGE_LINKS = _BY_NAME["Postage Links"]
WIREGRASS_NO_TWO = _BY_NAME["Wiregrass No. 2"]
BY_NAME = {course.name: course for course in COURSES}


def find(name):
    """A course by name, or None. Names are what a golfer types."""
    wanted = str(name or "").strip().lower()
    for course in COURSES:
        if course.name.lower() == wanted:
            return course
    return None
