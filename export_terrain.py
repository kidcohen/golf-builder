"""Export a course as terrain Unity can render: for each hole, the real
satellite photograph of the hole and the real 3DEP heightfield under it.

Unity drapes the photo over the heightfield as one textured mesh - the GSPro
recipe. Nothing here is invented: the picture is the place and the heights are
the survey.

    python export_terrain.py "Foothill West"
"""
import sys, os, math, json, re
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import golf_aerial as A
from golf_relief import Ground, step_from

TILES = os.path.join(os.environ.get("TEMP", "."), "golf_terrain_tiles")
RCACHE = os.path.join(os.environ.get("TEMP", "."), "golf_terrain_relief")
OUT = "unity/Assets/Resources/Terrain"


def safe(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def surround(ground, hole, zoom_wide=13, span=3500.0, grid=150):
    """A wide, coarse layer of the REAL land around a hole - the actual coast, hills
    and terrain that surround it - draped from real satellite imagery + elevation in
    the hole's own frame, so the background is the true landscape, not a fake ridge."""
    tee = hole["tee_latlon"]; bearing = hole.get("bearing_deg", 0.0)
    ypp = A.metres_per_pixel(tee[0], zoom_wide) * 1.09361
    reach = span + 300
    n = 2 * (int(math.ceil((reach / ypp) / 256)) + 1) + 1
    canvas, corner, got, want = A.fetch(tee[0], tee[1], zoom_wide, n, n, TILES)
    tx, ty = A.pixel_of(tee[0], tee[1], zoom_wide, corner)
    rot = canvas.rotate(bearing, center=(tx, ty), resample=Image.BICUBIC, expand=False)
    left, right = int(tx - span / ypp), int(tx + span / ypp)
    top, bot = int(ty - span / ypp), int(ty + span / ypp)   # image top = far (+span)
    crop = rot.crop((max(left, 0), max(top, 0),
                     min(right, rot.width), min(bot, rot.height))).convert("RGB")
    if crop.width > 2000:
        crop = crop.resize((2000, int(crop.height * 2000 / crop.width)), Image.LANCZOS)
    tee_h = ground.height_yards(*tee)
    alongs = np.linspace(-span, span, grid + 1)             # i=0 near, i=grid far
    acrs = np.linspace(-span, span, grid + 1)
    heights = np.zeros((grid + 1, grid + 1))
    for i, al in enumerate(alongs):
        for j, ac in enumerate(acrs):
            lat, lon = step_from(tee, al, ac, bearing)
            try:
                heights[i, j] = float(ground.height_yards(lat, lon)) - tee_h
            except Exception:
                heights[i, j] = 0.0
    meta = {"span": span, "grid": grid, "tiles": f"{got}/{want}",
            "heights": [[round(float(v), 1) for v in row] for row in heights]}
    return crop, meta


def one_hole(ground, hole, zoom, step_yd, corridor):
    tee = hole["tee_latlon"]; bearing = hole.get("bearing_deg", 0.0)
    length = float(hole["length_yards"])

    # Fetch only as many tiles as this hole needs to reach past its green, so a
    # higher zoom (more pixels per yard, sharper turf) stays affordable: a short
    # par 3 pulls a small square, a par 5 a larger one.
    ypp = A.metres_per_pixel(tee[0], zoom) * 1.09361
    reach = length + corridor + 90
    n = 2 * (int(math.ceil((reach / ypp) / 256)) + 1) + 1
    canvas, corner, got, want = A.fetch(tee[0], tee[1], zoom, n, n, TILES)
    tx, ty = A.pixel_of(tee[0], tee[1], zoom, corner)
    rot = canvas.rotate(bearing, center=(tx, ty), resample=Image.BICUBIC, expand=False)

    centreline = hole.get("centreline") or [[0.0, 0.0], [length, 0.0]]
    max_ac = max(abs(p[1]) for p in centreline)
    a0, a1 = -20.0, length + 55.0
    W = min(210.0, max(corridor, max_ac + 40.0))   # wide enough to hold the fairway's bend AND the surrounding course (adjacent holes, paths, foliage)
    # orthophoto crop over exactly the terrain extent (bearing points up)
    left, right = int(tx - W / ypp), int(tx + W / ypp)
    top, bot = int(ty - a1 / ypp), int(ty - a0 / ypp)
    crop = rot.crop((max(left, 0), max(top, 0),
                     min(right, rot.width), min(bot, rot.height))).convert("RGB")
    if crop.width > 2600:
        crop = crop.resize((2600, int(crop.height * 2600 / crop.width)), Image.LANCZOS)

    # heightfield on the same extent, yards above the tee
    tee_h = ground.height_yards(*tee)
    na = int((a1 - a0) / step_yd); nx = int(2 * W / step_yd)
    alongs = np.linspace(a0, a1, na + 1)
    acrs = np.linspace(-W, W, nx + 1)
    heights = np.zeros((na + 1, nx + 1))
    for i, al in enumerate(alongs):
        for j, ac in enumerate(acrs):
            lat, lon = step_from(tee, al, ac, bearing)
            try:
                heights[i, j] = float(ground.height_yards(lat, lon)) - tee_h
            except Exception:
                heights[i, j] = 0.0
    # smooth 1 m survey noise
    for _ in range(2):
        heights[1:-1, 1:-1] = (heights[1:-1, 1:-1] + heights[:-2, 1:-1] + heights[2:, 1:-1]
                               + heights[1:-1, :-2] + heights[1:-1, 2:]) / 5.0


    def _feat(kind):
        return [[round(float(t["along"]), 1), round(float(t["across"]), 1),
                 round(float(t.get("radius_yards", 4.0)), 1)]
                for t in hole.get("hazards", [])
                if (t.get("kind") or t.get("feature")) == kind]
    trees, water, bunkers = _feat("trees"), _feat("water"), _feat("bunker")

    meta = {
        "hole": hole["number"], "par": hole["par"], "length_yards": round(length, 1),
        "bearing_deg": round(float(bearing), 1),
        "tee_latlon": [round(float(tee[0]), 7), round(float(tee[1]), 7)],   # kept so the hole can be re-exported without a re-crawl
        "extent": {"along0": a0, "along1": a1, "across": W},
        "grid": {"na": na, "nx": nx},
        "height_min": round(float(heights.min()), 2),
        "height_max": round(float(heights.max()), 2),
        "tiles": f"{got}/{want}",
        "heights": [[round(float(v), 2) for v in row] for row in heights],
        "centreline": [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in centreline],
        "trees": trees, "water": water, "bunkers": bunkers,
    }
    return crop, meta


DEFAULT_WEATHER = {"wind_speed_mph": 7.0, "wind_dir_deg": 270, "rain": 0.0, "wetness": 0.0}


def fetch_weather(lat, lon):
    """Current conditions at the course, from open-meteo (free, no key)."""
    import urllib.request
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           "&current=wind_speed_10m,wind_direction_10m,rain,precipitation"
           "&hourly=precipitation&past_hours=6&forecast_hours=1&wind_speed_unit=mph")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "golf-sim"})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        cur = d.get("current", {})
        recent = sum(float(x or 0) for x in (d.get("hourly", {}).get("precipitation") or [])[-6:])
        return {"wind_speed_mph": round(float(cur.get("wind_speed_10m") or 0), 1),
                "wind_dir_deg": round(float(cur.get("wind_direction_10m") or 0)),
                "rain": round(min(1.0, float(cur.get("rain") or cur.get("precipitation") or 0) / 2.0), 2),
                "wetness": round(min(1.0, recent / 5.0), 2)}
    except Exception as e:
        print("  weather fetch failed:", e); return dict(DEFAULT_WEATHER)


