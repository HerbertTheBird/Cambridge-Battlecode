"""
GameState: reconstructs and maintains the full game state from a replay's
sequence of per-turn updates.  All entity positions, HP, cooldowns, and
team resources are kept current so MockController can answer queries.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from replay_parser import (
    GameMap, Pos, CoreInfo,
    PlaceEntity, MoveBuilderBot, RemoveEntity, UpdateHp,
    UpdatePlayers, SetActionCooldown, SetMoveCooldown,
)

# ── Per-entity state ──────────────────────────────────────────────────────────

@dataclass
class EntityState:
    id:              int
    entity_type:     str        # "BUILDER_BOT", "CORE", "GUNNER", …
    team:            int        # 0 = Team A, 1 = Team B
    pos:             Pos
    hp:              int
    maxhp:           int
    action_cooldown: int = 0
    move_cooldown:   int = 0
    ammo_type:       int = 0    # ResourceType int
    ammo_amount:     int = 0
    direction:       int = 0    # Direction int (0=CENTRE, 1=NORTH, …)
    marker_value:    int = 0
    stored_resource: int = 0
    bridge_target:   Optional[Pos] = None

    def is_mobile(self) -> bool:
        return self.entity_type == "BUILDER_BOT"


# ── Mobile entity types (for spatial index choice) ────────────────────────────
_MOBILE = {"BUILDER_BOT"}


class GameState:
    """
    Tracks full game state at the current turn.

    Advance by calling advance_turn(turn.updates) for each GameTurn.
    """

    def __init__(self, game_map: GameMap) -> None:
        self.map     = game_map
        self.width   = game_map.width
        self.height  = game_map.height

        # terrain[y][x] — Environment int: 0=EMPTY, 1=WALL, 2=ORE_TI, 3=ORE_AX
        self.terrain: list[list[int]] = [
            list(row) for row in game_map.terrain
        ]

        self.entities: dict[int, EntityState] = {}
        # Spatial indices
        self._pos_building: dict[tuple[int, int], int] = {}   # pos → id (non-mobile)
        self._pos_builder:  dict[tuple[int, int], int] = {}   # pos → id (builder bots)

        # Team resources  (team 0 = A, team 1 = B)
        self.titanium: list[int] = [1000, 1000]   # STARTING_TITANIUM
        self.axionite: list[int] = [0, 0]

        self.current_round: int = 0

        # Seed CORE entities from map metadata (cores are never placed via PlaceEntity)
        _CORE_MAX_HP = 500
        for core in game_map.cores:
            self._add(EntityState(
                id=core.id, entity_type="CORE", team=core.team,
                pos=core.pos, hp=_CORE_MAX_HP, maxhp=_CORE_MAX_HP,
            ))

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _key(self, pos: Pos) -> tuple[int, int]:
        return (pos.x, pos.y)

    def _add(self, e: EntityState) -> None:
        self.entities[e.id] = e
        k = self._key(e.pos)
        if e.entity_type in _MOBILE:
            self._pos_builder[k] = e.id
        else:
            self._pos_building[k] = e.id

    def _remove(self, eid: int) -> None:
        e = self.entities.pop(eid, None)
        if e is None:
            return
        k = self._key(e.pos)
        if e.entity_type in _MOBILE:
            self._pos_builder.pop(k, None)
        else:
            self._pos_building.pop(k, None)

    # ── Update application ────────────────────────────────────────────────────

    def _apply(self, upd) -> None:
        if isinstance(upd, PlaceEntity):
            raw = upd.entity
            self._add(EntityState(
                id=raw.id, entity_type=raw.entity_type, team=raw.team,
                pos=raw.pos, hp=raw.hp, maxhp=raw.maxhp,
                action_cooldown=raw.action_cooldown,
                move_cooldown=raw.move_cooldown,
                ammo_type=raw.ammo_type, ammo_amount=raw.ammo_amount,
                direction=raw.direction, marker_value=raw.marker_value,
                stored_resource=raw.stored_resource,
                bridge_target=raw.bridge_target,
            ))

        elif isinstance(upd, MoveBuilderBot):
            e = self.entities.get(upd.id)
            if e:
                self._pos_builder.pop(self._key(e.pos), None)
                e.pos = upd.to
                self._pos_builder[self._key(upd.to)] = upd.id

        elif isinstance(upd, RemoveEntity):
            self._remove(upd.id)

        elif isinstance(upd, UpdateHp):
            e = self.entities.get(upd.id)
            if e:
                e.hp = max(0, e.hp + upd.delta)

        elif isinstance(upd, UpdatePlayers):
            self.titanium[0] = upd.a_titanium
            self.axionite[0] = upd.a_axionite
            self.titanium[1] = upd.b_titanium
            self.axionite[1] = upd.b_axionite

        elif isinstance(upd, SetActionCooldown):
            e = self.entities.get(upd.id)
            if e:
                e.action_cooldown = upd.value

        elif isinstance(upd, SetMoveCooldown):
            e = self.entities.get(upd.id)
            if e:
                e.move_cooldown = upd.value

    def advance_turn(self, updates: list) -> None:
        """Apply all updates for one turn and increment the round counter."""
        self.current_round += 1
        for upd in updates:
            self._apply(upd)

    # ── Query helpers (used by MockController) ─────────────────────────────────

    def get_tile_env(self, x: int, y: int) -> int:
        if 0 <= y < len(self.terrain) and 0 <= x < len(self.terrain[y]):
            return self.terrain[y][x]
        return 0

    def get_building_at(self, x: int, y: int) -> Optional[int]:
        return self._pos_building.get((x, y))

    def get_builder_at(self, x: int, y: int) -> Optional[int]:
        return self._pos_builder.get((x, y))

    def get_resources(self, team: int) -> tuple[int, int]:
        """Return (titanium, axionite) for the given team (0=A, 1=B)."""
        return self.titanium[team], self.axionite[team]

    def is_passable(self, x: int, y: int) -> bool:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        if self.get_tile_env(x, y) == 1:   # WALL
            return False
        if self.get_building_at(x, y) is not None:
            return False
        return True

    def entities_for_team(self, team: int) -> list[EntityState]:
        return [e for e in self.entities.values() if e.team == team]
