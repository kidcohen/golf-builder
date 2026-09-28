"""What this simulator allows, kept somewhere a golfer can change it.

Limits belong to the people playing rather than to the code. A practice
session wants a shot back whenever you like; a match played for something does
not want any. Both are golf, so neither is hard-wired here - the numbers live
in a settings file next to the player keys, and anything not set falls back to
what most people would expect.

Nothing in this module knows what a golf ball does. It reads a file, checks the
values are sane, and hands them over.
"""

import json
import os
from pathlib import Path

SETTINGS_FILENAME = ".golf_simulator_settings.json"
SETTINGS_PATH_ENV = "GOLF_SIMULATOR_SETTINGS"

# None means no limit. 0 means not allowed at all. Both are worth being able to
# say, and neither is the same as the other.
DEFAULTS = {
    # Shots a golfer may take back, per hole on a course and per session on the
    # range. Open by default: a simulator is mostly used for practice, and a
    # round that wants it off can say so.
    "redo_limit": None,
    # Pick up and move on. Nobody learns anything from a fourteenth shot.
    "max_strokes_per_hole": 12,
    # Balls in the bucket. None opens the range and leaves it open.
    "range_shots_per_player": 3,
    # A full round unless told otherwise.
    "holes": 18,
}

# Settings that count things and may be left open, but can never be negative.
_COUNTS = ("redo_limit", "max_strokes_per_hole", "range_shots_per_player")


def settings_path():
    """Where the settings live. Overridable, mostly so tests do not use yours."""
    override = os.environ.get(SETTINGS_PATH_ENV)
    return Path(override) if override else Path.home() / SETTINGS_FILENAME


def load_settings(path=None):
    """Read the settings, falling back to the defaults for anything unsaid.

    A file that is missing, unreadable or malformed is not worth stopping a
    round over - the defaults are playable golf, so they are used and the
    round goes on.
    """
    path = Path(path) if path is not None else settings_path()
    settings = dict(DEFAULTS)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings

    if isinstance(stored, dict):
        settings.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return validated(settings)


def save_settings(settings, path=None):
    """Write the settings back, keeping only what this simulator understands."""
    path = Path(path) if path is not None else settings_path()
    keep = validated({**DEFAULTS, **{k: v for k, v in settings.items() if k in DEFAULTS}})
    path.write_text(json.dumps(keep, indent=2), encoding="utf-8")
    return keep


def validated(settings):
    """Make sense of what was asked for, without arguing about it.

    A nonsensical number is replaced by the default rather than raised over.
    Somebody hand-editing a file should not be able to lock themselves out of
    a round they were about to play.
    """
    clean = dict(DEFAULTS)
    for key, value in settings.items():
        if key not in DEFAULTS:
            continue
        if key in _COUNTS:
            if value is None:
                clean[key] = None
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            clean[key] = number if number >= 0 else DEFAULTS[key]
        elif key == "holes":
            clean[key] = value if value in (9, 18) else DEFAULTS[key]
    if clean["max_strokes_per_hole"] is not None and clean["max_strokes_per_hole"] < 1:
        clean["max_strokes_per_hole"] = DEFAULTS["max_strokes_per_hole"]
    return clean


def describe(settings):
    """The settings as lines a golfer can read back."""
    redo = settings.get("redo_limit")
    strokes = settings.get("max_strokes_per_hole")
    shots = settings.get("range_shots_per_player")
    return [
        "Shots you may take back: " + (
            "as many as you like" if redo is None
            else "none" if redo == 0
            else f"{redo} per hole"
        ),
        f"Pick up after: {strokes} strokes" if strokes else "Pick up after: never",
        "Range: " + ("open, no limit" if shots is None else f"{shots} shots each"),
        f"Round: {settings.get('holes')} holes",
    ]
