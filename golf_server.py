"""Asyncio TCP server for the driving range.

Speaks newline-delimited JSON, one object per line, matching the protocol the
existing client (`network_client.py`) already implements. One connection is one
player;
the game starts once the range is full.

The game rules live in golf_game. This module only moves messages.
"""

import asyncio
import json
import logging
import math

from golf_course import COURSE_HOLES, CoursePlayGame, holes_for
from golf_library import COURSES, DEFAULT_TEES
from golf_library import find as find_course
from golf_game import MAX_PLAYERS, NUM_SHOTS_RANGE, DrivingRangeGame, GameError
from golf_history import fit_from_history
from golf_scoring import FORMATS, STROKE_PLAY, describe_standings
from golf_settings import describe as describe_settings
from golf_settings import load_settings, validated
from golf_store import SupabaseStore
from golf_surface import describe_surface
from golf_terrain import describe_layout
from golf_websocket import ProtocolFault as WebSocketFault
from golf_websocket import wrap as websocket_wrap
from golf_weather import get_conditions_for_city

logger = logging.getLogger(__name__)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8888
DEFAULT_CITY = "St Andrews"
DRIVING_RANGE = "driving_range"
COURSE_PLAY = "course_play"

# A shot produces a few thousand integration steps. The client draws two plots
# a few hundred pixels wide, so sending every step wastes bandwidth on detail
# nobody can see - and the same volume would land in the database later.
MAX_TRAJECTORY_POINTS = 200

# Bounds for inbound swing metrics. Generous enough for any real shot, tight
# enough that a typo or a hand-crafted client cannot ask for nonsense.
BALL_METRIC_BOUNDS = {
    "ball_speed_mph": (0.0, 250.0),
    "launch_angle_deg": (-20.0, 70.0),
    "launch_direction_deg": (-60.0, 60.0),
    "back_spin_rpm": (-5000.0, 15000.0),
    "side_spin_rpm": (-15000.0, 15000.0),
    "rifle_spin_rpm": (-15000.0, 15000.0),
    "spin_axis_deg": (-75.0, 75.0),
}

CLUB_METRIC_BOUNDS = {
    "club_speed_mph": (0.0, 160.0),
    "attack_angle_deg": (-20.0, 20.0),
    "dynamic_loft_deg": (-5.0, 70.0),
    "club_path_deg": (-30.0, 30.0),
    "swing_direction_deg": (-30.0, 30.0),
    "face_angle_deg": (-45.0, 45.0),
    "face_angle_to_path_deg": (-45.0, 45.0),
    "closure_rate_deg_per_sec": (-2000.0, 2000.0),
    "impact_offset_toe_in": (-2.0, 2.0),
    "impact_offset_high_in": (-2.0, 2.0),
    "dynamic_lie_deg": (-20.0, 20.0),
}

# The ball numbers the engine needs. A monitor that reports all of them is
# taken at its word.
REQUIRED_BALL_FIELDS = (
    "ball_speed_mph",
    "launch_angle_deg",
    "launch_direction_deg",
    "back_spin_rpm",
    "side_spin_rpm",
    "rifle_spin_rpm",
)

# The least club delivery the impact model needs to produce them instead.
REQUIRED_CLUB_FIELDS = ("club_speed_mph", "attack_angle_deg", "dynamic_loft_deg")


class ProtocolError(Exception):
    """The client sent something the server will not act on."""


def decimate_trajectory(points, target=MAX_TRAJECTORY_POINTS):
    """Thin a trajectory for transmission, keeping the parts that shape it.

    Every bounce is preserved, along with the first and last points, so the
    plotted flight still lands and stops where it really did.
    """
    if len(points) <= target:
        return [list(point) for point in points]

    step = math.ceil(len(points) / target)
    keep = set(range(0, len(points), step))
    keep.add(len(points) - 1)
    keep.update(index for index, point in enumerate(points) if point[8] == 1)
    return [list(points[index]) for index in sorted(keep)]


