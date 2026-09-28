"""How high the ground is, so a hole has hills rather than a flat plan.

Every waypoint on every hole here carries a height, and every one of those
heights was mine. The ball flight already knows what a drop does to a shot,
so a green fifty feet below the tee costs a club without anything having to
say so - but only if the fifty feet is real. Invented, it is just a number
that makes the hole play differently from the hole it is copying.

Terrarium tiles carry the height in the pixel itself: metres above sea level
are R*256 + G + B/256 - 32768. They use the same web-mercator scheme as the
satellite imagery, so a hole already located for a photograph is already
located for its relief.

What this is good for, and what it is not:

  Hole-scale relief is real. St Andrews reads five to thirteen metres across
  a kilometre and Augusta reads seventy-four to ninety-nine, which is the
  difference between a links and a hillside and is exactly right.

  Green-scale contour is at or past the limit of the data. The postings are
  about thirty metres apart in most of the world - roughly one across a whole
  green - and finer only where a country has surveyed better. A green's tilt
  read from this is a hint, not a measurement, and `slope_at` says so by
  returning how much ground it actually had to work with.
"""

import hashlib
import io
import math
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

TERRAIN_URL = ("https://s3.amazonaws.com/elevation-tiles-prod/terrarium/"
               "{z}/{x}/{y}.png")
TILE_PIXELS = 256
EARTH_CIRCUMFERENCE_M = 40_075_016.686
YARDS_PER_METRE = 1.09361

# Zoom 14 is about nine metres a pixel at golfing latitudes. The underlying
# survey is coarser than that nearly everywhere, so this is not more detail
# than exists - it is enough not to throw away the detail that does.
DEFAULT_ZOOM = 14
# Below this the fit is being asked to find a slope in fewer postings than a
# slope needs, and it will happily return one.
ENOUGH_SAMPLES = 6
# How far apart the ground was actually surveyed - about thirty metres, which
# is SRTM and is most of the world. Not the same thing as how far apart the
# tile's pixels are: at zoom 14 those are under six yards, but they are
# interpolated up from this, and counting them would have called a green's
# tilt trustworthy on forty samples that are really about one. Where a
# country has surveyed finer the tiles carry it and this is pessimistic,
# which is the direction to be wrong in.
SURVEY_SPACING_YARDS = 33.0
# Real ground surveyed at a metre always moves a little across seven hundred
# metres, even on a links. A patch flatter than this is a service saying it
# has nothing without saying so.
FLAT_IS_NOT_GROUND = 0.05


def height_from_pixel(pixel):
    """Metres above sea level, out of a terrarium pixel."""
    red, green, blue = pixel[0], pixel[1], pixel[2]
    return (red * 256.0 + green + blue / 256.0) - 32768.0


def tile_of(lat, lon, zoom):
    """Which tile a point falls in, fractionally, so it can be interpolated."""
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    radians = math.radians(lat)
    y = (1.0 - math.log(math.tan(radians) + 1 / math.cos(radians)) / math.pi) / 2.0 * n
    return x, y


def metres_per_pixel(lat, zoom):
    """Ground covered by one pixel of a tile at this latitude."""
    return (EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat))
            / (2 ** zoom * TILE_PIXELS))


def download_tile(zoom, x, y, cache=None):
    """One terrarium tile, kept on disk once fetched.

    A course is a few tiles and two hundred courses asked for repeatedly is
    rude to a service that charges nothing.
    """
    from PIL import Image

    if cache is not None:
        cache = Path(cache)
        cache.mkdir(parents=True, exist_ok=True)
        kept = cache / f"terrain_{zoom}_{x}_{y}.png"
        if kept.exists():
            return Image.open(kept).convert("RGB")

    request = urllib.request.Request(
        TERRAIN_URL.format(z=zoom, x=x, y=y),
        headers={"User-Agent": "golf-simulator-course-research"})
    with urllib.request.urlopen(request, timeout=30) as answer:
        raw = answer.read()
    if cache is not None:
        kept.write_bytes(raw)
    return Image.open(io.BytesIO(raw)).convert("RGB")