def main(course_name):
    ground = Ground(cache=RCACHE)
    courses = {c["name"]: c for c in json.load(open("courses_ground.json"))["courses"]}
    if course_name not in courses:
        print("no such gathered course:", course_name); return
    course = courses[course_name]

    folder = os.path.join(OUT, safe(course_name))
    os.makedirs(folder, exist_ok=True)
    listing, missing = [], []
    for hole in course["holes"]:
        if not hole.get("tee_latlon"):
            missing.append(hole["number"]); continue
        crop, meta = one_hole(ground, hole, zoom=19, step_yd=2.0, corridor=85.0)
        stem = f"hole_{hole['number']:02d}"
        crop.save(os.path.join(folder, stem + "_ortho.jpg"), quality=88)
        json.dump(meta, open(os.path.join(folder, stem + ".json"), "w"))
        listing.append({"hole": hole["number"], "stem": stem, "par": meta["par"],
                        "length": meta["length_yards"]})
        print(f"  hole {hole['number']:2d}  {meta['length_yards']:.0f}y  "
              f"relief {meta['height_max'] - meta['height_min']:.0f}y  ortho {crop.size}")
    coords = next((h["tee_latlon"] for h in course["holes"] if h.get("tee_latlon")), None)
    weather = fetch_weather(*coords) if coords else dict(DEFAULT_WEATHER)
    json.dump({"course": course_name, "after": course.get("modelled_on"),
               "holes": listing, "missing": missing, "weather": weather},
              open(os.path.join(folder, "_course.json"), "w"), indent=1)
    print(f"{course_name}: {len(listing)} holes exported to {folder}  weather={weather}"
          + (f"  (no coords for {missing})" if missing else ""))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Foothill West")
