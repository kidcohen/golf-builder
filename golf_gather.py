"""Run the gather: turn the researched courses into the ground the game reads.

This existed only as a script in a session's scratchpad, which meant the data
file could be read and checked but not rebuilt - the one step that turns a
crawl into a course lived nowhere. It lives here now.

    python golf_gather.py                      every course
    python golf_gather.py "Auld Links"         one of them
    python golf_gather.py --dry                say what would happen

A gather is slow the first time a course is seen and fast afterwards, because
every answer from the map is kept. Rebuilding all nineteen from a warm cache
is a minute; the first crawl of a new course is several.

Nothing here decides what a hole should be. It reads `courses_researched.json`,
asks the map and the survey about each course in it, and writes what they say
to `courses_ground.json` along with a list of what somebody should look at.
"""

import argparse
import json
import sys
from pathlib import Path

from golf_ground import concerns, gather, save
from golf_relief import Ground

HERE = Path(__file__).parent
RESEARCHED = HERE / "courses_researched.json"
GATHERED = HERE / "courses_ground.json"


def researched(path=None):
    """The courses somebody has researched, in the order they were written."""
    path = Path(path or RESEARCHED)
    return json.loads(path.read_text(encoding="utf-8"))["courses"]


def one(entry, ground, cache):
    """Gather a single course, keeping what the research knows about it."""
    course = gather({
        "name": entry["name"],
        "box": entry["box"],
        "card": entry["card"],
        # The name the map uses, which is what the boundary is chosen by.
        # Without it nothing is filtered and a club's neighbours come too.
        "osm_name": entry.get("osm_name"),
        "modelled_on": entry.get("modelled_on"),
        "hole_names": {int(number): name
                       for number, name in (entry.get("hole_names") or {}).items()},
    }, ground=ground, cache=cache)
    course["modelled_on"] = entry.get("modelled_on")
    return course


def describe(course):
    """One line saying what came back, and how much of it is trustworthy."""
    holes = course["holes"]
    sand = sum(1 for hole in holes for thing in hole["hazards"]
               if thing["kind"] == "bunker")
    tees = sum(len(hole["tees"]) for hole in holes)
    measured = sum(1 for hole in holes if hole["green"]["trustworthy"])
    climb = max((max((abs(height) for height in hole["heights_yards"]),
                     default=0.0) for hole in holes), default=0.0)
    return (f"{len(holes)}/{course['holes_on_the_card']} holes, {sand} bunkers, "
            f"{tees} tee boxes, {measured} greens measured rather than "
            f"guessed, biggest climb {climb:.0f} yd")


def run(names=None, cache=None, into=None, source=None, say=print):
    """Gather the courses asked for, or all of them, and write the result.

    Writing only the ones asked for would replace nineteen courses with one,
    so a partial run keeps what it did not gather. Losing eighteen courses to
    a one course rerun is a thing that happened.
    """
    wanted = set(names or [])
    entries = researched(source)
    kept = {}
    if into and Path(into).exists() and wanted:
        kept = {course["name"]: course for course in
                json.loads(Path(into).read_text(encoding="utf-8"))["courses"]}

    ground = Ground(cache=cache)
    gathered, failed = [], []
    for entry in entries:
        if wanted and entry["name"] not in wanted:
            continue
        if not entry.get("modelled_on"):
            say(f"  {entry['name']}: no real course recorded, skipped")
            continue

        say(f"\n=== {entry['name']}  ({entry['modelled_on']}) ===")
        try:
            course = one(entry, ground, cache)
        except Exception as fault:
            failed.append(entry["name"])
            say(f"  FAILED: {type(fault).__name__}: {str(fault)[:160]}")
            continue

        gathered.append(course)
        kept.pop(course["name"], None)
        say(f"  {describe(course)}")
        for said in concerns(course)[:6]:
            say(f"    - {said}")

    everything = gathered + list(kept.values())
    if into:
        path = save(into, everything)
        say(f"\n{len(gathered)} gathered, {len(kept)} kept as they were "
            f"-> {path} ({path.stat().st_size // 1024} KB)")
    if failed:
        say(f"{len(failed)} failed: {', '.join(failed)}")
    return everything


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("courses", nargs="*",
                        help="course names; all of them if none given")
    parser.add_argument("--cache", default=str(HERE / ".crawl_cache"),
                        help="where answers from the map and the survey are kept")
    parser.add_argument("--into", default=str(GATHERED))
    parser.add_argument("--dry", action="store_true",
                        help="say what would be gathered and stop")
    asked = parser.parse_args(argv)

    if asked.dry:
        for entry in researched():
            if asked.courses and entry["name"] not in asked.courses:
                continue
            print(f"  {entry['name']:20} {entry.get('modelled_on') or '(unpaired)'}")
        return 0

    run(asked.courses, cache=asked.cache, into=asked.into)
    return 0


if __name__ == "__main__":
    sys.exit(main())
