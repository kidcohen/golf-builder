"""Read a real golf course's ground out of OpenStreetMap.

The shaping of every hole here has been mine: which way it bends, where the
sand is, how big the green is. The cards were researched and the ground was
not, and that gap is the difference between a course of the right length and
the course itself.

OpenStreetMap has these places mapped hole by hole. A golf=hole way is the
routing from tee to green with its number and par; golf=bunker, golf=green,
golf=tee, golf=fairway and golf=water_hazard are the ground itself as
outlines, and natural=wood, natural=tree and natural=water are what is around
it. St Andrews alone carries three hundred and fifty-eight bunkers and
ninety-nine holes, named - Burn, Bobby Jones, Hole O'Cross - so a course can be
crawled rather than guessed at or read off an image by eye.

This turns that into the frame the simulator works in: yards, tee at the
origin, x down the hole and z across it. Nothing here decides what a hole
should be. It reads what is there.

Two kinds of thing come back, and the difference matters. Trouble is what a
ball can finish in and has to be played out of - sand, water, a tree standing
on its own in the fairway. Scenery is what a hole looks like - the wood down
the left, the gorse, the water beyond the green. Scenery is kept as outlines
rather than circles because it is drawn rather than landed in, and a wood
turned into a circle would swallow the hole it is supposed to line.
"""

import hashlib
import json
import time
import math
import urllib.parse
import urllib.request
from pathlib import Path

# Overpass is free, popular and therefore busy, and the main instance spends
# parts of the day refusing everything. These all speak the same language and
# carry the whole map, so a crawl that would have lost an afternoon to one
# server's bad hour simply asks the next one.
#
# Carrying the whole map is the part that has to be checked rather than
# assumed. overpass.osm.ch was in this list and answered every question with
# a cheerful HTTP 200 and no elements at all - for St Andrews and for Oakmont
# alike - because it serves a regional extract. A mirror that fails is
# harmless. A mirror that confidently answers "there is no golf course there"
# emptied a course and got the emptiness cached.
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
OVERPASS = OVERPASS_MIRRORS[0]
YARDS_PER_METRE = 1.09361

# What a ball can finish in, by its golf tag.
TROUBLE = {
    "bunker": "bunker",
    "water_hazard": "water",
    "lateral_water_hazard": "water",
}
# What a hole looks like, by tag value. Drawn, not landed in.
WOODED = ("wood", "forest", "scrub", "tree_row", "heath")
SCENERY = {"wood": "trees", "forest": "trees", "scrub": "trees",
           "tree_row": "trees", "heath": "heath", "water": "water",
           "sand": "sand", "grassland": "grass", "rough": "rough"}

# How far off the line still belongs to this hole. A links course lays its
# holes side by side, so everything mapped nearby would otherwise land on all
# of them.
CORRIDOR_YARDS = 70.0
# How close to a hole something has to be to belong to the course that hole
# is on, when the map draws no outline to be inside of.
NEAR_A_HOLE = 90.0
BEHIND_THE_TEE_YARDS = 40.0
PAST_THE_GREEN_YARDS = 60.0
# A hole's way starts at one tee, and the mapped line runs a little short of
# the card, so the championship tee is a short way back from it rather than a
# long one. Reaching further finds the previous hole's tees instead: at a
# hundred and sixty it made the fifth at St Andrews play six hundred and
# ninety-six yards off a card that says five hundred and sixty-eight.
TEE_SEARCH_YARDS = 90.0
# A fairway narrower than this is a fragment of one; wider than this is two
# holes' fairways mapped as one area, which links courses do constantly.
MIN_FAIRWAY = 18.0
MAX_FAIRWAY = 85.0
# Scenery is drawn further out than trouble is played from - a wood a hundred
# yards left is not in play but is the whole look of the hole.
SCENERY_YARDS = 160.0
# A tree mapped on its own is in play in a way a wood is not. Radius is its
# influence rather than its trunk: you are not in the branches, you are behind
# the thing and have to go round it.
LONE_TREE_RADIUS = 5.0
# The largest thing that can honestly be held as a circle. Pine Valley's sandy
# waste really is a hundred and fifteen yards across and really is sand you
# play out of, so this is not tight. Past it, a circle centred on the middle
# of the outline covers ground that is not the hazard at all.
BIGGEST_CIRCLE_YARDS = 130.0


