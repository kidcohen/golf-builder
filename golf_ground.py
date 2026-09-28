"""Everything real about a course, gathered once and written down.

Three sources, and each is trusted only for what it actually knows.

  The card is researched, and it owns the numbers: par, yardage, stroke
  index. It is a published fact about how the course is set up to play.

  OpenStreetMap owns the ground: every bunker, the water, the greens, the
  tee boxes, the trees, and how wide the fairway is. It does not own length -
  a mapped way traces the routing between tee and green, so it runs short of
  the card by ten or twenty yards, and by a hundred on a hole where the back
  tee is somewhere else entirely.

  The elevation owns the hills, and says how finely it knows each one.

Reconciling them is one decision, made once here: the card's length wins and
the mapped shape is stretched onto it. That keeps every card, par and stroke
index already proved, and it puts the sand where the sand is. A bunker three
hundred yards out on a mapped line that came back fifty short would otherwise
sit fifty yards from where a golfer will actually find it.

Nothing here decides what a hole should be. It gathers what is known and
writes it to a file, so that building a course is reading rather than
crawling, and so a re-crawl that changes a hole shows up as a diff somebody
can look at.
"""

import json
import math
from pathlib import Path

from golf_crawl import crawl
from golf_relief import Ground, heights_along, slope_at, step_from

# A green bigger than this is shared by two holes, which St Andrews does seven
# times. The whole thing is really there and a putt across it really does
# travel that far, but it is not the target: a golfer plays to their half.
SINGLE_GREEN_MAX_YARDS = 32.0
SHARED_GREEN_TARGET = 0.55
# Below this a mapped hole is not the hole on the card - it is a fragment, or
# the wrong course out of a box that had two in it.
CLOSE_ENOUGH = 0.55


def target_radius(mapped_radius_yards):
    """How much green a golfer is actually playing to.

    A shared green is a real thing and a huge one, but calling the whole of
    it the target makes a mid-iron to the fifth at St Andrews a formality.
    """
    if mapped_radius_yards is None:
        return None
    if mapped_radius_yards <= SINGLE_GREEN_MAX_YARDS:
        return round(mapped_radius_yards, 1)
    return round(max(SINGLE_GREEN_MAX_YARDS,
                     mapped_radius_yards * SHARED_GREEN_TARGET), 1)


def stretch(mapped, card_length_yards):
    """Put the mapped shape onto the card's length.

    Everything on a hole is placed as yards down it, so scaling the length
    means scaling all of it together - the centreline, the sand, the tees.
    Scaling only the line would leave the bunkers where they were and change
    which of them a drive reaches.
    """
    mapped_length = mapped["length_yards"]
    if not mapped_length:
        return None

    line = mapped["centreline"]

    def walked(factor):
        return sum(math.dist((a[0] * factor, a[1]), (b[0] * factor, b[1]))
                   for a, b in zip(line, line[1:]))

    # Stretching down the hole only means the distance walked is no longer
    # simply the factor times the old one: a hole that bends keeps its
    # sideways movement while its length grows, so it has to be stretched a
    # little further to still play the card's distance. Solved rather than
    # assumed, because the card states the distance played and that is the
    # number the whole reconciliation rests on.
    factor = card_length_yards / mapped_length
    for _ in range(20):
        got = walked(factor)
        if not got or abs(got - card_length_yards) < 0.05:
            break
        factor *= card_length_yards / got

    # Down the hole only. A card is longer than a mapped line because it is
    # measured from a tee further back, not because the hole is wider, and
    # stretching across as well made a hole whose card is half again its
    # mapped length half again as wide - Turnberry came back with sand a
    # hundred and sixty-two yards off a centreline the crawl filters at
    # seventy.
    return {
        "centreline": [[round(x * factor, 1), round(z, 1)]
                       for x, z in mapped["centreline"]],
        "hazards": [{**hazard,
                     "along": round(hazard["along"] * factor, 1)}
                    for hazard in mapped["hazards"]],
        # What a tee plays is worked out from where it ends up, not scaled
        # alongside it. Scaling both left them disagreeing by a couple of
        # yards, because the factor is solved against the walked length and
        # a tee's distance is measured against the card.
        "tees": [{**tee,
                  "along": round(tee["along"] * factor, 1),
                  "plays_yards": round(
                      card_length_yards - tee["along"] * factor, 1)}
                 for tee in mapped["tees"]],
        "scenery": [{**area,
                     "outline": [[round(x * factor, 1), round(z, 1)]
                                 for x, z in area["outline"]]}
                    for area in mapped["scenery"]],
        "stretched_by": round(factor, 4),
    }


def pick(mapped_holes, number, name=None, card_length=None):
    """The mapped hole that is this hole on the card.

    A crawl box holds whole clubs - St Andrews came back with eighty-eight
    holes for a course that has eighteen - so a number alone is not enough to
    choose by. Three things settle it, in order of how much they can be
    trusted.

    The name, where OSM carries one. Only the Old Course and a few others do.

    The course's own outline, which is done before this is ever called.

    Then the card. Where an outline still holds more than eighteen holes -
    Augusta's holds twenty-seven because the par three course is inside it,
    Pine Valley's twenty-six, Turnberry's forty-three because three courses
    share a name - the numbers repeat and the lengths do not. A hundred yard
    twelfth is not the twelfth on a card that says five hundred and forty-
    five, and choosing the nearer of the two is how the wrong one stops being
    picked.
    """
    def near_the_card(hole):
        """Could this be the hole on the card at all?

        Applied to a single candidate as well as to a choice between several.
        Checking only when there was something to choose between let
        Turnberry's eleventh through at five hundred and sixty-eight yards
        against a card that says two hundred and fifteen: it was the only
        hole numbered eleven inside that outline, so nothing ever asked
        whether it was the right one.
        """
        if card_length is None:
            return True
        return abs(hole["length_yards"] - card_length) <= card_length * (
            1.0 - CLOSE_ENOUGH)

    if name:
        by_name = [hole for hole in mapped_holes if hole.get("name") == name]
        if len(by_name) == 1:
            return by_name[0]

    by_number = [hole for hole in mapped_holes if hole.get("number") == number]
    if not by_number:
        return None
    if len(by_number) > 1 and card_length is None:
        # Several candidates and nothing to tell them apart. Taking the first
        # is taking whichever the map happened to list first.
        return None

    # Nearest on length, and only if it is near enough to be the same hole at
    # all. Every candidate a long way from the card means none of them is it.
    nearest = min(by_number,
                  key=lambda hole: abs(hole["length_yards"] - card_length)
                  if card_length is not None else 0)
    return nearest if near_the_card(nearest) else None


