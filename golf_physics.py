"""Golf ball flight simulation.

Single source of truth for trajectory calculation, wind conversion and swing
feedback. The aerodynamic model covers Reynolds-dependent drag, Magnus lift
from spin, ground bounce and roll.

Coordinate system (right-handed golfer assumed):
    X  downrange, positive away from the tee
    Y  vertical, positive up
    Z  right of downrange, positive is a slice for a RH golfer
"""

import numpy as np

# --- Ball properties ---
BALL_MASS = 0.04593  # kg (standard golf ball)
BALL_RADIUS = 0.021335  # m
BALL_AREA = np.pi * BALL_RADIUS**2  # cross-sectional area, m^2

# --- Air properties ---
DEFAULT_AIR_DENSITY = 1.225  # kg/m^3
AIR_VISCOSITY = 1.81e-5  # kg/(m.s), dynamic viscosity of air at ~15-20C

# --- Simulation parameters ---
DT = 0.005  # integration time step, seconds
GRAVITY = 9.81  # m/s^2
MAX_SIM_TIME_SEC = 20.0  # hard stop for a single shot

# --- Aerodynamic coefficients ---
# Lift and drag both follow the shapes measured in golf ball wind tunnel work
# rather than straight lines with a ceiling bolted on.
#
#   Cl = CL_MAX * S / (CL_HALF_SPIN_RATIO + S)
#
# Lift rises steeply with spin at first and then plateaus, which is what stops
# a wedge ballooning without needing an arbitrary cap. CL_HALF_SPIN_RATIO is
# the spin ratio at which lift reaches half its maximum.
LIFT_COEFFICIENT_MAX = 0.36
CL_HALF_SPIN_RATIO = 0.11

#   Cd = DRAG_BASE + DRAG_PER_SPIN_RATIO * S
#
# Drag climbs steadily with spin: a spinning ball drags more than a smooth
# one. How much it climbs is what governs the balance between drag and lift,
# and therefore how much further the ball flies in thin air.
DRAG_COEFFICIENT_BASE = 0.22
DRAG_PER_SPIN_RATIO = 0.22
DRAG_COEFFICIENT_AT_REST = 0.5
# Dimples exist to trip the boundary layer early, so a golf ball's drag crisis
# happens near Re 5e4 - far below a smooth sphere's. Setting this too high
# invents drag through the whole back half of every flight, and does it worse
# in thin air, where the same speed gives a lower Reynolds number. That
# cancels the extra distance altitude should give.
DRAG_TRANSITION_REYNOLDS = 5.0e4

# --- Ground interaction ---
DEFAULT_COR_GROUND = 0.5  # coefficient of restitution for bounce
DEFAULT_ROLLING_FRICTION_COEFF = 0.12  # fitted so a 10 mph putt rolls ~9 yards
MIN_ROLL_SPEED = 0.05  # m/s, speed below which rolling stops
MIN_FLIGHT_SPEED_FOR_AERO = 0.1  # m/s, below which aero effects are negligible
# Below this rebound speed the ball cannot rise by even its own radius, so it is
# rolling rather than bouncing. sqrt(2*g*r) ~ 0.65 m/s.
MIN_BOUNCE_SPEED = float(np.sqrt(2 * GRAVITY * BALL_RADIUS))
BOUNCE_TANGENTIAL_RETENTION = 0.50  # fraction of horizontal speed kept per bounce
BOUNCE_SPIN_RETENTION = 0.9  # fraction of spin kept per bounce
# A ball landing with heavy backspin digs into the turf and checks instead of
# running out. Below the threshold backspin has no effect; at the full value the
# bounce scrubs as much forward speed as it can.
SPIN_CHECK_THRESHOLD_RPM = 3000.0
SPIN_CHECK_FULL_RPM = 12000.0
MIN_BOUNCE_TANGENTIAL_RETENTION = 0.15
ROLL_SPIN_DECAY_PER_SEC = 1.5

YARDS_PER_METRE = 1.09361
MPH_TO_MPS = 0.44704


