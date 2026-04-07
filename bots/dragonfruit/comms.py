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


class Comms:
    def __init__(self):
        self.symmetry: Symmetry | None = None
        self.launch_orders: list[tuple[Position, int, Position | None, int, int]] = []
        self._seen_launch_marker_ids: set[int] = set()

    # We shouldn't store launcher orders indefinitely
    # Found a game where builder bot kept trying to walk past an allied launcher and got launched back
    # TODO: Make launcher destroy markers after fulfilling launch
    def reset_turn(self, current_round: int | None = None):
        if current_round is not None:
            self.prune_launch_orders(current_round)

    def prune_launch_orders(self, current_round: int):
        self.launch_orders = [
            order
            for order in self.launch_orders
            if current_round - order[4] < LAUNCH_ORDER_MAX_AGE
        ]

    def remove_launch_order(self, marker_id: int):
        self.launch_orders = [order for order in self.launch_orders if order[3] != marker_id]

    def encode_symmetry(self, symmetry: Symmetry) -> int:
        return symmetry.value & 0x3

    def encode_launch_order(self, builder_id: int, target: Position) -> int:
        return (
            (1 << LAUNCHER_BIT)
            | (1 << LAUNCHER_ORDER_BIT)
            | (target.x << ID_BITS)
            | (target.y << (ID_BITS + COORD_BITS))
            | (builder_id & _ID_MASK)
        )

    def read_marker(self, msg: int, pos: Position | None = None, marker_id: int | None = None, current_round: int | None = None):
        if ((msg >> LAUNCHER_BIT) & 1) and ((msg >> LAUNCHER_ORDER_BIT) & 1):
            if marker_id is None or current_round is None or marker_id in self._seen_launch_marker_ids:
                return
            target = Position((msg >> ID_BITS) & _COORD_MASK, (msg >> (ID_BITS + COORD_BITS)) & _COORD_MASK)
            builder_id = msg & _ID_MASK
            self._seen_launch_marker_ids.add(marker_id)
            self.launch_orders.append((target, builder_id, pos, marker_id, current_round))
            return
        sym_bits = msg & 0x3
        if sym_bits != 0:
            self.symmetry = Symmetry(sym_bits)