class Relief:
    """The height of the ground, asked for a point at a time.

    Takes how it gets a tile rather than going and getting one, so the whole
    of this can be tested against ground whose shape is known instead of
    against whatever the real world happens to do near a golf course.
    """

    def __init__(self, zoom=DEFAULT_ZOOM, tile_source=None, cache=None):
        self.zoom = zoom
        self._tiles = {}
        self._source = tile_source or (
            lambda z, x, y: download_tile(z, x, y, cache=cache))

    def _tile(self, x, y):
        if (x, y) not in self._tiles:
            image = self._source(self.zoom, x, y)
            self._tiles[(x, y)] = np.asarray(image, dtype=np.float64)
        return self._tiles[(x, y)]

    def _pixel(self, tile_x, tile_y, px, py):
        """One pixel's height, reaching into the neighbouring tile if it must."""
        tile_x += px // TILE_PIXELS
        tile_y += py // TILE_PIXELS
        px, py = px % TILE_PIXELS, py % TILE_PIXELS
        row = self._tile(tile_x, tile_y)[py][px]
        return height_from_pixel(row)

    def height_metres(self, lat, lon):
        """Metres above sea level, interpolated between postings.

        Nearest-pixel would make a hole climb in steps of whatever the tile
        resolution happens to be, and a fairway that rises nine metres at one
        stride is not a fairway.
        """
        x, y = tile_of(lat, lon, self.zoom)
        tile_x, tile_y = int(x), int(y)
        px = (x - tile_x) * TILE_PIXELS
        py = (y - tile_y) * TILE_PIXELS
        left, top = int(px), int(py)
        fx, fy = px - left, py - top

        corners = [[self._pixel(tile_x, tile_y, left + dx, top + dy)
                    for dx in (0, 1)] for dy in (0, 1)]
        upper = corners[0][0] * (1 - fx) + corners[0][1] * fx
        lower = corners[1][0] * (1 - fx) + corners[1][1] * fx
        return upper * (1 - fy) + lower * fy

    def height_yards(self, lat, lon):
        return self.height_metres(lat, lon) * YARDS_PER_METRE

    def spacing_yards(self, lat=None, lon=None):
        """How far apart the ground was really surveyed here.

        Asked of the source rather than assumed by the caller, because the
        answer decides whether a fitted slope is a measurement or a guess and
        the caller has no way of knowing it.
        """
        return SURVEY_SPACING_YARDS


def _fetch_raster(url, cache, timeout=90):
    """A float raster from a URL, kept once fetched, or None if there is none.

    An empty cached file records "asked, and there is nothing there", so a
    course in a country nobody has flown is not asked about once per waypoint
    for ever.
    """
    kept = None
    if cache is not None:
        cache = Path(cache)
        cache.mkdir(parents=True, exist_ok=True)
        kept = cache / ("lidar_" + hashlib.blake2b(
            url.encode("utf-8"), digest_size=12).hexdigest() + ".tif")
        if kept.exists():
            raw = kept.read_bytes()
            return Lidar._as_array(raw) if raw else None

    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "golf-simulator-course-research"})
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            raw = answer.read()
    except Exception:
        raw = b""
    grid = Lidar._as_array(raw)
    if kept is not None:
        kept.write_bytes(raw if grid is not None else b"")
    return grid


def three_dep_patches(cache=None):
    """The United States, at a metre, from USGS 3DEP. No key, no charge."""
    service = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
               "3DEPElevation/ImageServer/exportImage")

    def source(bounds, pixels):
        west, south, east, north = bounds
        query = urllib.parse.urlencode({
            "bbox": f"{west},{south},{east},{north}",
            "bboxSR": 3857, "imageSR": 3857,
            "size": f"{pixels},{pixels}",
            "format": "tiff", "pixelType": "F32",
            "interpolation": "RSP_BilinearInterpolation",
            "f": "image"})
        return _fetch_raster(f"{service}?{query}", cache)

    return source