def ask_overpass(query, timeout=180, patience=3, cache=None):
    """Ask Overpass, waiting it out rather than falling over.

    It is a free service under constant load and it says so plainly: a busy
    moment is 429, a heavy query is 504. Both mean try again shortly rather
    than give up, and a crawl of two hundred courses that cannot survive a
    busy minute is not a crawl.

    An answer already had is kept, because the ground under a golf course does
    not move and asking a second time for it is only rude. It also means work
    on a course carries on through a busy hour on the service, which is how
    most of an afternoon gets lost otherwise.
    """
    if cache is not None:
        cache = Path(cache)
        cache.mkdir(parents=True, exist_ok=True)
        kept = cache / (
            "overpass_"
            + hashlib.blake2b(query.encode("utf-8"), digest_size=12).hexdigest()
            + ".json")
        if kept.exists():
            return json.loads(kept.read_text(encoding="utf-8"))

    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    complaints = []
    nothing_there = None
    for attempt in range(patience):
        answered = 0
        for mirror in OVERPASS_MIRRORS:
            host = mirror.split("/")[2]
            request = urllib.request.Request(mirror, data=body)
            request.add_header("User-Agent", "golf-simulator-course-research")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as answer:
                    raw = answer.read()
                payload = json.loads(raw)
            except urllib.error.HTTPError as fault:
                complaints.append(f"{host} said {fault.code}")
                if fault.code not in (429, 504):
                    raise RuntimeError(
                        f"Overpass said {fault.code}: "
                        f"{fault.read().decode('utf-8', 'replace')[:200]}")
                continue
            except (urllib.error.URLError, TimeoutError, OSError,
                    json.JSONDecodeError) as fault:
                complaints.append(f"{host}: {fault}"[:60])
                continue

            answered += 1
            if not payload.get("elements"):
                # Well formed, and empty. That might be the truth, or it might
                # be a server that cannot see this part of the world - one of
                # them could not, and said so with a cheerful 200. Ask another
                # before believing there is no golf course there.
                nothing_there = payload
                complaints.append(f"{host} had nothing")
                continue

            if cache is not None:
                kept.write_bytes(raw)
            return payload

        # Every mirror answered and every one of them said the same.
        if answered == len(OVERPASS_MIRRORS) and nothing_there is not None:
            return nothing_there
        # Every mirror busy at once means waiting is the only thing left.
        if attempt < patience - 1:
            time.sleep(20 * (attempt + 1))

    if nothing_there is not None:
        # Never cached. An empty answer is precisely the one worth asking
        # again another day, and keeping it makes a bad minute permanent.
        return nothing_there
    raise RuntimeError("no Overpass mirror would answer: "
                       + "; ".join(complaints[-4:]))


def around(south, west, north, east, cache=None):
    """Every golf feature and everything around it in a box, with geometry.

    Asked for in three goes rather than one. Overpass charges by how much work
    a query is, and golf features, woods and individual trees over a whole
    links are three heavy searches: together they time out, separately they do
    not. The scenery is also allowed to fail where the golf is not - a course
    with no wood drawn down the left is still the course, and a course with no
    bunkers is nothing at all.
    """
    box = f"{south},{west},{north},{east}"
    natural = "|".join(WOODED + ("water", "sand", "grassland"))

    elements = ask_overpass(
        f'[out:json][timeout:180];'
        f'(way["golf"]({box});way["leisure"="golf_course"]({box}););out geom;',
        cache=cache).get("elements", [])

    for query, needed in (
        (f'[out:json][timeout:180];'
         f'(way["natural"~"^({natural})$"]({box});'
         f'way["landuse"="forest"]({box}););out geom;', False),
        (f'[out:json][timeout:120];node["natural"="tree"]({box});out;', False),
    ):
        try:
            elements += ask_overpass(query, cache=cache).get("elements", [])
        except RuntimeError:
            if needed:
                raise
    return {"elements": elements}


