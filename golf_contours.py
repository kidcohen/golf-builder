"""Researched green contours, for the holes no metre survey reaches.

The gathered survey gives a real green tilt only where a metre-resolution
survey covers the course - the United States and England. Scotland, Ireland and
Australia it does not, and there the tilt has to be read the way the cards were:
off published green-complex diagrams and course guides, hole by hole, and
written down. `courses_contours.json` holds those readings.

This is the one route to a real green on those courses. It is deliberately not
inferred: a green outside the surveyed countries comes back from the terrarium
data about thirty metres apart, which is roughly one posting across a whole
green, so a tilt fitted to it would be a number with no green under it. A read
contour is a fact off a diagram; a fitted one there is a guess wearing a
decimal.

A hole not in the file, or in it with its numbers still null, is shaped by its
course's character exactly as before - so the file changes nothing until it is
filled, and each hole it gains is one green that stops being invented.
"""
import json
from pathlib import Path

from golf_terrain import GreenContour

CONTOURS_FILE = Path(__file__).parent / "courses_contours.json"


def load_contours(path=CONTOURS_FILE):
    """Every researched contour by course name, or empty if the file is gone."""
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("courses", {})


def researched_green(contours, course_name, hole_number, character_stimp=None):
    """The read contour for one hole, or None if it has not been read yet.

    A contour counts as read only once its direction and gradient are both
    filled - a hole with either still null is not a green anybody measured. Pace
    is optional: left null it takes the course character's, because a guide
    gives the fall of a green far more often than it gives its speed.
    """
    entry = (contours.get(course_name) or {}).get(str(hole_number))
    if not entry:
        return None
    aspect = entry.get("falls_towards_deg")
    fall = entry.get("fall_per_yard")
    if aspect is None or fall is None:
        return None
    stimp = entry.get("stimp")
    if stimp is None:
        stimp = character_stimp if character_stimp is not None else 10.0
    return GreenContour(float(aspect), float(fall), float(stimp))
