"""What this server sends, declared once, so a client can be generated from it.

Every field name the Unity client reads was typed out by hand - fifty-one of
them across four files - and a misspelling does not fail. C# asks JSON for a
field that is not there, gets nothing back, and draws an empty hole with no
error anywhere. A script existed to catch that by pulling the names back out of
the source and comparing them, which is a compiler's job being done by a
regular expression.

So the messages are declared here and the C# is generated from the declaration.
A field renamed on the server changes the generated class, and any C# still
using the old name stops compiling. That is the whole point: the mistake
becomes impossible rather than merely detectable.

A declaration is a second description of something the code already does, and a
second description can go stale. So it is checked against messages captured off
a real round: anything the server actually sent that is not declared here is an
error. The declaration cannot quietly fall behind what is on the wire.
"""

from dataclasses import dataclass

# The C# type each kind of value becomes. Nullable where the server may send
# null - a hole with no boundary sends null for its out of bounds distance, and
# a client reading that as zero fences every open links hole at nothing.
TEXT, NUMBER, WHOLE, FLAG = "string", "float?", "int?", "bool?"
TEXT_LIST, NUMBER_LIST = "List<string>", "List<float>"
NUMBER_GRID = "List<List<float>>"     # a trajectory: points of ten numbers


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    about: str = ""


@dataclass(frozen=True)
class Message:
    """One kind of message, and everything it may carry."""

    kind: str
    about: str
    fields: tuple
    # Nested objects, by the field that holds them.
    parts: tuple = ()


@dataclass(frozen=True)
class Part:
    """An object inside a message, such as a hole's layout."""

    field: str
    name: str
    about: str
    fields: tuple
    parts: tuple = ()
    repeated: bool = False


GREEN = Part(
    field="green", name="GreenShape", repeated=False,
    about="Where the green is and which way it falls, so a putt can be read.",
    fields=(
        Field("x", NUMBER), Field("z", NUMBER),
        Field("radius_yards", NUMBER),
        Field("falls_towards_deg", NUMBER, "0 is towards the pin, 90 its right"),
        Field("fall_per_yard", NUMBER, "gradient; 0.02 is a gentle green"),
        Field("stimp", NUMBER, "how quick it runs"),
    ),
)

WAYPOINT = Part(
    field="centreline", name="Waypoint", repeated=True,
    about="A point on the hole's centreline, and how high the ground is there.",
    fields=(Field("x", NUMBER), Field("z", NUMBER), Field("height_yards", NUMBER)),
)

HAZARD = Part(
    field="hazards", name="Hazard", repeated=True,
    about="Something the designer put there, and what it costs.",
    fields=(
        Field("kind", TEXT), Field("lie_type", TEXT),
        Field("x", NUMBER), Field("z", NUMBER), Field("radius_yards", NUMBER),
        Field("costs_a_stroke", FLAG, "water and out of bounds are not lies"),
    ),
)

LAYOUT = Part(
    field="layout", name="HoleGround",
    about="The ground of one hole: everything needed to draw it.",
    fields=(
        Field("length_yards", NUMBER), Field("fairway_width_yards", NUMBER),
        Field("rough_band_yards", NUMBER),
        Field("out_of_bounds_yards", NUMBER, "null on a hole with no boundary"),
        Field("out_of_bounds_side", WHOLE, "0 both, +1 right, -1 left"),
        Field("turn_degrees", NUMBER), Field("elevation_change_yards", NUMBER),
    ),
    parts=(WAYPOINT, HAZARD, GREEN),
)

FLIGHT = Part(
    field="results_ball_flight", name="BallFlight",
    about="One shot as the physics flew it.",
    fields=(
        Field("total_carry_yards", NUMBER),
        Field("total_distance_with_roll_yards", NUMBER),
        Field("max_height_yards", NUMBER), Field("final_z_yards", NUMBER),
        Field("final_x_yards", NUMBER, "down the line it was struck on"),
        Field("flight_time_sec", NUMBER, "to first ground contact"),
        Field("total_time_sec", NUMBER, "until it stopped rolling"),
        Field("simulation_capped", FLAG, "the integration hit its limit"),
        Field("trajectory_points", NUMBER_GRID,
              "ten numbers a point: x y z, vx vy vz, t, flight/bounce/roll"),
    ),
)

SEQUENCE_GAPS = Part(
    field="sequence_gaps_ms", name="SequenceGaps",
    about="How long each segment waited for the one below it.",
    fields=(Field("thorax_after_pelvis_ms", NUMBER),
            Field("lead_arm_after_thorax_ms", NUMBER),
            Field("club_after_lead_arm_ms", NUMBER)),
)

SPEED_GAINS = Part(
    field="speed_gain_ratios", name="SpeedGains",
    about="What each segment multiplied the one below it by.",
    fields=(Field("thorax_over_pelvis", NUMBER),
            Field("lead_arm_over_thorax", NUMBER),
            Field("club_over_lead_arm", NUMBER)),
)

