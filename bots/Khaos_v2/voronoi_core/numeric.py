import math
from decimal import Decimal

NUMERIC_MODE_DECIMAL = "decimal"
NUMERIC_MODE_FLOAT = "float"

_numeric_mode = NUMERIC_MODE_DECIMAL
_is_float = False
_FLOAT_EPSILON = 1e-9
_FLOAT_SQRT_EPSILON = 1e-12


def set_numeric_mode(mode: str) -> None:
    global _numeric_mode, _is_float
    if mode not in (NUMERIC_MODE_DECIMAL, NUMERIC_MODE_FLOAT):
        raise ValueError(f"Unsupported foronoi numeric mode: {mode}")
    _numeric_mode = mode
    _is_float = (mode == NUMERIC_MODE_FLOAT)


def get_numeric_mode() -> str:
    return _numeric_mode


def to_number(value):
    if value is None:
        return None
    if _is_float:
        if type(value) is float:
            return value
        return float(value)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def is_zero(value, epsilon: float = _FLOAT_EPSILON) -> bool:
    if _is_float:
        return -epsilon <= value <= epsilon
    return value == 0


def is_close(a, b, epsilon: float = _FLOAT_EPSILON) -> bool:
    if _is_float:
        diff = a - b
        return -epsilon <= diff <= epsilon
    return a == b


def sqrt(value):
    if _is_float:
        if type(value) is not float:
            value = float(value)
        if value < 0.0 and value > -_FLOAT_SQRT_EPSILON:
            value = 0.0
        return math.sqrt(value)
    return Decimal.sqrt(value)
