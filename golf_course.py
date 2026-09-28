"""Course play: holes, strokes, distance to the pin and scoring against par.

Each hole runs along the X axis from a tee at the origin to a pin at
(length, 0, 0). A player is assumed to aim at the flag from wherever their ball
lies, so the shot the engine produces - which is measured relative to the aim
line - is rotated by the current bearing to the pin before being applied. Slice
your approach and you finish genuinely offline, with the next shot aimed back
at the flag from there.

Knows nothing about networking. The rules live here; the server moves messages.
"""

import math
from dataclasses import dataclass, field

from golf_game import MAX_PLAYERS, GameError, check_redo_allowed, play_shot
from golf_scoring import FORMATS, STROKE_PLAY, standings
from golf_surface import (
    CLEAR,
    apply_lie_to_metrics,
    describe_encounters,
    feature_at,
    roll_out,
)
from golf_terrain import (
    GREEN,
    REPLAYED_FROM_LAST_SHOT,
    HoleLayout,
    aim_from,
    ground_at,
    height_at,
    putt_break,
    is_playable,
    more_punishing,
    penalty_for,
    straight_hole,
)
from golf_weather import standard_conditions


@dataclass(frozen=True)
class Hole:
    number: int
    length_yards: float
    par: int
    # Difficulty ranking, 1 being the hardest. Handicap shots go to the low
    # numbers first. Defaults to playing order when a course does not say.
    stroke_index: int = None
    # The shape of it. A hole that is not given one runs straight from tee to
    # pin with fairway either side, which is how every hole here used to play.
    layout: HoleLayout = None

    def __post_init__(self):
        if self.stroke_index is None:
            object.__setattr__(self, "stroke_index", self.number)
        if self.layout is None:
            object.__setattr__(self, "layout", straight_hole(self.length_yards))
        else:
            # One source of truth for how far a hole plays. A dogleg is as long
            # as the way it is walked, and the card should not disagree with the
            # ground.
            object.__setattr__(self, "length_yards", self.layout.length_yards)


# A full eighteen: par 72, 6855 yards, two par 3s and two par 5s on each nine.
#
# The stroke indexes are the part that has to be right, because handicap shots
# are allotted by them in every format here. Two rules shape them, and both are
# how golf actually does it rather than how a list numbers itself:
#
#   The odd ones fall on the front nine and the even ones on the back, so a
#   golfer receiving nine shots gets them spread over the round instead of all
#   of them before the turn.
#
#   Within a nine they follow how much hole there is per shot allowed to reach
#   the green - length over par less its two putts. That is what makes a 440
#   yard par 4 the hardest hole out here and a 495 yard par 5 the easiest:
#   220 yards a shot against 165. Ranking on length alone would call the par 5
#   the hardest hole on the course, which no golfer playing it would agree with.
#
#   The hardest holes are the 4th and the 13th. The 1st, 9th, 10th and 18th are
#   deliberately not among them - a match should not be decided by a shot given
#   away before anyone has swung, or handed back walking up the last.
COURSE_HOLES = (
    # Out                                    yards per shot to the green
    Hole(1, 400.0, 4, stroke_index=7),     # 200.0
    Hole(2, 170.0, 3, stroke_index=15),    # 170.0
    Hole(3, 525.0, 5, stroke_index=13),    # 175.0
    Hole(4, 440.0, 4, stroke_index=1),     # 220.0
    Hole(5, 355.0, 4, stroke_index=11),    # 177.5
    Hole(6, 205.0, 3, stroke_index=5),     # 205.0
    Hole(7, 495.0, 5, stroke_index=17),    # 165.0
    Hole(8, 385.0, 4, stroke_index=9),     # 192.5
    Hole(9, 420.0, 4, stroke_index=3),     # 210.0
    # Home
    Hole(10, 410.0, 4, stroke_index=6),    # 205.0
    Hole(11, 160.0, 3, stroke_index=18),   # 160.0
    Hole(12, 555.0, 5, stroke_index=12),   # 185.0
    Hole(13, 445.0, 4, stroke_index=2),    # 222.5
    Hole(14, 365.0, 4, stroke_index=14),   # 182.5
    Hole(15, 190.0, 3, stroke_index=10),   # 190.0
    Hole(16, 505.0, 5, stroke_index=16),   # 168.3
    Hole(17, 395.0, 4, stroke_index=8),    # 197.5
    Hole(18, 430.0, 4, stroke_index=4),    # 215.0
)
# Nine is the front nine of the same course, not a course of its own. A golfer
# playing it off a full handicap receives the nine shots whose stroke index
# falls out here, which is what playing half a round off a full handicap means.
FRONT_NINE = COURSE_HOLES[:9]
BACK_NINE = COURSE_HOLES[9:]