def england_patches(cache=None):
    """England, at a metre, from the Environment Agency's composite DTM.

    A WCS rather than an image service, because a WMS of a terrain model
    returns a picture of the ground shaded to look like ground - grey pixels,
    not heights. The coverage itself is what a green's tilt is fitted through.
    """
    service = ("https://environment.data.gov.uk/spatialdata/"
               "lidar-composite-digital-terrain-model-dtm-1m/wcs")
    coverage = ("13787b9a-26a4-4775-8523-806d13af58fc"
                "__Lidar_Composite_Elevation_DTM_1m")

    def source(bounds, pixels):
        west, south, east, north = bounds
        # Named axes, not x and y: asked the other way this service answers
        # 500 rather than saying which it wanted.
        query = (f"service=WCS&version=2.0.1&request=GetCoverage"
                 f"&coverageId={coverage}"
                 f"&subset=E({west},{east})&subset=N({south},{north})"
                 f"&format=image/tiff")
        return _fetch_raster(f"{service}?{query}", cache)

    return source


def england(cache=None, patch_metres=700.0):
    return Lidar(patch_metres=patch_metres, cache=cache,
                 projection=BritishGrid, patch_source=england_patches(cache))


_SCOT_BUCKET = "https://srsp-open-data.s3.eu-west-2.amazonaws.com/"


def _os_tile10(E, N):
    """The 10 km Ordnance Survey grid ref for an easting/northing (e.g. NO51)."""
    E, N = int(E), int(N)
    e100, n100 = E // 100000, N // 100000
    l1 = (19 - n100) - (19 - n100) % 5 + (e100 + 10) // 5
    l2 = (19 - n100) * 5 % 25 + e100 % 5
    if l1 > 7:
        l1 += 1                       # the OS letter grid skips 'I'
    if l2 > 7:
        l2 += 1
    return (chr(ord('A') + l1) + chr(ord('A') + l2)
            + f"{(E % 100000) // 10000}{(N % 100000) // 10000}")


def _os_quadrant(E, N):
    return ('N' if N % 10000 >= 5000 else 'S') + ('E' if E % 10000 >= 5000 else 'W')


def scotland_patches(cache=None):
    """Scotland, 50 cm / 1 m, from the Scottish Remote Sensing Portal.

    Its GeoServer WCS is disabled and the WMS only draws pictures, so the
    bare-earth DTM is read straight from the open Cloud-Optimised GeoTIFFs on
    S3 - a window at a time, which needs rasterio. Same British grid England's
    DTM uses, so the projection is shared.
    """
    import urllib.request

    def head(url):
        try:
            urllib.request.urlopen(urllib.request.Request(url, method="HEAD"),
                                   timeout=12)
            return True
        except Exception:
            return False

    def cog(E, N):
        t10 = _os_tile10(E, N)
        t5 = t10 + _os_quadrant(E, N)
        for phase, res, tile in [(5, "50CM", t5), (6, "50CM", t5), (4, "50CM", t5),
                                 (3, "50CM", t5), (1, "1M", t10), (2, "1M", t10)]:
            url = (f"{_SCOT_BUCKET}lidar/phase-{phase}/dtm/27700/gridded/"
                   f"{tile}_{res}_DTM_PHASE{phase}.tif")
            if head(url):
                return url
        return None

    def source(bounds, pixels):
        try:
            import rasterio
            from rasterio.windows import from_bounds
        except Exception:
            return None
        E = (bounds[0] + bounds[2]) / 2.0
        N = (bounds[1] + bounds[3]) / 2.0
        # Only Great Britain has an OS grid ref; outside it the tile letters
        # overflow chr(). Guard so a non-GB course falls through to SRTM cleanly.
        if not (0.0 <= E < 800000.0 and 0.0 <= N < 1300000.0):
            return None
        url = cog(E, N)
        if not url:
            return None
        try:
            with rasterio.open(url) as ds:
                window = from_bounds(bounds[0], bounds[1], bounds[2], bounds[3],
                                     ds.transform)
                arr = ds.read(1, window=window, out_shape=(pixels, pixels),
                              boundless=True, fill_value=-1e12)
                nod = ds.nodata
            arr = np.asarray(arr, dtype=np.float64)
            if nod is not None:
                arr[arr == nod] = -1e12
            # nodata must sit BELOW Lidar.NO_DATA_BELOW (-1e10) or the reader treats
            # it as real ground; mask sentinels / sea / anything off a sane land range
            arr[(arr < -200.0) | (arr > 1400.0)] = -1e12
            return arr
        except Exception:
            return None

    return source