def get_bounce_tangential_retention(backspin_rpm):
    """Fraction of horizontal speed a bounce keeps, given backspin at impact.

    A driver arrives with little spin and runs out; a wedge arrives with three
    times as much, digs in and checks.
    """
    excess = max(0.0, backspin_rpm - SPIN_CHECK_THRESHOLD_RPM)
    span = SPIN_CHECK_FULL_RPM - SPIN_CHECK_THRESHOLD_RPM
    check_factor = max(0.0, 1.0 - excess / span)
    return max(
        MIN_BOUNCE_TANGENTIAL_RETENTION,
        BOUNCE_TANGENTIAL_RETENTION * check_factor,
    )


def get_drag_coefficient_cd(reynolds_number, spin_ratio_S):
    """Drag coefficient for a golf ball, rising with spin.

    Below the transition Reynolds number the dimples have not yet tripped the
    boundary layer and drag is much higher; above it the coefficient settles.
    """
    if reynolds_number < 1e-3:
        return DRAG_COEFFICIENT_AT_REST
    if reynolds_number < DRAG_TRANSITION_REYNOLDS:
        cd_base = DRAG_COEFFICIENT_AT_REST - (reynolds_number / DRAG_TRANSITION_REYNOLDS) * (
            DRAG_COEFFICIENT_AT_REST - DRAG_COEFFICIENT_BASE
        )
    else:
        cd_base = DRAG_COEFFICIENT_BASE
    return max(0.15, min(cd_base + DRAG_PER_SPIN_RATIO * spin_ratio_S, 0.9))


def get_lift_coefficient_cl(spin_ratio_S):
    """Lift coefficient for a golf ball: steep at first, then plateauing.

    The saturating shape is what keeps a high-spin wedge from ballooning,
    without an arbitrary ceiling.
    """
    if spin_ratio_S <= 0:
        return 0.0
    return LIFT_COEFFICIENT_MAX * spin_ratio_S / (CL_HALF_SPIN_RATIO + spin_ratio_S)


