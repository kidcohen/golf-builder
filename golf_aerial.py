"""Look at a hole from above, and draw what was crawled on top of it.

The crawl says where the sand is. It does not say whether the crawl is right,
and a hole built from data nobody has looked at is a hole nobody has checked.
This fetches the satellite imagery of a hole and marks on it every bunker,
tree and tee the crawl found, so the two can be set side by side and the
disagreements seen rather than assumed away.

Esri's World Imagery is open and needs no key. Tiles are 256 pixels and the
usual web-mercator scheme, so a few of them stitched together cover a hole at
a zoom where bunkers, trees and water are plain.

This is a research tool. Nothing in the game calls it.
"""

import math
import urllib.request
from pathlib import Path

TILE_URL = ("https://services.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}")
TILE_PIXELS = 256
EARTH_CIRCUMFERENCE_M = 40_075_016.686
YARDS_PER_METRE = 1.09361

# What each thing is drawn in. Sand light, water blue, trees green, tees red -
# the point is to tell them apart at a glance against grass and sand that look
# much the same from six hundred feet up.
COLOURS = {
    "bunker": (255, 214, 64),
    "water": (64, 160, 255),
    "trees": (64, 220, 96),
    "tee": (255, 64, 64),
    "centreline": (255, 255, 255),
}


def tile_of(lat, lon, zoom):
    """Which tile a point falls in, and where in it."""
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    radians = math.radians(lat)
    y = (1.0 - math.log(math.tan(radians) + 1 / math.cos(radians)) / math.pi) / 2.0 * n
    return x, y


def metres_per_pixel(lat, zoom):
    """Ground covered by one pixel, which is what makes a scale bar possible."""
    return (EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat))
            / (2 ** zoom * TILE_PIXELS))


def _download(zoom, x, y, into):
    """One tile from disk cache or the service; None if it can't be had."""
    from PIL import Image
    cached = Path(into) / f"tile_{zoom}_{x}_{y}.jpg"
    try:
        if not cached.exists():
            request = urllib.request.Request(
                TILE_URL.format(z=zoom, x=x, y=y),
                headers={"User-Agent": "golf-simulator-course-research"})
            with urllib.request.urlopen(request, timeout=30) as answer:
                cached.write_bytes(answer.read())
        return Image.open(cached).convert("RGB")
    except Exception:
        return None


def _is_placeholder(img):
    """Esri answers an area it has no imagery for at this zoom with a flat grey
    'Map data not yet available' tile, not an error. Almost every pixel is grey."""
    import numpy as np
    a = np.asarray(img.resize((64, 64)), dtype=int)
    return float(((a.max(2) - a.min(2)) < 8).mean()) > 0.85


