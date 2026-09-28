"""Course conditions from live weather data.

Turns a city name into the two things the physics engine actually needs: air
density and a wind vector in simulation coordinates. Weather comes from
Open-Meteo, which needs no API key.

Nothing here raises on network failure. If the service is unreachable the
module falls back to standard sea-level conditions and says so, so a round of
golf still works on a train.
"""

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from golf_physics import DEFAULT_AIR_DENSITY

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
IP_LOCATION_URL = "https://ipapi.co/json/"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_FIELDS = (
    "temperature_2m,relative_humidity_2m,precipitation,"
    "wind_speed_10m,wind_direction_10m,surface_pressure"
)
DEFAULT_TIMEOUT_SEC = 10
R_SPECIFIC_DRY_AIR = 287.058  # J/(kg.K)
R_SPECIFIC_WATER_VAPOUR = 461.495  # J/(kg.K)

# --- Ground conditions ---------------------------------------------------
# Rain softens a course: the ball lands with less bounce and rolls less far.
# Baked ground does the opposite, which is why links golf in a dry summer
# runs so much further than the same course in February.
FIRM_COR_GROUND = 0.55
SOFT_COR_GROUND = 0.32
FIRM_ROLLING_FRICTION = 0.09
SOFT_ROLLING_FRICTION = 0.24

# Rain now, in mm, at which the ground is treated as fully soft.
PRECIPITATION_FOR_SOFT_GROUND_MM = 2.0
# Damp air alone softens turf a little, even without rain falling.
HUMIDITY_FOR_DAMP_GROUND_PCT = 95.0
DAMP_GROUND_WETNESS = 0.35


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float
    name: str
    country: str = ""
    region: str = ""

    def __str__(self):
        parts = [self.name, self.region, self.country]
        return ", ".join(part for part in parts if part)


@dataclass(frozen=True)
class Conditions:
    """Everything a shot needs to know about where and when it is played.

    The air it flies through, the ground it lands on, and the readings those
    were derived from. New conditions belong here as fields with defaults, so
    that adding one does not disturb anything that already reads this.
    """

    air_density: float
    wind_vector_mph: tuple
    description: str
    location_name: str
    is_live: bool
    # Ground, derived from the weather rather than assumed.
    cor_ground: float = FIRM_COR_GROUND
    rolling_friction: float = FIRM_ROLLING_FRICTION
    # The raw readings, kept so anything else can respond to them.
    temperature_c: float = None
    humidity_pct: float = None
    precipitation_mm: float = None
    wetness: float = 0.0  # 0 baked, 1 saturated


def standard_conditions(location_name="Unknown", reason="weather unavailable"):
    """Sea-level, still-air conditions, used whenever live data is unavailable."""
    return Conditions(
        air_density=DEFAULT_AIR_DENSITY,
        wind_vector_mph=(0.0, 0.0, 0.0),
        description=f"Standard conditions ({reason})",
        location_name=location_name,
        is_live=False,
    )


