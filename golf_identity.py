"""Who a golfer is, as distinct from what they are called.

A name is not an identity. Two people called Alice are two golfers, and one
Alice who starts entering herself as "Ali" is still the same golfer - but
matching on the typed name gets both of those backwards, merging strangers and
splitting one person's history in half.

So each golfer gets a key: a long random string, made once and kept on the
machine they play from. The name stays what it always was, something to show
on a leaderboard, and it can change without disturbing anything.

Deliberately not accounts. No passwords, nothing to sign up for, nothing to
lose, and nothing here to handle on anyone's behalf. A golfer who wants to
play from another machine can copy their key across; a golfer who does not
care never sees it.
"""

import json
import uuid
from pathlib import Path

DEFAULT_STORE = Path.home() / ".golf_simulator_players.json"


def new_key():
    """A key with no meaning in it, so it says nothing about the golfer."""
    return uuid.uuid4().hex


def load_keys(path=DEFAULT_STORE):
    """Everyone this machine already knows. Unreadable files read as empty."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_keys(keys, path=DEFAULT_STORE):
    """Remember them. Failing to write costs the key, never the round."""
    try:
        Path(path).write_text(json.dumps(keys, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def key_for(name, path=DEFAULT_STORE):
    """This golfer's key, made and remembered the first time they play."""
    keys = load_keys(path)
    lookup = str(name).strip().lower()
    if lookup not in keys:
        keys[lookup] = new_key()
        save_keys(keys, path)
    return keys[lookup]


def keys_for(names, path=DEFAULT_STORE):
    """Keys for everyone hitting off this bay, in one pass over the file."""
    keys = load_keys(path)
    issued, changed = {}, False
    for name in names:
        lookup = str(name).strip().lower()
        if lookup not in keys:
            keys[lookup] = new_key()
            changed = True
        issued[name] = keys[lookup]
    if changed:
        save_keys(keys, path)
    return issued


def adopt_key(name, key, path=DEFAULT_STORE):
    """Take on a key made elsewhere, so a golfer can play from two machines."""
    keys = load_keys(path)
    keys[str(name).strip().lower()] = str(key).strip()
    save_keys(keys, path)
    return keys[str(name).strip().lower()]
