"""Persistence for rounds and shots, via Supabase's PostgREST API.

Nothing here is allowed to interrupt a game. Every method returns None or
False on failure and logs it. A dropped connection costs you a saved shot,
not your round.

Configuration comes from two environment variables:

    SUPABASE_URL          https://<project-ref>.supabase.co
    SUPABASE_SERVICE_KEY  a service role key

With either missing, the store is simply disabled and the simulator runs
exactly as it did before persistence existed.
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

URL_ENV = "SUPABASE_URL"
SERVICE_KEY_ENV = "SUPABASE_SERVICE_KEY"
DEFAULT_TIMEOUT_SEC = 10

TRANSPORT_FAILURE = 0  # no HTTP status: the request never completed
CONFLICT = 409

# Metrics promoted out of jsonb into their own columns, so a golfer's history
# can be grouped and trended per club without unpacking blobs.
CLUB_COLUMNS = (
    "club_speed_mph",
    "attack_angle_deg",
    "dynamic_loft_deg",
    "club_path_deg",
    "swing_direction_deg",
    "face_angle_deg",
    "closure_rate_deg_per_sec",
    "impact_offset_toe_in",
    "impact_offset_high_in",
    "dynamic_lie_deg",
)
BALL_COLUMNS = (
    "ball_speed_mph",
    "launch_angle_deg",
    "launch_direction_deg",
    "back_spin_rpm",
    "side_spin_rpm",
    "spin_axis_deg",
)


def http_request(method, url, headers, body=None, timeout=DEFAULT_TIMEOUT_SEC):
    """Perform one request. Returns (status_code, parsed_body).

    A status of 0 means the request never reached the server.
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        logger.warning("Supabase %s %s returned %s: %s", method, url, error.code, detail)
        return error.code, None
    except (OSError, ValueError) as error:
        logger.warning("Supabase %s %s failed: %s", method, url, error)
        return TRANSPORT_FAILURE, None


