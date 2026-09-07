"""
DODO — skills/weather.py
Fetches current weather information using Open-Meteo API with wttr.in fallback.
"""

from typing import Optional
import urllib.parse
import requests

GEOCODING_API_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_API_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_CITY = "Delhi"

# WMO Weather interpretation codes (WW)
WMO_CODE_MAP = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snowfall",
    73: "moderate snowfall",
    75: "heavy snowfall",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def _wmo_to_description(code: int) -> str:
    """Convert WMO weather code to a human-readable description."""
    if code in WMO_CODE_MAP:
        return WMO_CODE_MAP[code]
    if 1 <= code <= 3:
        return "partly cloudy"
    if 45 <= code <= 48:
        return "foggy"
    if 51 <= code <= 55:
        return "drizzle"
    if 61 <= code <= 65:
        return "rain"
    if 71 <= code <= 77:
        return "snow"
    if 80 <= code <= 82:
        return "showers"
    if 95 <= code <= 99:
        return "thunderstorm"
    return "partly cloudy"


def _geocode_city(city: str) -> Optional[tuple[float, float, str]]:
    """
    Geocode city name to (latitude, longitude, resolved_name).
    Returns None if geocoding fails or city is not found.
    """
    try:
        params = {"name": city, "count": 1, "language": "en", "format": "json"}
        resp = requests.get(GEOCODING_API_URL, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results")
            if results and len(results) > 0:
                first = results[0]
                lat = first.get("latitude")
                lon = first.get("longitude")
                name = first.get("name", city)
                if lat is not None and lon is not None:
                    return float(lat), float(lon), str(name)
    except Exception:
        pass
    return None


def _get_weather_open_meteo(city: str) -> Optional[str]:
    """
    Fetch weather using Open-Meteo API.
    Returns natural language string or None on failure.
    """
    geo = _geocode_city(city)
    if not geo:
        return None

    lat, lon, resolved_city = geo
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
    }

    try:
        resp = requests.get(FORECAST_API_URL, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            current = data.get("current", {})
            temp = current.get("temperature_2m")
            humidity = current.get("relative_humidity_2m")
            code = current.get("weather_code", 0)

            if temp is not None:
                desc = _wmo_to_description(int(code))
                temp_formatted = f"{round(float(temp))}°C"
                humidity_str = f" Humidity is {humidity}%." if humidity is not None else ""
                return f"It's {temp_formatted} and {desc} in {resolved_city}.{humidity_str}"
    except Exception:
        pass
    return None


def _get_weather_wttr(city: str) -> Optional[str]:
    """
    Fallback weather fetcher using wttr.in.
    Returns natural language string or None on failure.
    """
    try:
        encoded_city = urllib.parse.quote(city)
        url = f"https://wttr.in/{encoded_city}?format=j1"
        resp = requests.get(url, timeout=5, headers={"User-Agent": "curl/7.68.0"})
        if resp.status_code == 200:
            data = resp.json()
            conditions = data.get("current_condition", [])
            if conditions:
                curr = conditions[0]
                temp = curr.get("temp_C")
                humidity = curr.get("humidity")
                desc_list = curr.get("weatherDesc", [])
                desc = desc_list[0].get("value", "clear").lower() if desc_list else "clear"
                if temp is not None:
                    humidity_str = f" Humidity is {humidity}%." if humidity else ""
                    return f"It's {temp}°C and {desc} in {city.title()}.{humidity_str}"

        # Plain text fallback if JSON format is unavailable
        text_resp = requests.get(
            f"https://wttr.in/{encoded_city}?format=%C,+%t,+Humidity:+%h",
            timeout=5,
            headers={"User-Agent": "curl/7.68.0"},
        )
        if text_resp.status_code == 200 and text_resp.text.strip():
            return f"Weather in {city.title()}: {text_resp.text.strip()}."
    except Exception:
        pass
    return None


def get_weather(city: Optional[str] = None) -> str:
    """
    Get current weather for a city in natural language.
    Defaults to Delhi if no city is provided.

    Args:
        city: The name of the city (optional).

    Returns:
        A natural language weather description.
    """
    target_city = city.strip() if city and city.strip() else DEFAULT_CITY

    # Primary source: Open-Meteo API
    report = _get_weather_open_meteo(target_city)
    if report:
        return report

    # Secondary fallback: wttr.in
    report = _get_weather_wttr(target_city)
    if report:
        return report

    # Graceful error handling
    return f"Sorry, I couldn't fetch the weather for '{target_city}'. Please check the city name or internet connection."