BODY = Part(
    field="body_analysis", name="BodyReading",
    about="How the golfer moved, as exercise science measures an athlete.",
    fields=(
        Field("sequence_order", TEXT_LIST, "which segment peaked, in order"),
        Field("is_proximal_to_distal", FLAG, "did it run from the ground up"),
        Field("weak_transfers", TEXT_LIST),
        Field("segments_peaking_together", TEXT_LIST),
        Field("x_factor_deg", NUMBER), Field("x_factor_vs_typical", NUMBER),
        Field("x_factor_stretch_deg", NUMBER),
        Field("stretches_in_transition", FLAG),
        Field("tempo_ratio", NUMBER), Field("tempo_vs_typical", NUMBER),
        Field("total_swing_time_ms", NUMBER),
        Field("peak_vertical_force_bw", NUMBER), Field("force_vs_typical", NUMBER),
        Field("peak_force_time_ms", NUMBER),
        Field("lead_pressure_at_impact_pct", NUMBER),
        Field("force_peaks_before_impact", FLAG),
        Field("pressure_shifted_to_lead_side", FLAG),
        Field("pelvis_thrust_mm", NUMBER), Field("pelvis_sway_mm", NUMBER),
        Field("early_extension", FLAG), Field("excessive_sway", FLAG),
        Field("side_bend_at_impact_deg", NUMBER),
        Field("loses_side_bend", FLAG, "standing up out of the shot"),
        Field("head_movement_mm", NUMBER),
    ),
    parts=(SEQUENCE_GAPS, SPEED_GAINS),
)

COURSE = Part(
    field="course", name="CourseCard",
    about="Where the round is being played, and what it is modelled on.",
    fields=(
        Field("name", TEXT), Field("after", TEXT), Field("character", TEXT),
        Field("par", WHOLE), Field("yards", WHOLE), Field("card_source", TEXT),
        Field("tees", TEXT), Field("tees_available", TEXT_LIST),
    ),
)

WHERE_THE_SHOT_WENT = Part(
    field="mode_specific_data", name="ShotOnHole",
    about="What a shot did to a round, when a course is being played.",
    fields=(
        Field("hole_number", WHOLE), Field("strokes_this_hole", WHOLE),
        Field("new_ball_position_abs", NUMBER_LIST),
        Field("distance_to_pin_yards", NUMBER),
        Field("has_holed_out", FLAG),
        Field("played_from", NUMBER_LIST, "where the trajectory starts"),
        Field("aim_bearing_deg", NUMBER, "the line it is turned onto"),
        Field("shot_number", WHOLE), Field("longest_shot_so_far", NUMBER),
    ),
)

MESSAGES = (
    Message(kind="WELCOME", about="A golfer has a seat.",
            fields=(Field("player_id", WHOLE), Field("player_name", TEXT),
                    Field("message", TEXT))),
    Message(kind="PLAYER_JOINED", about="Somebody else arrived.",
            fields=(Field("name", TEXT), Field("player_count", WHOLE),
                    Field("max_players", WHOLE))),
    Message(kind="GAME_START", about="The round is on.",
            fields=(Field("game_mode", TEXT), Field("course_location", TEXT),
                    Field("weather_description", TEXT),
                    Field("wind_conditions", NUMBER_LIST),
                    Field("course_condition", TEXT),
                    Field("num_holes_course", WHOLE),
                    Field("scoring_format", TEXT),
                    Field("num_shots_per_player_range", WHOLE)),
            parts=(COURSE,)),
    Message(kind="HOLE_START_INFO", about="Standing on a tee.",
            fields=(Field("hole_number", WHOLE), Field("length_yards", NUMBER),
                    Field("par", WHOLE), Field("stroke_index", WHOLE)),
            parts=(LAYOUT,)),
    Message(kind="YOUR_TURN", about="It is this golfer's shot.",
            fields=(Field("player_name", TEXT), Field("player_id", WHOLE),
                    Field("game_mode", TEXT), Field("shot_number", WHOLE),
                    Field("weather_description", TEXT),
                    Field("wind_conditions", NUMBER_LIST),
                    Field("hole_number", WHOLE), Field("hole_length", NUMBER),
                    Field("hole_par", WHOLE),
                    Field("ball_position_on_hole", NUMBER_LIST),
                    Field("distance_to_pin_yards", NUMBER),
                    Field("current_strokes", WHOLE), Field("lie_type", TEXT))),
    Message(kind="TURN_UPDATE", about="Whose turn it is now.",
            fields=(Field("mode", TEXT), Field("current_player_name", TEXT),
                    Field("current_round_or_hole", WHOLE))),
    Message(kind="SHOT_RESULT", about="A ball has been struck.",
            fields=(Field("player_id", WHOLE), Field("player_name", TEXT),
                    Field("feedback", TEXT_LIST),
                    Field("modelled_ball_fields", TEXT_LIST)),
            parts=(FLIGHT, BODY, WHERE_THE_SHOT_WENT)),
    Message(kind="SHOT_TAKEN_BACK", about="A shot is being played again.",
            fields=(Field("player_id", WHOLE), Field("player_name", TEXT),
                    Field("message", TEXT))),
    Message(kind="ERROR", about="The server will not do that.",
            fields=(Field("message", TEXT),)),
)

BY_KIND = {message.kind: message for message in MESSAGES}

# Fields that appear on the wire and are deliberately not given to the client.
# Named rather than ignored, so the check below stays total: anything sent and
# not listed here has to be declared.
NOT_FOR_THE_CLIENT = {
    "type",                    # the client dispatches on it before parsing
    "input_metrics_log",       # what was sent up, echoed back
    "delivery_analysis",       # read as feedback lines instead
    "standings", "lines", "summary", "trims",  # scoreboard shapes
    "may_rejoin", "player_keys", "handicaps", "body_metrics",
}


def declared_fields(message):
    """Every field name this message declares, nested parts included."""
    names = {field.name for field in message.fields}
    for part in message.parts:
        names.add(part.field)
        names |= _part_fields(part)
    return names


def _part_fields(part):
    names = {field.name for field in part.fields}
    for inner in part.parts:
        names.add(inner.field)
        names |= _part_fields(inner)
    return names
