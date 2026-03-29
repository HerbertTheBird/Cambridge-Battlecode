"""
MockController: implements the full cambc Controller interface against a
GameState snapshot reconstructed from a .replay26 replay file.

Read methods:  query the GameState and return real game-world data.
Action methods: log what the bot decided to do, then return without
                side-effects (the replay is not changed).

The object is STATEFUL — pass the same instance to every call of
Player.run() for one unit; the bot stores it as module-level `rc` and
reads it on every turn.  Update the shared GameState externally before
each turn; this controller automatically reflects the new state.
"""

from __future__ import annotations
import math
from typing import Optional

from game_state import GameState, EntityState

# ── Constants ──────────────────────────────────────────────────────────────────

# Vision radius squared by entity type (from GameConstants)
_VISION_SQ: dict[str, int] = {
    "BUILDER_BOT": 20, "CORE": 36, "GUNNER": 13,
    "SENTINEL": 32,    "BREACH": 13, "LAUNCHER": 26,
}

# Direction int → (dx, dy)  (matches cambc enum values)
_DIR_DELTA: dict[int, tuple[int, int]] = {
    0: (0, 0),  1: (0, 1),   2: (1, 1),  3: (1, 0),  4: (1, -1),
    5: (0, -1), 6: (-1, -1), 7: (-1, 0), 8: (-1, 1),
}

# Building costs (titanium, axionite) — from GameConstants
_BUILD_COST: dict[str, tuple[int, int]] = {
    "CONVEYOR":          (3, 0),
    "SPLITTER":          (6, 0),
    "BRIDGE":            (20, 0),
    "ARMOURED_CONVEYOR": (10, 5),
    "HARVESTER":         (80, 0),
    "ROAD":              (1, 0),
    "BARRIER":           (3, 0),
    "GUNNER":            (10, 0),
    "SENTINEL":          (15, 0),
    "BREACH":            (30, 10),
    "LAUNCHER":          (20, 0),
    "FOUNDRY":           (120, 0),
    "BUILDER_BOT":       (50, 0),
}