def calculate_trajectory(
    ball_speed_mph,
    launch_angle_deg,
    launch_side_angle_deg,
    back_spin_rpm,
    side_spin_rpm,
    rifle_spin_rpm,
    wind_vector_mph=(0.0, 0.0, 0.0),
    current_air_density=DEFAULT_AIR_DENSITY,
    current_cor_ground=DEFAULT_COR_GROUND,
    current_rolling_friction_coeff=DEFAULT_ROLLING_FRICTION_COEFF,
    landing_height_yards=0.0,
):
    """Integrate a full shot from launch through bounce and roll.

    Returns a dict of distances in yards plus `trajectory_points`, a list of
    10-tuples: (x, y, z) in yards, (vx, vy, vz) in m/s, time in seconds, and
    the is_flight / is_bounce / is_roll flags.

    `landing_height_yards` is where the ground is, relative to the ball's feet.
    Positive is uphill, and the shot lands sooner because it runs out of drop
    before it runs out of speed - which is why an uphill green takes a club
    more, and why the same swing downhill will not stop. The ball then bounces
    and rolls out on that level, so a green fifty feet up is played to as it
    would be walked to.
    """
    v0_mps = ball_speed_mph * MPH_TO_MPS
    angle_rad_vertical = np.deg2rad(launch_angle_deg)
    angle_rad_horizontal = np.deg2rad(launch_side_angle_deg)

    v_horizontal_mag = v0_mps * np.cos(angle_rad_vertical)
    vx0 = v_horizontal_mag * np.cos(angle_rad_horizontal)
    vy0 = v0_mps * np.sin(angle_rad_vertical)
    vz0 = v_horizontal_mag * np.sin(angle_rad_horizontal)

    r = np.array([0.0, 0.0, 0.0])  # position, m, relative to the hitting spot
    v = np.array([vx0, vy0, vz0])  # velocity, m/s

    # Spin vector in world coordinates, rad/s. For a RH golfer, positive
    # side_spin_rpm is a slice (curves +Z) and positive back_spin_rpm is lift
    # (+Y). Given v is primarily +X, that requires -omega_y and +omega_z
    # respectively for the Magnus cross product to point the right way.
    omega_rps = np.array([
        rifle_spin_rpm / 60.0 * 2 * np.pi,   # about X, the flight axis
        -side_spin_rpm / 60.0 * 2 * np.pi,   # about Y, the vertical axis
        back_spin_rpm / 60.0 * 2 * np.pi,    # about Z, the transverse axis
    ])

    wind_v_mps = np.array(wind_vector_mph) * MPH_TO_MPS

    # Where the ground is for this shot. Uphill greens sit above the ball's
    # feet, so the flight ends against a higher floor than it started from.
    ground_m = landing_height_yards / YARDS_PER_METRE

    t = 0.0
    trajectory_points = []
    max_height_m = 0.0
    flight_time_sec = None  # set at first ground contact
    simulation_capped = False
    state = "flight"

    trajectory_points.append(np.concatenate((r, v, [t, 1, 0, 0])))

    while True:
        if r[1] > max_height_m and state == "flight":
            max_height_m = r[1]

        if state == "flight":
            if r[1] <= ground_m and v[1] < 0:  # at or below ground, moving down
                # Interpolate to the precise impact point.
                dt_to_impact = (ground_m - r[1]) / v[1] if abs(v[1]) > 1e-3 else DT
                if 0 < dt_to_impact < DT:
                    r_impact = r + v * dt_to_impact
                    v_impact = v
                    t_impact = t + dt_to_impact
                else:  # landed in the previous step, or exactly now
                    r_impact = r.copy()
                    r_impact[1] = ground_m
                    v_impact = v.copy()
                    t_impact = t

                trajectory_points.append(
                    np.concatenate((r_impact, v_impact, [t_impact, 0, 1, 0]))
                )
                if flight_time_sec is None:
                    flight_time_sec = t_impact

                rebound_speed = -v_impact[1] * current_cor_ground
                if abs(rebound_speed) < MIN_BOUNCE_SPEED:
                    # Too slow to leave the turf. The ball settles into a roll,
                    # and since it never leaves the ground there is no bounce to
                    # scrub horizontal speed - rolling friction takes over.
                    v[0] = v_impact[0]
                    v[1] = 0.0
                    v[2] = v_impact[2]
                    state = "roll"
                else:
                    backspin_rpm_at_impact = omega_rps[2] * 60.0 / (2 * np.pi)
                    retention = get_bounce_tangential_retention(backspin_rpm_at_impact)
                    v[0] = v_impact[0] * retention
                    v[1] = rebound_speed
                    v[2] = v_impact[2] * retention
                    state = "flight"
                omega_rps *= BOUNCE_SPIN_RETENTION

                r = r_impact
                t = t_impact

                if state == "roll" and np.linalg.norm(np.array([v[0], v[2]])) < MIN_ROLL_SPEED:
                    break  # no roll speed left either

                trajectory_points.append(np.concatenate(
                    (r, v, [t, (1 if state == "flight" else 0), 0, (1 if state == "roll" else 0)])
                ))
                t += DT
                continue

            v_air = v - wind_v_mps
            air_speed = np.linalg.norm(v_air)
            accel_aero = np.array([0.0, 0.0, 0.0])

            if air_speed > MIN_FLIGHT_SPEED_FOR_AERO:
                reynolds_number = (
                    current_air_density * air_speed * (2 * BALL_RADIUS)
                ) / AIR_VISCOSITY
                spin_magnitude_rps = np.linalg.norm(omega_rps)
                spin_ratio_S = (BALL_RADIUS * spin_magnitude_rps) / air_speed

                cd = get_drag_coefficient_cd(reynolds_number, spin_ratio_S)
                f_drag_magnitude = 0.5 * current_air_density * air_speed**2 * BALL_AREA * cd
                accel_aero += (-f_drag_magnitude / BALL_MASS) * (v_air / air_speed)

                if spin_magnitude_rps > 1e-3:
                    cl = get_lift_coefficient_cl(spin_ratio_S)
                    f_magnus_magnitude = (
                        0.5 * current_air_density * air_speed**2 * BALL_AREA * cl
                    )
                    magnus_direction = np.cross(
                        omega_rps / spin_magnitude_rps, v_air / air_speed
                    )
                    norm_magnus_direction = np.linalg.norm(magnus_direction)
                    if norm_magnus_direction > 1e-6:
                        f_magnus_vector = f_magnus_magnitude * (
                            magnus_direction / norm_magnus_direction
                        )
                        accel_aero += f_magnus_vector / BALL_MASS

            accel = np.array([0.0, -GRAVITY, 0.0]) + accel_aero

            v_prev_step = v.copy()
            v += accel * DT
            r += v_prev_step * DT  # v from the start of the step is more stable

        elif state == "roll":
            if np.linalg.norm(np.array([v[0], v[2]])) < MIN_ROLL_SPEED:
                break
            r[1] = ground_m
            v[1] = 0.0

            decel_roll_mag = current_rolling_friction_coeff * GRAVITY
            v_horizontal_speed = np.linalg.norm(np.array([v[0], 0, v[2]]))
            if v_horizontal_speed > 1e-6:
                v_horizontal_dir = np.array([v[0], 0, v[2]]) / v_horizontal_speed
                accel_roll_vec = -decel_roll_mag * v_horizontal_dir

                v_new_x = v[0] + accel_roll_vec[0] * DT
                v_new_z = v[2] + accel_roll_vec[2] * DT
                # Clamp at zero rather than letting friction reverse the roll.
                if np.sign(v_new_x) != np.sign(v[0]) and abs(v[0]) > 1e-6:
                    v[0] = 0.0
                else:
                    v[0] = v_new_x
                if np.sign(v_new_z) != np.sign(v[2]) and abs(v[2]) > 1e-6:
                    v[2] = 0.0
                else:
                    v[2] = v_new_z
            else:
                v[0] = 0.0
                v[2] = 0.0
                break

            omega_rps *= (1 - DT * ROLL_SPIN_DECAY_PER_SEC)
            r += v * DT

        trajectory_points.append(np.concatenate(
            (r, v, [t, (1 if state == "flight" else 0), 0, (1 if state == "roll" else 0)])
        ))
        t += DT
        if t > MAX_SIM_TIME_SEC:
            simulation_capped = True
            break

    final_r = trajectory_points[-1][:3] if trajectory_points else r
    carry_r = final_r
    for i in range(1, len(trajectory_points)):
        if trajectory_points[i - 1][7] == 1 and trajectory_points[i][7] == 0:
            # First transition out of flight. If it was logged as a bounce, that
            # point is the landing spot; otherwise the ball rolled or stopped,
            # so the previous point is the last airborne one.
            carry_r = (
                trajectory_points[i][:3]
                if trajectory_points[i][8] == 1
                else trajectory_points[i - 1][:3]
            )
            break

    total_carry_distance_m = np.linalg.norm(np.array([carry_r[0], 0, carry_r[2]]))
    total_distance_with_roll_m = np.linalg.norm(np.array([final_r[0], 0, final_r[2]]))

    return {
        "total_carry_yards": total_carry_distance_m * YARDS_PER_METRE,
        "max_height_yards": max_height_m * YARDS_PER_METRE,
        "flight_time_sec": flight_time_sec if flight_time_sec is not None else t,
        "total_time_sec": t,
        "final_x_yards": final_r[0] * YARDS_PER_METRE,
        "final_z_yards": final_r[2] * YARDS_PER_METRE,
        "total_distance_with_roll_yards": total_distance_with_roll_m * YARDS_PER_METRE,
        # True when the ball was still moving at MAX_SIM_TIME_SEC, meaning the
        # distances above are where it had got to, not where it came to rest.
        "simulation_capped": simulation_capped,
        "trajectory_points": [
            (
                p[0] * YARDS_PER_METRE, p[1] * YARDS_PER_METRE, p[2] * YARDS_PER_METRE,
                p[3], p[4], p[5],
                p[6], int(p[7]), int(p[8]), int(p[9]),
            )
            for p in trajectory_points
        ],
    }