def _check_bounds(block_name, block, bounds):
    """Every reported value must be a number inside its plausible range."""
    for field, value in block.items():
        if field not in bounds or value is None:
            continue  # unknown fields pass through; the monitor may report extras
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProtocolError(f"{block_name}.{field} must be a number")
        low, high = bounds[field]
        if not low <= value <= high:
            raise ProtocolError(
                f"{block_name}.{field} must be between {low} and {high}, got {value}"
            )


def validate_swing_metrics(metrics):
    """Raise ProtocolError if the shot cannot be played from what was reported.

    A golfer's equipment may report ball launch, club delivery, or both. Either
    is enough on its own: measured ball data is flown as measured, and club
    delivery alone is enough for the impact model to produce it.
    """
    if not isinstance(metrics, dict):
        raise ProtocolError("metrics must be an object")

    ball = metrics.get("ball_launch_metrics") or {}
    club = metrics.get("club_metrics") or {}
    if not isinstance(ball, dict) or not isinstance(club, dict):
        raise ProtocolError("ball_launch_metrics and club_metrics must be objects")
    if not ball and not club:
        raise ProtocolError(
            "metrics must include ball_launch_metrics, club_metrics, or both"
        )

    _check_bounds("ball_launch_metrics", ball, BALL_METRIC_BOUNDS)
    _check_bounds("club_metrics", club, CLUB_METRIC_BOUNDS)

    missing_ball = [f for f in REQUIRED_BALL_FIELDS if ball.get(f) is None]
    if not missing_ball:
        return  # fully measured

    missing_club = [f for f in REQUIRED_CLUB_FIELDS if club.get(f) is None]
    if missing_club:
        raise ProtocolError(
            "not enough to play a shot: ball_launch_metrics is missing "
            f"{', '.join(missing_ball)}, and modelling it from the swing needs "
            f"club_metrics.{', club_metrics.'.join(missing_club)}"
        )