class MockController:
    """
    Stateful mock Controller for replay analysis.

    Create one instance per unit; share one GameState across all instances.
    The bot stores this object as its module-level `rc`; on every turn
    advance the shared GameState *before* calling Player.run(this).
    """

    __slots__ = ("_gs", "_unit_id")

    def __init__(self, gs: GameState, unit_id: int) -> None:
        self._gs      = gs
        self._unit_id = unit_id

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _me(self) -> EntityState:
        e = self._gs.entities.get(self._unit_id)
        if e is None:
            raise RuntimeError(f"Unit {self._unit_id} no longer in game state")
        return e

    def _log(self, msg: str) -> None:
        try:
            me = self._me()
            label = f"{me.entity_type}#{self._unit_id}@{me.pos}"
        except RuntimeError:
            label = f"UNIT#{self._unit_id}@?"
        print(
            f"[DBG][T{self._gs.current_round:04d}][{label}] {msg}",
            flush=True,
        )

    def _afford(self, build_type: str) -> bool:
        """Return True if the team can afford to build the given type."""
        cost_ti, cost_ax = _BUILD_COST.get(build_type, (0, 0))
        me = self._me()
        ti, ax = self._gs.get_resources(me.team)
        return ti >= cost_ti and ax >= cost_ax

    # ── Identity & round ──────────────────────────────────────────────────────

    def get_id(self) -> int:
        return self._unit_id

    def get_current_round(self) -> int:
        return self._gs.current_round

    def get_entity_type(self, entity_id: int = None):
        from cambc import EntityType
        if entity_id is None:
            return EntityType[self._me().entity_type]
        e = self._gs.entities.get(entity_id)
        return EntityType[e.entity_type] if e else None

    def get_team(self, entity_id: int = None):
        from cambc import Team
        if entity_id is None:
            return Team.A if self._me().team == 0 else Team.B
        e = self._gs.entities.get(entity_id)
        if e is None:
            return None
        return Team.A if e.team == 0 else Team.B

    def get_position(self, entity_id: int = None):
        from cambc import Position
        if entity_id is None:
            p = self._me().pos
        else:
            e = self._gs.entities.get(entity_id)
            if e is None:
                return None
            p = e.pos
        return Position(p.x, p.y)

    # ── Status ────────────────────────────────────────────────────────────────

    def get_hp(self, entity_id: int = None) -> int:
        if entity_id is None:
            return self._me().hp
        e = self._gs.entities.get(entity_id)
        return e.hp if e else 0

    def get_max_hp(self, entity_id: int = None) -> int:
        if entity_id is None:
            return self._me().maxhp
        e = self._gs.entities.get(entity_id)
        return e.maxhp if e else 0

    def get_action_cooldown(self, entity_id: int = None) -> int:
        if entity_id is None:
            return self._me().action_cooldown
        e = self._gs.entities.get(entity_id)
        return e.action_cooldown if e else 0

    def get_move_cooldown(self, entity_id: int = None) -> int:
        if entity_id is None:
            return self._me().move_cooldown
        e = self._gs.entities.get(entity_id)
        return e.move_cooldown if e else 0

    def get_ammo_amount(self, entity_id: int = None) -> int:
        if entity_id is None:
            return self._me().ammo_amount
        e = self._gs.entities.get(entity_id)
        return e.ammo_amount if e else 0

    def get_ammo_type(self, entity_id: int = None):
        from cambc import ResourceType
        _map = {0: None, 1: ResourceType.TITANIUM,
                2: ResourceType.RAW_AXIONITE, 3: ResourceType.REFINED_AXIONITE}
        if entity_id is None:
            return _map.get(self._me().ammo_type)
        e = self._gs.entities.get(entity_id)
        return _map.get(e.ammo_type if e else 0)

    def get_global_resources(self) -> list[int]:
        """Return [titanium, axionite] for our team."""
        ti, ax = self._gs.get_resources(self._me().team)
        return [ti, ax, 0]

    def get_vision_radius_sq(self) -> int:
        return _VISION_SQ.get(self._me().entity_type, 20)

    def get_scale_percent(self) -> int:
        area = self._gs.width * self._gs.height
        return max(100, area // 6)

    def get_unit_count(self) -> int:
        team = self._me().team
        return sum(1 for e in self._gs.entities.values()
                   if e.team == team and e.entity_type == "BUILDER_BOT")

    def get_cpu_time_elapsed(self) -> int:
        return 0

    # ── Map info ──────────────────────────────────────────────────────────────

    def get_map_width(self) -> int:
        return self._gs.width

    def get_map_height(self) -> int:
        return self._gs.height

    def get_tile_env(self, position):
        from cambc import Environment
        _env_map = {
            0: Environment.EMPTY,
            1: Environment.EMPTY,          # WALL — bots shouldn't build here anyway
            2: Environment.ORE_TITANIUM,
            3: Environment.ORE_AXIONITE,
        }
        return _env_map.get(self._gs.get_tile_env(position.x, position.y), Environment.EMPTY)

    def get_tile_building_id(self, position) -> Optional[int]:
        return self._gs.get_building_at(position.x, position.y)

    def get_tile_builder_bot_id(self, position) -> Optional[int]:
        return self._gs.get_builder_at(position.x, position.y)

    def is_tile_passable(self, position) -> bool:
        return self._gs.is_passable(position.x, position.y)

    def is_in_vision(self, position) -> bool:
        me = self._me()
        dx = position.x - me.pos.x
        dy = position.y - me.pos.y
        return dx * dx + dy * dy <= self.get_vision_radius_sq()

    # ── Spatial queries ───────────────────────────────────────────────────────

    def get_nearby_tiles(self, dist_sq: int = None):
        from cambc import Position
        if dist_sq is None:
            dist_sq = self.get_vision_radius_sq()
        me = self._me()
        r  = int(math.isqrt(dist_sq)) + 1
        result = []
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if dx * dx + dy * dy <= dist_sq:
                    x, y = me.pos.x + dx, me.pos.y + dy
                    if 0 <= x < self._gs.width and 0 <= y < self._gs.height:
                        result.append(Position(x, y))
        return result

    def get_nearby_buildings(self, dist_sq: int = None):
        if dist_sq is None:
            dist_sq = self.get_vision_radius_sq()
        me = self._me()
        return [
            eid for eid, e in self._gs.entities.items()
            if e.entity_type not in ("BUILDER_BOT",)
            and (e.pos.x - me.pos.x) ** 2 + (e.pos.y - me.pos.y) ** 2 <= dist_sq
        ]

    def get_nearby_entities(self, dist_sq: int = None):
        if dist_sq is None:
            dist_sq = self.get_vision_radius_sq()
        me = self._me()
        return [
            eid for eid, e in self._gs.entities.items()
            if (e.pos.x - me.pos.x) ** 2 + (e.pos.y - me.pos.y) ** 2 <= dist_sq
        ]

    def get_nearby_units(self, dist_sq: int = None):
        if dist_sq is None:
            dist_sq = self.get_vision_radius_sq()
        me = self._me()
        return [
            eid for eid, e in self._gs.entities.items()
            if e.entity_type == "BUILDER_BOT"
            and (e.pos.x - me.pos.x) ** 2 + (e.pos.y - me.pos.y) ** 2 <= dist_sq
        ]

    def get_direction_from_to(self, from_pos, to_pos):
        from cambc import Direction
        dx = to_pos.x - from_pos.x
        dy = to_pos.y - from_pos.y
        sx = (1 if dx > 0 else -1 if dx < 0 else 0)
        sy = (1 if dy > 0 else -1 if dy < 0 else 0)
        _dir_map = {
            (0, 0):   Direction.CENTRE,    (0, 1):   Direction.NORTH,
            (1, 1):   Direction.NORTHEAST, (1, 0):   Direction.EAST,
            (1, -1):  Direction.SOUTHEAST, (0, -1):  Direction.SOUTH,
            (-1, -1): Direction.SOUTHWEST, (-1, 0):  Direction.WEST,
            (-1, 1):  Direction.NORTHWEST,
        }
        return _dir_map.get((sx, sy), Direction.CENTRE)

    # ── Entity-specific queries ───────────────────────────────────────────────

    def get_marker_value(self, marker_id: int) -> int:
        e = self._gs.entities.get(marker_id)
        return e.marker_value if e and e.entity_type == "MARKER" else 0

    def get_stored_resource_id(self, entity_id: int = None) -> Optional[int]:
        e = self._gs.entities.get(entity_id if entity_id is not None else self._unit_id)
        return e.stored_resource if e else None

    def get_load(self, entity_id: int = None) -> int:
        return 0   # simplified; conveyor load not tracked

    def get_bridge_target(self, entity_id: int = None):
        from cambc import Position
        e = self._gs.entities.get(entity_id if entity_id is not None else self._unit_id)
        if e and e.bridge_target:
            return Position(e.bridge_target.x, e.bridge_target.y)
        return None

    def get_conveyor_speed(self, entity_id: int = None) -> int:
        return 1

    def get_vision_radius_sq_of(self, entity_id: int) -> int:
        e = self._gs.entities.get(entity_id)
        return _VISION_SQ.get(e.entity_type, 20) if e else 20

    def get_gunner_target(self):
        """Compute closest in-line entity in the turret's facing direction."""
        from cambc import Position
        me = self._me()
        dx, dy = _DIR_DELTA.get(me.direction, (0, 0))
        if dx == 0 and dy == 0:
            return None
        x, y  = me.pos.x + dx, me.pos.y + dy
        max_r = int(math.isqrt(_VISION_SQ.get("GUNNER", 13))) + 1
        for _ in range(max_r):
            if not (0 <= x < self._gs.width and 0 <= y < self._gs.height):
                break
            if self._gs.get_building_at(x, y) or self._gs.get_builder_at(x, y):
                return Position(x, y)
            x += dx; y += dy
        return None

    # ── Feasibility checks (approximate) ─────────────────────────────────────

    def can_move(self, direction) -> bool:
        me = self._me()
        if me.move_cooldown > 0:
            return False
        dval = direction.value if hasattr(direction, "value") else int(direction)
        dx, dy = _DIR_DELTA.get(dval, (0, 0))
        return self._gs.is_passable(me.pos.x + dx, me.pos.y + dy)

    def can_heal(self, position) -> bool:
        me = self._me()
        ti, _ = self._gs.get_resources(me.team)
        return me.action_cooldown == 0 and ti >= 1

    def can_build_harvester(self, position) -> bool:
        me = self._me()
        if me.action_cooldown != 0:
            return False
        env = self._gs.get_tile_env(position.x, position.y)
        return env in (2, 3) and self._afford("HARVESTER")

    def can_build_road(self, position) -> bool:
        return self._me().action_cooldown == 0 and self._afford("ROAD")

    def can_build_conveyor(self, position, direction) -> bool:
        return self._me().action_cooldown == 0 and self._afford("CONVEYOR")

    def can_build_splitter(self, position, direction) -> bool:
        return self._me().action_cooldown == 0 and self._afford("SPLITTER")

    def can_build_bridge(self, position, target) -> bool:
        return self._me().action_cooldown == 0 and self._afford("BRIDGE")

    def can_build_armoured_conveyor(self, position, direction) -> bool:
        return self._me().action_cooldown == 0 and self._afford("ARMOURED_CONVEYOR")

    def can_build_gunner(self, position, direction) -> bool:
        return self._me().action_cooldown == 0 and self._afford("GUNNER")

    def can_build_sentinel(self, position, direction) -> bool:
        return self._me().action_cooldown == 0 and self._afford("SENTINEL")

    def can_build_breach(self, position, direction) -> bool:
        return self._me().action_cooldown == 0 and self._afford("BREACH")

    def can_build_launcher(self, position) -> bool:
        return self._me().action_cooldown == 0 and self._afford("LAUNCHER")

    def can_build_foundry(self, position) -> bool:
        return self._me().action_cooldown == 0 and self._afford("FOUNDRY")

    def can_build_barrier(self, position) -> bool:
        return self._me().action_cooldown == 0 and self._afford("BARRIER")

    def can_destroy(self, building_pos) -> bool:
        return self._gs.get_building_at(building_pos.x, building_pos.y) is not None

    def can_place_marker(self, position) -> bool:
        return True   # simplified

    def can_fire(self, target) -> bool:
        me = self._me()
        return (me.action_cooldown == 0 and
                me.entity_type in ("BUILDER_BOT", "GUNNER", "SENTINEL", "BREACH"))

    def can_launch(self, bot_pos, target) -> bool:
        me = self._me()
        return me.entity_type == "LAUNCHER" and me.action_cooldown == 0

    def can_spawn(self, position) -> bool:
        me = self._me()
        return (me.entity_type == "CORE" and me.action_cooldown == 0
                and self._afford("BUILDER_BOT"))

    # ── Cost queries ──────────────────────────────────────────────────────────

    def _cost(self, build_type: str) -> tuple[int, int]:
        return _BUILD_COST.get(build_type, (0, 0))

    def get_conveyor_cost(self):          return self._cost("CONVEYOR")
    def get_splitter_cost(self):          return self._cost("SPLITTER")
    def get_bridge_cost(self):            return self._cost("BRIDGE")
    def get_armoured_conveyor_cost(self): return self._cost("ARMOURED_CONVEYOR")
    def get_harvester_cost(self):         return self._cost("HARVESTER")
    def get_road_cost(self):              return self._cost("ROAD")
    def get_barrier_cost(self):           return self._cost("BARRIER")
    def get_gunner_cost(self):            return self._cost("GUNNER")
    def get_sentinel_cost(self):          return self._cost("SENTINEL")
    def get_breach_cost(self):            return self._cost("BREACH")
    def get_launcher_cost(self):          return self._cost("LAUNCHER")
    def get_foundry_cost(self):           return self._cost("FOUNDRY")
    def get_builder_bot_cost(self):       return self._cost("BUILDER_BOT")

    # ── Action methods (log only, no side-effects) ────────────────────────────

    def move(self, direction) -> None:
        self._log(f"move({direction.name})")

    def spawn_builder(self, position) -> int:
        self._log(f"spawn_builder({position})")
        return 0

    def build_conveyor(self, position, direction) -> int:
        self._log(f"build_conveyor({position}, {direction.name})")
        return 0

    def build_splitter(self, position, direction) -> int:
        self._log(f"build_splitter({position}, {direction.name})")
        return 0

    def build_bridge(self, position, target) -> int:
        self._log(f"build_bridge({position}, target={target})")
        return 0

    def build_armoured_conveyor(self, position, direction) -> int:
        self._log(f"build_armoured_conveyor({position}, {direction.name})")
        return 0

    def build_harvester(self, position) -> int:
        self._log(f"build_harvester({position})")
        return 0

    def build_road(self, position) -> int:
        self._log(f"build_road({position})")
        return 0

    def build_barrier(self, position) -> int:
        self._log(f"build_barrier({position})")
        return 0

    def build_gunner(self, position, direction) -> int:
        self._log(f"build_gunner({position}, {direction.name})")
        return 0

    def build_sentinel(self, position, direction) -> int:
        self._log(f"build_sentinel({position}, {direction.name})")
        return 0

    def build_breach(self, position, direction) -> int:
        self._log(f"build_breach({position}, {direction.name})")
        return 0

    def build_launcher(self, position) -> int:
        self._log(f"build_launcher({position})")
        return 0

    def build_foundry(self, position) -> int:
        self._log(f"build_foundry({position})")
        return 0

    def heal(self, position) -> None:
        self._log(f"heal({position})")

    def destroy(self, building_pos) -> None:
        self._log(f"destroy({building_pos})")

    def self_destruct(self) -> None:
        self._log("self_destruct()")

    def resign(self) -> None:
        self._log("resign()")

    def fire(self, target) -> None:
        self._log(f"fire({target})")

    def launch(self, bot_pos, target) -> None:
        self._log(f"launch(bot={bot_pos}, target={target})")

    def place_marker(self, position, value: int) -> None:
        self._log(f"place_marker({position}, {value:#010x})")

    def draw_indicator_dot(self, pos, r: int, g: int, b: int) -> None:
        self._log(f"draw_indicator_dot({pos}, rgb=({r},{g},{b}))")

    def draw_indicator_line(self, pos_a, pos_b, r: int, g: int, b: int) -> None:
        self._log(f"draw_indicator_line({pos_a} → {pos_b}, rgb=({r},{g},{b}))")
