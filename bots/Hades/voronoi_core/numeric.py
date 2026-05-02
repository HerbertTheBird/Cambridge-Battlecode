import math
from decimal import Decimal

NUMERIC_MODE_DECIMAL = "decimal"
NUMERIC_MODE_FLOAT = "float"

_numeric_mode = NUMERIC_MODE_DECIMAL
_FLOAT_EPSILON = 1e-9
_FLOAT_SQRT_EPSILON = 1e-12


def set_numeric_mode(mode: str) -> None:
    global _numeric_mode
    if mode not in (NUMERIC_MODE_DECIMAL, NUMERIC_MODE_FLOAT):
        raise ValueError(f"Unsupported foronoi numeric mode: {mode}")
    _numeric_mode = mode


def get_numeric_mode() -> str:
    return _numeric_mode


def to_number(value):
    if value is None:
        return None
    if _numeric_mode == NUMERIC_MODE_FLOAT:
        return float(value)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def is_zero(value, epsilon: float = _FLOAT_EPSILON) -> bool:
    if _numeric_mode == NUMERIC_MODE_FLOAT:
        return abs(float(value)) <= epsilon
    return value == 0


def is_close(a, b, epsilon: float = _FLOAT_EPSILON) -> bool:
    if _numeric_mode == NUMERIC_MODE_FLOAT:
        return abs(float(a) - float(b)) <= epsilon
    return a == b


def sqrt(value):
    if _numeric_mode == NUMERIC_MODE_FLOAT:
        value = float(value)
        if value < 0.0 and value > -_FLOAT_SQRT_EPSILON:
            value = 0.0
        return math.sqrt(value)
    return Decimal.sqrt(value)