class GolfServer:
    """Owns one game and the connections playing it."""

    def __init__(self, city=DEFAULT_CITY, shots_per_player=NUM_SHOTS_RANGE,
                 max_players=MAX_PLAYERS, fetch_conditions=None, store=None,
                 mode=DRIVING_RANGE, holes=COURSE_HOLES, room_code=None,
                 game_format=STROKE_PLAY, settings=None, course=None,
                 tees=DEFAULT_TEES):
        if mode not in (DRIVING_RANGE, COURSE_PLAY):
            raise ValueError(f"unknown game mode: {mode!r}")

        # Which of the library's courses this is, if it is one of them. Kept so
        # players can be told where they are playing - a hole is a shape, and
        # the name of the place it is a shape of belongs with it.
        self.course = course
        self.tees = tees or DEFAULT_TEES
        if course is not None and city == DEFAULT_CITY:
            # A course brings its own weather. Kept here rather than in the
            # command line that usually sets it up, because it is a fact about
            # the course rather than a convenience for one caller - anything
            # else building a server would otherwise get the Old Course in the
            # wrong town's wind.
            city = course.location

        # What this round allows. Read from the golfer's settings unless the
        # caller has already decided - a test, or a command line that said so.
        self.settings = validated(settings) if settings is not None else load_settings()
        self.redo_limit = self.settings["redo_limit"]
        self.max_strokes_per_hole = self.settings["max_strokes_per_hole"]

        # A round reachable from another city is reachable by anyone who finds
        # the port. Without a code the server is open, which is fine on a home
        # network and not fine anywhere else.
        self.room_code = str(room_code).strip() if room_code else None
        self.game_format = game_format
        self.default_city = city
        self.city = city
        self.mode = mode
        self.holes = tuple(holes)
        self.shots_per_player = shots_per_player
        self.max_players = max_players
        self._fetch_conditions = fetch_conditions or get_conditions_for_city
        self.store = store if store is not None else SupabaseStore()
        self.game = self._new_game()
        self.writers = {}  # player_id -> StreamWriter
        self.session_id = None
        self.stored_player_ids = {}  # in-game player_id -> database player id
        self.player_keys = {}  # name -> the key that says which golfer they are
        self._write_queue = None
        self._write_task = None

    def _new_game(self, conditions=None):
        """A fresh game of whichever kind this server is hosting."""
        if self.mode == COURSE_PLAY:
            return CoursePlayGame(
                conditions=conditions, holes=self.holes, max_players=self.max_players,
                course_seed=self.city, game_format=self.game_format,
                redo_limit=self.redo_limit,
                max_strokes_per_hole=self.max_strokes_per_hole,
            )
        return DrivingRangeGame(
            conditions=conditions, shots_per_player=self.shots_per_player,
            max_players=self.max_players, redo_limit=self.redo_limit,
        )

    @property
    def playing_a_course(self):
        return self.mode == COURSE_PLAY

    # --- Background writes ---------------------------------------------------

    async def _enqueue_write(self, operation, *args):
        """Queue a database write to happen behind the player's back.

        Shots are broadcast immediately and saved afterwards. A single worker
        drains the queue in order, so a shot cannot be written before the shot
        that preceded it, nor a session closed before its last shot lands.
        """
        if not self.store.enabled:
            return
        if self._write_task is None or self._write_task.done():
            self._write_queue = asyncio.Queue()
            self._write_task = asyncio.create_task(self._drain_writes(self._write_queue))
        self._write_queue.put_nowait((operation, args))

    async def _drain_writes(self, queue):
        """Drain one queue, and only that queue.

        The worker is handed its queue rather than reading the attribute: if a
        replacement queue were ever installed mid-drain, an item taken from the
        old one would be marked done against the new one, leaving the old
        counter permanently above zero and any join() on it waiting forever.
        """
        while True:
            operation, args = await queue.get()
            try:
                await asyncio.to_thread(operation, *args)
            except Exception:  # a write must never take the server with it
                logger.exception("Database write failed")
            finally:
                queue.task_done()

    async def wait_for_writes(self):
        """Block until every queued write has been attempted."""
        if self._write_queue is not None:
            await self._write_queue.join()

    async def close(self):
        """Finish outstanding writes, then stop the worker.

        Without this, stopping the server mid-queue would silently discard
        whatever had not been written yet.
        """
        await self.wait_for_writes()
        if self._write_task is not None:
            self._write_task.cancel()
            try:
                await self._write_task
            except asyncio.CancelledError:
                pass
            self._write_task = None

    # --- Message plumbing ----------------------------------------------------

    async def send(self, player_id, message):
        writer = self.writers.get(player_id)
        if writer is None or writer.is_closing():
            return
        try:
            writer.write(json.dumps(message).encode("utf-8") + b"\n")
            await writer.drain()
        except (ConnectionError, OSError):
            self.writers.pop(player_id, None)

    async def broadcast(self, message):
        """Send once per connection, not once per player.

        Two golfers sharing a bay share a socket, and would otherwise be told
        everything twice.
        """
        sent = set()
        for player_id, writer in list(self.writers.items()):
            if writer is None or id(writer) in sent:
                continue
            sent.add(id(writer))
            await self.send(player_id, message)

    # --- Connection lifecycle ------------------------------------------------

    async def handle_client(self, reader, writer):
        # A connection may hold more than one golfer: two people sharing a bay
        # share a launch monitor, so they share a client.
        seated = []
        try:
            # A browser cannot open a plain socket, so a WebGL build arrives as
            # an HTTP upgrade instead. It is answered and then wrapped, and
            # everything below here reads lines and writes lines exactly as it
            # did before - one server, one protocol, reachable two ways.
            reader, writer = await websocket_wrap(reader, writer)
        except WebSocketFault as fault:
            print(f"Refused a connection: {fault}")
            writer.close()
            return

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8").strip() or "{}")
                except json.JSONDecodeError:
                    await self._send_error(writer, "malformed JSON")
                    continue

                await self._dispatch(message, seated, writer)
        except (ConnectionError, asyncio.IncompleteReadError, OSError):
            pass
        finally:
            await self._disconnect(seated, writer)

    async def _dispatch(self, message, seated, writer):
        message_type = message.get("type")
        try:
            if message_type == "JOIN":
                await self._handle_join(message, seated, writer)
            elif message_type == "SUBMIT_SHOT_DETAILED":
                await self._handle_shot(message, seated)
            elif message_type == "REDO_SHOT":
                await self._handle_redo(seated)
            else:
                raise ProtocolError(f"unknown message type: {message_type!r}")
        except (ProtocolError, GameError) as error:
            await self._send_error(writer, str(error))

    async def _send_error(self, writer, text):
        """Errors go to the socket, not the player.

        A client rejected at JOIN - because the range is full, or the game has
        already started - has no player to address, and silence would look like
        a hang.

        Every error sent before a golfer is seated is final: a wrong room code,
        a full range, a round already under way. The client reads it that way,
        reporting it and stopping rather than waiting for a turn that is not
        coming. An error that a client could recover from must not be sent
        before its WELCOME, or the client will hang up on a round it could
        have joined.
        """
        if writer is None or writer.is_closing():
            return
        payload = {"type": "ERROR", "message": text}
        try:
            writer.write(json.dumps(payload).encode("utf-8") + b"\n")
            await writer.drain()
        except (ConnectionError, OSError):
            pass

    def _check_room_code(self, message):
        """A round reachable from another city needs a door with a lock on it."""
        if not self.room_code:
            return
        if str(message.get("room_code") or "").strip() != self.room_code:
            raise ProtocolError("wrong or missing room code")

    def _named_in(self, message):
        """The golfers this connection is joining as. One, or a whole bay."""
        names = message.get("names")
        if not isinstance(names, list):
            names = [message.get("name")]
        cleaned = [str(name).strip() for name in names if str(name or "").strip()]
        return cleaned or ["Player"]

    async def _handle_join(self, message, seated, writer):
        self._check_room_code(message)

        if seated:
            raise ProtocolError("already joined on this connection")

        names = self._named_in(message)
        rejoining = [name for name in names if self._absent_player(name)]
        if rejoining and len(rejoining) == len(names):
            return await self._handle_rejoin(names, seated, writer)

        handicaps = message.get("handicaps") or {}
        keys = message.get("player_keys") or {}
        if message.get("player_key") and len(names) == 1:
            keys = {names[0]: message["player_key"]}
        self.player_keys.update({name: keys[name] for name in names if keys.get(name)})

        for name in names:
            handicap = handicaps.get(name, message.get("handicap") or 0.0)
            if self.playing_a_course:
                player = self.game.add_player(name, handicap=float(handicap or 0.0))
            else:
                player = self.game.add_player(name)  # a range has no card
            self.writers[player.player_id] = writer
            seated.append(player)

        # Whoever opens the round picks where it is played. Later joiners take
        # the course as they find it, the same as turning up at a real one.
        #
        # A named course is not moveable at all. The Old Course is at St
        # Andrews, and a golfer joining from anywhere else is joining a round
        # being played there - letting them drag the weather to their own town
        # would give two people in one match different wind, which is not a
        # match. Where the golfers are standing has no bearing on it: they are
        # all hitting into the same simulated hole.
        requested = str(message.get("location") or "").strip()
        if requested and self.course is not None:
            await self._send_error(
                writer,
                f"{self.course.name} is at {self.course.location} - "
                "everyone in this round plays the same weather")
        elif requested and len(self.game.players) == len(seated):
            self.city = requested

        for player in seated:
            await self.send(player.player_id, {
                "type": "WELCOME",
                "player_id": player.player_id,
                "player_name": player.name,
                "message": f"Welcome, {player.name}.",
            })
        await self.broadcast({
            "type": "PLAYER_JOINED",
            "name": ", ".join(player.name for player in seated),
            "player_count": len(self.game.players),
            "max_players": self.max_players,
        })

        if len(self.game.players) >= self.max_players:
            await self._start_game()

    def _absent_player(self, name):
        """A player of this round whose connection has dropped."""
        if not self.game.started:
            return None
        for player in self.game.players:
            if player.name.lower() == str(name).strip().lower() \
                    and self.writers.get(player.player_id) is None:
                return player
        return None

    async def _handle_rejoin(self, names, seated, writer):
        """Put a dropped player back in their own seat, mid-round.

        Over the internet a connection will drop; ending everyone's round when
        it does is not acceptable. The seat is held, so they come back to the
        round exactly where they left it.
        """
        for name in names:
            player = self._absent_player(name)
            self.writers[player.player_id] = writer
            seated.append(player)
            await self.send(player.player_id, {
                "type": "WELCOME",
                "player_id": player.player_id,
                "player_name": player.name,
                "message": f"Welcome back, {player.name}. Your round is where you left it.",
            })

        await self.broadcast({
            "type": "PLAYER_REJOINED",
            "name": ", ".join(player.name for player in seated),
        })
        # If the round had been waiting on them, it can go on now.
        await self._announce_turn()

    async def _disconnect(self, seated, writer):
        """Someone's connection went away.

        Mid-round their seat is held rather than the round being torn down:
        connections drop, and everyone else losing their card because of it
        would be worse than waiting. In the lobby, before anything is at
        stake, the seat is given up so it is not held by a ghost.
        """
        departed = [player for player in seated
                    if self.writers.get(player.player_id) is writer]
        if departed:
            mid_round = self.game.started and not self.game.is_over
            for player in departed:
                if mid_round:
                    self.writers[player.player_id] = None  # seat held
                else:
                    self.writers.pop(player.player_id, None)

            await self.broadcast({
                "type": "PLAYER_LEFT",
                "name": ", ".join(player.name for player in departed),
                "may_rejoin": mid_round,
            })
            if mid_round:
                await self.broadcast({
                    "type": "GAME_INTERRUPTED",
                    "message": (f"{', '.join(p.name for p in departed)} dropped out. "
                                "The round waits for them to come back."),
                })
            if not self._anyone_still_here():
                # Nobody is left to wait for, so give the course back.
                await self._reset()
        if not writer.is_closing():
            writer.close()

    def _anyone_still_here(self):
        return any(writer is not None for writer in self.writers.values())

    async def _reset(self):
        """Drop every connection and open a fresh lobby.

        Players are identified by position in the roster, so a game cannot
        outlive its participants. Closing the sockets tells connected clients
        the round is done; the next players to connect get a clean range.
        """
        await self._close_stored_session()
        self.city = self.default_city  # the next round picks its own course
        for player_id, writer in list(self.writers.items()):
            self.writers.pop(player_id, None)
            # A held seat has no socket - its player dropped and had not come
            # back by the time the round was given up on.
            if writer is not None and not writer.is_closing():
                writer.close()
        self.game = self._new_game()

    # --- Game flow -----------------------------------------------------------

    async def _start_game(self):
        # urllib is blocking; keep it off the event loop.
        self.game.conditions = await asyncio.to_thread(self._fetch_conditions, self.city)
        if self.playing_a_course:
            # The course is only known once the first player has chosen it, so
            # the puddles are keyed now rather than at construction.
            self.game.course_seed = self.city
        self.game.start()
        await self._open_stored_session()

        conditions = self.game.conditions
        start = {
            "type": "GAME_START",
            "game_mode": self.mode,
            "course_location": conditions.location_name,
            "weather_description": conditions.description,
            "wind_conditions": list(conditions.wind_vector_mph),
        }
        if self.playing_a_course:
            start["num_holes_course"] = len(self.holes)
            start["scoring_format"] = self.game_format
            if self.course is not None:
                # Where they are playing, and honestly what it is modelled on.
                # A golfer standing on the 1st should be told the name of the
                # place, and told which real course it answers to.
                start["course"] = {
                    "name": self.course.name,
                    "after": self.course.after,
                    "character": self.course.character,
                    "par": self.course.par,
                    "yards": round(self.course.yards),
                    "card_source": self.course.card_source,
                    # Which tees this round is off, and which others exist.
                    # A golfer looking at a card should know whether they are
                    # playing the members' course or the championship one.
                    "tees": self.tees,
                    "tees_available": list(self.course.tee_names()),
                }
        else:
            start["num_shots_per_player_range"] = self.shots_per_player

        # What the weather has left lying about, if anything.
        underfoot = describe_surface(conditions, self.city)
        if underfoot:
            start["course_condition"] = underfoot
        await self.broadcast(start)

        # Tell anyone the model has been fitted to that it has been.
        for player in self.game.players:
            if player.trims:
                await self.send(player.player_id, {
                    "type": "MODEL_FITTED",
                    "message": "Ball flight modelled from your own shot history.",
                    "trims": player.trims,
                })

        if self.playing_a_course:
            await self._announce_hole()
        await self._announce_turn()

    async def _announce_hole(self):
        hole = self.game.current_hole
        if hole is None:
            return
        await self.broadcast({
            "type": "HOLE_START_INFO",
            "hole_number": hole.number,
            "length_yards": hole.length_yards,
            "par": hole.par,
            "stroke_index": hole.stroke_index,
            # The ground itself, so a client can draw the hole rather than
            # describe it. Sent once when the hole opens: it does not change
            # while the hole is played, and nobody wants it on every shot.
            "layout": describe_layout(hole.layout),
        })

    async def _open_stored_session(self):
        """Record the round and its players. Failures cost history, not play.

        These two writes are awaited rather than queued, because every later
        shot needs the ids they return. Nobody is mid-swing at game start, so
        the latency lands where it cannot be felt.
        """
        if not self.store.enabled:
            return
        self.session_id = await asyncio.to_thread(
            self.store.start_session, self.game.conditions, self.mode,
            self.shots_per_player,
        )
        for player in self.game.players:
            stored_id = await asyncio.to_thread(
                self.store.find_or_create_player, player.name,
                self.player_keys.get(player.name),
            )
            self.stored_player_ids[player.player_id] = stored_id
            player.trims = await self._fitted_trims(stored_id)

    async def _fitted_trims(self, stored_player_id):
        """What this golfer's own shots say the model should do for them.

        Only affects numbers the simulator has to model. A golfer whose
        monitor measures the ball is unaffected, because there is nothing to
        adjust - and a golfer with too little history gets the defaults rather
        than a fit taken from noise.
        """
        if stored_player_id is None:
            return {}
        history = await asyncio.to_thread(
            self.store.shots_for_player, stored_player_id
        )
        return fit_from_history(history)

    async def _close_stored_session(self):
        if self.session_id is not None:
            await self._enqueue_write(self.store.end_session, self.session_id)
        self.session_id = None
        self.stored_player_ids = {}

    async def _announce_turn(self):
        player = self.game.current_player
        if player is None:
            return

        conditions = self.game.conditions
        your_turn = {
            "type": "YOUR_TURN",
            "player_name": player.name,
            "game_mode": self.mode,
            "weather_description": conditions.description,
            "wind_conditions": list(conditions.wind_vector_mph),
        }

        if self.playing_a_course:
            hole = self.game.current_hole
            round_or_hole = hole.number
            your_turn.update({
                "hole_number": hole.number,
                "hole_length": hole.length_yards,
                "hole_par": hole.par,
                "ball_position_on_hole": list(player.ball_position),
                "distance_to_pin_yards": self.game.distance_to_pin(player),
                "current_strokes": player.strokes,
                # What they are playing from, so they can correct it if we
                # have it wrong - they are standing over the ball, we are not.
                "lie_type": self.game.lie_for(player),
            })
        else:
            round_or_hole = self.game.shot_number
            your_turn["shot_number"] = self.game.shot_number

        await self.broadcast({
            "type": "TURN_UPDATE",
            "mode": self.mode,
            "current_round_or_hole": round_or_hole,
            "current_player_name": player.name,
        })
        await self.send(player.player_id, your_turn)

    async def _handle_redo(self, seated):
        """Give the last shot back to whoever played it.

        Asked for by a connection rather than by a player: a shared bay speaks
        for everyone sitting at it, and the golfer reaching for the button is
        the one who just hit the bad one.
        """
        if not seated:
            raise ProtocolError("join before taking a shot back")

        ours = {player.player_id for player in seated}
        last = getattr(self.game, "_undo", None)
        if last is None:
            raise GameError("there is no shot to play again")
        if last["player_id"] not in ours:
            raise ProtocolError("that shot was not played from this bay")

        player = self.game.redo_shot(last["player_id"])
        await self.broadcast({
            "type": "SHOT_TAKEN_BACK",
            "player_id": player.player_id,
            "player_name": player.name,
            "message": f"{player.name} is playing that one again.",
        })
        await self._announce_turn()

    async def _handle_shot(self, message, seated):
        if not seated:
            raise ProtocolError("join before submitting a shot")
        current = self.game.current_player
        if current is None:
            raise GameError("game is over")

        # A shared bay sends one shot from one monitor; it belongs to whichever
        # of its golfers is up.
        if not any(player.player_id == current.player_id for player in seated):
            raise ProtocolError(f"it is {current.name}'s turn")
        player = current

        metrics = message.get("metrics")
        validate_swing_metrics(metrics)
        outcome = self.game.submit_shot(metrics)

        ball_flight = dict(outcome.ball_flight)
        ball_flight["trajectory_points"] = decimate_trajectory(
            outcome.ball_flight["trajectory_points"]
        )

        await self._enqueue_write(
            self.store.record_shot,
            self.session_id,
            self.stored_player_ids.get(outcome.player_id),
            getattr(outcome, "shot_number", None) or outcome.strokes_this_hole,
            metrics,
            ball_flight,
            outcome.feedback,
            outcome.ball_launch,
            outcome.delivery,
            outcome.modelled_fields,
            outcome.body,
            getattr(outcome, "hole_number", None),
        )

        # Report the resolved numbers, so a golfer whose monitor reported only
        # the swing sees the ball launch that was played rather than blanks.
        resolved_metrics = dict(metrics)
        resolved_metrics["ball_launch_metrics"] = outcome.ball_launch

        await self.broadcast({
            "type": "SHOT_RESULT",
            "player_id": outcome.player_id,
            "player_name": player.name,
            "input_metrics_log": resolved_metrics,
            "results_ball_flight": ball_flight,
            "feedback": outcome.feedback,
            "modelled_ball_fields": list(outcome.modelled_fields),
            "delivery_analysis": outcome.delivery,
            "body_analysis": outcome.body,
            "mode_specific_data": self._mode_specific(outcome),
        })

        await self._after_shot()

    def _mode_specific(self, outcome):
        """The part of a shot result that only makes sense in one mode."""
        if self.playing_a_course:
            return {
                "hole_number": outcome.hole_number,
                "strokes_this_hole": outcome.strokes_this_hole,
                "new_ball_position_abs": list(outcome.ball_position),
                "distance_to_pin_yards": outcome.distance_to_pin_yards,
                "has_holed_out": outcome.has_holed_out,
                # Where to put the trajectory and which way to turn it. The
                # engine flies every ball from the origin down its own axis,
                # so without these a client draws the second shot of a hole
                # starting from the tee and pointing down the first one.
                "played_from": list(outcome.played_from),
                "aim_bearing_deg": outcome.aim_bearing_deg,
            }
        return {
            "shot_number": outcome.shot_number,
            "longest_shot_so_far": outcome.longest_shot_yards,
        }

    async def _after_shot(self):
        """Move the game on: next player, next hole, or done."""
        if self.playing_a_course and self.game.is_hole_complete:
            hole = self.game.current_hole
            await self.broadcast({
                "type": "HOLE_END_SUMMARY",
                "hole_number": hole.number,
                "summary": self.game.hole_summary(hole.number),
            })
            # Where everyone stands, which a card of strokes does not say.
            await self.broadcast({
                "type": "LEADERBOARD",
                "standings": self.game.standings(),
                "lines": describe_standings(self.game.standings()),
            })
            self.game.advance_hole()
            if not self.game.is_over:
                await self._announce_hole()

        if self.game.is_over:
            await self.broadcast({
                "type": "GAME_OVER",
                "mode": self.mode,
                "summary": self.game.summary(),
            })
            # GAME_OVER has been drained to each socket, so closing now still
            # delivers it. The round is finished; open up for the next.
            await self._reset()
        else:
            await self._announce_turn()


