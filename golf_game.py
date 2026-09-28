"""Driving range game: shot execution, turn order and longest-shot scoring.

Knows nothing about networking. A caller drives it by adding players, starting
the game, and submitting swing metrics for whoever's turn it is.

Playing conditions are passed in rather than fetched. Weather lookup costs two
HTTP round trips, so it happens once when a game is set up, never per shot.
"""

from dataclasses import dataclass, field

from golf_biomechanics import analyse_body, describe_body
from golf_impact import analyse_delivery, complete_ball_launch
from golf_physics import analyze_shot_for_feedback, calculate_trajectory
from golf_weather import standard_conditions

# Golf is played in twos, threes and fours. Four is the practical limit on a
# course and a sensible one on a range.
MAX_PLAYERS = 4
NUM_SHOTS_RANGE = 3

DEFAULT_LIE = "Tee"


class GameError(Exception):
    """A game rule was broken - wrong turn, game not started, range full."""


@dataclass
class Player:
    player_id: int
    name: str
    shots_taken: int = 0
    longest_shot_yards: float = 0.0
    # Fitted from this golfer's own stored shots, when there are enough.
    trims: dict = field(default_factory=dict)
    # A golfer who drops out mid-round keeps their place and their score, and
    # is skipped until they come back, rather than stalling everyone else.
    connected: bool = True


@dataclass(frozen=True)
class ShotOutcome:
    player_id: int
    ball_flight: dict
    feedback: list
    shot_number: int
    longest_shot_yards: float
    ball_launch: dict = field(default_factory=dict)
    modelled_fields: tuple = ()
    delivery: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ShotExecution:
    """One swing, resolved and flown."""

    ball_flight: dict
    feedback: list
    ball_launch: dict  # measured where the monitor reported it, modelled where not
    modelled_fields: tuple  # which ball numbers the simulator had to supply
    delivery: dict  # swing-quality readings from how the club arrived
    body: dict  # kinematic sequence, x-factor, tempo, ground reaction, posture


