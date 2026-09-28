"""Find where a hole bends, by looking at it.

Thirteen of the eighteen mapped hole ways at St Andrews are a straight line
from tee to green. They carry the routing and not the shape, so a dogleg -
which is most of what makes a hole ask a question off the tee - is the one
thing neither the card nor the map has.

The photograph has it. A fairway is mown, and mown grass from six hundred
feet up is a distinctly different green from rough, heather, sand or trees:
brighter, and far more uniform because it is cut to one height. That is
enough to pick it out of a picture without recognising anything.

So: take the strip of ground between tee and green, find the mown grass in
it, and walk down the hole asking where the middle of that grass is at each
step. Where the middle wanders off the straight line, the hole bends.

THIS DOES NOT WORK YET, AND NOTHING SHOULD CALL IT.

It passes its tests, and its tests are the problem: they are drawn by the
test itself, in colours the test chose, and on those it finds a dogleg of
forty-eight yards where forty-eight yards were painted. On a photograph of
the actual Old Course it finds nothing of the kind. Every hole came back
bending fifty to sixty-seven yards, including the eighteenth, which is
straight. The line it walks zigzags across the whole corridor.

The reason is simple and was visible the moment the mask was drawn over the
picture rather than reasoned about. On a links, the dunes, the rough, the
gorse banks and the fairway are all green. The separation between them that
these thresholds rely on is one I invented along with the fixtures. Cut grass
really is brighter than rough - but not by a margin that survives one
photograph to the next, and not on ground where the rough is marram and sand.

Kept rather than deleted because the negative result is worth having: a
fairway cannot be found by colour alone, so a dogleg will have to come from
the mapped fairway polygon where OSM has one, or from the research, and not
from a threshold. Left here with its tests so the next attempt starts from
what is already known not to work.
"""

import math

import numpy as np

from golf_relief import YARDS_PER_METRE, step_from

# How far either side of the line to look. Wider than a fairway on purpose:
# the bend is found by seeing where the grass is not centred.
CORRIDOR_YARDS = 70.0
# Steps down the hole. About one every twenty yards on a par four, which is
# finer than any hole bends.
STATIONS = 24
# Below this fraction of stations carrying grass, there was nothing to see -
# cloud, drought, a course that does not mow to type - and no bend is claimed.
ENOUGH_GRASS = 0.45
# A wander smaller than this is mowing, not architecture.
REAL_BEND_YARDS = 12.0


# What separates a fairway from everything else it sits next to. Rough is the
# hard one and the reason the first attempt failed: rough is green too. What
# it is not is bright - grass cut to eight millimetres reflects a good deal
# more than grass left at fifty, and it is that, rather than colour, that
# tells them apart from above. Trees are greener than either and darker than
# both. Sand and water are not green at all.
GREEN_ENOUGH = 0.44          # of the pixel's total light
BRIGHT_ENOUGH = 150.0        # summed across the three channels
GREENER_THAN_BLUE = 1.10


def mown(image):
    """Which pixels are cut grass, as a mask over an aerial photograph.

    Thresholds rather than anything cleverer, and they are calibrated rather
    than derived - which means they will be wrong somewhere. A course
    photographed in drought, under cloud, or in a season when the rough is
    burnt as pale as the fairway will not separate. That is what `seen` in
    `bend_of` is for: it reports how much of the hole this actually found, and
    a hole it could not see is left straight rather than guessed at.
    """
    picture = np.asarray(image.convert("RGB"), dtype=np.float64)
    green, blue = picture[..., 1], picture[..., 2]
    brightness = picture.sum(axis=2) + 1e-6

    return ((green / brightness > GREEN_ENOUGH)
            & (brightness > BRIGHT_ENOUGH)
            & (green > blue * GREENER_THAN_BLUE))


def line_of_sight(tee_latlon, bearing_deg, length_yards, zoom, corner,
                  to_pixel):
    """Where each station down the hole lands on the photograph."""
    stations = []
    for step in range(STATIONS + 1):
        along = length_yards * step / STATIONS
        stations.append((along, to_pixel(along, 0.0)))
    return stations


def bend_of(image, tee_latlon, bearing_deg, length_yards, to_pixel,
            yards_per_pixel):
    """Where the mown ground is, a step at a time down the hole.

    Returns the centreline it found in the hole's own frame, how far it
    strays from straight, and how much of the hole it actually saw grass on.
    """
    grass = mown(image)
    rows, columns = grass.shape

    found, seen = [], 0
    for step in range(STATIONS + 1):
        along = length_yards * step / STATIONS

        # Sample across the corridor at this distance down the hole.
        offsets, hits = [], []
        across = -CORRIDOR_YARDS
        while across <= CORRIDOR_YARDS:
            x, y = to_pixel(along, across)
            column, row = int(round(x)), int(round(y))
            if 0 <= row < rows and 0 <= column < columns:
                offsets.append(across)
                hits.append(bool(grass[row, column]))
            across += max(1.0, yards_per_pixel)

        mown_here = [offset for offset, hit in zip(offsets, hits) if hit]
        if len(mown_here) >= 3:
            seen += 1
            found.append((round(along, 1), round(float(np.median(mown_here)), 1)))
        else:
            found.append((round(along, 1), None))

    carried = seen / float(STATIONS + 1)
    walked = [(along, across) for along, across in found if across is not None]
    wander = max((abs(across) for _, across in walked), default=0.0)

    return {
        "centreline": walked,
        "bend_yards": round(wander, 1),
        "seen": round(carried, 2),
        # A hole is only called bent if the grass was actually there to say
        # so, and if what it says is architecture rather than mowing.
        "bends": bool(carried >= ENOUGH_GRASS and wander >= REAL_BEND_YARDS),
        "trustworthy": bool(carried >= ENOUGH_GRASS),
    }


def waypoints_from(bend, keep=5):
    """The found line thinned down to the few points a hole is built from.

    A hole is a handful of waypoints, not two dozen. The ends are kept
    because they are the tee and the green, and the rest are spread evenly
    between them.
    """
    line = bend["centreline"]
    if len(line) <= keep:
        return list(line)
    wanted = [round(i * (len(line) - 1) / (keep - 1)) for i in range(keep)]
    return [line[i] for i in sorted(set(wanted))]
