from __future__ import annotations


SENSORGRAM_DISPLAY_WINDOW_OPTIONS: tuple[tuple[str, float], ...] = (
    ("1 min", 60.0),
    ("5 min", 300.0),
    ("15 min", 900.0),
    ("30 min", 1800.0),
    ("60 min", 3600.0),
)
SENSORGRAM_DISPLAY_WINDOW_SECONDS: tuple[float, ...] = tuple(seconds for _, seconds in SENSORGRAM_DISPLAY_WINDOW_OPTIONS)
SENSORGRAM_DISPLAY_WINDOW_LABELS: dict[float, str] = {float(seconds): label for label, seconds in SENSORGRAM_DISPLAY_WINDOW_OPTIONS}


def normalize_sensorgram_display_window_s(value: object | None = None, current_value: float = 60.0) -> float:
    allowed_values = SENSORGRAM_DISPLAY_WINDOW_SECONDS
    current = current_value if value is None else value
    try:
        seconds = float(current)
    except (TypeError, ValueError):
        return allowed_values[0]
    if not seconds == seconds or seconds <= 0:  # NaN-safe check without extra imports
        return allowed_values[0]
    return min(allowed_values, key=lambda candidate: abs(candidate - seconds))


def cycle_sensorgram_display_window_s(current_value: float) -> float:
    normalized = normalize_sensorgram_display_window_s(current_value, current_value)
    try:
        index = SENSORGRAM_DISPLAY_WINDOW_SECONDS.index(float(normalized))
    except ValueError:
        index = -1
    return SENSORGRAM_DISPLAY_WINDOW_SECONDS[(index + 1) % len(SENSORGRAM_DISPLAY_WINDOW_SECONDS)]