async def main(host=DEFAULT_HOST, port=DEFAULT_PORT, city=DEFAULT_CITY,
               mode=DRIVING_RANGE, players=MAX_PLAYERS, room_code=None,
               game_format=STROKE_PLAY, holes=None, settings=None,
               course_name=None, tees=None):
    # The golfer's settings, unless the command line said otherwise. Flags win,
    # because somebody typing one now means it more than a file typed once.
    chosen = load_settings()
    chosen.update({
        # A flag left off says nothing and the settings stand. A flag set to
        # "none" says no limit, which is a value in its own right.
        key: (None if value == "unlimited" else value)
        for key, value in (settings or {}).items() if value is not None
    })
    chosen = validated(chosen)

    # A named course brings its own holes and its own weather. Playing Auld
    # Links should mean playing in whatever St Andrews is doing today, and
    # nobody should have to type the town in as well as the course.
    played = find_course(course_name) if course_name else None
    if course_name and played is None:
        raise ValueError(
            f"no course called {course_name!r}. Try one of: "
            + ", ".join(sorted(c.name for c in COURSES))
        )
    if played is not None and tees:
        # A course is not one length. Raised rather than shrugged at: a golfer
        # who asked for the championship tees and got the members' would find
        # out four hundred yards into the round.
        played = played.from_tees(tees)
    if played is not None:
        mode = COURSE_PLAY   # the weather follows the course, in the server

    server_state = GolfServer(
        city=city, mode=mode, max_players=players, room_code=room_code,
        game_format=game_format, settings=chosen, course=played,
        tees=tees or DEFAULT_TEES,
        holes=(played.holes if played
               else holes_for(holes or chosen["holes"])),
    )
    server = await asyncio.start_server(server_state.handle_client, host, port)
    what = (f"{played.name} off the {tees or DEFAULT_TEES} tees - "
            f"{len(played.holes)} holes, par {played.par}, "
            f"{played.yards:.0f} yards" if played
            else f"{len(server_state.holes)}-hole course"
            if server_state.playing_a_course else "Driving range")
    # The server's own city, not the one asked for: a named course overrides it
    # with its own, and printing the request rather than the answer told a
    # golfer they were playing North Carolina weather in St Andrews.
    print(f"{what} open on {host}:{port} - {server_state.city} "
          "unless a player picks elsewhere")
    if server_state.room_code:
        print(f"Room code: {server_state.room_code} - players need this to join")
    else:
        print("No room code set. Anyone who can reach this port can join.")
    for line in describe_settings(server_state.settings):
        print(f"  {line}")
    print(f"Waiting for {players} players...")
    try:
        async with server:
            await server.serve_forever()
    finally:
        await server_state.close()