def yards_between(first, second):
    """Ground distance between two lat/lon points, in yards."""
    lat = math.radians((first[0] + second[0]) / 2.0)
    north = (second[0] - first[0]) * 111_320.0
    east = (second[1] - first[1]) * 111_320.0 * math.cos(lat)
    return math.hypot(north, east) * YARDS_PER_METRE


class Frame:
    """The hole's own coordinates: tee at the origin, x down it, z across.

    Every distance the simulator works in is yards from the tee along the line
    of play, so the crawl has to land there rather than in degrees. Flat earth
    over four hundred yards, which is accurate to well under a yard.
    """

    def __init__(self, tee, green):
        self.tee = tee
        run_north = (green[0] - tee[0]) * 111_320.0
        run_east = (green[1] - tee[1]) * 111_320.0 * math.cos(math.radians(tee[0]))
        self.bearing = math.atan2(run_east, run_north)

    def place(self, point):
        """A lat/lon as (down the hole, across it) in yards."""
        north = (point[0] - self.tee[0]) * 111_320.0
        east = (point[1] - self.tee[1]) * 111_320.0 * math.cos(
            math.radians(self.tee[0]))
        along = (north * math.cos(self.bearing) + east * math.sin(self.bearing))
        across = (-north * math.sin(self.bearing) + east * math.cos(self.bearing))
        return along * YARDS_PER_METRE, across * YARDS_PER_METRE


def outline_of(feature):
    """A mapped feature's points, whether it is an outline or a single node.

    A wood is a way with geometry; a tree is a node with nothing but a
    position. Both are things on the course and both have to be placeable.
    """
    shape = feature.get("geometry")
    if shape:
        return [(point["lat"], point["lon"]) for point in shape]
    if "lat" in feature and "lon" in feature:
        return [(feature["lat"], feature["lon"])]
    return []


def centre_of(geometry):
    """The middle of a mapped outline."""
    points = geometry if isinstance(geometry[0], tuple) else [
        (p["lat"], p["lon"]) for p in geometry]
    return (sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points))


def radius_of(geometry):
    """How big an outline is, as the radius of a circle covering it.

    The simulator holds a bunker as a circle, so an outline has to become one.
    The furthest point from the middle rather than the average, because a
    bunker a ball can reach the edge of is a bunker it is in.
    """
    points = geometry if isinstance(geometry[0], tuple) else [
        (p["lat"], p["lon"]) for p in geometry]
    middle = centre_of(points)
    return max(yards_between(middle, point) for point in points)


def across_spread(placed, length):
    """How wide a mapped area is across the hole, in yards.

    Only the part of it alongside the hole counts, and the outermost tenth
    either side is dropped: a fairway polygon usually runs on into the next
    one's, and one stray point would otherwise decide the width.
    """
    on_hole = sorted(across for along, across in placed
                     if 0.0 <= along <= length)
    if len(on_hole) < 6:
        return None
    edge = len(on_hole) // 10
    width = on_hole[-1 - edge] - on_hole[edge]
    return round(width, 1) if MIN_FAIRWAY <= width <= MAX_FAIRWAY else None


def kind_of(feature):
    """What a mapped feature is, in this simulator's terms.

    Returns (what, played) - what it is, and whether a ball finishes in it.
    """
    tags = feature.get("tags", {})
    golf = tags.get("golf")
    if golf in TROUBLE:
        return TROUBLE[golf], True
    if golf in ("green", "tee", "fairway", "rough", "driving_range"):
        return golf, True
    if tags.get("natural") == "tree":
        return "trees", True
    natural = tags.get("natural") or (
        "forest" if tags.get("landuse") == "forest" else None)
    if natural in SCENERY:
        return SCENERY[natural], False
    return None, False