def scotland(cache=None, patch_metres=700.0):
    return Lidar(patch_metres=patch_metres, cache=cache,
                 projection=BritishGrid, patch_source=scotland_patches(cache))


def united_states(cache=None, patch_metres=700.0):
    return Lidar(patch_metres=patch_metres, cache=cache,
                 projection=Mercator, patch_source=three_dep_patches(cache))


class Mercator:
    """Web mercator, which the American service speaks.

    Mercator's units are not metres: they stretch by one over the cosine of
    the latitude, so a patch of a given number of ground metres is that much
    wider in the coordinates it has to be asked for.
    """

    LIMIT = 20037508.34

    @staticmethod
    def to_xy(lat, lon):
        x = lon * Mercator.LIMIT / 180.0
        y = (math.log(math.tan((90.0 + lat) * math.pi / 360.0))
             / (math.pi / 180.0)) * Mercator.LIMIT / 180.0
        return x, y

    @staticmethod
    def units_per_metre(lat):
        return 1.0 / math.cos(math.radians(lat))


class BritishGrid:
    """The Ordnance Survey National Grid, which England's DTM is written in.

    Already in metres, so nothing stretches. Getting here from the satellite
    positions everything else uses needs a datum shift as well as a
    projection - Britain's grid is tied to an ellipsoid fitted to Britain in
    1830, and skipping the shift puts a course a hundred metres from where it
    is. The seven parameter shift used here lands within about five metres,
    which is close enough to fit a green's tilt and not close enough to place
    a bunker, so it is used for the one and not the other.
    """

    # Airy 1830, and the Helmert shift from WGS84 onto it.
    A, B = 6377563.396, 6356256.909
    SCALE, LAT0, LON0 = 0.9996012717, math.radians(49.0), math.radians(-2.0)
    EAST0, NORTH0 = 400000.0, -100000.0
    SHIFT = (-446.448, 125.157, -542.060)
    ROTATE = tuple(math.radians(seconds / 3600.0)
                   for seconds in (-0.1502, -0.2470, -0.8421))
    STRETCH = 20.4894e-6

    @staticmethod
    def to_xy(lat, lon):
        phi, lam = math.radians(lat), math.radians(lon)

        # WGS84 geodetic -> cartesian, shifted, -> Airy geodetic.
        wgs_a, wgs_e2 = 6378137.0, 6.69437999014e-3
        nu = wgs_a / math.sqrt(1 - wgs_e2 * math.sin(phi) ** 2)
        x = nu * math.cos(phi) * math.cos(lam)
        y = nu * math.cos(phi) * math.sin(lam)
        z = (1 - wgs_e2) * nu * math.sin(phi)

        tx, ty, tz = BritishGrid.SHIFT
        rx, ry, rz = BritishGrid.ROTATE
        s = 1 + BritishGrid.STRETCH
        x2 = tx + s * x - rz * y + ry * z
        y2 = ty + rz * x + s * y - rx * z
        z2 = tz - ry * x + rx * y + s * z

        a, b = BritishGrid.A, BritishGrid.B
        e2 = 1 - (b * b) / (a * a)
        p = math.hypot(x2, y2)
        phi = math.atan2(z2, p * (1 - e2))
        for _ in range(6):
            nu = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
            phi = math.atan2(z2 + e2 * nu * math.sin(phi), p)
        lam = math.atan2(y2, x2)

        # Airy geodetic -> transverse mercator on the National Grid.
        f0, phi0, lam0 = BritishGrid.SCALE, BritishGrid.LAT0, BritishGrid.LON0
        n = (a - b) / (a + b)
        nu = a * f0 / math.sqrt(1 - e2 * math.sin(phi) ** 2)
        rho = a * f0 * (1 - e2) * (1 - e2 * math.sin(phi) ** 2) ** -1.5
        eta2 = nu / rho - 1
        ma = (1 + n + 1.25 * n * n + 1.25 * n ** 3) * (phi - phi0)
        mb = ((3 * n + 3 * n * n + 2.625 * n ** 3)
              * math.sin(phi - phi0) * math.cos(phi + phi0))
        mc = ((1.875 * n * n + 1.875 * n ** 3)
              * math.sin(2 * (phi - phi0)) * math.cos(2 * (phi + phi0)))
        md = ((35.0 / 24.0) * n ** 3
              * math.sin(3 * (phi - phi0)) * math.cos(3 * (phi + phi0)))
        m = b * f0 * (ma - mb + mc - md)

        tan2 = math.tan(phi) ** 2
        one = m + BritishGrid.NORTH0
        two = nu / 2 * math.sin(phi) * math.cos(phi)
        three = (nu / 24 * math.sin(phi) * math.cos(phi) ** 3
                 * (5 - tan2 + 9 * eta2))
        threea = (nu / 720 * math.sin(phi) * math.cos(phi) ** 5
                  * (61 - 58 * tan2 + tan2 * tan2))
        four = nu * math.cos(phi)
        five = nu / 6 * math.cos(phi) ** 3 * (nu / rho - tan2)
        six = (nu / 120 * math.cos(phi) ** 5
               * (5 - 18 * tan2 + tan2 * tan2 + 14 * eta2 - 58 * tan2 * eta2))
        d = lam - lam0
        return (BritishGrid.EAST0 + four * d + five * d ** 3 + six * d ** 5,
                one + two * d ** 2 + three * d ** 4 + threea * d ** 6)

    @staticmethod
    def units_per_metre(lat):
        return 1.0