class SupabaseStore:
    """Writes rounds and shots. Disabled, harmlessly, when unconfigured."""

    def __init__(self, url=None, service_key=None, request=http_request):
        # None means "look at the environment"; an empty string means "no
        # database, deliberately". Conflating the two let anything asking for
        # a disabled store - a test, a verification script - quietly pick up
        # live credentials from the environment and write to the real thing.
        self.url = (os.environ.get(URL_ENV, "") if url is None else url).rstrip("/")
        self.service_key = (
            os.environ.get(SERVICE_KEY_ENV, "") if service_key is None else service_key
        )
        self._request = request

    @property
    def enabled(self):
        return bool(self.url and self.service_key)

    # --- Plumbing ------------------------------------------------------------

    def _headers(self, prefer=None):
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _endpoint(self, table, **params):
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        return f"{self.url}/rest/v1/{table}{query}"

    def _insert(self, table, row):
        """Insert one row and return its id, or None."""
        status, data = self._request(
            "POST",
            self._endpoint(table),
            self._headers(prefer="return=representation"),
            [row],
        )
        if data:
            return data[0].get("id")
        if status not in (TRANSPORT_FAILURE, CONFLICT):
            logger.warning("Insert into %s returned %s with no row", table, status)
        return None

    def _select_one(self, table, **params):
        params.setdefault("limit", 1)
        status, data = self._request("GET", self._endpoint(table, **params), self._headers())
        return data[0] if data else None

    # --- Players -------------------------------------------------------------

    def find_or_create_player(self, name, player_key=None):
        """Resolve a golfer, creating them on first appearance.

        A key identifies them; the name is only what shows on a leaderboard.
        Given a key, the name is kept up to date, so a golfer who starts
        entering themselves differently keeps one history rather than two.

        Without a key - a golfer playing locally who has never needed one -
        the old behaviour stands and the name is matched case-insensitively.
        """
        if not self.enabled:
            return None

        if player_key:
            return self._player_by_key(player_key, name)

        # Names are no longer unique in the database - they were never an
        # identity - so the capitalisation check has to happen here rather than
        # falling out of a constraint violation. Without it "Bob" and "bob"
        # become two golfers.
        existing = self._select_one("players", select="id", name=f"eq.{name}") or \
            self._select_one("players", select="id", name=f"ilike.{name}")
        if existing:
            return existing.get("id")
        return self._insert("players", {"name": name})

    def _player_by_key(self, player_key, name):
        """The golfer holding this key, created if it is new to us."""
        existing = self._select_one(
            "players", select="id,name", player_key=f"eq.{player_key}")
        if existing:
            if name and existing.get("name") != name:
                self._request(
                    "PATCH",
                    self._endpoint("players", id=f"eq.{existing['id']}"),
                    self._headers(),
                    {"name": name},
                )
            return existing.get("id")

        created = self._insert("players", {"name": name, "player_key": player_key})
        if created is not None:
            return created
        # Someone else claimed the key between our look-up and our insert.
        raced = self._select_one("players", select="id", player_key=f"eq.{player_key}")
        return raced.get("id") if raced else None

    def find_player(self, name, player_key=None):
        """Look a golfer up without creating them."""
        if not self.enabled:
            return None
        if player_key:
            found = self._select_one("players", select="id",
                                     player_key=f"eq.{player_key}")
            return found.get("id") if found else None
        existing = self._select_one("players", select="id", name=f"eq.{name}") or \
            self._select_one("players", select="id", name=f"ilike.{name}")
        return existing.get("id") if existing else None

    # --- Reading a history back ----------------------------------------------

    HISTORY_COLUMNS = (
        "id,created_at,session_id,hole_number,shot_number,club_label,lie_type,"
        "carry_yards,total_yards,apex_yards,side_yards,smash_factor,spin_loft_deg,"
        "club_speed_mph,attack_angle_deg,dynamic_loft_deg,club_path_deg,"
        "swing_direction_deg,face_angle_deg,closure_rate_deg_per_sec,"
        "impact_offset_toe_in,impact_offset_high_in,dynamic_lie_deg,"
        "ball_speed_mph,launch_angle_deg,launch_direction_deg,back_spin_rpm,"
        "side_spin_rpm,spin_axis_deg,modelled_ball_fields"
    )

    def shots_for_player(self, player_id, limit=2000):
        """A golfer's shots, oldest first, without the heavy blobs.

        The trajectory and the raw metric blocks are deliberately left behind:
        a history query wants the numbers, not a few hundred kilobytes of
        flight path per shot.
        """
        if not self.enabled or player_id is None:
            return []
        status, rows = self._request(
            "GET",
            self._endpoint(
                "shots", select=self.HISTORY_COLUMNS,
                player_id=f"eq.{player_id}", order="id.asc", limit=limit,
            ),
            self._headers(),
        )
        return rows or []

    def history_for(self, name, limit=2000):
        """Every shot a named golfer has hit."""
        return self.shots_for_player(self.find_player(name), limit=limit)

    # --- Sessions ------------------------------------------------------------

    def start_session(self, conditions, game_mode, shots_per_player):
        if not self.enabled:
            return None
        return self._insert("sessions", {
            "game_mode": game_mode,
            "course_location": conditions.location_name,
            "weather_description": conditions.description,
            "air_density": conditions.air_density,
            "wind_vector_mph": list(conditions.wind_vector_mph),
            "is_live_weather": conditions.is_live,
            "shots_per_player": shots_per_player,
        })

    def end_session(self, session_id):
        if not self.enabled or session_id is None:
            return False
        status, _ = self._request(
            "PATCH",
            self._endpoint("sessions", id=f"eq.{session_id}"),
            self._headers(),
            {"ended_at": datetime.now(timezone.utc).isoformat()},
        )
        return 200 <= status < 300

    # --- Shots ---------------------------------------------------------------

    def record_shot(self, session_id, player_id, shot_number, swing_metrics,
                    ball_flight, feedback, ball_launch=None, delivery=None,
                    modelled_fields=(), body=None, hole_number=None):
        """Save one shot, with the swing that produced it broken out.

        The headline club-delivery and ball-launch numbers get their own
        columns so a golfer's history is queryable per club, rather than
        locked inside jsonb.
        """
        if not self.enabled or session_id is None or player_id is None:
            return None

        club_metrics = swing_metrics.get("club_metrics") or {}
        context = swing_metrics.get("shot_context") or {}
        ball = ball_launch if ball_launch is not None else (
            swing_metrics.get("ball_launch_metrics") or {}
        )
        delivery = delivery or {}

        row = {
            "session_id": session_id,
            "player_id": player_id,
            # On the range this counts shots; on a course it counts strokes on
            # the hole, and hole_number says which hole they were on.
            "shot_number": shot_number,
            "hole_number": hole_number,
            # No club label of ours is written. The delivery columns below are
            # the measurement; grouping shots into a golfer's bag is done over
            # their history, not guessed one shot at a time. Any name the
            # device itself reported is kept verbatim, for display only.
            "club_label": context.get("club"),
            "lie_type": context.get("lie_type"),
            "carry_yards": ball_flight.get("total_carry_yards"),
            "total_yards": ball_flight.get("total_distance_with_roll_yards"),
            "apex_yards": ball_flight.get("max_height_yards"),
            "side_yards": ball_flight.get("final_z_yards"),
            "smash_factor": delivery.get("smash_factor") or ball.get("smash_factor"),
            "spin_loft_deg": delivery.get("spin_loft_deg"),
            "swing_metrics": swing_metrics,
            "ball_flight": ball_flight,
            "ball_launch": ball,
            "delivery": delivery,
            "modelled_ball_fields": list(modelled_fields),
            "feedback": feedback,
            # Body movement, where the golfer has capture or pressure data.
            "body_metrics": swing_metrics.get("body_metrics") or None,
            "body_analysis": body or None,
            "x_factor_deg": (body or {}).get("x_factor_deg"),
            "tempo_ratio": (body or {}).get("tempo_ratio"),
            "peak_vertical_force_bw": (body or {}).get("peak_vertical_force_bw"),
            "sequence_is_proximal_to_distal": (body or {}).get("is_proximal_to_distal"),
        }
        for field in CLUB_COLUMNS:
            row[field] = club_metrics.get(field)
        for field in BALL_COLUMNS:
            row[field] = ball.get(field)
        return self._insert("shots", row)
