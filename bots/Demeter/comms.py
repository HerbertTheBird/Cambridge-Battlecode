from cambc import Position

from globals import Symmetry

LAUNCHER_BIT = 31
LAUNCHER_ORDER_BIT = 30
ID_BITS = 12
COORD_BITS = 6
_ID_MASK = (1 << ID_BITS) - 1
_COORD_MASK = (1 << COORD_BITS) - 1
LAUNCH_ORDER_ID_MASK = _ID_MASK
LAUNCH_ORDER_MAX_AGE = 5

symmetry: Symmetry | None = None
launch_orders: list[tuple[int, int, int | None, int, int]] = []
_seen_launch_marker_ids: set[int] = set()

def init():
    global symmetry, launch_orders, _seen_launch_marker_ids
    symmetry = None
    launch_orders = []
    _seen_launch_marker_ids = set()

def reset_turn(current_round: int | None = None):
    if current_round is not None:
        prune_launch_orders(current_round)

def prune_launch_orders(current_round: int):
    global launch_orders
    launch_orders = [
        order
        for order in launch_orders
        if current_round - order[4] < LAUNCH_ORDER_MAX_AGE
    ]

def remove_launch_order(marker_id: int):
    global launch_orders
    launch_orders = [order for order in launch_orders if order[3] != marker_id]

def encode_symmetry(sym: Symmetry) -> int:
    return sym.value & 0x3

def encode_launch_order(builder_id: int, target: Position) -> int:
    return (
        (1 << LAUNCHER_BIT)
        | (1 << LAUNCHER_ORDER_BIT)
        | (target.x << ID_BITS)
        | (target.y << (ID_BITS + COORD_BITS))
        | (builder_id & _ID_MASK)
    )

def decode_launch_order_target(msg: int) -> Position:
    return Position((msg >> ID_BITS) & _COORD_MASK, (msg >> (ID_BITS + COORD_BITS)) & _COORD_MASK)

def read_marker(msg: int, pos: Position | None = None, marker_id: int | None = None, current_round: int | None = None):
    global symmetry
    if ((msg >> LAUNCHER_BIT) & 1) and ((msg >> LAUNCHER_ORDER_BIT) & 1):
        if marker_id is None or current_round is None or marker_id in _seen_launch_marker_ids:
            return
        builder_id = msg & _ID_MASK
        _seen_launch_marker_ids.add(marker_id)
        marker_idx = None if pos is None else (pos.y << COORD_BITS) | pos.x
        launch_orders.append((msg, builder_id, marker_idx, marker_id, current_round))
        return
    sym_bits = msg & 0x3
    if sym_bits != 0:
        symmetry = Symmetry(sym_bits)