def analyze_shot_for_feedback(external_swing_metrics, shot_results_ball_flight, lie_type="Tee"):
    """Return a list of human-readable feedback lines for a shot."""
    feedback = []
    club = external_swing_metrics.get("club_metrics", {})
    ball = external_swing_metrics.get("ball_launch_metrics", {})
    club_speed = club.get("club_speed_mph", 0)
    ball_speed = ball.get("ball_speed_mph", 0)
    path = club.get("club_path_deg", 0)
    face_to_path = club.get("face_angle_to_path_deg", 0)
    attack_angle = club.get("attack_angle_deg", 0)
    dynamic_loft = club.get("dynamic_loft_deg", 0)
    back_spin = ball.get("back_spin_rpm", 0)
    launch_angle = ball.get("launch_angle_deg", 0)
    side_spin = ball.get("side_spin_rpm", 0)
    total_dist_yards = shot_results_ball_flight.get("total_distance_with_roll_yards", 0)

    feedback.append(f"--- Swing & Impact Analysis (Lie: {lie_type}, RH Golfer Assumed) ---")
    smash = ball_speed / club_speed if club_speed > 0.1 else 0
    feedback.append(
        f"  Club Speed: {club_speed:.1f} mph | Ball Speed: {ball_speed:.1f} mph | Smash: {smash:.2f}"
    )
    feedback.append(
        f"  Attack Angle: {attack_angle:.1f}° | Dyn. Loft: {dynamic_loft:.1f}° | Launch Angle: {launch_angle:.1f}°"
    )
    feedback.append(f"  Club Path: {path:.1f}° | Face Angle to Path: {face_to_path:.1f}°")
    feedback.append(f"  Back Spin: {back_spin:.0f} RPM | Side Spin: {side_spin:.0f} RPM")

    if lie_type == "Rough":
        if smash < 1.38 and club_speed > 50:
            feedback.append("  Rough: Energy transfer (smash) can be lower from rough.")
        if abs(side_spin) > 700:
            feedback.append("  Rough: Higher side spin might indicate club twisting in grass.")
    elif lie_type == "Sand":
        feedback.append("  Sand Lie (Greenside Assumed):")
        if launch_angle < 22 and dynamic_loft < 35:
            feedback.append("    Consider more launch/loft for soft landing from sand.")
        if back_spin < 4500 and total_dist_yards < 50:
            feedback.append("    More backspin helps stop greenside bunker shots.")
    elif lie_type in ["Green-side", "Putting"]:
        feedback.append(f"  Short Game ({lie_type}):")
        if total_dist_yards > 60 and lie_type != "Fairway":
            feedback.append(
                f"    Distance ({total_dist_yards:.0f}yd) is quite long for typical '{lie_type}' scenario."
            )
        if abs(side_spin) > 400:
            feedback.append(
                f"    Minimize side spin ({side_spin:.0f} RPM) for short game accuracy."
            )
        if lie_type == "Putting":
            if launch_angle > 2.5:
                feedback.append(
                    f"    Putting: Launch angle ({launch_angle:.1f}°) is high; aim for minimal launch and true roll."
                )
            if abs(back_spin) > 500 and back_spin < -10:
                feedback.append(
                    f"    Putting: Significant backspin ({back_spin:.0f} RPM) is unusual. Topspin or true roll preferred."
                )

    # D-plane based ball-flight prediction.
    if path < -1.5 and face_to_path > 0.5:
        feedback.append(
            "  Ball Flight: PULL-SLICE tendency likely (Out-to-in path, clubface open to that path)."
        )
    elif path > 1.5 and face_to_path < -0.5:
        feedback.append(
            "  Ball Flight: PUSH-HOOK tendency likely (In-to-out path, clubface closed to that path)."
        )
    elif abs(path) < 1.5 and abs(face_to_path) > 2.0:
        if face_to_path > 2.0:
            feedback.append(
                "  Ball Flight: STRAIGHT PUSH-SLICE likely (Good path, but face significantly open)."
            )
        else:
            feedback.append(
                "  Ball Flight: STRAIGHT PULL-HOOK likely (Good path, but face significantly closed)."
            )
    elif abs(face_to_path) < 1.5 and abs(path) > 2.5:
        if path < -2.5:
            feedback.append(
                "  Ball Flight: PULL likely (Path significantly out-to-in, face square)."
            )
        else:
            feedback.append(
                "  Ball Flight: PUSH likely (Path significantly in-to-out, face square)."
            )

    return feedback