class Lidar:
    """One metre ground, where a country has flown it and gives it away.

    USGS 3DEP covers most of the United States and serves it from an image
    service with no key. It is fine enough to carry a bunker: asked for one at
    Oakmont, the elevation comes back with the bunker's outline in it. A green
    is three times a bunker's width, so this is what green contour comes from.

    Outside a flown country there is simply nothing, and that is not an error
    - it is the ordinary case for most of the world. `height_metres` returns
    None and the caller falls back to the coarse ground.

    Works in patches rather than points. A hole is one request; a point at a
    time would be five hundred.
    """

    # Anything at or past this is the service saying "nothing here" rather
    # than a place below sea level.
    NO_DATA_BELOW = -1e10
    MERCATOR_LIMIT = Mercator.LIMIT

    def __init__(self, patch_metres=700.0, metres_per_pixel=1.0,
                 patch_source=None, cache=None, projection=Mercator):
        self.patch_metres = patch_metres
        self.metres_per_pixel = metres_per_pixel
        self.cache = Path(cache) if cache else None
        self.projection = projection
        self._patches = []
        self._empty = []
        self._source = patch_source or three_dep_patches(self.cache)

    @staticmethod
    def to_mercator(lat, lon):
        return Mercator.to_xy(lat, lon)

    def _download(self, bounds, pixels):
        """Kept for callers that had it; the source does this now."""
        return self._source(bounds, pixels)

    @staticmethod
    def _as_array(raw):
        """The raster, or None if the service sent something that is not one.

        Outside coverage it does not answer with a polite error - it sends a
        truncated image, which is why this is caught rather than trusted.
        """
        from PIL import Image

        if not raw or raw[:4] not in (b"II*\x00", b"MM\x00*"):
            return None
        try:
            grid = np.asarray(Image.open(io.BytesIO(raw)), dtype=np.float64)
        except Exception:
            return None
        if grid.ndim != 2 or grid.size == 0:
            return None
        if not np.any(grid > Lidar.NO_DATA_BELOW):
            return None
        return grid

    @staticmethod
    def is_ground(grid):
        """Is this a survey of somewhere, or a service declining politely?

        Ground surveyed at a metre always moves a little across seven hundred
        of them, even on a links. Asked for Northern Ireland, which it does
        not cover, England's service answered with a flat raster rather than
        with nothing - and a flat raster reads as a course with no hills and
        a green whose contour is a measurement. Royal County Down and Royal
        Portrush both came back that way.
        """
        if grid is None:
            return False
        real = grid[grid > Lidar.NO_DATA_BELOW]
        if real.size == 0:
            return False
        return float(real.max() - real.min()) >= FLAT_IS_NOT_GROUND

    def _patch_for(self, lat, lon):
        """The loaded patch covering a point, fetching one if there is none."""
        x, y = self.projection.to_xy(lat, lon)
        for bounds, grid in self._patches:
            if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
                return bounds, grid
        for bounds in self._empty:
            if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
                return None, None

        # A projection's units are not always metres. Mercator's stretch by
        # one over the cosine of the latitude; the British grid's do not.
        half = ((self.patch_metres / 2.0)
                * self.projection.units_per_metre(lat))
        bounds = (x - half, y - half, x + half, y + half)
        pixels = max(2, int(self.patch_metres / self.metres_per_pixel))
        # Checked here rather than where the bytes are decoded, so that every
        # source passes through it: the next country added would otherwise
        # have to remember to repeat the guard, and would not.
        grid = self._source(bounds, pixels)
        if not self.is_ground(grid):
            self._empty.append(bounds)
            return None, None
        self._patches.append((bounds, grid))
        return bounds, grid

    def covers(self, lat, lon):
        return self.height_metres(lat, lon) is not None

    def height_metres(self, lat, lon):
        """Metres above sea level, or None where the ground was never flown."""
        bounds, grid = self._patch_for(lat, lon)
        if grid is None:
            return None

        west, south, east, north = bounds
        rows, columns = grid.shape
        x, y = self.projection.to_xy(lat, lon)
        # Image rows run north to south, the opposite of a projection's y.
        px = (x - west) / (east - west) * (columns - 1)
        py = (north - y) / (north - south) * (rows - 1)
        px = min(max(px, 0.0), columns - 1.001)
        py = min(max(py, 0.0), rows - 1.001)

        left, top = int(px), int(py)
        fx, fy = px - left, py - top
        corners = grid[top:top + 2, left:left + 2]
        if corners.shape != (2, 2) or not np.all(corners > self.NO_DATA_BELOW):
            return None
        upper = corners[0, 0] * (1 - fx) + corners[0, 1] * fx
        lower = corners[1, 0] * (1 - fx) + corners[1, 1] * fx
        return float(upper * (1 - fy) + lower * fy)

    def height_yards(self, lat, lon):
        metres = self.height_metres(lat, lon)
        return None if metres is None else metres * YARDS_PER_METRE

    def spacing_yards(self, lat=None, lon=None):
        return self.metres_per_pixel * YARDS_PER_METRE