def _tile(zoom, x, y, into, fallback=4):
    """The tile at (zoom, x, y); where the service has no imagery at that zoom (remote
    courses - Tasmania, Cape Breton, Izu...), the matching quarter of the parent tile a
    level or more down, scaled up - softer, but the real ground, never a grey square."""
    from PIL import Image
    img = _download(zoom, x, y, into)
    if img is not None and not _is_placeholder(img):
        return img
    for up in range(1, fallback + 1):
        z, k = zoom - up, 2 ** up
        par = _download(z, x // k, y // k, into)
        if par is None or _is_placeholder(par):
            continue
        s = TILE_PIXELS // k
        qx, qy = (x % k) * s, (y % k) * s
        return par.crop((qx, qy, qx + s, qy + s)).resize((TILE_PIXELS, TILE_PIXELS), Image.BICUBIC)
    return img                                       # nothing better anywhere: keep what we had


def fetch(lat, lon, zoom, across, down, into):
    """A grid of tiles centred on a point, stitched into one image.

    Returns the image and where its top-left corner sits, so anything else
    can be placed on it afterwards.
    """
    from PIL import Image

    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    centre_x, centre_y = tile_of(lat, lon, zoom)
    first_x = int(centre_x) - across // 2
    first_y = int(centre_y) - down // 2

    canvas = Image.new("RGB", (across * TILE_PIXELS, down * TILE_PIXELS),
                       (32, 40, 32))
    got = 0
    for ix in range(across):
        for iy in range(down):
            x, y = first_x + ix, first_y + iy
            img = _tile(zoom, x, y, into)
            if img is not None:
                canvas.paste(img, (ix * TILE_PIXELS, iy * TILE_PIXELS))
                got += 1                   # (a tile nobody has at all stays a dark square)

    return canvas, (first_x, first_y), got, across * down


def pixel_of(lat, lon, zoom, corner):
    """Where a lat/lon lands on an image fetched by `fetch`."""
    x, y = tile_of(lat, lon, zoom)
    return ((x - corner[0]) * TILE_PIXELS, (y - corner[1]) * TILE_PIXELS)


def draw_relief(relief, image, zoom, corner, tee_latlon, spacing_yards=1.0):
    """Shade the ground by how it falls, over the photograph.

    A hole photographed from overhead shows its sand and its trees plainly
    and its hills not at all: a rise reads as a faint change of colour, if it
    reads at all. This puts the shape back - contour lines every yard of
    height, so a plateau, a punchbowl and a fall away behind a green can be
    seen rather than assumed.
    """
    from PIL import Image, ImageDraw

    width, height = image.size
    per_pixel = metres_per_pixel(tee_latlon[0], zoom)
    n = 2 ** zoom

    # Height at a coarse grid, then contour lines between the bands.
    step = 8
    across = range(0, width, step)
    down = range(0, height, step)
    grid = []
    for py in down:
        row = []
        for px in across:
            x = corner[0] + px / TILE_PIXELS
            y = corner[1] + py / TILE_PIXELS
            lon = x / n * 360.0 - 180.0
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
            row.append(relief.height_yards(lat, lon))
        grid.append(row)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    lowest = min(min(row) for row in grid)
    for iy, py in enumerate(down):
        for ix, px in enumerate(across):
            band = int((grid[iy][ix] - lowest) / spacing_yards)
            right = int((grid[iy][min(ix + 1, len(row) - 1)] - lowest) / spacing_yards)
            below = int((grid[min(iy + 1, len(grid) - 1)][ix] - lowest) / spacing_yards)
            if band != right or band != below:
                shade = 40 + (band % 5) * 40
                draw.rectangle([px, py, px + step, py + step],
                               fill=(255, shade, 0, 150))

    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"),
                (0, 0))
    return image, round(max(max(r) for r in grid) - lowest, 1)


def draw_hole(hole, tee_latlon, bearing_deg, image, zoom, corner):
    """Mark a crawled hole onto its own photograph.

    The crawl works in the hole's frame - yards down it and across it - so
    getting back to a picture means undoing that: rotate by the hole's bearing
    and step out from the tee.
    """
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    per_pixel = metres_per_pixel(tee_latlon[0], zoom)
    bearing = math.radians(bearing_deg)

    def to_pixel(along, across):
        metres_north = (along * math.cos(bearing) - across * math.sin(bearing))
        metres_east = (along * math.sin(bearing) + across * math.cos(bearing))
        lat = tee_latlon[0] + (metres_north / YARDS_PER_METRE) / 111_320.0
        lon = tee_latlon[1] + (metres_east / YARDS_PER_METRE) / (
            111_320.0 * math.cos(math.radians(tee_latlon[0])))
        return pixel_of(lat, lon, zoom, corner)

    line = [to_pixel(x, z) for x, z in hole["centreline"]]
    if len(line) > 1:
        draw.line(line, fill=COLOURS["centreline"], width=3)

    for thing in hole["hazards"]:
        middle = to_pixel(thing["along"], thing["across"])
        radius = max(3.0, (thing["radius_yards"] / YARDS_PER_METRE) / per_pixel)
        draw.ellipse([middle[0] - radius, middle[1] - radius,
                      middle[0] + radius, middle[1] + radius],
                     outline=COLOURS.get(thing["kind"], (255, 255, 255)), width=3)

    for tee in hole["tees"]:
        middle = to_pixel(tee["along"], tee["across"])
        draw.rectangle([middle[0] - 6, middle[1] - 6, middle[0] + 6, middle[1] + 6],
                       outline=COLOURS["tee"], width=3)

    return image