def holes_for(count):
    """The eighteen, its front nine, or whatever else is asked for by number."""
    if count == 18:
        return COURSE_HOLES
    if count == 9:
        return FRONT_NINE
    raise ValueError(f"a round is 9 or 18 holes, not {count}")
GREEN_RADIUS_YARDS = 20.0
GIMME_PUTT_RADIUS_YARDS = 3.0  # inside this, the ball is holed with one more stroke
MAX_STROKES_PER_HOLE = 12  # pick up and move on

TEE = (0.0, 0.0, 0.0)


@dataclass
class CoursePlayer:
    player_id: int
    name: str
    ball_position: tuple = TEE
    strokes: int = 0
    holed_out: bool = False
    hole_scores: dict = field(default_factory=dict)  # hole number -> strokes
    trims: dict = field(default_factory=dict)  # fitted from their own history
    handicap: float = 0.0  # shots received, allotted to the hardest holes


@dataclass(frozen=True)
class CourseShotOutcome:
    player_id: int
    ball_flight: dict
    feedback: list
    hole_number: int
    strokes_this_hole: int
    ball_position: tuple
    distance_to_pin_yards: float
    has_holed_out: bool
    ball_launch: dict = field(default_factory=dict)
    modelled_fields: tuple = ()
    delivery: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)
    # Where the shot was struck from and along what line. The engine flies a
    # ball from the origin, straight down its own x axis - so a trajectory is
    # in the shot's own frame, not the hole's. Anything drawing one has to be
    # told where to put it and which way to turn it, and it must be told
    # rather than work it out: where a golfer aimed is a decision this made,
    # and a client deriving it a second time would eventually disagree.
    played_from: tuple = (0.0, 0.0, 0.0)
    aim_bearing_deg: float = 0.0


def distance_between(first, second):
    """Ground distance, ignoring height."""
    return math.hypot(second[0] - first[0], second[2] - first[2])


def advance_ball(ball_position, pin_position, ball_flight):
    """Apply a shot played from `ball_position` towards the target.

    The engine reports final_x down the line the golfer was standing on and
    final_z right of it, so both are rotated onto that line.

    The line is the target and nothing else. A golfer in a bay hits at a screen
    showing the hole; where the ball actually starts and how it bends are
    measured off the strike, and that is the whole of where it goes. There used
    to be an aim offset here that a client could send with a shot, which let a
    number typed next to the ball turn it seventy yards - a steering control in
    a simulator built to fly a real strike. Playing out to the right is done by
    aiming out to the right and hitting it there, which arrives as launch
    direction, measured, like everything else about the strike.
    """
    bearing = math.atan2(
        pin_position[2] - ball_position[2], pin_position[0] - ball_position[0]
    )
    downrange = ball_flight["final_x_yards"]
    sideways = ball_flight["final_z_yards"]

    return (
        ball_position[0] + downrange * math.cos(bearing) - sideways * math.sin(bearing),
        0.0,
        ball_position[2] + downrange * math.sin(bearing) + sideways * math.cos(bearing),
    )