def read_hole(way, features):
    """One mapped hole, as this simulator describes ground.

    The way itself is the line of play - tee to green, bending where the hole
    bends - so it becomes the centreline directly. Everything mapped near it
    becomes the hole: its trouble, its green, its tees, and what it looks like.
    """
    line = way.get("geometry") or []
    if len(line) < 2:
        return None

    tee = (line[0]["lat"], line[0]["lon"])
    green_end = (line[-1]["lat"], line[-1]["lon"])
    frame = Frame(tee, green_end)

    centreline = [frame.place((point["lat"], point["lon"])) for point in line]
    length = sum(math.dist(centreline[i], centreline[i + 1])
                 for i in range(len(centreline) - 1))

    hazards, scenery, tees = [], [], []
    green_radius = fairway_width = None
    fairway_start = fairway_end = None       # where the mown fairway polygon begins/ends along the hole

    for feature in features:
        what, played = kind_of(feature)
        if what is None:
            continue
        shape = outline_of(feature)
        if not shape:
            continue
        along, across = frame.place(centre_of(shape))

        # Only what is actually on this hole, and how far out that reaches
        # depends on what it is. Trouble has to be in the corridor to matter;
        # scenery is drawn well outside it; tee boxes sit behind the start of
        # the way, because the way begins at one tee and the others are back
        # from it.
        reach = CORRIDOR_YARDS if played else SCENERY_YARDS
        if abs(across) > reach:
            continue
        behind = TEE_SEARCH_YARDS if what == "tee" else BEHIND_THE_TEE_YARDS
        beyond = length + PAST_THE_GREEN_YARDS + (reach - CORRIDOR_YARDS)
        if not -behind <= along <= beyond:
            continue

        # `played` as well as `what`, because both a tagged water hazard and
        # the open sea come back called water. Only the first is something a
        # golfer's ball finishes in; the second is the view. Checking the kind
        # alone put the Firth of Clyde on the sixth at Turnberry as a forty
        # yard hazard a hundred and forty-six yards left of the fairway.
        if what in ("bunker", "water") and played:
            size = radius_of(shape)
            if size > BIGGEST_CIRCLE_YARDS:
                # Too big to be a circle. A lake beside a hole came back as a
                # hundred and eighty yard radius sitting fourteen yards off
                # the centreline, which as a circle covers the whole hole and
                # drowns every shot played on it. Kept as something to draw,
                # left out of what a ball can finish in, because a wrong
                # hazard is worse than a missing one.
                scenery.append({
                    "kind": what,
                    "too_big_to_play": True,
                    "outline": [[round(x, 1), round(z, 1)]
                                for x, z in (frame.place(p) for p in shape)],
                })
                continue
            hazards.append({"kind": what, "along": round(along, 1),
                            "across": round(across, 1),
                            "radius_yards": round(size, 1)})
        elif what == "trees" and played:
            # A tree mapped on its own, standing in the corridor.
            hazards.append({"kind": "trees", "along": round(along, 1),
                            "across": round(across, 1),
                            "radius_yards": LONE_TREE_RADIUS})
        elif what == "green":
            # The green belonging to this hole is the one at the end of it.
            if abs(along - length) <= 45.0:
                green_radius = round(radius_of(shape), 1)
        elif what == "tee":
            # Tee boxes behind the way's start are longer sets; ahead of it,
            # shorter. Either way the hole plays from here to the green. Past
            # a third of the way down it is the next hole's tee, not this
            # hole's forward one.
            if along <= 0.40 * length:
                tees.append({"along": round(along, 1),
                             "across": round(across, 1),
                             "plays_yards": round(length - along, 1),
                             "name": feature.get("tags", {}).get("name")})
        elif what == "fairway":
            # How far it spreads across the hole, not how big it is. A fairway
            # is a long thin polygon, so its radius is half its length and
            # says nothing whatever about how wide it is to hit.
            placed = [frame.place(point) for point in shape]
            spread = across_spread(placed, length)
            if spread and (fairway_width is None or spread > fairway_width):
                fairway_width = spread
            # Where the fairway sits ALONG the hole - its near edge is where the
            # mown fairway begins (rough/tee carry before it). Only the part in
            # the hole corridor counts; a polygon that runs on into the next
            # hole is clipped to this hole's length.
            if spread:
                on = sorted(a for a, ac in placed
                            if abs(ac) <= CORRIDOR_YARDS and 0.0 <= a <= length)
                if len(on) >= 4:
                    lo, hi = on[len(on) // 20], on[-1 - len(on) // 20]  # drop stray tips
                    if fairway_start is None or lo < fairway_start:
                        fairway_start = round(lo, 1)
                    if fairway_end is None or hi > fairway_end:
                        fairway_end = round(hi, 1)
        elif not played:
            scenery.append({
                "kind": what,
                "outline": [[round(x, 1), round(z, 1)]
                            for x, z in (frame.place(p) for p in shape)],
            })

    tags = way.get("tags", {})
    return {
        "number": int(tags["ref"]) if str(tags.get("ref", "")).isdigit() else None,
        "name": tags.get("name"),
        "par": int(tags["par"]) if str(tags.get("par", "")).isdigit() else None,
        "length_yards": round(length, 1),
        # Where the hole is on the earth and which way it points. Everything
        # else here is in the hole's own frame, which is what the simulator
        # wants, but that frame is unanchored: without these you cannot put a
        # crawled bunker back on a photograph and check it is really there.
        "tee_latlon": [round(tee[0], 6), round(tee[1], 6)],
        "bearing_deg": round(math.degrees(frame.bearing), 2),
        "centreline": [(round(x, 1), round(z, 1)) for x, z in centreline],
        "hazards": sorted(hazards, key=lambda h: h["along"]),
        "tees": sorted(tees, key=lambda t: -t["plays_yards"]),
        "green_radius_yards": green_radius,
        "fairway_width_yards": fairway_width,
        "fairway_start_yards": fairway_start,
        "fairway_end_yards": fairway_end,
        "scenery": scenery,
    }


def inside(point, polygon):
    """Is a point within a mapped outline? Ray casting, counting crossings."""
    lat, lon = point
    crossings = False
    previous = polygon[-1]
    for corner in polygon:
        if (corner[0] > lat) != (previous[0] > lat):
            where = (previous[1] - corner[1]) * (lat - corner[0]) / (
                previous[0] - corner[0]) + corner[1]
            if lon < where:
                crossings = not crossings
        previous = corner
    return crossings


def golf_courses(elements):
    """Every named golf course outline in what came back."""
    courses = []
    for feature in elements:
        tags = feature.get("tags", {})
        if tags.get("leisure") != "golf_course":
            continue
        outline = outline_of(feature)
        if len(outline) >= 4 and tags.get("name"):
            courses.append((tags["name"], outline))
    return courses


def the_one_called(courses, wanted):
    """The course outline whose name is the one asked for.

    A box is drawn round a club and a club is rarely one course. Oakmont's
    box asked for eighteen holes and returned six hundred and sixteen
    bunkers, which is three and a half times what Oakmont has, because the
    neighbours were in it. Matching on the name rather than on the box is the
    difference between a course and a district.
    """
    if not wanted:
        return None
    plain = "".join(c for c in wanted.lower() if c.isalnum())
    best, best_score = None, 0
    for name, outline in courses:
        theirs = "".join(c for c in name.lower() if c.isalnum())
        if theirs == plain:
            return outline
        # The longest run of the asked-for name that appears in theirs.
        score = sum(1 for size in range(4, len(plain) + 1)
                    if plain[:size] in theirs)
        if score > best_score:
            best, best_score = outline, score
    return best


def reconstruct_holes(found, ground, belongs):
    """When a course has no golf=hole routing ways mapped (many do not), rebuild
    the holes from the golf=green and golf=tee polygons it DOES have: pair each
    green with the tee that plays to it, order them into a round, and run each
    tee->green line through read_hole so it picks up that hole's sand, water and
    fairway exactly as a real hole way would. Order is by mapped hole number
    (ref) when present, else a nearest-green-to-next-tee walking chain.
    """
    greens, tees = [], []
    for f in found:
        g = f.get("tags", {}).get("golf")
        if g not in ("green", "tee") or not belongs(f):
            continue
        shape = outline_of(f)
        if not shape:
            continue
        ref = f.get("tags", {}).get("ref")
        ref = int(ref) if str(ref).isdigit() else None
        (greens if g == "green" else tees).append({"c": centre_of(shape), "ref": ref})
    if len(greens) < 9 or len(tees) < 9:
        return []
    # greedy tee<->green pairing over plausible hole lengths, shortest first
    cand = []
    for gi, gr in enumerate(greens):
        for ti, te in enumerate(tees):
            d = yards_between(gr["c"], te["c"])
            if 60.0 <= d <= 700.0:
                cand.append((d, gi, ti))
    cand.sort()
    used_g, used_t, pairs = set(), set(), []
    for d, gi, ti in cand:
        if gi in used_g or ti in used_t:
            continue
        used_g.add(gi); used_t.add(ti)
        pairs.append([tees[ti], greens[gi]])
    if len(pairs) < 12:
        return []
    # order into a round
    if sum(1 for t, g in pairs if g["ref"]) >= len(pairs) * 0.6:
        pairs.sort(key=lambda p: (p[1]["ref"] or 99))
    else:
        chain, rest = [pairs[0]], pairs[1:]
        while rest:
            lg = chain[-1][1]["c"]
            k = min(range(len(rest)), key=lambda i: yards_between(lg, rest[i][0]["c"]))
            chain.append(rest.pop(k))
        pairs = chain
    holes = []
    for num, (te, gr) in enumerate(pairs[:18], 1):
        way = {"geometry": [{"lat": te["c"][0], "lon": te["c"][1]},
                            {"lat": gr["c"][0], "lon": gr["c"][1]}],
               "tags": {"golf": "hole", "ref": str(num)}}
        h = read_hole(way, ground)
        if h:
            h["number"] = num
            h["reconstructed"] = True
            holes.append(h)
    return holes


def crawl(south, west, north, east, course_name=None, cache=None):
    """Every hole of a course, read into simulator terms.

    Given a name, only what is actually inside that course's own outline is
    returned. Without one, everything in the box comes back, which is what a
    first look at an area wants and is not what building a course wants.
    """
    found = around(south, west, north, east, cache=cache).get("elements", [])
    boundary = the_one_called(golf_courses(found), course_name)

    def belongs(feature):
        if boundary is None:
            return True
        shape = outline_of(feature)
        return bool(shape) and inside(centre_of(shape), boundary)

    # Some courses have no outline drawn at all - Oakmont is one - so there is
    # nothing to be inside of. Falling back to the box counts the neighbours'
    # sand, so fall back to the holes instead: what belongs to a course is
    # what lies along the holes it is made of.
    if boundary is None and course_name:
        # Walked, not just the corners: a hole mapped as two points has
        # nothing within reach of its own middle otherwise.
        walked = []
        for feature in found:
            if feature.get("tags", {}).get("golf") != "hole":
                continue
            line = outline_of(feature)
            for first, second in zip(line, line[1:]):
                steps = max(1, int(yards_between(first, second) / 50.0))
                walked += [(first[0] + (second[0] - first[0]) * i / steps,
                            first[1] + (second[1] - first[1]) * i / steps)
                           for i in range(steps + 1)]

        if walked:
            def belongs(feature, points=walked):
                # A hole is the course, so it never has to prove it belongs.
                if feature.get("tags", {}).get("golf") == "hole":
                    return True
                shape = outline_of(feature)
                if not shape:
                    return False
                middle = centre_of(shape)
                return any(yards_between(middle, point) <= NEAR_A_HOLE
                           for point in points)

    ground = [f for f in found
              if kind_of(f)[0] is not None and belongs(f)]

    holes = []
    for way in found:
        tags = way.get("tags", {})
        if tags.get("golf") != "hole" or not belongs(way):
            continue
        read = read_hole(way, ground)
        if read:
            read["course_hint"] = tags.get("operator") or tags.get("addr:city")
            holes.append(read)
    # No routing ways mapped (or too few)? Rebuild the round from greens + tees.
    real = [h for h in holes if h and (h.get("length_yards") or 0) > 60]
    if len(real) < 14:
        rebuilt = reconstruct_holes(found, ground, belongs)
        if len(rebuilt) > len(real):
            holes = rebuilt
    return holes
