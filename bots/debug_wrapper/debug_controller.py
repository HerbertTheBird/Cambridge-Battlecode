"""
DebugController: a transparent proxy around the real cambc Controller that
logs every action (mutation) call to stdout before executing it.

All read-only / query methods fall through via __getattr__ and are NOT logged,
since logging them produces overwhelming noise.

Log format:
    [DBG][T{turn:04d}][{entity_type}#{id}@{pos}] method(args...)
"""

from __future__ import annotations


class DebugController:
    """Wraps a real Controller, logging every action before forwarding it."""

    __slots__ = ("_c", "_round", "_id", "_etype", "_pos")

    def __init__(self, real_c) -> None:
        self._c = real_c
        self._round: int = real_c.get_current_round()
        self._id: int = real_c.get_id()
        self._etype = real_c.get_entity_type()
        self._pos = real_c.get_position()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        print(
            f"[DBG][T{self._round:04d}][{self._etype.name}#{self._id}@{self._pos}] {msg}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Transparent passthrough for all reads (via __getattr__)
    # This is the fallback — only called when no explicit override exists.
    # ------------------------------------------------------------------

    def __getattr__(self, name: str):
        return getattr(self._c, name)

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    def move(self, direction) -> None:
        self._log(f"move({direction.name})")
        result = self._c.move(direction)
        # Update cached position after a successful move
        self._pos = self._c.get_position()
        return result

    def can_move(self, direction) -> bool:
        return self._c.can_move(direction)

    # ------------------------------------------------------------------
    # Building (builder bot actions)
    # ------------------------------------------------------------------

    def build_conveyor(self, position, direction) -> int:
        self._log(f"build_conveyor({position}, {direction.name})")
        return self._c.build_conveyor(position, direction)

    def build_splitter(self, position, direction) -> int:
        self._log(f"build_splitter({position}, {direction.name})")
        return self._c.build_splitter(position, direction)

    def build_bridge(self, position, target) -> int:
        self._log(f"build_bridge({position}, target={target})")
        return self._c.build_bridge(position, target)

    def build_armoured_conveyor(self, position, direction) -> int:
        self._log(f"build_armoured_conveyor({position}, {direction.name})")
        return self._c.build_armoured_conveyor(position, direction)

    def build_harvester(self, position) -> int:
        self._log(f"build_harvester({position})")
        return self._c.build_harvester(position)

    def build_road(self, position) -> int:
        self._log(f"build_road({position})")
        return self._c.build_road(position)

    def build_barrier(self, position) -> int:
        self._log(f"build_barrier({position})")
        return self._c.build_barrier(position)

    def build_gunner(self, position, direction) -> int:
        self._log(f"build_gunner({position}, {direction.name})")
        return self._c.build_gunner(position, direction)

    def build_sentinel(self, position, direction) -> int:
        self._log(f"build_sentinel({position}, {direction.name})")
        return self._c.build_sentinel(position, direction)

    def build_breach(self, position, direction) -> int:
        self._log(f"build_breach({position}, {direction.name})")
        return self._c.build_breach(position, direction)

    def build_launcher(self, position) -> int:
        self._log(f"build_launcher({position})")
        return self._c.build_launcher(position)

    def build_foundry(self, position) -> int:
        self._log(f"build_foundry({position})")
        return self._c.build_foundry(position)

    # ------------------------------------------------------------------
    # Healing & destruction
    # ------------------------------------------------------------------

    def heal(self, position) -> None:
        self._log(f"heal({position})")
        return self._c.heal(position)

    def destroy(self, building_pos) -> None:
        self._log(f"destroy({building_pos})")
        return self._c.destroy(building_pos)

    def self_destruct(self) -> None:
        self._log("self_destruct()")
        return self._c.self_destruct()

    def resign(self) -> None:
        self._log("resign()")
        return self._c.resign()

    # ------------------------------------------------------------------
    # Markers
    # ------------------------------------------------------------------

    def place_marker(self, position, value: int) -> None:
        self._log(f"place_marker({position}, {value:#010x})")
        return self._c.place_marker(position, value)

    # ------------------------------------------------------------------
    # Combat
    # ------------------------------------------------------------------

    def fire(self, target) -> None:
        self._log(f"fire({target})")
        return self._c.fire(target)

    def launch(self, bot_pos, target) -> None:
        self._log(f"launch(bot={bot_pos}, target={target})")
        return self._c.launch(bot_pos, target)

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def spawn_builder(self, position) -> int:
        self._log(f"spawn_builder({position})")
        return self._c.spawn_builder(position)

    # ------------------------------------------------------------------
    # Indicators (saved to replay — log them so we see the bot's intent)
    # ------------------------------------------------------------------

    def draw_indicator_dot(self, pos, r: int, g: int, b: int) -> None:
        self._log(f"draw_indicator_dot({pos}, rgb=({r},{g},{b}))")
        return self._c.draw_indicator_dot(pos, r, g, b)

    def draw_indicator_line(self, pos_a, pos_b, r: int, g: int, b: int) -> None:
        self._log(f"draw_indicator_line({pos_a} → {pos_b}, rgb=({r},{g},{b}))")
        return self._c.draw_indicator_line(pos_a, pos_b, r, g, b)
