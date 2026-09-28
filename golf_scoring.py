"""Ways of settling a round.

Stroke play, stableford and match play are three readings of the same card,
and handicaps sit under all of them: a golfer receives shots on the hardest
holes, and everything is decided on the net score.

Pure functions over scorecards. Nothing here knows about games, servers or
databases, so a format can be checked against a card written on paper.
"""

STROKE_PLAY = "stroke_play"
STABLEFORD = "stableford"
MATCH_PLAY = "match_play"

FORMATS = (STROKE_PLAY, STABLEFORD, MATCH_PLAY)

# A full handicap is allotted over eighteen holes, even when fewer are played.
HOLES_ON_A_CARD = 18

# Net par is worth two, and a hole is worth nothing once it is two over.
STABLEFORD_POINTS_FOR_NET_PAR = 2


def strokes_received(handicap, stroke_index, holes_on_card=HOLES_ON_A_CARD):
    """How many shots a golfer gets on a hole of this difficulty.

    The hardest hole is stroke index 1. A handicap of 18 gives a shot
    everywhere; 20 gives two shots on the two hardest and one on the rest.
    """
    if not handicap or stroke_index is None:
        return 0
    whole_rounds, remainder = divmod(int(handicap), holes_on_card)
    return whole_rounds + (1 if stroke_index <= remainder else 0)


def net_strokes(strokes, handicap, stroke_index, holes_on_card=HOLES_ON_A_CARD):
    """Gross strokes less the shots received on that hole."""
    if strokes is None:
        return None
    return strokes - strokes_received(handicap, stroke_index, holes_on_card)


def stableford_points(net, par):
    """Two for a net par, one more for each shot better, nothing below zero."""
    if net is None:
        return 0
    return max(0, STABLEFORD_POINTS_FOR_NET_PAR + (par - net))


def hole_result(scores, par, stroke_index, handicaps=None, holes_on_card=HOLES_ON_A_CARD):
    """One hole, read every way at once.

    `scores` maps player id to gross strokes; `handicaps` maps player id to
    handicap. Players with no score for the hole are left out.
    """
    handicaps = handicaps or {}
    result = {}
    for player_id, gross in scores.items():
        if gross is None:
            continue
        net = net_strokes(gross, handicaps.get(player_id), stroke_index, holes_on_card)
        result[player_id] = {
            "gross": gross,
            "net": net,
            "strokes_received": strokes_received(
                handicaps.get(player_id), stroke_index, holes_on_card),
            "gross_vs_par": gross - par,
            "net_vs_par": net - par,
            "points": stableford_points(net, par),
        }
    return result


def hole_winners(results):
    """Whoever had the lowest net score on a hole. More than one means halved."""
    if not results:
        return []
    best = min(entry["net"] for entry in results.values())
    return [player_id for player_id, entry in results.items() if entry["net"] == best]


def match_standing(hole_results, player_ids):
    """Holes up, for a match between two players.

    Returns (leader_id, holes_up, holes_remaining_unknown). A match is only
    meaningful between two, so anything else comes back level.
    """
    if len(player_ids) != 2:
        return None, 0
    first, second = player_ids
    lead = 0
    for results in hole_results:
        winners = hole_winners(results)
        if len(winners) != 1:
            continue  # halved
        lead += 1 if winners[0] == first else -1

    if lead == 0:
        return None, 0
    return (first, lead) if lead > 0 else (second, -lead)


def card(holes, scores_by_hole, handicaps=None, holes_on_card=HOLES_ON_A_CARD):
    """Every hole of a round, read every way.

    `holes` is the course; `scores_by_hole` maps hole number to {player: gross}.
    """
    return [
        hole_result(scores_by_hole.get(hole.number, {}), hole.par,
                    getattr(hole, "stroke_index", hole.number),
                    handicaps, holes_on_card)
        for hole in holes
    ]


def standings(holes, scores_by_hole, players, handicaps=None,
              game_format=STROKE_PLAY, holes_on_card=HOLES_ON_A_CARD):
    """Where everyone stands, in the format being played.

    `players` maps player id to name, so the answer can be read without
    looking anything else up.
    """
    results = card(holes, scores_by_hole, handicaps, holes_on_card)

    table = []
    for player_id, name in players.items():
        played = [entry[player_id] for entry in results if player_id in entry]
        table.append({
            "player_id": player_id,
            "name": name,
            "holes_played": len(played),
            "gross": sum(e["gross"] for e in played),
            "net": sum(e["net"] for e in played),
            "gross_vs_par": sum(e["gross_vs_par"] for e in played),
            "net_vs_par": sum(e["net_vs_par"] for e in played),
            "points": sum(e["points"] for e in played),
        })

    if game_format == STABLEFORD:
        table.sort(key=lambda row: (-row["points"], row["name"]))
    else:
        table.sort(key=lambda row: (row["net"], row["name"]))

    standing = {"format": game_format, "table": table}
    if game_format == MATCH_PLAY:
        leader, holes_up = match_standing(results, list(players))
        standing["match"] = {
            "leader_id": leader,
            "holes_up": holes_up,
            "description": describe_match(players, leader, holes_up, len(results),
                                          sum(1 for r in results if r)),
        }
    return standing


def describe_match(players, leader_id, holes_up, holes_total, holes_played):
    """A match score the way it is actually said out loud.

    "Three and two" means three up with two to play, which is over. A match
    that goes the distance is won "one up" - never "one and none", which is
    not a thing anybody says.
    """
    if leader_id is None:
        return "All square"

    remaining = holes_total - holes_played
    if remaining <= 0:
        return f"{players[leader_id]} wins {holes_up} up"
    if holes_up > remaining:
        return f"{players[leader_id]} wins {holes_up} and {remaining}"
    return f"{players[leader_id]} {holes_up} up"


def describe_standings(standing):
    """The table as lines a golfer can read."""
    rows = standing.get("table") or []
    if not rows:
        return ["Nobody has played a hole yet."]

    lines = []
    if standing["format"] == STABLEFORD:
        lines.append("Stableford:")
        for row in rows:
            lines.append(f"  {row['name']}: {row['points']} pts "
                         f"({row['holes_played']} holes)")
    elif standing["format"] == MATCH_PLAY:
        lines.append(f"Match: {standing['match']['description']}")
        for row in rows:
            lines.append(f"  {row['name']}: net {row['net']} "
                         f"({row['net_vs_par']:+d})")
    else:
        lines.append("Stroke play:")
        for row in rows:
            lines.append(f"  {row['name']}: {row['gross']} gross, "
                         f"net {row['net']} ({row['net_vs_par']:+d})")
    return lines