class Ground:
    """The best height available at a point, and honest about which it is.

    Fine where a country has flown it, coarse everywhere else. Most of the
    world is the coarse case and always will be, so this is not a stopgap: it
    is how a course in Fife and a course in North Carolina both get built
    without pretending they were surveyed the same way.
    """

    def __init__(self, fine=None, coarse=None, cache=None):
        if fine is None:
            fine = [united_states(cache=cache), england(cache=cache),
                    scotland(cache=cache)]
        elif not isinstance(fine, (list, tuple)):
            fine = [fine]
        self.fine_sources = list(fine)
        self.coarse = coarse if coarse is not None else Relief(cache=cache)

    @property
    def fine(self):
        """The first flown source, for callers that only want to ask one."""
        return self.fine_sources[0] if self.fine_sources else None

    def height_and_spacing(self, lat, lon):
        """The height here, and how finely it is known, in one asking.

        One answer rather than two, because working out which source has a
        point is the same work as reading it, and asking twice for the two
        halves of one answer is how they drift apart.
        """
        for source in self.fine_sources:
            height = source.height_metres(lat, lon)
            if height is not None:
                return height, source.spacing_yards()
        return self.coarse.height_metres(lat, lon), self.coarse.spacing_yards()

    def flown_by(self, lat, lon):
        """Which source has this place, or None if only the coarse one does."""
        for source in self.fine_sources:
            if source.height_metres(lat, lon) is not None:
                return source
        return None

    def height_metres(self, lat, lon):
        return self.height_and_spacing(lat, lon)[0]

    def height_yards(self, lat, lon):
        return self.height_metres(lat, lon) * YARDS_PER_METRE

    def spacing_yards(self, lat, lon):
        """How finely this particular place is known."""
        return self.height_and_spacing(lat, lon)[1]