def _option(argv, flag, fallback=None):
    """Read `--flag value` out of the command line."""
    if flag in argv:
        position = argv.index(flag) + 1
        if position < len(argv):
            return argv[position]
    return fallback


def _count(argv, flag):
    """A number off the command line, or None if it was not given.

    None means the flag was left off, so whatever is in the settings stands.
    "none" or "open" means no limit, which is a different thing entirely and
    has to be sayable - otherwise a golfer with a limit saved can never turn
    it off for one round without editing a file.
    """
    raw = _option(argv, flag)
    if raw is None:
        return None
    if str(raw).strip().lower() in ("none", "open", "unlimited", "off"):
        return "unlimited"
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{flag} needs a number, or 'none' for no limit")


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    try:
        asyncio.run(main(
            mode=COURSE_PLAY if "--course" in argv else DRIVING_RANGE,
            # --at "Auld Links" plays one of the library's courses, which
            # brings its own holes and the weather where it stands.
            course_name=_option(argv, "--at"),
            # --tees championship, where a course offers more than one set.
            tees=_option(argv, "--tees"),
            players=int(_option(argv, "--players", MAX_PLAYERS)),
            room_code=_option(argv, "--room-code"),
            game_format=_option(argv, "--format", STROKE_PLAY),
            holes=int(_option(argv, "--holes")) if "--holes" in argv else None,
            settings={
                # "none" says no limit, which is not the same as leaving the
                # flag off - that says "whatever my settings already are".
                "redo_limit": _count(argv, "--redos"),
                "max_strokes_per_hole": _count(argv, "--max-strokes"),
                "range_shots_per_player": _count(argv, "--shots"),
            },
        ))
    except KeyboardInterrupt:
        print("\nServer shutting down.")
    except ValueError as error:
        # A mistyped round is worth saying plainly rather than a traceback.
        print(f"Cannot start: {error}")