def play_shot(swing_metrics, conditions, trims=None, landing_height_yards=0.0):
    """Run one shot through the impact model, the physics engine and feedback.

    `swing_metrics` is the structure the client sends: `club_metrics`,
    `ball_launch_metrics` and `shot_context`. A golfer whose equipment reports
    only club delivery gets the ball launch modelled from it; anything the
    monitor did measure is used as measured.

    `trims` are fitted from this golfer's own history, so the modelled half
    behaves like their swing rather than like an average one. They cannot
    touch anything their equipment measured.
    """
    trims = trims or {}
    # Ball-position guard: if the camera saw the ball in the wrong state for this
    # lie (teed on a turf shot, or on the ground for a tee shot), the swing does
    # not register. The rig computes this and passes it in shot_context["vision"];
    # a missing or allowing verdict lets the shot through untouched.
    guard = swing_metrics.get("shot_context", {}).get("vision")
    if isinstance(guard, dict) and guard.get("register") is False:
        raise GameError(guard.get("reason")
                        or "the ball is in the wrong spot for this lie - the swing did not register")

    ball, modelled_fields = complete_ball_launch(
        swing_metrics,
        spin_trim=trims.get("spin_trim", 1.0),
        launch_trim=trims.get("launch_trim", 1.0),
    )

    ball_flight = calculate_trajectory(
        ball_speed_mph=ball.get("ball_speed_mph", 0.0),
        launch_angle_deg=ball.get("launch_angle_deg", 0.0),
        # The client calls horizontal launch `launch_direction_deg`; the engine
        # calls the same quantity `launch_side_angle_deg`. This is the only
        # place the two vocabularies meet.
        launch_side_angle_deg=ball.get("launch_direction_deg", 0.0),
        back_spin_rpm=ball.get("back_spin_rpm", 0.0),
        side_spin_rpm=ball.get("side_spin_rpm", 0.0),
        rifle_spin_rpm=ball.get("rifle_spin_rpm", 0.0),
        wind_vector_mph=conditions.wind_vector_mph,
        current_air_density=conditions.air_density,
        # Ground the course is actually in today. Rain softens it, so the ball
        # lands with less bounce and stops sooner; baked turf runs.
        current_cor_ground=conditions.cor_ground,
        current_rolling_friction_coeff=conditions.rolling_friction,
        # How much the ground rises between here and where this is aimed. A
        # range is flat; a course is not.
        landing_height_yards=landing_height_yards,
    )

    club_metrics = swing_metrics.get("club_metrics") or {}
    delivery = analyse_delivery(club_metrics)

    # Analyse against the resolved numbers: a golfer with no ball data should
    # still get a smash factor, not a row of zeroes.
    #
    # Club path and face-to-path are filled in the same way. Monitors that
    # report an absolute face angle do not report face-to-path, and without
    # this the D-plane analysis reads zero and never diagnoses a shot shape.
    resolved_club = dict(club_metrics)
    for field in ("club_path_deg", "face_to_path_deg"):
        target = "face_angle_to_path_deg" if field == "face_to_path_deg" else field
        if resolved_club.get(target) is None and delivery.get(field) is not None:
            resolved_club[target] = delivery[field]

    resolved_metrics = dict(swing_metrics)
    resolved_metrics["ball_launch_metrics"] = ball
    resolved_metrics["club_metrics"] = resolved_club
    lie_type = swing_metrics.get("shot_context", {}).get("lie_type", DEFAULT_LIE)
    feedback = analyze_shot_for_feedback(resolved_metrics, ball_flight, lie_type=lie_type)

    # Body movement, where the golfer has capture or pressure data. This is the
    # link below club delivery: the body moves the club, the club moves the ball.
    body_metrics = swing_metrics.get("body_metrics") or {}
    feedback = feedback + describe_body(body_metrics)

    # Smash is the ratio of the two numbers actually flown, whether they were
    # measured or modelled. It needs both blocks, so it belongs here rather
    # than in either analysis on its own.
    club_speed = club_metrics.get("club_speed_mph")
    ball_speed = ball.get("ball_speed_mph")
    if club_speed and ball_speed:
        delivery["smash_factor"] = ball_speed / club_speed

    return ShotExecution(
        ball_flight=ball_flight,
        feedback=feedback,
        ball_launch=ball,
        modelled_fields=tuple(modelled_fields),
        delivery=delivery,
        body=analyse_body(body_metrics),
    )


def check_redo_allowed(limit, used, per):
    """Whether another shot may be taken back, and why not if it may not.

    Said once here so the range and the course refuse in the same words. A
    golfer who has run out should be told they have run out, not told there is
    nothing to take back - those are different problems with different fixes.
    """
    if limit == 0:
        raise GameError("taking shots back is switched off for this round")
    if limit is not None and used >= limit:
        raise GameError(
            f"no shots left to take back - {limit} per {per} is the limit here"
        )


