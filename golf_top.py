"""Add a course from OpenStreetMap alone: crawl its holes, export the terrain.

The researched pipeline (golf_gather) needs a hand-made scorecard - a person
reads the yardages and stroke index off a card first. That is why there are
nineteen courses and not two hundred.

This path needs neither. A golf=hole way in OSM is the tee-to-green routing, so
its geometry already gives the length, the tee, the bearing, the centreline and
the bunkers down it - the exact fields export_terrain wants. Given a course
name and the box it sits in, a course is added end to end with nobody typing a
card. That is what puts the top 200 in reach.

    from golf_top import add_course
    add_course("Oakmont Country Club", (40.518, -79.850, 40.541, -79.818))
"""
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request

import golf_crawl as C
import export_terrain as ET
from golf_relief import Ground


def safe(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def locate(name, tries=3):
    """The box a named course sits in, from OpenStreetMap's own gazetteer.

    Nominatim returns a bounding box for a named feature; a golf course's box is
    its outline, which is exactly the box the crawl wants. We pad it a touch so
    a hole that runs to the edge of the drawn outline is not clipped, and bias
    the search to golf so `Augusta` finds the club, not the city.
    """
    q = name if re.search(r"golf|links|course|club|national", name, re.I) else name + " golf course"
    url = ("https://nominatim.openstreetmap.org/search?"
           + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1, "polygon_geojson": 0}))
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "golf-sim/1.0"})
            hits = json.loads(urllib.request.urlopen(req, timeout=25).read())
            if not hits:
                return None
            s, n, w, e = (float(x) for x in hits[0]["boundingbox"])  # south, north, west, east
            pad_lat = max(0.004, (n - s) * 0.15)
            pad_lon = max(0.005, (e - w) * 0.15)
            return (s - pad_lat, w - pad_lon, n + pad_lat, e + pad_lon)
        except Exception as ex:
            last = ex
            time.sleep(1.5 + attempt)
    raise RuntimeError(f"could not locate {name!r}: {last}")


def crawl_course(name, box, tries=5):
    """Crawl one course, riding out Overpass's bad moments (it is busy and free)."""
    last = None
    for attempt in range(tries):
        try:
            return C.crawl(*box, course_name=name)
        except Exception as e:                       # truncated response, mirror hiccup, timeout
            last = e
            time.sleep(3 + attempt * 2)
    raise RuntimeError(f"Overpass would not answer for {name!r}: {last}")


def add_course(name, box=None, out=None, zoom=19, step_yd=2.0, corridor=85.0, min_holes=6,
               label=None):
    """Crawl a course from OSM and export its terrain. Returns (folder, holes_done).

    With no `box`, the course is located from its name alone. A multi-course club
    (Bethpage's five, say) has every course in one box, its holes named "Black 1",
    "Blue 1", ...; pass `label="Black"` to keep just that course's eighteen. Only
    holes OSM actually maps come through; a course too thinly mapped to be worth
    building raises rather than shipping a handful of holes as if whole.
    """
    out = out or ET.OUT
    if box is None:
        box = locate(name)
        if box is None:
            raise RuntimeError(f"{name}: not found in OpenStreetMap")
    holes = crawl_course(name, box)
    if label:
        pre = label.lower()
        holes = [h for h in holes if (h.get("name") or "").lower().startswith(pre)]
    holes = [h for h in holes
             if h.get("number") and (h.get("length_yards") or 0) > 30 and h.get("tee_latlon")]
    holes.sort(key=lambda h: h["number"])
    # de-dup a number that maps twice (a tee ground split into two ways): keep the longest
    seen = {}
    for h in holes:
        n = h["number"]
        if n not in seen or h["length_yards"] > seen[n]["length_yards"]:
            seen[n] = h
    holes = [seen[n] for n in sorted(seen)]
    if len(holes) < min_holes:
        raise RuntimeError(f"{name}: only {len(holes)} holes mapped in OSM - too few to build")

    ground = Ground(cache=ET.RCACHE)
    folder = os.path.join(out, safe(name))
    os.makedirs(folder, exist_ok=True)
    listing = []
    for h in holes:
        hole = {
            "number": h["number"], "par": h.get("par") or _par_from_length(h["length_yards"]),
            "length_yards": h["length_yards"], "tee_latlon": h["tee_latlon"],
            "bearing_deg": h["bearing_deg"], "centreline": h["centreline"], "hazards": h["hazards"],
        }
        crop, meta = ET.one_hole(ground, hole, zoom=zoom, step_yd=step_yd, corridor=corridor)
        stem = f"hole_{h['number']:02d}"
        crop.save(os.path.join(folder, stem + "_ortho.jpg"), quality=88)
        json.dump(meta, open(os.path.join(folder, stem + ".json"), "w"))
        listing.append({"hole": h["number"], "stem": stem,
                        "par": meta["par"], "length": meta["length_yards"]})

    coords = holes[0]["tee_latlon"]
    weather = ET.fetch_weather(*coords)
    json.dump({"course": name, "source": "openstreetmap", "holes": listing, "weather": weather},
              open(os.path.join(folder, "_course.json"), "w"), indent=1)
    return folder, len(listing)


def _par_from_length(yards):
    """A sensible par when OSM did not tag one: by length, the usual thresholds."""
    if yards < 260:
        return 3
    if yards < 470:
        return 4
    return 5
