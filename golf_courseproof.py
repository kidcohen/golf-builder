"""Proof that a course can actually be played, before anybody is asked to play it.

Two hundred courses is more than anyone will play through by hand, and a hole
that cannot be finished is not a thing a unit test notices - every part of it
works, and the round still never ends. So each course is walked round by a
golfer who is not very good but is entirely consistent, and what comes back is
either a card or a list of what is wrong with the place.

What this catches, which nothing else does:

  A hole nobody can finish. A green ringed by water, a fairway too narrow to
  hold a ball, a dogleg that turns back past its own tee.

  A hole that is not the hole the card claims. Two hundred yards of par 5, or
  a par 3 that plays 400.

  A stroke index that does not add up, once eighteen of them are together.

  A green nobody can putt on, because the tilt on it is steeper than a putt
  can hold.

It is not a judgement of whether a course is any good. It is a check that it
is golf.
"""

from hashlib import blake2b

from golf_course import MAX_STROKES_PER_HOLE, CoursePlayGame
from golf_scoring import HOLES_ON_A_CARD
from golf_terrain import GREEN, SHORTEST_PLAYABLE_YARDS, ground_at
from golf_weather import standard_conditions

# A swing that is repeatable rather than impressive, scaled to what is left.
# Consistency is the point: anything that goes wrong is the course's doing.
FULL_SWING_YARDS = 240.0
# How far off line a full shot can stray. Enough that a fairway thirty yards
# wide is a different proposition from one ninety yards wide, which is the
# whole difference between a parkland course and a links.
MISS_DEGREES = 4.0


def miss_by(seed, hole_number, stroke):
    """How far off line this particular shot goes, in degrees.

    A golfer who never misses cannot tell a narrow fairway from a wide one,
    and this one did not: every parkland course in the library came out easier
    than every links course, because trees and tight driving lines cost a
    player who hits it straight exactly nothing, while big greens cost
    everybody putts. The ordering was a property of the golfer rather than of
    the courses.

    Deterministic, so a course still plays the same every time it is proved -
    the miss is a function of which shot it is, not of chance.
    """
    stamp = f"{seed}|{hole_number}|{stroke}".encode("utf-8")
    spread = int.from_bytes(blake2b(stamp, digest_size=2).digest(), "big") / 65535.0
    return (spread - 0.5) * 2.0 * MISS_DEGREES


def swing_for(distance_yards, lie="Fairway", miss_deg=0.0):
    """Take enough club for what is left, rather than driver from ten yards."""
    target = min(max(distance_yards or FULL_SWING_YARDS, 1.0), FULL_SWING_YARDS)
    share = max(target / FULL_SWING_YARDS, 0.015)
    # A full swing sprays; a putt does not. The miss scales with the shot.
    offline = miss_deg * share
    return {
        "club_metrics": {
            "club_speed_mph": round(98.0 * share ** 0.55, 1),
            "club_path_deg": 0.0,
            "face_angle_to_path_deg": 0.0,
            "attack_angle_deg": -2.0,
            "dynamic_loft_deg": round(15.0 + 40.0 * (1.0 - share), 1),
        },
        "ball_launch_metrics": {
            "ball_speed_mph": round(141.0 * share ** 0.55, 1),
            "launch_angle_deg": round(13.0 + 22.0 * (1.0 - share), 1),
            "launch_direction_deg": round(offline, 2),
            "back_spin_rpm": round(2900.0 + 4000.0 * (1.0 - share), 1),
            "side_spin_rpm": round(offline * 180.0, 1),
            "rifle_spin_rpm": 0.0,
        },
        "shot_context": {"lie_type": lie},
    }