class DrivingRangeGame:
    """Each player takes a fixed number of shots; longest one wins.

    Or none at all: `shots_per_player=None` opens the range and leaves it
    open, which is what practice is. A bucket of balls that runs out is a
    competition, and there are times you just want to keep hitting.
    """

    def __init__(self, conditions=None, shots_per_player=NUM_SHOTS_RANGE,
                 max_players=MAX_PLAYERS, redo_limit=None):
        if shots_per_player is not None and shots_per_player < 1:
            raise ValueError("shots_per_player must be at least 1, or None to practise")
        if max_players < 1:
            raise ValueError("max_players must be at least 1")

        # How many shots each golfer may take back this session. None is as
        # many as they like, 0 is none at all.
        self.redo_limit = redo_limit
        self._redos_used = {}

        self.conditions = conditions if conditions is not None else standard_conditions()
        self.shots_per_player = shots_per_player
        self.max_players = max_players
        self.players = []
        self.started = False
        self._turn_index = 0
        # The one shot that can still be taken back. Cleared as soon as
        # anything else happens, because by then it is not the last shot.
        self._undo = None

    @property
    def is_practice(self):
        """An open range, with no end to play towards."""
        return self.shots_per_player is None

    # --- Setup ---------------------------------------------------------------

    def add_player(self, name):
        if self.started:
            raise GameError("cannot join a game that has already started")
        if len(self.players) >= self.max_players:
            raise GameError(f"the range is full ({self.max_players} players)")

        player = Player(player_id=len(self.players), name=name)
        self.players.append(player)
        return player

    def start(self):
        if self.started:
            raise GameError("game has already started")
        if not self.players:
            raise GameError("cannot start a game with no players")
        self.started = True

    # --- Turn state ----------------------------------------------------------

    @property
    def current_player(self):
        """Whoever is due to hit, or None once the game is over."""
        if not self.started or self.is_over:
            return None
        return self.players[self._turn_index]

    @property
    def shot_number(self):
        """Which shot the current player is about to take, counting from 1."""
        player = self.current_player
        return None if player is None else player.shots_taken + 1

    @property
    def is_over(self):
        if self.is_practice:
            return False  # you stop when you have had enough
        return self.started and all(
            player.shots_taken >= self.shots_per_player for player in self.players
        )

    # --- Play ----------------------------------------------------------------

    def submit_shot(self, swing_metrics):
        """Play the current player's shot and pass the turn on."""
        if not self.started:
            raise GameError("game has not started")
        if self.is_over:
            raise GameError("game is over")

        player = self.players[self._turn_index]
        execution = play_shot(swing_metrics, self.conditions, player.trims)

        # Taken before the shot counts for anything, so it can be given back.
        # The longest shot is remembered rather than recomputed: a max cannot
        # be undone, and the shot being taken back may have been the best one.
        self._undo = {
            "player_id": player.player_id,
            "shots_taken": player.shots_taken,
            "longest_shot_yards": player.longest_shot_yards,
            "turn_index": self._turn_index,
        }

        player.shots_taken += 1
        distance = execution.ball_flight["total_distance_with_roll_yards"]
        player.longest_shot_yards = max(player.longest_shot_yards, distance)
        self._advance_turn()

        return ShotOutcome(
            player_id=player.player_id,
            ball_flight=execution.ball_flight,
            feedback=execution.feedback,
            shot_number=player.shots_taken,
            longest_shot_yards=player.longest_shot_yards,
            ball_launch=execution.ball_launch,
            modelled_fields=execution.modelled_fields,
            delivery=execution.delivery,
            body=execution.body,
        )

    def redo_shot(self, player_id=None):
        """Take the last shot back and play it again.

        Only the shot just played, and only by whoever played it. Once anyone
        else has hit there is nothing to take back - their shot is the last
        one, and unpicking further would mean deciding whose round to rewrite.
        """
        if self._undo is None:
            raise GameError("there is no shot to play again")
        whose = self._undo["player_id"]
        if player_id is not None and player_id != whose:
            raise GameError(f"the last shot was {self.players[whose].name}'s")
        check_redo_allowed(self.redo_limit, self._redos_used.get(whose, 0), "session")

        self._redos_used[whose] = self._redos_used.get(whose, 0) + 1
        player = self.players[whose]
        player.shots_taken = self._undo["shots_taken"]
        player.longest_shot_yards = self._undo["longest_shot_yards"]
        self._turn_index = self._undo["turn_index"]
        self._undo = None
        return player

    def _advance_turn(self):
        """Round-robin to the next player who still owes a shot."""
        for offset in range(1, len(self.players) + 1):
            candidate = (self._turn_index + offset) % len(self.players)
            owed = self.is_practice or \
                self.players[candidate].shots_taken < self.shots_per_player
            if owed:
                self._turn_index = candidate
                return
        # Nobody left to hit; is_over now reports True.

    # --- Results -------------------------------------------------------------

    def summary(self):
        """Longest shot per player, keyed by player id."""
        return {
            player.player_id: {
                "name": player.name,
                "longest_shot": player.longest_shot_yards,
            }
            for player in self.players
        }