class CoursePlayGame:
    """Players play their own ball, alternating strokes, hole by hole."""

    def __init__(self, conditions=None, holes=COURSE_HOLES, max_players=MAX_PLAYERS,
                 course_seed=None, game_format=STROKE_PLAY, redo_limit=None,
                 max_strokes_per_hole=MAX_STROKES_PER_HOLE):
        if not holes:
            raise ValueError("a course needs at least one hole")
        if max_players < 1:
            raise ValueError("max_players must be at least 1")
        if game_format not in FORMATS:
            raise ValueError(f"unknown format: {game_format!r}")

        self.game_format = game_format
        self.conditions = conditions if conditions is not None else standard_conditions()
        # Puddles fall in different places on different courses. Keying them to
        # the course means the same one floods the same way every time.
        self.course_seed = course_seed if course_seed is not None \
            else self.conditions.location_name
        self.holes = tuple(holes)
        self.max_players = max_players
        self.players = []
        self.started = False
        self._hole_index = 0
        # Order of honour on the tee: joining order until a hole has been won.
        self._honour_order = []
        # The one shot that can still be taken back.
        self._undo = None
        # Shots each golfer may take back, counted per hole so the allowance
        # comes back on the next tee rather than running out on the third.
        self.redo_limit = redo_limit
        self.max_strokes_per_hole = max_strokes_per_hole
        self._redos_this_hole = {}

    # --- Setup ---------------------------------------------------------------

    def add_player(self, name, handicap=0.0):
        if self.started:
            raise GameError("cannot join a game that has already started")
        if len(self.players) >= self.max_players:
            raise GameError(f"the course is full ({self.max_players} players)")

        player = CoursePlayer(player_id=len(self.players), name=name,
                              handicap=handicap or 0.0)
        self.players.append(player)
        self._honour_order.append(player.player_id)
        return player

    def start(self):
        if self.started:
            raise GameError("game has already started")
        if not self.players:
            raise GameError("cannot start a game with no players")
        self.started = True

    # --- Where things are ----------------------------------------------------

    @property
    def current_hole(self):
        if self._hole_index >= len(self.holes):
            return None
        return self.holes[self._hole_index]

    @property
    def pin_position(self):
        hole = self.current_hole
        if hole is None:
            return None
        return (hole.layout.pin.x, 0.0, hole.layout.pin.z)

    def aim_position(self, player):
        """What this player is playing at: the flag, or the corner of a dogleg.

        Aiming through the trees at a green you cannot see is a decision a
        golfer is allowed to make - with an aim offset - but it is not the one
        made for them.
        """
        hole = self.current_hole
        if hole is None:
            return None
        target = aim_from(hole.layout, player.ball_position[0], player.ball_position[2])
        return (target.x, 0.0, target.z)

    @property
    def current_player(self):
        """Whoever is away, by the rules rather than by rotation.

        Golf is not played in a circle. The player furthest from the hole
        plays, and on the tee the honour goes to whoever won the last hole.
        Both fall out of one sort: furthest first, ties broken by honour -
        which covers the tee, where everyone is the same distance away.
        """
        if not self.started or self.is_over or self.is_hole_complete:
            return None

        still_playing = [player for player in self.players if not player.holed_out]
        if not still_playing:
            return None
        return min(
            still_playing,
            key=lambda player: (-self.distance_to_pin(player), self._honour_rank(player)),
        )

    def _honour_rank(self, player):
        """Where this player stands in the order of honour, lower being first."""
        if player.player_id in self._honour_order:
            return self._honour_order.index(player.player_id)
        return player.player_id

    def distance_to_pin(self, player):
        return distance_between(player.ball_position, self.pin_position)

    def climb_to_target(self, player):
        """How much the ground rises between this ball and what it is aimed at.

        What a golfer means by "it is playing fifteen feet uphill": the ground
        where the shot is going, against the ground they are standing on. Not
        where it lands - nobody knows that until it has landed - which is the
        same approximation a golfer makes looking up the hill.
        """
        hole = self.current_hole
        if hole is None:
            return 0.0
        ball = player.ball_position
        target = aim_from(hole.layout, ball[0], ball[2])
        return (height_at(hole.layout, target.x, target.z)
                - height_at(hole.layout, ball[0], ball[2]))

    def ground_under(self, player):
        """The ground the designer put where this ball is lying."""
        hole = self.current_hole
        if hole is None:
            return CLEAR
        return ground_at(hole.layout, player.ball_position[0], player.ball_position[2])

    def surface_under(self, player):
        """What this ball is actually sitting in.

        Two things have a say: what the hole is made of there, and what the
        weather has left on top of it. Whichever costs the shot more wins, so a
        bunker with an inch of water in it plays as water.
        """
        if player.strokes == 0:
            return CLEAR  # teed up, above anything on the ground
        weather = feature_at(player.ball_position, self.conditions, self.course_seed)
        return more_punishing(self.ground_under(player), weather)

    def lie_for(self, player, override=None):
        """The lie this shot is played from.

        Derived from where the ball actually is, unless the golfer says
        otherwise. They are standing over the ball and the simulator is not, so
        an override is taken at face value - it replaces the derivation
        entirely rather than being layered on top of it.

        Failing an override, standing water or leaves beat the general lie: it
        does not matter that you are on the green if your ball is in a puddle
        on it.
        """
        if override:
            return override

        surface = self.surface_under(player)
        if not surface.is_clear:
            return surface.lie_type
        if player.strokes == 0:
            return "Tee"
        if self.distance_to_pin(player) <= GREEN_RADIUS_YARDS:
            return "Putting"
        return "Fairway"

    @property
    def is_hole_complete(self):
        return self.started and all(player.holed_out for player in self.players)

    @property
    def is_over(self):
        return self.started and self._hole_index >= len(self.holes)

    # --- Play ----------------------------------------------------------------

    def submit_shot(self, swing_metrics):
        if not self.started:
            raise GameError("game has not started")
        if self.is_over:
            raise GameError("game is over")
        if self.is_hole_complete:
            raise GameError("hole is complete; advance to the next one")

        player = self.current_player
        hole = self.current_hole

        # Everything this shot is about to change, kept so it can be given
        # back. Taken before the shot rather than after, for obvious reasons.
        self._undo = {
            "player_id": player.player_id,
            "ball_position": player.ball_position,
            "strokes": player.strokes,
            "holed_out": player.holed_out,
            "hole_scores": dict(player.hole_scores),
        }

        metrics = dict(swing_metrics)
        context = dict(metrics.get("shot_context", {}))
        override = context.get("lie_override")
        # An aim sent with a shot used to turn the whole thing. It is ignored
        # rather than read: a golfer hits a ball and the strike says where it
        # goes, and any line they chose to stand on shows up in the launch
        # direction their equipment measured.
        context.pop("aim_offset_deg", None)

        context["lie_type"] = self.lie_for(player, override)
        metrics["shot_context"] = context

        # Whatever the weather left there is charged to the shot - unless the
        # golfer has told us what they are actually playing from, in which case
        # they are describing the ball in front of them and we take their word.
        surface = CLEAR if override else self.surface_under(player)
        metrics = apply_lie_to_metrics(metrics, surface)

        execution = play_shot(metrics, self.conditions, player.trims,
                              landing_height_yards=self.climb_to_target(player))
        ball_flight = execution.ball_flight
        player.strokes += 1

        played_from = player.ball_position
        aimed_at = self.aim_position(player)
        free_rest = advance_ball(played_from, aimed_at, ball_flight)
        player.ball_position, met = self._settle(played_from, free_rest, ball_flight)
        feedback = list(execution.feedback) + describe_encounters(met)

        # Water and out of bounds are not lies to be played from. They cost a
        # stroke and the ball goes back in play somewhere else, which is the
        # rules of golf rather than anything the ground does to a shot.
        player.ball_position, relief = self._take_relief(player, played_from)
        feedback.extend(relief)
        distance = self.distance_to_pin(player)

        if distance <= GIMME_PUTT_RADIUS_YARDS:
            player.strokes += 1  # the gimme
            self._hole_out(player, hole)
        elif player.strokes >= self.max_strokes_per_hole:
            self._hole_out(player, hole)  # picked up

        return CourseShotOutcome(
            player_id=player.player_id,
            ball_flight=ball_flight,
            feedback=feedback,
            ball_launch=execution.ball_launch,
            modelled_fields=execution.modelled_fields,
            delivery=execution.delivery,
            body=execution.body,
            hole_number=hole.number,
            strokes_this_hole=player.strokes,
            ball_position=player.ball_position,
            distance_to_pin_yards=distance,
            has_holed_out=player.holed_out,
            played_from=played_from,
            aim_bearing_deg=math.degrees(math.atan2(
                aimed_at[2] - played_from[2], aimed_at[0] - played_from[0])),
        )

    def redo_shot(self, player_id=None):
        """Take the last shot back and play it again.

        Only the shot just played, and only by whoever played it. Once the next
        player has hit, or the hole has been closed out, there is nothing to
        take back - unpicking further would mean deciding whose round to
        rewrite, and a card everyone has moved on from is not a draft.

        Not the rules of golf. It is the rules of a practice round, which is
        most of what a simulator is for.
        """
        if self._undo is None:
            raise GameError("there is no shot to play again")
        whose = self._undo["player_id"]
        if player_id is not None and player_id != whose:
            raise GameError(f"the last shot was {self.players[whose].name}'s")
        check_redo_allowed(self.redo_limit, self._redos_this_hole.get(whose, 0), "hole")

        self._redos_this_hole[whose] = self._redos_this_hole.get(whose, 0) + 1
        player = self.players[whose]
        player.ball_position = self._undo["ball_position"]
        player.strokes = self._undo["strokes"]
        player.holed_out = self._undo["holed_out"]
        player.hole_scores = dict(self._undo["hole_scores"])
        self._undo = None
        return player

    def _take_relief(self, player, played_from):
        """Put a ball that finished in trouble back into play, at a cost.

        Out of bounds is stroke and distance: play again from where the last
        one was struck. Water is a stroke and a drop, back on the line the ball
        came in on until there is ground worth standing on - which is the
        nearest thing to dropping behind the hazard that a hole made of circles
        can offer.
        """
        ground = self.ground_under(player)
        if is_playable(ground):
            return player.ball_position, ()

        player.strokes += penalty_for(ground)
        if ground.kind in REPLAYED_FROM_LAST_SHOT:
            return played_from, ["  Out of bounds - stroke and distance."]
        return (self._back_on_the_line(player.ball_position, played_from),
                ["  In the water - a stroke, and a drop."])

    def _back_on_the_line(self, entered, played_from, step_yards=2.0):
        """Walk back down the line the ball came in on, until there is ground.

        Past the last shot if need be. A golfer dropping behind a hazard may go
        back as far as they like on that line, and they have to be able to: the
        shot might have been played from a spot that is itself no good to drop
        on, and stopping at it would leave the ball in the water it just came
        out of.
        """
        layout = self.current_hole.layout
        run_x = played_from[0] - entered[0]
        run_z = played_from[2] - entered[2]
        span = math.hypot(run_x, run_z)
        if span == 0.0:
            return layout.tee.x, 0.0, layout.tee.z

        step_x, step_z = run_x / span * step_yards, run_z / span * step_yards
        for step in range(1, int(layout.length_yards / step_yards) + 1):
            spot = (entered[0] + step_x * step, 0.0, entered[2] + step_z * step)
            if is_playable(ground_at(layout, spot[0], spot[2])):
                return spot
        return layout.tee.x, 0.0, layout.tee.z

    def _settle(self, played_from, free_rest, ball_flight):
        """Where the ball finishes, having actually travelled the ground.

        The engine says where it would come to rest on open turf. The course
        then has its say over the run-out: water pulls it up, a branch stops
        it, leaves take the pace off, and a crosswind pushes it off line.
        """
        total = ball_flight.get("total_distance_with_roll_yards") or 0.0
        carry = ball_flight.get("total_carry_yards") or 0.0
        if total <= 0 or carry >= total:
            return free_rest, ()

        fraction = carry / total
        landed = (
            played_from[0] + (free_rest[0] - played_from[0]) * fraction,
            0.0,
            played_from[2] + (free_rest[2] - played_from[2]) * fraction,
        )
        # A ball pitching straight into water stops there without running.
        if feature_at(landed, self.conditions, self.course_seed).stops_roll:
            return landed, ("casual_water",)

        rested, met = roll_out(landed, free_rest, self.conditions, self.course_seed)
        return self._green_has_its_say(landed, rested), met

    def _green_has_its_say(self, landed, rested):
        """A ball rolling across a tilted green is carried by it.

        Only once it is down and running, and only on the green - a ball still
        in the air is not being carried by anything, and the slope of a putting
        surface has no opinion about a shot crossing it fifty feet up.
        """
        layout = self.current_hole.layout
        if layout.green.is_flat:
            return rested
        if ground_at(layout, landed[0], landed[2]) is not GREEN:
            return rested
        return putt_break(layout, landed, rested)

    def _hole_out(self, player, hole):
        player.holed_out = True
        player.hole_scores[hole.number] = player.strokes

    def advance_hole(self):
        """Move to the next hole, putting every ball back on the tee.

        The honour passes to whoever took fewest strokes on the hole just
        finished, ties keeping the order they already had.
        """
        if not self.is_hole_complete:
            raise GameError("hole is not finished yet")

        finished = self.holes[self._hole_index]
        self._honour_order = [
            player.player_id
            for player in sorted(
                self.players,
                key=lambda p: (p.hole_scores.get(finished.number, MAX_STROKES_PER_HOLE),
                               self._honour_rank(p)),
            )
        ]

        self._hole_index += 1
        # A card everyone has walked off is not a draft.
        self._undo = None
        self._redos_this_hole = {}  # a fresh allowance on the next tee
        for player in self.players:
            player.ball_position = TEE
            player.strokes = 0
            player.holed_out = False

    # --- Results -------------------------------------------------------------

    def hole_summary(self, hole_number=None):
        """Strokes and score against par for one hole, keyed by player id."""
        hole = next(
            (h for h in self.holes if h.number == hole_number),
            self.holes[min(self._hole_index, len(self.holes) - 1)],
        )
        return {
            player.player_id: {
                "name": player.name,
                "strokes": player.hole_scores.get(hole.number, 0),
                "score_vs_par": player.hole_scores.get(hole.number, 0) - hole.par,
            }
            for player in self.players
        }

    def scores_by_hole(self):
        """The card so far: hole number -> {player id: gross strokes}."""
        card = {}
        for player in self.players:
            for hole_number, strokes in player.hole_scores.items():
                card.setdefault(hole_number, {})[player.player_id] = strokes
        return card

    def standings(self):
        """Where everyone stands, in the format being played."""
        return standings(
            self.holes,
            self.scores_by_hole(),
            {player.player_id: player.name for player in self.players},
            {player.player_id: player.handicap for player in self.players},
            self.game_format,
        )

    def summary(self):
        """Final scores against par, keyed by player id.

        Keeps the shape the client already renders, and carries the net and
        format-specific numbers alongside it.
        """
        par_by_hole = {hole.number: hole.par for hole in self.holes}
        table = {row["player_id"]: row for row in self.standings()["table"]}
        return {
            player.player_id: {
                "name": player.name,
                "total_score_vs_par": sum(
                    strokes - par_by_hole[number]
                    for number, strokes in player.hole_scores.items()
                ),
                "hole_by_hole_strokes": dict(player.hole_scores),
                "handicap": player.handicap,
                "net_vs_par": table.get(player.player_id, {}).get("net_vs_par", 0),
                "points": table.get(player.player_id, {}).get("points", 0),
            }
            for player in self.players
        }