def check_card(holes):
    """What is wrong with these holes as a card, before anyone tees off."""
    faults = []
    for hole in holes:
        # Yards per shot allowed to reach the green. Outside this range the
        # hole is not the par it says it is.
        # The upper bound is set by the real world rather than by taste:
        # Oakmont's 8th is a par 3 of 293 yards, the longest in championship
        # golf, and a check that called it a fault would be rejecting the
        # course rather than checking it.
        per_shot = hole.length_yards / max(hole.par - 2, 1)
        if not SHORTEST_PLAYABLE_YARDS <= per_shot <= 300.0:
            faults.append(
                f"hole {hole.number} is a par {hole.par} of {hole.length_yards:.0f} "
                f"yards, which plays at {per_shot:.0f} yards a shot"
            )
        # A par that does not track its own yardage is the signature of a card
        # transcribed wrong - the numbers and the pars coming out of step by a
        # row. Both of these are outside anything golf actually builds, and a
        # 559 yard par 4 sitting next to a 476 yard par 5 on the same card is
        # how one of them was caught.
        # The bounds sit just outside the real world rather than where taste
        # would put them. Pebble Beach's 9th is a par 4 of 526 yards, the
        # longest in championship golf, so 540 is the first figure that is
        # certainly wrong. Short par 5s of 460 exist; 430 does not.
        if hole.par == 4 and hole.length_yards > 540:
            faults.append(
                f"hole {hole.number} is {hole.length_yards:.0f} yards and called "
                "a par 4, which no course builds - check the card is not out of step"
            )
        if hole.par == 5 and hole.length_yards < 430:
            faults.append(
                f"hole {hole.number} is {hole.length_yards:.0f} yards and called "
                "a par 5, which is short enough to doubt the card"
            )
        if hole.layout.green.fall_per_yard > 0.06:
            faults.append(
                f"hole {hole.number} green falls {hole.layout.green.fall_per_yard:.0%} "
                "per yard, which is steeper than a ball will sit on"
            )

    indexes = sorted(hole.stroke_index for hole in holes)
    if len(holes) == HOLES_ON_A_CARD and indexes != list(range(1, HOLES_ON_A_CARD + 1)):
        faults.append(f"stroke indexes are {indexes}, not 1 to {HOLES_ON_A_CARD}")
    if len(set(indexes)) != len(indexes):
        faults.append("two holes share a stroke index, so a shot is given away twice")
    return faults


def play_round(holes, conditions=None, seed="proof"):
    """Walk the course round. Returns the card and anything that went wrong."""
    game = CoursePlayGame(
        conditions=conditions if conditions is not None else standard_conditions(),
        holes=holes, max_players=1, course_seed=seed,
    )
    game.add_player("Proof")
    game.start()
    player = game.players[0]

    card, faults = {}, []
    for hole in holes:
        stroke = 0
        while not player.holed_out:
            stroke += 1
            left = game.distance_to_pin(player)
            lie = game.lie_for(player)
            game.submit_shot(
                swing_for(left, lie, miss_by(seed, hole.number, stroke)))

        strokes = player.hole_scores.get(hole.number, MAX_STROKES_PER_HOLE)
        card[hole.number] = strokes
        if strokes >= MAX_STROKES_PER_HOLE:
            faults.append(
                f"hole {hole.number} ({hole.par} par, {hole.length_yards:.0f}yds) "
                "could not be finished - picked up at the limit"
            )
        if not game.is_over:
            game.advance_hole()

    return card, faults


def verify(holes, conditions=None, seed="proof"):
    """Everything wrong with this course, as a list. Empty means it is golf."""
    faults = check_card(holes)
    card, played = play_round(holes, conditions, seed)
    return faults + played, card


def describe(holes, conditions=None, seed="proof"):
    """The verdict, as lines to read."""
    faults, card = verify(holes, conditions, seed)
    par = sum(hole.par for hole in holes)
    total = sum(card.values())

    lines = [
        f"{len(holes)} holes, par {par}, "
        f"{sum(h.length_yards for h in holes):.0f} yards",
        f"round played in {total} ({total - par:+d})",
    ]
    lines.extend(f"  FAULT: {fault}" for fault in faults)
    if not faults:
        lines.append("  no faults - every hole finished")
    return lines