def step_from(origin, along_yards, across_yards, bearing_deg):
    """A lat/lon a given number of yards down a hole and across it."""
    bearing = math.radians(bearing_deg)
    north = (along_yards * math.cos(bearing) - across_yards * math.sin(bearing))
    east = (along_yards * math.sin(bearing) + across_yards * math.cos(bearing))
    lat = origin[0] + (north / YARDS_PER_METRE) / 111_320.0
    lon = origin[1] + (east / YARDS_PER_METRE) / (
        111_320.0 * math.cos(math.radians(origin[0])))
    return lat, lon


def heights_along(relief, tee_latlon, bearing_deg, centreline):
    """How high each waypoint is, in yards above the tee.

    Above the tee rather than above the sea, because that is what the hole
    plays as and what a waypoint holds. A downhill hole comes back negative.
    """
    tee_height = relief.height_yards(*tee_latlon)
    heights = []
    for along, across in centreline:
        point = step_from(tee_latlon, along, across, bearing_deg)
        # float() rather than the numpy one the tiles arrive as: these are
        # written to a file and read back, and numpy's does not survive that.
        heights.append(round(float(relief.height_yards(*point) - tee_height), 1))
    return heights


def slope_at(relief, centre_latlon, bearing_deg, radius_yards):
    """Which way the ground falls here and how hard, in the hole's frame.

    A plane fitted through the ground around a point, which is exactly what
    the simulator holds a green as. Returns the direction of fall in degrees
    (0 towards the pin, 90 right of that), the gradient per yard, and how many
    postings the fit actually had - because over a green that number is often
    one or two, and a tilt fitted through two postings is a guess wearing a
    number's clothes.
    """
    step = max(2.0, radius_yards / 3.0)
    reach = int(radius_yards // step)
    alongs, acrosses, heights = [], [], []
    for i in range(-reach, reach + 1):
        for j in range(-reach, reach + 1):
            along, across = i * step, j * step
            if math.hypot(along, across) > radius_yards:
                continue
            point = step_from(centre_latlon, along, across, bearing_deg)
            alongs.append(along)
            acrosses.append(across)
            heights.append(relief.height_yards(*point))

    # How much genuinely different ground the fit saw, rather than how many
    # times it sampled between the same two postings. Asked of the source,
    # because the same twenty yard circle is one posting on coarse ground and
    # thirteen hundred on flown ground, and only the source knows which.
    spacing = relief.spacing_yards(*centre_latlon)
    postings = max(1, int((2 * radius_yards / spacing) ** 2))

    design = np.column_stack([alongs, acrosses, np.ones(len(alongs))])
    (fall_along, fall_across, _), *_ = np.linalg.lstsq(
        design, np.array(heights), rcond=None)

    gradient = math.hypot(fall_along, fall_across)
    falls_towards = math.degrees(math.atan2(-fall_across, -fall_along))
    return {
        "falls_towards_deg": round(falls_towards % 360.0, 1),
        "fall_per_yard": round(float(gradient), 4),
        "postings": postings,
        "spacing_yards": round(spacing, 2),
        "trustworthy": postings >= ENOUGH_SAMPLES,
    }