def read_hole_ground(mapped, card, ground):
    """One hole: the card's numbers on the mapped ground, with its hills."""
    number, par, card_length, stroke_index = card
    shaped = stretch(mapped, card_length)
    if shaped is None:
        return None

    tee = tuple(mapped["tee_latlon"])
    bearing = mapped["bearing_deg"]
    heights = heights_along(ground, tee, bearing, shaped["centreline"])

    pin = step_from(tee, card_length, 0.0, bearing)
    mapped_green = mapped.get("green_radius_yards")
    green = slope_at(ground, pin, bearing,
                     radius_yards=(mapped_green or 20.0) * 0.75)

    return {
        "number": number,
        "name": mapped.get("name"),
        "par": par,
        "stroke_index": stroke_index,
        # The card's, always. The mapped one is kept so the gap is visible
        # rather than quietly absorbed.
        "length_yards": card_length,
        "mapped_length_yards": mapped["length_yards"],
        "tee_latlon": mapped["tee_latlon"],
        "bearing_deg": bearing,
        "centreline": shaped["centreline"],
        "heights_yards": heights,
        "hazards": shaped["hazards"],
        "tees": shaped["tees"],
        "scenery": shaped["scenery"],
        "green_radius_yards": mapped_green,
        "green_target_radius_yards": target_radius(mapped_green),
        "green_is_shared": bool(mapped_green
                                and mapped_green > SINGLE_GREEN_MAX_YARDS),
        "fairway_width_yards": mapped.get("fairway_width_yards"),
        "green": green,
    }


def gather(course, ground=None, cache=None):
    """Everything known about one course, from every source at once.

    `course` is the researched half: a name, the box it sits in, and the card
    as (number, par, length, stroke index) a hole at a time, optionally with
    the name OSM knows each hole by.
    """
    ground = ground or Ground(cache=cache)
    # By name, not by box. A box is drawn round a club and a club is rarely
    # one course: Oakmont's returned six hundred and sixteen bunkers, three
    # and a half times what Oakmont has, because the neighbours were in it.
    mapped_holes = crawl(*course["box"],
                         course_name=course.get("osm_name")
                         or course.get("modelled_on"),
                         cache=cache)

    holes, missing = [], []
    for card in course["card"]:
        number, _, card_length, _ = card
        mapped = pick(mapped_holes, number,
                      (course.get("hole_names") or {}).get(number),
                      card_length=card_length)
        if mapped is None:
            missing.append(number)
            continue
        read = read_hole_ground(mapped, card, ground)
        if read is None:
            missing.append(number)
        else:
            holes.append(read)

    return {
        "name": course["name"],
        "box": list(course["box"]),
        "holes": holes,
        # Said out loud rather than left to be noticed. A course eleven holes
        # of which were found is not a course, and silence would make it look
        # like one.
        "holes_not_found": missing,
        "holes_on_the_card": len(course["card"]),
    }


def concerns(gathered):
    """What about this course somebody should look at.

    A gather that went wrong rarely throws. It comes back plausible and
    wrong: a hole matched to its neighbour, a green that is really two, a
    contour fitted through one posting. These are the things worth a look.
    """
    said = []
    if gathered["holes_not_found"]:
        said.append(f"{len(gathered['holes_not_found'])} holes not found on the "
                    f"map: {gathered['holes_not_found']}")

    for hole in gathered["holes"]:
        gap = hole["length_yards"] - hole["mapped_length_yards"]
        if abs(gap) > hole["length_yards"] * (1.0 - CLOSE_ENOUGH):
            said.append(f"hole {hole['number']}: card {hole['length_yards']} "
                        f"against {hole['mapped_length_yards']:.0f} mapped, "
                        f"which is too far apart to be the same hole")
        if not hole["hazards"]:
            said.append(f"hole {hole['number']}: nothing mapped on it at all")
        if hole["green_is_shared"]:
            said.append(f"hole {hole['number']}: green is "
                        f"{hole['green_radius_yards']:.0f} yards across, so it is "
                        f"shared - playing to {hole['green_target_radius_yards']:.0f}")
        if not hole["green"]["trustworthy"]:
            said.append(f"hole {hole['number']}: green contour is a hint, "
                        f"ground known every {hole['green']['spacing_yards']:.0f} yards")
        if len(hole["centreline"]) < 3:
            said.append(f"hole {hole['number']}: mapped as a straight line, "
                        f"so it has no bend in it")
    return said


def save(path, courses):
    """Write the gathered ground where the game can read it."""
    path = Path(path)
    path.write_text(json.dumps({"courses": courses}, indent=1), encoding="utf-8")
    return path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["courses"]
