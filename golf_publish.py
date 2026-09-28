"""Publish the course library to the server, so every bay plays the same golf.

The courses lived in a Python file on one machine. That cannot hold for a
simulator whose whole point is a golfer in one city playing a friend in
another: two bays with two copies of a library are two libraries, and the day
they differ is the day a round means nothing.

So the library is pushed to the backend, card and shaping both, and the
comparison is total - anything on the server that is not in the library is
removed, so the server is what the library says rather than everything the
library has ever said.

Nothing here decides what a course is. golf_library owns that, and this moves
it. Run it after adding a course:

    python golf_publish.py
"""

import json
import os
import urllib.error
import urllib.request

from golf_library import COURSES, TERRAIN_PROVENANCE

URL_ENV = "SUPABASE_URL"
SERVICE_KEY_ENV = "SUPABASE_SERVICE_KEY"


class Backend:
    """The smallest PostgREST client that can publish a library."""

    def __init__(self, url=None, service_key=None):
        self.url = (os.environ.get(URL_ENV, "") if url is None else url).rstrip("/")
        self.key = (os.environ.get(SERVICE_KEY_ENV, "")
                    if service_key is None else service_key)

    @property
    def reachable(self):
        return bool(self.url and self.key)

    def _call(self, method, path, body=None, prefer=None):
        request = urllib.request.Request(
            f"{self.url}/rest/v1/{path}", method=method,
            data=None if body is None else json.dumps(body).encode("utf-8"),
        )
        request.add_header("apikey", self.key)
        request.add_header("Authorization", f"Bearer {self.key}")
        request.add_header("Content-Type", "application/json")
        if prefer:
            request.add_header("Prefer", prefer)
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                raw = answer.read()
                return json.loads(raw) if raw else []
        except urllib.error.HTTPError as fault:
            raise RuntimeError(
                f"{method} {path} -> {fault.code}: {fault.read().decode()[:300]}")

    def get(self, path):
        return self._call("GET", path)

    def insert(self, table, rows):
        if not rows:
            return []
        return self._call("POST", table, rows, prefer="return=representation")

    def delete(self, path):
        return self._call("DELETE", path, prefer="return=minimal")


def shaping_of(hole):
    """The ground of one hole, as it is stored beside its card."""
    layout = hole.layout
    return {
        "centreline": [{"x": round(w.x, 2), "z": round(w.z, 2),
                        "height_yards": round(w.height_yards, 2)}
                       for w in layout.waypoints],
        "fairway_width_yards": layout.fairway_width_yards,
        "green_radius_yards": layout.green_radius_yards,
        "green": {
            "falls_towards_deg": round(layout.green.falls_towards_deg, 1),
            "fall_per_yard": round(layout.green.fall_per_yard, 4),
            "stimp": layout.green.stimp,
        },
        "hazards": [{"kind": h.feature.kind, "x": round(h.x, 2),
                     "z": round(h.z, 2), "radius_yards": round(h.radius_yards, 1)}
                    for h in layout.hazards],
        "out_of_bounds_yards": layout.out_of_bounds_yards,
        "out_of_bounds_side": layout.out_of_bounds_side,
    }


def publish(backend=None):
    """Put the library on the server. Returns what was written."""
    backend = backend or Backend()
    if not backend.reachable:
        raise RuntimeError(
            f"no backend: set {URL_ENV} and {SERVICE_KEY_ENV}")

    # Everything, replaced. A course removed from the library has to leave the
    # server too, or the server slowly becomes every course there has ever
    # been rather than the ones that exist.
    backend.delete("courses?id=gt.0")

    written = {"courses": 0, "tees": 0, "holes": 0}
    for course in COURSES:
        row = backend.insert("courses", [{
            "name": course.name,
            "after": course.after,
            "location": course.location,
            "character": course.character,
            "card_source": course.card_source,
            "off_the_card": list(course.off_the_card),
            "terrain_note": TERRAIN_PROVENANCE,
        }])[0]
        written["courses"] += 1

        for tees in course.tee_names():
            played = course.from_tees(tees)
            tee_row = backend.insert("course_tees", [{
                "course_id": row["id"],
                "name": tees,
                "source": played.card_source,
                "par": played.par,
                "yards": round(played.yards),
            }])[0]
            written["tees"] += 1

            backend.insert("course_holes", [{
                "tees_id": tee_row["id"],
                "number": hole.number,
                "par": hole.par,
                "yards": round(hole.length_yards),
                "stroke_index": hole.stroke_index,
                "ground": shaping_of(hole),
            } for hole in played.holes])
            written["holes"] += len(played.holes)

    return written


if __name__ == "__main__":
    done = publish()
    print(f"published {done['courses']} courses, {done['tees']} sets of tees, "
          f"{done['holes']} holes")
