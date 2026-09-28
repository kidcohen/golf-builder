"""Show a golfer what their stored shots say about them.

    python golf_report.py "Alice"

Needs the same SUPABASE_URL and SUPABASE_SERVICE_KEY the server uses. Without
them there is no history to read, and it says so rather than pretending.
"""

import sys

from golf_history import build_report, describe_report
from golf_store import SupabaseStore


def report_for(name, store=None):
    """The lines to print for one golfer."""
    store = store if store is not None else SupabaseStore()
    if not store.enabled:
        return [
            "No database configured.",
            "Set SUPABASE_URL and SUPABASE_SERVICE_KEY to keep a history.",
        ]

    if store.find_player(name) is None:
        return [f"No golfer called {name!r} has played yet."]

    return [f"{name}", "=" * len(name)] + describe_report(build_report(
        store.history_for(name)
    ))


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(__doc__.strip())
        return 1

    for line in report_for(" ".join(argv)):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