def fetch_json(url, timeout=DEFAULT_TIMEOUT_SEC):
    """GET a URL and parse JSON. Returns None on any failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        # OSError covers URLError, timeouts and socket errors; ValueError covers
        # malformed JSON. There is nothing useful to do with any of them.
        return None


MAX_LOCATION_CANDIDATES = 5


def find_locations(city_name, count=MAX_LOCATION_CANDIDATES, fetch=fetch_json):
    """Every place matching a name, best match first.

    Place names are not unique and the first hit is often not the one meant:
    "Augusta" resolves to the state capital in Maine, a thousand miles from
    the course in Georgia. Returning the alternatives lets a golfer say which
    one they meant instead of silently playing somewhere else.
    """
    query = urllib.parse.urlencode(
        {"name": city_name, "count": count, "language": "en", "format": "json"}
    )
    data = fetch(f"{GEOCODING_URL}?{query}")
    if not data or not data.get("results"):
        return []

    return [
        Location(
            latitude=result["latitude"],
            longitude=result["longitude"],
            name=result.get("name", city_name),
            country=result.get("country", ""),
            region=result.get("admin1", ""),
        )
        for result in data["results"]
        if result.get("latitude") is not None and result.get("longitude") is not None
    ]


def get_coordinates_for_city(city_name, fetch=fetch_json):
    """Look up a city. Returns the best match, or None if it cannot be resolved."""
    candidates = find_locations(city_name, count=1, fetch=fetch)
    return candidates[0] if candidates else None


def get_current_weather(latitude, longitude, fetch=fetch_json):
    """Fetch current conditions. Returns the raw `current` block, or None."""
    query = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": CURRENT_FIELDS,
            "wind_speed_unit": "mph",
            "timezone": "auto",
        }
    )
    data = fetch(f"{FORECAST_URL}?{query}")
    return data.get("current") if data else None


def saturation_vapour_pressure_pa(temperature_celsius):
    """Pressure of water vapour in fully saturated air, by the Magnus formula."""
    return 610.78 * 10 ** (
        7.5 * temperature_celsius / (237.3 + temperature_celsius)
    )


def calculate_air_density(temperature_celsius, pressure_hpa, relative_humidity_pct=None):
    """Air density in kg/m^3 from temperature (C), pressure (hPa) and humidity.

    Moist air is *less* dense than dry air, because water vapour is lighter
    than the nitrogen and oxygen it displaces - which is why a ball flies
    slightly further on a muggy day. The effect is small beside temperature
    and pressure, but it is free to include and it is the direction most
    people guess wrong.

    Falls back to the standard value if temperature or pressure is missing, or
    if either is a figure no golf course has ever stood in.

    That last guard is not defensive habit. Pressure and humidity are adjacent
    numbers of the same type, and swapping them is silent: hand this 30 hPa and
    1013% and it will clamp the humidity, accept a near vacuum, and return
    0.03 kg/m^3 with no complaint - a plausible-looking number for an
    atmosphere nobody could stand up in, which would then fly every ball on the
    course three hundred yards. A wrong answer that announces itself is worth
    more than a confident one.
    """
    if temperature_celsius is None or pressure_hpa is None:
        return DEFAULT_AIR_DENSITY
    # Surface pressure ranges from about 870 hPa in the eye of a typhoon to
    # 1085 at the pole; the lowest course in the world sits near 1060 and the
    # highest near 700. Outside this, something has been passed in the wrong
    # place rather than the weather being remarkable.
    if not 650.0 <= pressure_hpa <= 1100.0:
        return DEFAULT_AIR_DENSITY
    # Golf is played between about -20C and 50C. Beyond that, likewise.
    if not -60.0 <= temperature_celsius <= 70.0:
        return DEFAULT_AIR_DENSITY

    temperature_kelvin = temperature_celsius + 273.15
    if temperature_kelvin <= 0:
        return DEFAULT_AIR_DENSITY

    total_pressure_pa = pressure_hpa * 100
    if relative_humidity_pct is None:
        return total_pressure_pa / (R_SPECIFIC_DRY_AIR * temperature_kelvin)

    vapour_pa = (max(0.0, min(relative_humidity_pct, 100.0)) / 100.0) * \
        saturation_vapour_pressure_pa(temperature_celsius)
    dry_pa = total_pressure_pa - vapour_pa
    return (
        dry_pa / (R_SPECIFIC_DRY_AIR * temperature_kelvin)
        + vapour_pa / (R_SPECIFIC_WATER_VAPOUR * temperature_kelvin)
    )


def ground_wetness(precipitation_mm=None, relative_humidity_pct=None):
    """How soft the ground is, from 0 (baked) to 1 (saturated).

    Rain dominates; damp air alone softens turf a little.
    """
    wetness = 0.0
    if precipitation_mm:
        wetness = min(1.0, precipitation_mm / PRECIPITATION_FOR_SOFT_GROUND_MM)
    if relative_humidity_pct is not None and \
            relative_humidity_pct >= HUMIDITY_FOR_DAMP_GROUND_PCT:
        wetness = max(wetness, DAMP_GROUND_WETNESS)
    return wetness


def ground_conditions(wetness):
    """Bounce and roll for ground of a given wetness.

    Returns (coefficient of restitution, rolling friction). Soft ground
    absorbs the bounce and stops the roll; firm ground gives both back.
    """
    wetness = max(0.0, min(wetness, 1.0))
    return (
        FIRM_COR_GROUND + wetness * (SOFT_COR_GROUND - FIRM_COR_GROUND),
        FIRM_ROLLING_FRICTION + wetness * (SOFT_ROLLING_FRICTION - FIRM_ROLLING_FRICTION),
    )


def convert_wind_to_simulation_vector(api_wind_speed_mph, api_wind_direction_degrees):
    """Convert reported wind into the simulator's coordinate frame.

    Weather services report the direction wind is coming FROM. The simulator
    wants the direction it is blowing TO, as [X, Y, Z] in mph, where positive X
    is a tailwind and positive Z is a crosswind from the left. Vertical wind is
    assumed to be zero.
    """
    if api_wind_speed_mph is None or api_wind_direction_degrees is None:
        return [0.0, 0.0, 0.0]

    theta_rad = math.radians(api_wind_direction_degrees)
    wind_x = -api_wind_speed_mph * math.cos(theta_rad)
    wind_z = -api_wind_speed_mph * math.sin(theta_rad)
    return [round(wind_x, 1), 0.0, round(wind_z, 1)]


def describe_weather(current):
    """One-line human summary of a raw Open-Meteo `current` block."""
    temperature = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    wind_speed = current.get("wind_speed_10m")
    wind_direction = current.get("wind_direction_10m")
    precipitation = current.get("precipitation")

    parts = []
    if temperature is not None:
        parts.append(f"{temperature:.1f}°C")
    if humidity is not None:
        parts.append(f"{humidity:.0f}% humidity")
    if wind_speed is not None and wind_direction is not None:
        parts.append(f"wind {wind_speed:.1f} mph from {wind_direction:.0f}°")
    if precipitation:
        parts.append(f"{precipitation:.1f} mm precipitation")
    return ", ".join(parts) if parts else "No weather detail reported"


def locate_by_ip(fetch=fetch_json):
    """Best guess at where this machine is, from its public IP address.

    Nothing is sent but the request itself - the service infers a location
    from the address it sees. Accuracy is city-level at best, and worse behind
    a VPN. Returns None on any failure, so a caller can fall back to asking.

    This reaches a third party, so it belongs behind an explicit choice by the
    golfer rather than happening by default.
    """
    data = fetch(IP_LOCATION_URL)
    if not data or data.get("latitude") is None or data.get("longitude") is None:
        return None
    return Location(
        latitude=data["latitude"],
        longitude=data["longitude"],
        name=data.get("city") or "Current location",
        country=data.get("country_name") or "",
        region=data.get("region") or "",
    )


def get_conditions_for_location(location, fetch=fetch_json):
    """Playing conditions at a resolved location."""
    current = get_current_weather(location.latitude, location.longitude, fetch=fetch)
    if current is None:
        return standard_conditions(str(location), "weather service unavailable")
    return conditions_from_reading(current, str(location))


def conditions_from_reading(current, location_name):
    """Turn one weather reading into everything the simulator responds to."""
    temperature = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    precipitation = current.get("precipitation")

    wetness = ground_wetness(precipitation, humidity)
    cor_ground, rolling_friction = ground_conditions(wetness)

    return Conditions(
        air_density=calculate_air_density(
            temperature, current.get("surface_pressure"), humidity
        ),
        wind_vector_mph=tuple(
            convert_wind_to_simulation_vector(
                current.get("wind_speed_10m"), current.get("wind_direction_10m")
            )
        ),
        description=describe_weather(current),
        location_name=location_name,
        is_live=True,
        cor_ground=cor_ground,
        rolling_friction=rolling_friction,
        temperature_c=temperature,
        humidity_pct=humidity,
        precipitation_mm=precipitation,
        wetness=wetness,
    )


def get_conditions_for_city(city_name, fetch=fetch_json):
    """Resolve a city to playing conditions, falling back to standard ones."""
    location = get_coordinates_for_city(city_name, fetch=fetch)
    if location is None:
        return standard_conditions(city_name, "location not found")
    return get_conditions_for_location(location, fetch=fetch)
