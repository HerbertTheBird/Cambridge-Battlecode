#!/usr/bin/env python3
"""
herberts_hardcoding.py

Standalone keyboard-driven self-play sandbox forked from bc_selfplay_ui.py.
Engine, map loader, and bot-code exporter are unchanged. The UI is replaced
with a single-team keyboard flow:

    arrows / wasd : move selection cursor (or pick direction in build sub-modes)
    space         : confirm
                       no building selected: move builder (auto-placing road
                                             when needed) or, on a core, spawn
                                             a builder on the cursor tile
                       conveyor / splitter : enter direction sub-mode, then place
                       bridge              : lock source, then move cursor to
                                             pick the target, then place
                       harvester / foundry : place at the cursor tile
    enter         : end the active unit's turn
    1 / 2 / 3 / 4 / 5 : select build type (conveyor / splitter / bridge /
                       harvester / foundry)
    0             : clear build selection
    delete        : destroy building on cursor (self-destruct if the cursor is
                    on the active builder)
    cmd-z         : undo last unit's turn
    cmd-shift-z   : redo

Whenever an action is unaffordable on Ti, the tool auto-converts just enough
Refined Axionite to Titanium (1 Ax -> 4 Ti, integer). All conversions for a
given round are summed and recorded as a single core convert call at that
round's core turn in the exported bot code.

Run:
    python herberts_hardcoding.py
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
import heapq
import math
import shlex
import shutil


# ----------------------------- basic types -----------------------------


@dataclass(frozen=True, order=True)
class Pos:
    x: int
    y: int

    def __add__(self, other: Tuple[int, int]) -> "Pos":
        dx, dy = other
        return Pos(self.x + dx, self.y + dy)

    def dist2(self, other: "Pos") -> int:
        dx = self.x - other.x
        dy = self.y - other.y
        return dx * dx + dy * dy


class Dir(Enum):
    N = (0, -1)
    NE = (1, -1)
    E = (1, 0)
    SE = (1, 1)
    S = (0, 1)
    SW = (-1, 1)
    W = (-1, 0)
    NW = (-1, -1)

    @property
    def dxdy(self) -> Tuple[int, int]:
        return self.value

    @property
    def cardinal(self) -> bool:
        dx, dy = self.value
        return (abs(dx) + abs(dy)) == 1

    def opposite(self) -> "Dir":
        dx, dy = self.value
        return DIR_BY_DXDY[(-dx, -dy)]

    def left90(self) -> "Dir":
        if not self.cardinal:
            raise ValueError("left90 only defined for cardinal directions")
        dx, dy = self.value
        return DIR_BY_DXDY[(dy, -dx)]

    def right90(self) -> "Dir":
        if not self.cardinal:
            raise ValueError("right90 only defined for cardinal directions")
        dx, dy = self.value
        return DIR_BY_DXDY[(-dy, dx)]


DIR_BY_DXDY: Dict[Tuple[int, int], Dir] = {d.value: d for d in Dir}
CARDINAL_DIRS: Tuple[Dir, ...] = (Dir.N, Dir.E, Dir.S, Dir.W)
ALL_MOVE_DIRS: Tuple[Dir, ...] = tuple(Dir)


class Terrain:
    EMPTY = 0
    CORE = 1
    TITANIUM_ORE = 2
    AXIONITE_ORE = 3
    WALL = 4


class Resource(Enum):
    TITANIUM = "Ti"
    RAW_AXIONITE = "RawAx"
    AXIONITE = "Ax"


@dataclass
class ResourceStack:
    kind: Resource
    amount: int = 10
    rid: int = -1

    def short(self) -> str:
        return f"{self.kind.value}{self.amount}#{self.rid}"


class EntityType(Enum):
    CORE = "core"
    BUILDER = "builder"
    ROAD = "road"
    CONVEYOR = "conveyor"
    SPLITTER = "splitter"
    BRIDGE = "bridge"
    ARMOURED_CONVEYOR = "armoured_conveyor"
    HARVESTER = "harvester"
    FOUNDRY = "foundry"


@dataclass(frozen=True)
class Cost:
    ti: int = 0
    ax: int = 0

    def scaled(self, scale_bps: int) -> "Cost":
        # Battlecode uses floor(scale * base cost). Apply independently to each component.
        return Cost(
            ti=(self.ti * scale_bps) // 10_000,
            ax=(self.ax * scale_bps) // 10_000,
        )

    def __bool__(self) -> bool:
        return self.ti != 0 or self.ax != 0

    def short(self) -> str:
        parts: List[str] = []
        if self.ti:
            parts.append(f"{self.ti} Ti")
        if self.ax:
            parts.append(f"{self.ax} Ax")
        return ", ".join(parts) if parts else "free"


@dataclass(frozen=True)
class Stat:
    max_hp: int
    base_cost: Cost
    scale_bps: int
    is_unit: bool = False
    walkable: bool = False


STATS: Dict[EntityType, Stat] = {
    EntityType.CORE: Stat(max_hp=500, base_cost=Cost(), scale_bps=0, is_unit=True, walkable=True),
    EntityType.BUILDER: Stat(max_hp=40, base_cost=Cost(ti=30), scale_bps=2000, is_unit=True),
    EntityType.ROAD: Stat(max_hp=4, base_cost=Cost(ti=1), scale_bps=50, walkable=True),
    EntityType.CONVEYOR: Stat(max_hp=20, base_cost=Cost(ti=3), scale_bps=100, walkable=True),
    EntityType.SPLITTER: Stat(max_hp=20, base_cost=Cost(ti=6), scale_bps=100, walkable=True),
    EntityType.BRIDGE: Stat(max_hp=20, base_cost=Cost(ti=20), scale_bps=1000, walkable=True),
    EntityType.ARMOURED_CONVEYOR: Stat(max_hp=50, base_cost=Cost(ti=5, ax=5), scale_bps=100, walkable=True),
    EntityType.HARVESTER: Stat(max_hp=30, base_cost=Cost(ti=20), scale_bps=500),
    EntityType.FOUNDRY: Stat(max_hp=50, base_cost=Cost(ti=40), scale_bps=5000),
}


@dataclass
class Entity:
    eid: int
    typ: EntityType
    team: int
    pos: Pos
    hp: int
    occupied: Set[Pos]
    direction: Optional[Dir] = None
    bridge_target: Optional[Pos] = None
    action_cd: int = 0
    move_cd: int = 0
    stored: Optional[ResourceStack] = None
    foundry_ti: Optional[ResourceStack] = None
    foundry_raw: Optional[ResourceStack] = None
    last_used_dir_round: Dict[Dir, int] = field(default_factory=dict)
    last_moved_round: int = -10**9
    built_round: int = 0
    next_harvest_round: Optional[int] = None
    alive: bool = True

    @property
    def is_unit(self) -> bool:
        return STATS[self.typ].is_unit

    @property
    def max_hp(self) -> int:
        return STATS[self.typ].max_hp

    def label(self) -> str:
        return f"#{self.eid} {self.typ.value} T{self.team} @({self.pos.x},{self.pos.y})"


@dataclass
class TeamState:
    titanium: int = 500
    axionite: int = 0
    ti_collected: int = 0
    ax_collected: int = 0
    scale_bps_extra: int = 0

    @property
    def scale_bps(self) -> int:
        return 10_000 + self.scale_bps_extra

    @property
    def scale_percent(self) -> float:
        return self.scale_bps / 100.0


class RuleError(Exception):
    pass


# ----------------------------- game engine -----------------------------


class Game:
    def __init__(self, terrain: List[List[int]], core_specs: Optional[List[Tuple[int, Pos, Set[Pos]]]] = None) -> None:
        if not terrain or not terrain[0]:
            raise ValueError("terrain must be a non-empty rectangular 2D grid")
        w = len(terrain[0])
        if any(len(row) != w for row in terrain):
            raise ValueError("terrain must be rectangular")

        self.h = len(terrain)
        self.w = w
        self.round = 0
        self.terrain: List[List[int]] = [list(row) for row in terrain]
        self.entities: Dict[int, Entity] = {}
        self.building_at: Dict[Pos, int] = {}
        self.unit_at: Dict[Pos, int] = {}
        self.spawn_order: List[int] = []
        self.teams: Dict[int, TeamState] = {}
        self.next_eid = 0
        self.next_rid = 1
        self.log: List[str] = []

        if core_specs is None:
            self._place_cores_from_terrain()
        else:
            self._place_cores_from_specs(core_specs)

    @classmethod
    def from_int_grid(cls, grid: Sequence[Sequence[int]]) -> "Game":
        return cls([list(row) for row in grid])

    # ----------------------------- setup -----------------------------

    def _place_cores_from_terrain(self) -> None:
        seen: Set[Pos] = set()
        core_regions: List[Set[Pos]] = []
        for y in range(self.h):
            for x in range(self.w):
                p = Pos(x, y)
                if self.terrain[y][x] != Terrain.CORE or p in seen:
                    continue
                comp: Set[Pos] = set()
                stack = [p]
                seen.add(p)
                while stack:
                    q = stack.pop()
                    comp.add(q)
                    for d in CARDINAL_DIRS:
                        n = q + d.dxdy
                        if self.in_bounds(n) and n not in seen and self.terrain[n.y][n.x] == Terrain.CORE:
                            seen.add(n)
                            stack.append(n)
                core_regions.append(comp)

        if not core_regions:
            raise ValueError("grid must contain at least one core tile (1)")

        for team, tiles in enumerate(core_regions):
            self.teams[team] = TeamState()
            cx = round(sum(p.x for p in tiles) / len(tiles))
            cy = round(sum(p.y for p in tiles) / len(tiles))
            # Convert core terrain into empty terrain plus a core building footprint.
            for p in tiles:
                self.terrain[p.y][p.x] = Terrain.EMPTY
            self._create_entity(EntityType.CORE, team=team, pos=Pos(cx, cy), occupied=tiles, charge_cost=False)

    def _place_cores_from_specs(self, core_specs: List[Tuple[int, Pos, Set[Pos]]]) -> None:
        if not core_specs:
            raise ValueError("map must contain at least one core")

        for team, center, tiles in sorted(core_specs, key=lambda spec: spec[0]):
            if team not in self.teams:
                self.teams[team] = TeamState()
            if not tiles:
                raise ValueError("core footprint cannot be empty")
            for p in tiles:
                if not self.in_bounds(p):
                    raise ValueError(f"core footprint tile out of bounds: {p}")
                self.terrain[p.y][p.x] = Terrain.EMPTY
            self._create_entity(
                EntityType.CORE,
                team=team,
                pos=center,
                occupied=set(tiles),
                charge_cost=False,
            )

    def _create_entity(
        self,
        typ: EntityType,
        team: int,
        pos: Pos,
        occupied: Optional[Set[Pos]] = None,
        direction: Optional[Dir] = None,
        bridge_target: Optional[Pos] = None,
        charge_cost: bool = True,
    ) -> Entity:
        if team not in self.teams:
            self.teams[team] = TeamState()
        stat = STATS[typ]
        occ = occupied if occupied is not None else {pos}
        eid = self.next_eid
        self.next_eid += 1
        ent = Entity(
            eid=eid,
            typ=typ,
            team=team,
            pos=pos,
            occupied=set(occ),
            hp=stat.max_hp,
            direction=direction,
            bridge_target=bridge_target,
            built_round=self.round,
        )
        if typ == EntityType.HARVESTER:
            ent.next_harvest_round = self.round  # first output happens at end of build round
        if typ in (EntityType.HARVESTER, EntityType.SPLITTER, EntityType.FOUNDRY):
            ent.last_used_dir_round = {d: -10**9 for d in CARDINAL_DIRS}
        if typ == EntityType.SPLITTER and direction is not None:
            ent.last_used_dir_round = {d: -10**9 for d in (direction, direction.left90(), direction.right90())}

        self.entities[eid] = ent
        # The core is both a unit and a building. It acts in spawn order, but it
        # also occupies its full footprint for delivery, spawning, and movement.
        if typ == EntityType.CORE:
            self.spawn_order.append(eid)
            for p in ent.occupied:
                self.building_at[p] = eid
        elif stat.is_unit:
            self.spawn_order.append(eid)
            if typ == EntityType.BUILDER:
                self.unit_at[pos] = eid
        else:
            for p in ent.occupied:
                self.building_at[p] = eid

        if charge_cost:
            self.teams[team].scale_bps_extra += stat.scale_bps
        return ent

    # ----------------------------- queries -----------------------------

    def in_bounds(self, p: Pos) -> bool:
        return 0 <= p.x < self.w and 0 <= p.y < self.h

    def terrain_at(self, p: Pos) -> int:
        if not self.in_bounds(p):
            return Terrain.WALL
        return self.terrain[p.y][p.x]

    def entity(self, eid: int) -> Entity:
        e = self.entities.get(eid)
        if e is None or not e.alive:
            raise RuleError(f"entity #{eid} does not exist or is dead")
        return e

    def building_at_pos(self, p: Pos) -> Optional[Entity]:
        eid = self.building_at.get(p)
        if eid is None:
            return None
        ent = self.entities.get(eid)
        return ent if ent and ent.alive else None

    def unit_at_pos(self, p: Pos) -> Optional[Entity]:
        eid = self.unit_at.get(p)
        if eid is None:
            return None
        ent = self.entities.get(eid)
        return ent if ent and ent.alive else None

    def unit_count(self, team: int) -> int:
        return sum(1 for e in self.entities.values() if e.alive and e.team == team and e.is_unit)

    def cost_for(self, team: int, typ: EntityType) -> Cost:
        return STATS[typ].base_cost.scaled(self.teams[team].scale_bps)

    def can_afford(self, team: int, cost: Cost) -> bool:
        ts = self.teams[team]
        return ts.titanium >= cost.ti and ts.axionite >= cost.ax

    def charge(self, team: int, cost: Cost) -> None:
        if not self.can_afford(team, cost):
            raise RuleError(f"team {team} cannot afford {cost.short()}; has {self.resources_short(team)}")
        self.teams[team].titanium -= cost.ti
        self.teams[team].axionite -= cost.ax

    def action_range2(self, ent: Entity) -> int:
        return 8 if ent.typ == EntityType.CORE else 2

    def in_action_range(self, ent: Entity, target: Pos) -> bool:
        return ent.pos.dist2(target) <= self.action_range2(ent)

    def is_walkable_for_builder(self, team: int, p: Pos) -> bool:
        b = self.building_at_pos(p)
        if b is None:
            return False
        if b.typ == EntityType.CORE:
            return b.team == team
        return STATS[b.typ].walkable

    def resources_short(self, team: int) -> str:
        ts = self.teams[team]
        return f"Ti={ts.titanium}, Ax={ts.axionite}, scale={ts.scale_percent:.1f}%"

    # ----------------------------- actions -----------------------------

    def spawn_builder(self, core_id: int, pos: Pos) -> Entity:
        core = self.entity(core_id)
        if core.typ != EntityType.CORE:
            raise RuleError("spawn_builder requires a core")
        if core.action_cd != 0:
            raise RuleError("core action cooldown is not 0")
        if pos not in core.occupied:
            raise RuleError("builder must spawn on one of the core footprint tiles")
        if self.unit_at_pos(pos) is not None:
            raise RuleError("spawn tile already has a unit")
        if self.unit_count(core.team) >= 50:
            raise RuleError("unit cap reached")
        cost = self.cost_for(core.team, EntityType.BUILDER)
        self.charge(core.team, cost)
        bot = self._create_entity(EntityType.BUILDER, core.team, pos)
        core.action_cd += 1
        self.log.append(
            f"T{core.team} core #{core.eid} spawned builder #{bot.eid} at {pos} "
            f"for {cost.short()} (eligible next round)"
        )
        return bot

    def convert_axionite(self, core_id: int, amount: int) -> None:
        core = self.entity(core_id)
        if core.typ != EntityType.CORE:
            raise RuleError("convert_axionite requires a core")
        if amount <= 0:
            raise RuleError("amount must be positive")
        ts = self.teams[core.team]
        if ts.axionite < amount:
            raise RuleError("not enough axionite")
        ts.axionite -= amount
        ti_gain = 4 * amount
        ts.titanium += ti_gain
        ts.ax_collected -= amount
        ts.ti_collected += ti_gain
        self.log.append(f"T{core.team} converted {amount} Ax -> {ti_gain} Ti")

    def move_builder(self, builder_id: int, direction: Dir) -> None:
        b = self.entity(builder_id)
        if b.typ != EntityType.BUILDER:
            raise RuleError("move_builder requires a builder")
        if b.move_cd != 0:
            raise RuleError("builder move cooldown is not 0")
        target = b.pos + direction.dxdy
        if not self.in_bounds(target):
            raise RuleError("move target out of bounds")
        if self.unit_at_pos(target) is not None:
            raise RuleError("move target already has a unit")
        if not self.is_walkable_for_builder(b.team, target):
            raise RuleError("builder can only move onto walkable buildings/roads/allied core")
        self.unit_at.pop(b.pos, None)
        b.pos = target
        b.occupied = {target}
        self.unit_at[target] = b.eid
        b.move_cd += 1
        self.log.append(f"builder #{b.eid} moved {direction.name} to {target}")

    def build(
        self,
        builder_id: int,
        typ: EntityType,
        pos: Pos,
        direction: Optional[Dir] = None,
        bridge_target: Optional[Pos] = None,
    ) -> Entity:
        b = self.entity(builder_id)
        if b.typ != EntityType.BUILDER:
            raise RuleError("build requires a builder")
        if b.action_cd != 0:
            raise RuleError("builder action cooldown is not 0")
        if typ in (EntityType.CORE, EntityType.BUILDER):
            raise RuleError("builders cannot build that entity type")
        if not self.in_action_range(b, pos):
            raise RuleError("build target is outside action radius")
        if not self.in_bounds(pos):
            raise RuleError("build target out of bounds")
        if self.building_at_pos(pos) is not None:
            raise RuleError("target tile already has a building")
        terrain = self.terrain_at(pos)
        if terrain == Terrain.WALL:
            raise RuleError("cannot build on a wall")
        if typ == EntityType.HARVESTER:
            if terrain not in (Terrain.TITANIUM_ORE, Terrain.AXIONITE_ORE):
                raise RuleError("harvester must be built on titanium or axionite ore")
        if self.unit_at_pos(pos) is not None and not STATS[typ].walkable:
            raise RuleError("only walkable buildings can be built under an existing builder")
        if typ in (EntityType.CONVEYOR, EntityType.SPLITTER, EntityType.ARMOURED_CONVEYOR):
            if direction is None or not direction.cardinal:
                raise RuleError(f"{typ.value} requires a cardinal direction")
        if typ == EntityType.BRIDGE:
            if bridge_target is None:
                raise RuleError("bridge requires a target position")
            if pos.dist2(bridge_target) > 9:
                raise RuleError("bridge target must be within Euclidean distance 3")
        cost = self.cost_for(b.team, typ)
        self.charge(b.team, cost)
        ent = self._create_entity(typ, b.team, pos, direction=direction, bridge_target=bridge_target)
        b.action_cd += 1
        extra = ""
        if direction is not None:
            extra += f" dir={direction.name}"
        if bridge_target is not None:
            extra += f" target={bridge_target}"
        self.log.append(f"builder #{b.eid} built {typ.value} #{ent.eid} at {pos}{extra} for {cost.short()}")
        return ent

    def heal(self, builder_id: int, pos: Pos) -> None:
        b = self.entity(builder_id)
        if b.typ != EntityType.BUILDER:
            raise RuleError("heal requires a builder")
        if b.action_cd != 0:
            raise RuleError("builder action cooldown is not 0")
        if not self.in_action_range(b, pos):
            raise RuleError("heal target outside action radius")
        targets: List[Entity] = []
        for ent in (self.building_at_pos(pos), self.unit_at_pos(pos)):
            if ent and ent.team == b.team and ent.hp < ent.max_hp:
                targets.append(ent)
        if not targets:
            raise RuleError("nothing friendly on that tile can be healed")
        self.charge(b.team, Cost(ti=1))
        for ent in targets:
            ent.hp = min(ent.max_hp, ent.hp + 4)
        b.action_cd += 1
        self.log.append(f"builder #{b.eid} healed {len(targets)} entity/entities at {pos}")

    def builder_attack_tile(self, builder_id: int) -> None:
        b = self.entity(builder_id)
        if b.typ != EntityType.BUILDER:
            raise RuleError("attack requires a builder")
        if b.action_cd != 0:
            raise RuleError("builder action cooldown is not 0")
        target = self.building_at_pos(b.pos)
        if target is None or target.team == b.team:
            raise RuleError("builder can only attack an enemy building on its current tile")
        if target.typ == EntityType.ARMOURED_CONVEYOR:
            raise RuleError("armoured conveyors are immune to builder attacks")
        self.charge(b.team, Cost(ti=2))
        b.action_cd += 1
        self.damage(target.eid, 2)
        self.log.append(f"builder #{b.eid} attacked building #{target.eid} for 2 damage")

    def destroy_building(self, actor_id: int, pos: Pos) -> None:
        actor = self.entity(actor_id)
        if actor.typ != EntityType.BUILDER:
            raise RuleError("destroy_building currently requires a builder")
        if not self.in_action_range(actor, pos):
            raise RuleError("destroy target outside action radius")
        target = self.building_at_pos(pos)
        if target is None:
            raise RuleError("no building at target")
        if target.team != actor.team:
            raise RuleError("can only destroy allied buildings")
        if target.typ == EntityType.CORE:
            raise RuleError("refusing to destroy own core in this sandbox")
        self.kill_entity(target.eid, remove_scale=True)
        self.log.append(f"builder #{actor.eid} destroyed allied {target.typ.value} #{target.eid}")

    def self_destruct(self, unit_id: int) -> None:
        ent = self.entity(unit_id)
        if ent.typ == EntityType.CORE:
            raise RuleError("core self-destruct disabled in this sandbox")
        self.kill_entity(unit_id, remove_scale=True)
        self.log.append(f"{ent.typ.value} #{ent.eid} self-destructed")

    # ----------------------------- damage/death -----------------------------

    def damage(self, eid: int, amount: int) -> None:
        ent = self.entity(eid)
        ent.hp -= amount
        if ent.hp <= 0:
            self.kill_entity(eid, remove_scale=True)

    def kill_entity(self, eid: int, remove_scale: bool) -> None:
        ent = self.entity(eid)
        ent.alive = False
        if ent.is_unit:
            if ent.typ == EntityType.BUILDER:
                self.unit_at.pop(ent.pos, None)
        else:
            for p in ent.occupied:
                self.building_at.pop(p, None)
        if remove_scale:
            self.teams[ent.team].scale_bps_extra -= STATS[ent.typ].scale_bps
            if self.teams[ent.team].scale_bps_extra < 0:
                self.teams[ent.team].scale_bps_extra = 0

    # ----------------------------- resource distribution -----------------------------

    def _new_stack(self, kind: Resource, amount: int = 10) -> ResourceStack:
        rid = self.next_rid
        self.next_rid += 1
        return ResourceStack(kind=kind, amount=amount, rid=rid)

    def _incoming_dir_for_adjacent(self, src: Pos, dst: Pos) -> Optional[Dir]:
        dx = src.x - dst.x
        dy = src.y - dst.y
        return DIR_BY_DXDY.get((dx, dy))

    def _can_accept(self, dst: Entity, stack: ResourceStack, src: Pos, via_bridge: bool = False) -> bool:
        if not dst.alive:
            return False
        if dst.typ == EntityType.CORE:
            return True
        if dst.typ in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR, EntityType.BRIDGE):
            if dst.stored is not None:
                return False
            if via_bridge or dst.typ == EntityType.BRIDGE:
                return True
            incoming = self._incoming_dir_for_adjacent(src, dst.pos)
            if incoming is None:
                return False
            # Conveyor accepts from any non-output direction.
            return incoming != dst.direction
        if dst.typ == EntityType.SPLITTER:
            if dst.stored is not None:
                return False
            if via_bridge:
                return True
            incoming = self._incoming_dir_for_adjacent(src, dst.pos)
            return incoming == dst.direction.opposite()
        if dst.typ == EntityType.FOUNDRY:
            # Refined axionite in `stored` blocks both input slots until it leaves.
            if dst.stored is not None:
                return False
            if stack.kind == Resource.TITANIUM:
                return dst.foundry_ti is None
            if stack.kind == Resource.RAW_AXIONITE:
                return dst.foundry_raw is None
            return False
        return False

    def _would_accept_after_drain(self, dst: Entity, stack: ResourceStack, src: Pos, via_bridge: bool = False) -> bool:
        # Edge-feasibility check used only for topological sort. Assumes dst's `stored`
        # slot will be drained this round (since dst is in outputs and processes first).
        # Geometric/type rules still apply: a conveyor whose output direction faces the
        # source can never accept from it, so no edge should be created.
        if not dst.alive:
            return False
        saved = dst.stored
        dst.stored = None
        try:
            return self._can_accept(dst, stack, src, via_bridge=via_bridge)
        finally:
            dst.stored = saved

    def _accept(self, dst: Entity, stack: ResourceStack, src: Pos, via_bridge: bool = False) -> bool:
        if not self._can_accept(dst, stack, src, via_bridge=via_bridge):
            return False
        if dst.typ == EntityType.CORE:
            ts = self.teams[dst.team]
            if stack.kind == Resource.TITANIUM:
                ts.titanium += stack.amount
                ts.ti_collected += stack.amount
                self.log.append(f"core #{dst.eid} received {stack.short()}")
            elif stack.kind == Resource.AXIONITE:
                ts.axionite += stack.amount
                ts.ax_collected += stack.amount
                self.log.append(f"core #{dst.eid} received {stack.short()}")
            elif stack.kind == Resource.RAW_AXIONITE:
                self.log.append(f"core #{dst.eid} destroyed raw axionite stack {stack.short()}")
            return True
        if dst.typ == EntityType.FOUNDRY:
            if stack.kind == Resource.TITANIUM:
                dst.foundry_ti = stack
            elif stack.kind == Resource.RAW_AXIONITE:
                dst.foundry_raw = stack
            return True
        dst.stored = stack
        return True

    def _try_send_to_pos(self, src: Entity, stack: ResourceStack, dst_pos: Pos, via_bridge: bool = False) -> bool:
        dst = self.building_at_pos(dst_pos)
        if dst is None:
            return False
        ok = self._accept(dst, stack, src.pos, via_bridge=via_bridge)
        if ok:
            self.log.append(f"{src.typ.value} #{src.eid} sent {stack.short()} to {dst.typ.value} #{dst.eid}")
        return ok

    def _try_output_dirs(self, src: Entity, stack: ResourceStack, dirs: Iterable[Dir]) -> Optional[Dir]:
        for d in dirs:
            dst = src.pos + d.dxdy
            if self._try_send_to_pos(src, stack, dst):
                return d
        return None

    def _output_options(self, ent: Entity) -> List[Dir]:
        if ent.typ in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR):
            return [ent.direction] if ent.direction else []
        if ent.typ == EntityType.SPLITTER and ent.direction:
            opts = [ent.direction, ent.direction.left90(), ent.direction.right90()]
            return sorted(opts, key=lambda d: ent.last_used_dir_round.get(d, -10**9))
        if ent.typ in (EntityType.HARVESTER, EntityType.FOUNDRY):
            return sorted(CARDINAL_DIRS, key=lambda d: ent.last_used_dir_round.get(d, -10**9))
        return []

    def distribute_resources(self) -> None:
        for _ in self.iter_distribute_resources():
            pass

    def iter_distribute_resources(self):
        # Snapshot output sources first so resources move only one hop per round.
        outputs: List[Tuple[int, ResourceStack]] = []
        for eid in sorted(self.entities):
            ent = self.entities[eid]
            if not ent.alive:
                continue
            if ent.typ == EntityType.HARVESTER:
                if ent.next_harvest_round is not None and self.round >= ent.next_harvest_round:
                    ore = self.terrain_at(ent.pos)
                    if ore == Terrain.TITANIUM_ORE:
                        outputs.append((eid, self._new_stack(Resource.TITANIUM)))
                    elif ore == Terrain.AXIONITE_ORE:
                        outputs.append((eid, self._new_stack(Resource.RAW_AXIONITE)))
                    else:
                        # No ore beneath; throttle to avoid re-checking every round.
                        ent.next_harvest_round = self.round + 4
            elif ent.typ in (EntityType.CONVEYOR, EntityType.SPLITTER, EntityType.BRIDGE,
                             EntityType.ARMOURED_CONVEYOR, EntityType.FOUNDRY):
                if ent.stored is not None:
                    outputs.append((eid, ent.stored))

        # Topological sort: process downstream nodes first so saturated chains can shift.
        # Edge A -> B means "A wants to send to B, so B must be processed first."
        out_eid_set = {eid for eid, _ in outputs}
        eid_to_stack = {eid: stack for eid, stack in outputs}
        deps: Dict[int, Set[int]] = {eid: set() for eid, _ in outputs}
        rev: Dict[int, Set[int]] = {eid: set() for eid, _ in outputs}
        for eid, _ in outputs:
            ent = self.entities[eid]
            stack = eid_to_stack[eid]
            via_bridge = (ent.typ == EntityType.BRIDGE)
            targets: List[Pos] = []
            if ent.typ == EntityType.BRIDGE:
                if ent.bridge_target is not None:
                    targets.append(ent.bridge_target)
            else:
                for d in self._output_options(ent):
                    targets.append(ent.pos + d.dxdy)
            for tp in targets:
                other = self.building_at_pos(tp)
                if other is None or not other.alive:
                    continue
                if other.eid == eid or other.eid not in out_eid_set:
                    continue
                # Skip edges where the destination cannot geometrically/typologically
                # accept this source's stack. This avoids spurious cycles like
                # F <-> C when F's output options include the input conveyor's tile
                # but the conveyor faces back into F (so F could never actually send to it).
                if not self._would_accept_after_drain(other, stack, ent.pos, via_bridge=via_bridge):
                    continue
                if other.eid not in deps[eid]:
                    deps[eid].add(other.eid)
                    rev[other.eid].add(eid)

        # Heap key: (last_moved_round, eid). Least-recently-used ties broken by eid.
        def heap_key(eid: int) -> Tuple[int, int]:
            ent = self.entities.get(eid)
            lmr = ent.last_moved_round if ent is not None else 0
            return (lmr, eid)

        ready = [heap_key(eid) for eid in deps if not deps[eid]]
        heapq.heapify(ready)
        ordered_eids: List[int] = []
        remaining = {eid: set(s) for eid, s in deps.items()}
        while ready:
            _, eid = heapq.heappop(ready)
            ordered_eids.append(eid)
            for dep in rev[eid]:
                remaining[dep].discard(eid)
                if not remaining[dep]:
                    heapq.heappush(ready, heap_key(dep))
        if len(ordered_eids) < len(deps):
            placed = set(ordered_eids)
            leftover = sorted(
                (eid for eid in deps if eid not in placed),
                key=heap_key,
            )
            ordered_eids.extend(leftover)

        outputs = [(eid, eid_to_stack[eid]) for eid in ordered_eids]

        for eid, stack in outputs:
            ent = self.entities.get(eid)
            if ent is None or not ent.alive:
                continue
            moved = False
            if ent.typ == EntityType.BRIDGE:
                if ent.bridge_target is not None:
                    moved = self._try_send_to_pos(ent, stack, ent.bridge_target, via_bridge=True)
            else:
                used = self._try_output_dirs(ent, stack, self._output_options(ent))
                if used is not None:
                    ent.last_used_dir_round[used] = self.round
                    moved = True

            if moved and ent.typ != EntityType.HARVESTER:
                # Remove the exact stack that was snapshotted, if it is still present.
                if ent.stored is not None and ent.stored.rid == stack.rid:
                    ent.stored = None
            elif moved and ent.typ == EntityType.HARVESTER:
                ent.next_harvest_round = self.round + 4
            elif not moved and ent.typ == EntityType.HARVESTER:
                self.log.append(f"harvester #{ent.eid} output was blocked; will retry next round")
            if moved:
                ent.last_moved_round = self.round
            yield ("transfer", eid, moved)

        # Process foundries after movement; refined output becomes available next distribution.
        for ent in self.entities.values():
            if not ent.alive or ent.typ != EntityType.FOUNDRY:
                continue
            if ent.stored is None and ent.foundry_ti is not None and ent.foundry_raw is not None:
                ti = ent.foundry_ti
                raw = ent.foundry_raw
                ent.foundry_ti = None
                ent.foundry_raw = None
                ent.stored = self._new_stack(Resource.AXIONITE, amount=min(ti.amount, raw.amount))
                self.log.append(f"foundry #{ent.eid} refined {ti.short()} + {raw.short()} -> {ent.stored.short()}")
                yield ("refine", ent.eid, True)

    # ----------------------------- round stepping -----------------------------

    def _grant_passive_titanium(self) -> None:
        # Passive titanium every 4 rounds. Round 0 is the first player-action round,
        # so award after rounds 3, 7, 11, ... have completed.
        if (self.round + 1) % 4 == 0:
            for team, ts in self.teams.items():
                ts.titanium += 10
                ts.ti_collected += 10
                self.log.append(f"T{team} gained 10 passive Ti")

    def _end_round_post_distribute(self) -> None:
        for ent in self.entities.values():
            if not ent.alive:
                continue
            if ent.action_cd > 0:
                ent.action_cd -= 1
            if ent.move_cd > 0:
                ent.move_cd -= 1
        self.round += 1

    def end_round(self) -> None:
        self._grant_passive_titanium()
        self.distribute_resources()
        self._end_round_post_distribute()

    def alive_units_in_turn_order(self) -> List[Entity]:
        return [self.entities[eid] for eid in self.spawn_order if eid in self.entities and self.entities[eid].alive]

    def alive_units_eligible_this_round(self) -> List[Entity]:
        """Units that existed before this round began, in engine turn order.

        A builder spawned by the core during round R must not act later in the
        same round. It first becomes eligible in round R+1. Cores are initial
        units and always stay eligible while alive.
        """
        out: List[Entity] = []
        for ent in self.alive_units_in_turn_order():
            if ent.typ == EntityType.CORE or ent.built_round < self.round:
                out.append(ent)
        return out

    def run_one_round_interactive(self) -> None:
        print(f"\n=== ROUND {self.round} ===")
        print(self.render())
        for team in sorted(self.teams):
            print(f"T{team}: {self.resources_short(team)}")
        for unit in self.alive_units_in_turn_order():
            self._interactive_unit_turn(unit)
        self.end_round()
        self.flush_log()

    def _interactive_unit_turn(self, unit: Entity) -> None:
        while unit.alive:
            print(f"\nTurn: {unit.label()} hp={unit.hp} action_cd={unit.action_cd} move_cd={unit.move_cd}")
            cmd = input("cmd> ").strip()
            if not cmd:
                continue
            try:
                done = self.execute_command(unit.eid, cmd)
                if done:
                    return
            except RuleError as e:
                print(f"RuleError: {e}")
            except (ValueError, IndexError) as e:
                print(f"Bad command: {e}")

    def execute_command(self, actor_id: int, cmd: str) -> bool:
        """Execute one interactive command. Returns True when the unit turn should end."""
        parts = shlex.split(cmd)
        if not parts:
            return False
        op = parts[0].lower()
        actor = self.entity(actor_id)

        if op in ("wait", "w", "pass", "done", "skip"):
            return True
        if op in ("help", "?"):
            print(self.help_for(actor))
            return False
        if op in ("map", "m"):
            print(self.render())
            return False
        if op in ("info", "i"):
            print(self.describe())
            return False
        if op == "log":
            self.flush_log()
            return False

        if actor.typ == EntityType.CORE:
            if op == "spawn":
                x, y = int(parts[1]), int(parts[2])
                self.spawn_builder(actor.eid, Pos(x, y))
                return True
            if op == "convert":
                self.convert_axionite(actor.eid, int(parts[1]))
                return False
            raise RuleError("core commands: spawn x y | convert amount | wait | map | info | help")

        if actor.typ == EntityType.BUILDER:
            if op == "move":
                self.move_builder(actor.eid, parse_dir(parts[1]))
                return True
            if op == "build":
                return self._execute_build_command(actor, parts)
            if op == "heal":
                self.heal(actor.eid, Pos(int(parts[1]), int(parts[2])))
                return True
            if op == "destroy":
                self.destroy_building(actor.eid, Pos(int(parts[1]), int(parts[2])))
                return False  # free/no cooldown, keep acting
            if op in ("self", "selfdestruct", "self_destruct"):
                self.self_destruct(actor.eid)
                return True
            if op in ("attack", "fire"):
                self.builder_attack_tile(actor.eid)
                return True
            raise RuleError("builder commands: move DIR | build ... | heal x y | destroy x y | self | wait")

        raise RuleError(f"no commands for unit type {actor.typ.value}")

    def _execute_build_command(self, actor: Entity, parts: List[str]) -> bool:
        if len(parts) < 4:
            raise ValueError("usage: build TYPE x y [dir] [target_x target_y]")
        kind = parts[1].lower().replace("-", "_")
        x, y = int(parts[2]), int(parts[3])
        pos = Pos(x, y)
        if kind in ("road", "r"):
            self.build(actor.eid, EntityType.ROAD, pos)
        elif kind in ("conveyor", "conv", "c"):
            self.build(actor.eid, EntityType.CONVEYOR, pos, direction=parse_dir(parts[4]))
        elif kind in ("splitter", "split", "s"):
            self.build(actor.eid, EntityType.SPLITTER, pos, direction=parse_dir(parts[4]))
        elif kind in ("bridge", "br"):
            tx, ty = int(parts[4]), int(parts[5])
            self.build(actor.eid, EntityType.BRIDGE, pos, bridge_target=Pos(tx, ty))
        elif kind in ("armoured", "armored", "armoured_conveyor", "armored_conveyor", "ac"):
            self.build(actor.eid, EntityType.ARMOURED_CONVEYOR, pos, direction=parse_dir(parts[4]))
        elif kind in ("harvester", "harv", "h"):
            self.build(actor.eid, EntityType.HARVESTER, pos)
        elif kind in ("foundry", "f"):
            self.build(actor.eid, EntityType.FOUNDRY, pos)
        else:
            raise ValueError(f"unknown build type {parts[1]!r}")
        return True

    # ----------------------------- rendering / debug -----------------------------

    def render(self) -> str:
        chars: List[List[str]] = []
        for y in range(self.h):
            row: List[str] = []
            for x in range(self.w):
                t = self.terrain[y][x]
                row.append({
                    Terrain.EMPTY: ".",
                    Terrain.TITANIUM_ORE: "T",
                    Terrain.AXIONITE_ORE: "A",
                    Terrain.WALL: "#",
                }.get(t, "?"))
            chars.append(row)

        for ent in self.entities.values():
            if not ent.alive or ent.typ == EntityType.BUILDER:
                continue
            for p in ent.occupied:
                chars[p.y][p.x] = self._building_char(ent)
        for ent in self.entities.values():
            if ent.alive and ent.typ == EntityType.BUILDER:
                chars[ent.pos.y][ent.pos.x] = "b" if ent.team == 0 else "B"

        header = "   " + "".join(str(x % 10) for x in range(self.w))
        lines = [header]
        for y, row in enumerate(chars):
            lines.append(f"{y:02d} " + "".join(row))
        return "\n".join(lines)

    def _building_char(self, ent: Entity) -> str:
        if ent.typ == EntityType.CORE:
            return "C" if ent.team == 0 else "K"
        if ent.typ == EntityType.ROAD:
            return "r"
        if ent.typ in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR):
            if ent.direction == Dir.N:
                return "^"
            if ent.direction == Dir.E:
                return ">"
            if ent.direction == Dir.S:
                return "v"
            if ent.direction == Dir.W:
                return "<"
            return "="
        if ent.typ == EntityType.SPLITTER:
            return "s"
        if ent.typ == EntityType.BRIDGE:
            return "="
        if ent.typ == EntityType.HARVESTER:
            return "h"
        if ent.typ == EntityType.FOUNDRY:
            return "f"
        return "?"

    def describe(self) -> str:
        lines: List[str] = [f"Round {self.round}"]
        for team in sorted(self.teams):
            ts = self.teams[team]
            lines.append(
                f"T{team}: Ti={ts.titanium} Ax={ts.axionite} "
                f"scale={ts.scale_percent:.1f}% collected(Ti={ts.ti_collected}, Ax={ts.ax_collected})"
            )
        lines.append("Entities:")
        for eid in sorted(self.entities):
            e = self.entities[eid]
            if not e.alive:
                continue
            extra = []
            if e.direction:
                extra.append(f"dir={e.direction.name}")
            if e.bridge_target:
                extra.append(f"target=({e.bridge_target.x},{e.bridge_target.y})")
            if e.stored:
                extra.append(f"stored={e.stored.short()}")
            if e.foundry_ti:
                extra.append(f"foundry_ti={e.foundry_ti.short()}")
            if e.foundry_raw:
                extra.append(f"foundry_raw={e.foundry_raw.short()}")
            if e.next_harvest_round is not None:
                extra.append(f"next_harvest={e.next_harvest_round}")
            if e.is_unit:
                extra.append(f"acd={e.action_cd} mcd={e.move_cd}")
            lines.append(f"  {e.label()} hp={e.hp}/{e.max_hp}" + (" " + " ".join(extra) if extra else ""))
        return "\n".join(lines)

    def flush_log(self) -> None:
        if not self.log:
            print("[no log]")
            return
        print("\n".join("[log] " + s for s in self.log))
        self.log.clear()

    def help_for(self, actor: Entity) -> str:
        common = "Common: wait | map | info | log | help"
        if actor.typ == EntityType.CORE:
            return (
                common + "\n"
                "Core: spawn x y | convert amount\n"
                "  spawn x y must choose an empty unit slot on the core footprint."
            )
        if actor.typ == EntityType.BUILDER:
            return (
                common + "\n"
                "Builder:\n"
                "  move DIR                         DIR=N,NE,E,SE,S,SW,W,NW\n"
                "  build road x y\n"
                "  build conveyor x y DIR\n"
                "  build splitter x y DIR\n"
                "  build bridge x y target_x target_y\n"
                "  build armoured_conveyor x y DIR\n"
                "  build harvester x y\n"
                "  build foundry x y\n"
                "  heal x y\n"
                "  destroy x y                      free; does not end turn\n"
                "  attack                           attacks enemy building under builder\n"
                "  self"
            )
        return common


# ----------------------------- helpers -----------------------------


def parse_dir(s: str) -> Dir:
    key = s.strip().upper()
    aliases = {
        "NORTH": "N",
        "EAST": "E",
        "SOUTH": "S",
        "WEST": "W",
        "UP": "N",
        "RIGHT": "E",
        "DOWN": "S",
        "LEFT": "W",
    }
    key = aliases.get(key, key)
    try:
        return Dir[key]
    except KeyError:
        raise ValueError(f"unknown direction {s!r}")




# ----------------------------- map26 loader -----------------------------
# This section is adapted from the user's route-planner loader. It keeps the
# default "maps/pong.map26" workflow while converting map26 terrain into this
# sandbox's terrain encoding.

import argparse
import os
import pickle
import tkinter as tk
from tkinter import filedialog, messagebox

Cell = Tuple[int, int]  # (row, col), matching the map26 helper code

ENV_EMPTY = 0
ENV_WALL = 1
ENV_ORE_TITANIUM = 2
ENV_ORE_AXIONITE = 3
TEAM_A = 0


@dataclass(frozen=True)
class CoreInfo:
    id: int
    team: int
    center: Cell
    footprint: Tuple[Cell, ...]


@dataclass
class MapData:
    path: str
    width: int
    height: int
    rows: List[List[int]]
    cores: List[CoreInfo]


def core_footprint(center: Cell, rows: int, cols: int) -> Tuple[Cell, ...]:
    r, c = center
    return tuple(
        (rr, cc)
        for rr in range(r - 1, r + 2)
        for cc in range(c - 1, c + 2)
        if 0 <= rr < rows and 0 <= cc < cols
    )


def read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    result, shift = 0, 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def read_tag(buf: bytes, pos: int) -> Tuple[int, int, int]:
    tag, pos = read_varint(buf, pos)
    return tag >> 3, tag & 7, pos


def skip_field(buf: bytes, pos: int, wire: int) -> int:
    if wire == 0:
        _, pos = read_varint(buf, pos)
    elif wire == 1:
        pos += 8
    elif wire == 2:
        length, pos = read_varint(buf, pos)
        pos += length
    elif wire == 5:
        pos += 4
    else:
        raise ValueError(f"unsupported wire type {wire}")
    if pos > len(buf):
        raise ValueError("truncated protobuf field")
    return pos


def parse_tile_row(buf: bytes) -> List[int]:
    row: List[int] = []
    pos = 0
    while pos < len(buf):
        field_num, wire, pos = read_tag(buf, pos)
        if field_num == 1 and wire == 2:
            length, pos = read_varint(buf, pos)
            end = pos + length
            while pos < end:
                value, pos = read_varint(buf, pos)
                row.append(value)
        elif field_num == 1 and wire == 0:
            value, pos = read_varint(buf, pos)
            row.append(value)
        else:
            pos = skip_field(buf, pos, wire)
    return row


def parse_pos(buf: bytes) -> Cell:
    x = y = 0
    pos = 0
    while pos < len(buf):
        field_num, wire, pos = read_tag(buf, pos)
        if field_num == 1 and wire == 0:
            x, pos = read_varint(buf, pos)
        elif field_num == 2 and wire == 0:
            y, pos = read_varint(buf, pos)
        else:
            pos = skip_field(buf, pos, wire)
    return (y, x)


def parse_core(buf: bytes) -> Tuple[int, int, Cell]:
    core_id = 0
    team = TEAM_A
    center = (0, 0)
    pos = 0
    while pos < len(buf):
        field_num, wire, pos = read_tag(buf, pos)
        if field_num == 1 and wire == 0:
            core_id, pos = read_varint(buf, pos)
        elif field_num == 2 and wire == 0:
            team, pos = read_varint(buf, pos)
        elif field_num == 3 and wire == 2:
            length, pos = read_varint(buf, pos)
            center = parse_pos(buf[pos : pos + length])
            pos += length
        else:
            pos = skip_field(buf, pos, wire)
    return core_id, team, center


def parse_map_message(buf: bytes) -> Tuple[int, int, List[List[int]], List[Tuple[int, int, Cell]]]:
    width = height = 0
    rows: List[List[int]] = []
    cores: List[Tuple[int, int, Cell]] = []
    pos = 0
    while pos < len(buf):
        field_num, wire, pos = read_tag(buf, pos)
        if field_num == 1 and wire == 0:
            width, pos = read_varint(buf, pos)
        elif field_num == 2 and wire == 0:
            height, pos = read_varint(buf, pos)
        elif field_num == 3 and wire == 2:
            length, pos = read_varint(buf, pos)
            rows.append(parse_tile_row(buf[pos : pos + length]))
            pos += length
        elif field_num == 4 and wire == 2:
            length, pos = read_varint(buf, pos)
            cores.append(parse_core(buf[pos : pos + length]))
            pos += length
        else:
            pos = skip_field(buf, pos, wire)
    return width, height, rows, cores


def load_map26(path: str) -> MapData:
    with open(path, "rb") as f:
        data = f.read()

    width, height, rows, core_specs = parse_map_message(data)
    if width <= 0 or height <= 0:
        raise ValueError(f"{path} does not look like a Map message")
    if len(rows) != height or any(len(row) != width for row in rows):
        raise ValueError(f"{path} has inconsistent dimensions")

    cores: List[CoreInfo] = []
    for core_id, team, center in core_specs:
        cores.append(
            CoreInfo(
                id=core_id,
                team=team,
                center=center,
                footprint=core_footprint(center, height, width),
            )
        )

    return MapData(path=path, width=width, height=height, rows=rows, cores=cores)


def auto_cell_size(rows: int, cols: int, max_w: int = 980, max_h: int = 780) -> int:
    return max(8, min(30, max_w // max(cols, 1), max_h // max(rows, 1)))


def game_from_map_data(map_data: MapData) -> Game:
    terrain: List[List[int]] = []
    for row in map_data.rows:
        out_row: List[int] = []
        for cell in row:
            if cell == ENV_EMPTY:
                out_row.append(Terrain.EMPTY)
            elif cell == ENV_WALL:
                out_row.append(Terrain.WALL)
            elif cell == ENV_ORE_TITANIUM:
                out_row.append(Terrain.TITANIUM_ORE)
            elif cell == ENV_ORE_AXIONITE:
                out_row.append(Terrain.AXIONITE_ORE)
            else:
                out_row.append(Terrain.EMPTY)
        terrain.append(out_row)

    core_specs: List[Tuple[int, Pos, Set[Pos]]] = []
    for core in map_data.cores:
        cr, cc = core.center
        tiles = {Pos(c, r) for r, c in core.footprint}
        core_specs.append((core.team, Pos(cc, cr), tiles))

    return Game(terrain, core_specs=core_specs)


SAMPLE_GRID = [
    # 0 empty, 1 core tile, 2 titanium ore, 3 axionite ore, 4 wall
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 1, 1, 1, 0, 0, 2, 0, 0, 3, 0, 0],
    [0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 1, 1, 1, 0, 0, 0, 4, 4, 4, 0, 0],
    [0, 0, 0, 0, 0, 2, 0, 0, 0, 3, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
]


# ----------------------------- bot code exporter -----------------------------
# The UI records successful manual actions and writes runnable CambC bot files.
# Generated turn numbers use the real engine convention where c.get_current_round()
# starts at 1, while this sandbox's internal round counter starts at 0.

DIR_TO_CAMBC = {
    Dir.N: "NORTH",
    Dir.NE: "NORTHEAST",
    Dir.E: "EAST",
    Dir.SE: "SOUTHEAST",
    Dir.S: "SOUTH",
    Dir.SW: "SOUTHWEST",
    Dir.W: "WEST",
    Dir.NW: "NORTHWEST",
}

GENERATED_HEADER = "# AUTO-GENERATED by herberts_hardcoding.py. Manual edits may be overwritten."


def pos_expr(p: Pos) -> str:
    return f"Position({p.x}, {p.y})"


def dir_expr(d: Dir) -> str:
    return f"Direction.{DIR_TO_CAMBC[d]}"


def indent_lines(lines: Sequence[str], spaces: int) -> List[str]:
    pad = " " * spaces
    return [pad + line if line else "" for line in lines]


@dataclass
class RecordedAction:
    turn: int
    lines: List[str]
    note: str = ""


class BotCodeExporter:
    """Writes a tiny hardcoded policy package into pong_bot/ as manual play happens."""

    def __init__(self, bot_dir: str) -> None:
        self.bot_dir = os.path.abspath(bot_dir)
        self.actions: Dict[str, Dict[int, List[List[str]]]] = {"core": {}}
        self.builder_spawn_turn_by_eid: Dict[int, int] = {}
        self.export_count = 0
        self.last_error: Optional[str] = None

    def reset_for_game(self, game: Game) -> None:
        self.actions = {"core": {}}
        self.builder_spawn_turn_by_eid.clear()
        for ent in game.entities.values():
            if ent.typ == EntityType.BUILDER and ent.team == 0:
                self.register_builder(ent, write=False)
        self.export_count = 0
        self.last_error = None

    def register_builder(self, ent: Entity, *, write: bool = False) -> None:
        if ent.typ != EntityType.BUILDER:
            return
        # CambC builders first run in the next round after the core spawns them;
        # c.get_current_round() - 1 is therefore the spawn round.
        self.builder_spawn_turn_by_eid[ent.eid] = ent.built_round + 1
        key = self.builder_key_for(ent)
        self.actions.setdefault(key, {})
        if write:
            self.write_files()

    def builder_key_for(self, ent: Entity) -> str:
        spawn_turn = self.builder_spawn_turn_by_eid.get(ent.eid, ent.built_round + 1)
        return f"builder_{spawn_turn}"

    def record_core(self, turn: int, lines: List[str]) -> None:
        self.actions.setdefault("core", {}).setdefault(turn, []).append(lines)

    def record_builder(self, ent: Entity, turn: int, lines: List[str]) -> None:
        self.register_builder(ent, write=False)
        key = self.builder_key_for(ent)
        self.actions.setdefault(key, {}).setdefault(turn, []).append(lines)

    def record_wait_turn(self, ent: Optional[Entity], turn: int) -> None:
        if ent is None or ent.team != 0:
            return
        if ent.typ == EntityType.CORE:
            self.actions.setdefault("core", {}).setdefault(turn, [])
        elif ent.typ == EntityType.BUILDER:
            self.register_builder(ent, write=False)
            self.actions.setdefault(self.builder_key_for(ent), {}).setdefault(turn, [])

    def write_files(self) -> None:
        os.makedirs(self.bot_dir, exist_ok=True)
        self._write_main_py()
        self._write_core_py()
        self._write_builder_modules()
        self._ensure_fallback_builder_py()
        self.export_count += 1
        self.last_error = None

    def try_write_files(self) -> Tuple[bool, str]:
        try:
            self.write_files()
            return True, f"Exported bot code to {self.bot_dir}"
        except Exception as e:
            self.last_error = str(e)
            return False, str(e)

    def _backup_once(self, path: str) -> None:
        if not os.path.exists(path):
            return
        backup = path + ".manual_backup"
        if os.path.exists(backup):
            return
        try:
            shutil.copy2(path, backup)
        except OSError:
            # Export should still proceed if backup fails due to permissions.
            pass

    def _write_text(self, rel: str, text: str, *, backup: bool = True) -> None:
        path = os.path.join(self.bot_dir, rel)
        if backup:
            self._backup_once(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def _builder_spawn_turns(self) -> List[int]:
        turns: Set[int] = set()
        for key in self.actions:
            if key.startswith("builder_"):
                try:
                    turns.add(int(key.split("_", 1)[1]))
                except ValueError:
                    pass
        return sorted(turns)

    def _write_main_py(self) -> None:
        spawn_turns = self._builder_spawn_turns()
        imports = ["import builder", "import core"]
        imports.extend(f"import builder_{turn}" for turn in spawn_turns)
        joined_imports = "\n".join(imports)
        builder_route_lines: List[str] = []
        if spawn_turns:
            for i, turn in enumerate(spawn_turns):
                prefix = "if" if i == 0 else "elif"
                builder_route_lines.append(f"                {prefix} self.spawn_turn == {turn}:")
                builder_route_lines.append(f"                    self.me = builder_{turn}")
            builder_route_lines.append("                else:")
            builder_route_lines.append("                    self.me = builder")
        else:
            builder_route_lines.append("                self.me = builder")
        builder_routes = "\n".join(builder_route_lines)
        text = f'''# main.py
{GENERATED_HEADER}

from cambc import Controller, EntityType
import random

{joined_imports}


class Player:
    def __init__(self):
        self.initialized = False
        self.me = None
        self.spawn_turn = None

    def run(self, c: Controller) -> None:
        etype = c.get_entity_type()

        if not self.initialized:
            random.seed(c.get_current_round())

            if etype == EntityType.CORE:
                self.me = core
            elif etype == EntityType.BUILDER_BOT:
                # The builder's first turn is normally one round after it was spawned.
                # This matches the user's SPAWN_TURN = c.get_current_round() - 1 pattern.
                self.spawn_turn = c.get_current_round() - 1
{builder_routes}
            else:
                return

            self.initialized = True

        if self.me is not None:
            self.me.run(c)
'''
        self._write_text("main.py", text)

    def _write_core_py(self) -> None:
        text = self._module_text("core", self.actions.get("core", {}))
        self._write_text("core.py", text)

    def _write_builder_modules(self) -> None:
        for turn in self._builder_spawn_turns():
            key = f"builder_{turn}"
            text = self._module_text(key, self.actions.get(key, {}))
            self._write_text(f"{key}.py", text, backup=False)

    def _ensure_fallback_builder_py(self) -> None:
        path = os.path.join(self.bot_dir, "builder.py")
        if os.path.exists(path):
            return
        text = f'''# builder.py
{GENERATED_HEADER}

from cambc import Controller


def run(c: Controller) -> None:
    return
'''
        self._write_text("builder.py", text, backup=False)

    def _module_text(self, module_name: str, turn_blocks: Dict[int, List[List[str]]]) -> str:
        lines: List[str] = [
            f"# {module_name}.py",
            GENERATED_HEADER,
            "",
            "from cambc import *",
            "",
            "",
            "def run(c: Controller) -> None:",
            "    turn = c.get_current_round()",
        ]
        if not turn_blocks:
            lines.extend(["    return", ""])
            return "\n".join(lines)

        for turn in sorted(turn_blocks):
            blocks = turn_blocks[turn]
            lines.append(f"    if turn == {turn}:")
            if blocks:
                for block in blocks:
                    if not block:
                        continue
                    lines.extend(indent_lines(block, 8))
            else:
                lines.append("        pass")
            lines.append("        return")
        lines.append("    return")
        lines.append("")
        return "\n".join(lines)


def action_spawn_builder_lines(pos: Pos) -> List[str]:
    return [
        f"pos = {pos_expr(pos)}",
        "if c.can_spawn(pos):",
        "    c.spawn_builder(pos)",
    ]


def action_convert_lines(amount: int) -> List[str]:
    return [
        f"amount = {amount}",
        "ti, ax = c.get_global_resources()",
        "if ax >= amount:",
        "    c.convert(amount)",
    ]


def action_move_lines(direction: Dir) -> List[str]:
    d = dir_expr(direction)
    return [
        f"direction = {d}",
        "if c.can_move(direction):",
        "    c.move(direction)",
    ]


def action_build_lines(kind: EntityType, pos: Pos, direction: Optional[Dir] = None, bridge_target: Optional[Pos] = None) -> List[str]:
    lines = [f"pos = {pos_expr(pos)}"]
    if kind == EntityType.ROAD:
        lines.extend(["if c.can_build_road(pos):", "    c.build_road(pos)"])
    elif kind == EntityType.CONVEYOR:
        lines.extend([f"direction = {dir_expr(direction or Dir.E)}", "if c.can_build_conveyor(pos, direction):", "    c.build_conveyor(pos, direction)"])
    elif kind == EntityType.SPLITTER:
        lines.extend([f"direction = {dir_expr(direction or Dir.E)}", "if c.can_build_splitter(pos, direction):", "    c.build_splitter(pos, direction)"])
    elif kind == EntityType.ARMOURED_CONVEYOR:
        lines.extend([f"direction = {dir_expr(direction or Dir.E)}", "if c.can_build_armoured_conveyor(pos, direction):", "    c.build_armoured_conveyor(pos, direction)"])
    elif kind == EntityType.BRIDGE:
        target = bridge_target or pos
        lines.extend([f"target = {pos_expr(target)}", "if c.can_build_bridge(pos, target):", "    c.build_bridge(pos, target)"])
    elif kind == EntityType.HARVESTER:
        lines.extend(["if c.can_build_harvester(pos):", "    c.build_harvester(pos)"])
    elif kind == EntityType.FOUNDRY:
        lines.extend(["if c.can_build_foundry(pos):", "    c.build_foundry(pos)"])
    else:
        lines.append("pass")
    return lines


def action_heal_lines(pos: Pos) -> List[str]:
    return [f"pos = {pos_expr(pos)}", "if c.can_heal(pos):", "    c.heal(pos)"]


def action_destroy_lines(pos: Pos) -> List[str]:
    return [f"pos = {pos_expr(pos)}", "if c.can_destroy(pos):", "    c.destroy(pos)"]


def action_fire_lines() -> List[str]:
    return ["pos = c.get_position()", "if c.can_fire(pos):", "    c.fire(pos)"]


def action_self_destruct_lines() -> List[str]:
    return ["c.self_destruct()"]


# ----------------------------- Tkinter UI -----------------------------

try:
    from PIL import Image, ImageTk
    _PIL_OK = True
except Exception:
    _PIL_OK = False

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# Each on-disk asset image already faces a known direction. We rotate from that
# baseline to whatever direction the placed entity actually faces.
ASSET_BASE_DIR: Dict[str, Dir] = {
    "conveyor": Dir.W,
    "splitter": Dir.N,
}

DIR_ANGLE_CW: Dict[Dir, int] = {Dir.N: 0, Dir.E: 90, Dir.S: 180, Dir.W: 270}


class Assets:
    """Loads AVIF assets, scales/rotates per cell size, caches PhotoImages."""

    def __init__(self) -> None:
        self._raw: Dict[str, "Image.Image"] = {}
        self._cache: Dict[Tuple[str, int, Optional[Dir]], "ImageTk.PhotoImage"] = {}
        self._loaded = False

    def _ensure(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not _PIL_OK:
            print("[asset] PIL not available; using fallback shapes", file=sys.stderr)
            return
        names = [
            "builder-bot", "conveyor", "core", "foundry", "harvester",
            "road", "splitter", "titanium-ore", "axionite-ore",
            "titanium", "axionite-raw", "axionite-refined",
        ]
        for n in names:
            path = os.path.join(ASSETS_DIR, n + ".avif")
            try:
                self._raw[n] = Image.open(path).convert("RGBA")
            except Exception as e:
                print(f"[asset] could not load {path}: {e}", file=sys.stderr)

    def get(self, name: str, size: int, direction: Optional[Dir] = None):
        self._ensure()
        if not _PIL_OK:
            return None
        raw = self._raw.get(name)
        if raw is None or size <= 0:
            return None
        key = (name, size, direction)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        img = raw
        base = ASSET_BASE_DIR.get(name)
        if base is not None and direction is not None and direction.cardinal:
            cw = (DIR_ANGLE_CW[direction] - DIR_ANGLE_CW[base]) % 360
            if cw:
                # PIL.rotate is counter-clockwise; negate for a CW rotation.
                img = img.rotate(-cw, resample=Image.BICUBIC)
        if img.size != (size, size):
            img = img.resize((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        self._cache[key] = photo
        return photo


# Number-key -> (label, EntityType). Road has no number; it is auto-placed
# during movement when the destination is not already walkable.
BUILD_KEYS: Dict[str, Tuple[str, EntityType]] = {
    "1": ("conveyor", EntityType.CONVEYOR),
    "2": ("splitter", EntityType.SPLITTER),
    "3": ("bridge", EntityType.BRIDGE),
    "4": ("harvester", EntityType.HARVESTER),
    "5": ("foundry", EntityType.FOUNDRY),
}

DIRECTIONAL_BUILDS = {EntityType.CONVEYOR, EntityType.SPLITTER}

BUILD_KEYS_LEGEND = "  ".join(f"{k}={label}" for k, (label, _typ) in BUILD_KEYS.items()) + "  0=clear"

RESOURCE_ASSET_NAME: Dict[Resource, str] = {
    Resource.TITANIUM: "titanium",
    Resource.RAW_AXIONITE: "axionite-raw",
    Resource.AXIONITE: "axionite-refined",
}

KEY_TO_DIR: Dict[str, Dir] = {
    "w": Dir.N, "W": Dir.N,
    "s": Dir.S, "S": Dir.S,
    "a": Dir.W, "A": Dir.W,
    "d": Dir.E, "D": Dir.E,
    "Up": Dir.N,
    "Down": Dir.S,
    "Left": Dir.W,
    "Right": Dir.E,
}

EMPTY_TILE_COLOR = "#372b29"  # rgba(55, 43, 41)

TERRAIN_COLORS = {
    Terrain.EMPTY: EMPTY_TILE_COLOR,
    Terrain.TITANIUM_ORE: EMPTY_TILE_COLOR,
    Terrain.AXIONITE_ORE: EMPTY_TILE_COLOR,
    Terrain.WALL: "#202124",
}

MY_TEAM_COLOR = "#8a5a35"
ENEMY_TEAM_COLOR = "#6b4aa0"


def cell_size_for(rows: int, cols: int, max_w: int = 1100, max_h: int = 880) -> int:
    """Pick a slightly larger cell size than auto_cell_size since the assets benefit from it."""
    return max(12, min(40, max_w // max(cols, 1), max_h // max(rows, 1)))


class HardcodeApp:
    SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".herberts_hardcoding_state.pkl")
    SAVE_VERSION = 1

    def __init__(self, root: tk.Tk, game: Game, *, title: str = "Hardcoding", bot_dir: str = "pong_bot") -> None:
        self.root = root
        self.game = game
        self.title = title
        self.cell_size = cell_size_for(game.h, game.w)
        self.assets = Assets()
        self.exporter = BotCodeExporter(bot_dir)
        self.exporter.reset_for_game(game)

        # Selection / sub-mode state.
        self.selected_tile: Optional[Pos] = None
        self.mode: str = "tile"  # "tile" | "direction" | "bridge_target"
        self.pending_building: Optional[EntityType] = None
        self.pending_dir: Dir = Dir.E
        self.bridge_source: Optional[Pos] = None

        # Turn / round state.
        self.turn_order: List[int] = []
        self.turn_index: int = 0
        self.round_active: bool = False

        # Step mode: when armed, finish_round pauses at the distribution phase
        # and `/` advances one transfer at a time.
        self._step_armed: bool = False
        self._dist_iter = None

        # When True, the core's turn is auto-skipped each round. Toggled by `.`.
        self._skip_core: bool = False

        # Per-round auto-convert sum (bot_turn -> total ax converted that round).
        self.round_convert_total: Dict[int, int] = {}

        # Linear undo / redo. Snapshots are taken at the start of each unit turn
        # (and at the start of every round). cursor points at the most recently
        # committed snapshot; the live state may sit tentatively past it until
        # the next turn boundary.
        self.history: List[dict] = []
        self.cursor: int = -1

        self.status_var = tk.StringVar(value="")
        self.state_var = tk.StringVar(value="")
        self.active_var = tk.StringVar(value="")
        self.build_var = tk.StringVar(value="")
        self.bot_dir_var = tk.StringVar(value=self.exporter.bot_dir)

        root.title(f"Herbert's Hardcoding - {title}")
        root.geometry("")
        self.build_ui()
        self.bind_keys()
        if not self._load_state():
            self.start_round()
            self.record_snapshot()
        self.redraw()
        self.refresh_side_panel()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------- UI scaffolding -----------------------------

    def build_ui(self) -> None:
        outer = tk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        left = tk.Frame(outer)
        left.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        legend = tk.Label(
            left,
            anchor="w",
            justify="left",
            text=(
                "wasd/arrows: cursor   space: confirm   enter: end turn   "
                "delete: destroy/self   /: step distribute   .: skip core   "
                "cmd-z/shift: undo/redo\n"
                "build keys: " + BUILD_KEYS_LEGEND + "\n"
                "drag: pan    zoom: cmd-+/-/0"
            ),
        )
        legend.pack(fill="x", pady=(0, 4))

        self.base_cell_size = self.cell_size
        self.zoom = 1.0

        canvas_frame = tk.Frame(left)
        canvas_frame.pack(fill="both", expand=True)
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        viewport_w = self.game.w * self.cell_size
        viewport_h = self.game.h * self.cell_size
        self.canvas = tk.Canvas(
            canvas_frame,
            width=viewport_w,
            height=viewport_h,
            bg=EMPTY_TILE_COLOR,
            highlightthickness=0,
            scrollregion=self._scrollregion(),
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self._scroll_y = tk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self._scroll_y.grid(row=0, column=1, sticky="ns")
        self._scroll_x = tk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)
        self._scroll_x.grid(row=1, column=0, sticky="ew")
        self.canvas.config(xscrollcommand=self._scroll_x.set, yscrollcommand=self._scroll_y.set)

        # Click + drag to pan.
        self.canvas.bind("<ButtonPress-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_drag)

        tk.Label(left, textvariable=self.status_var, anchor="w").pack(fill="x", pady=(4, 0))

        right = tk.Frame(outer, width=380)
        right.pack(side="right", fill="y", padx=(0, 8), pady=8)
        right.pack_propagate(False)

        tk.Label(right, text="My Team", font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill="x")
        tk.Label(right, textvariable=self.state_var, justify="left", anchor="w").pack(fill="x", pady=(0, 8))

        tk.Label(right, text="Active Unit", font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill="x")
        tk.Label(right, textvariable=self.active_var, justify="left", anchor="w", wraplength=360).pack(fill="x", pady=(0, 8))

        tk.Label(right, text="Build Selection", font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill="x")
        tk.Label(right, textvariable=self.build_var, justify="left", anchor="w", wraplength=360).pack(fill="x", pady=(0, 8))

        misc = tk.LabelFrame(right, text="Map / Export", padx=6, pady=6)
        misc.pack(fill="x", pady=(0, 8))
        tk.Button(misc, text="Load map26\u2026", command=self.load_map_dialog).pack(fill="x")
        tk.Button(misc, text="Reset progress", command=self.reset_progress).pack(fill="x", pady=(4, 0))
        tk.Button(misc, text="Reset to sample grid", command=self.reset_sample).pack(fill="x", pady=(4, 0))
        tk.Label(misc, text="Bot dir:", anchor="w").pack(fill="x", pady=(6, 0))
        tk.Entry(misc, textvariable=self.bot_dir_var).pack(fill="x")
        tk.Button(misc, text="Use bot dir", command=self.change_bot_dir).pack(fill="x", pady=(4, 0))
        tk.Button(misc, text="Export now", command=self.export_now).pack(fill="x", pady=(4, 0))

        tk.Label(right, text="Log", font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill="x")
        self.log_text = tk.Text(right, width=46, height=18, wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def bind_keys(self) -> None:
        # Bind on the root window so the canvas does not need keyboard focus.
        self.root.focus_set()
        for k in ("w", "W", "a", "A", "s", "S", "d", "D"):
            self.root.bind(f"<Key-{k}>", self.on_arrow)
        for k in ("Up", "Down", "Left", "Right"):
            self.root.bind(f"<{k}>", self.on_arrow)
        self.root.bind("<space>", self.on_space)
        self.root.bind("<Return>", self.on_enter)
        self.root.bind("<KP_Enter>", self.on_enter)
        for digit in "0123456789":
            self.root.bind(f"<Key-{digit}>", self.on_digit)
            self.root.bind(f"<KP_{digit}>", self.on_digit)
        self.root.bind("<Delete>", self.on_delete)
        self.root.bind("<BackSpace>", self.on_delete)
        self.root.bind("<Escape>", self.on_escape)
        self.root.bind("<Key-slash>", self.on_slash)
        self.root.bind("<Key-question>", self.on_slash)
        self.root.bind("<Key-period>", self.on_toggle_skip_core)
        # macOS uses Command, Linux/Windows use Control.
        for binding in ("<Command-z>", "<Command-Z>", "<Control-z>", "<Control-Z>"):
            self.root.bind(binding, self.on_undo)
        for binding in ("<Command-Shift-Z>", "<Command-Shift-z>",
                        "<Control-Shift-Z>", "<Control-Shift-z>"):
            self.root.bind(binding, self.on_redo)
        for binding in ("<Command-plus>", "<Command-equal>",
                        "<Control-plus>", "<Control-equal>"):
            self.root.bind(binding, lambda e: self._zoom_step(1.25))
        for binding in ("<Command-minus>", "<Control-minus>"):
            self.root.bind(binding, lambda e: self._zoom_step(1 / 1.25))
        for binding in ("<Command-0>", "<Control-0>"):
            self.root.bind(binding, lambda e: self._set_zoom(1.0))

    # ----------------------------- helpers -----------------------------

    def bot_turn(self) -> int:
        # CambC engine reports get_current_round() starting at 1; this sandbox starts at 0.
        return self.game.round + 1

    def find_my_core(self) -> Optional[Entity]:
        for ent in self.game.entities.values():
            if ent.alive and ent.team == 0 and ent.typ == EntityType.CORE:
                return ent
        return None

    def active_unit(self) -> Optional[Entity]:
        while self.turn_index < len(self.turn_order):
            eid = self.turn_order[self.turn_index]
            ent = self.game.entities.get(eid)
            if ent is not None and ent.alive and ent.team == 0:
                return ent
            self.turn_index += 1
        return None

    def start_round(self) -> None:
        self.turn_order = [e.eid for e in self.game.alive_units_eligible_this_round() if e.team == 0]
        self.turn_index = 0
        self.round_active = True
        self.round_convert_total.setdefault(self.bot_turn(), 0)
        self.append_log(f"=== Round {self.game.round} / bot turn {self.bot_turn()} ===")
        self.skip_to_live_or_end()
        self.enter_unit_turn()

    def skip_to_live_or_end(self) -> None:
        while self.turn_index < len(self.turn_order):
            eid = self.turn_order[self.turn_index]
            ent = self.game.entities.get(eid)
            if ent is not None and ent.alive and ent.team == 0:
                if self._skip_core and ent.typ == EntityType.CORE:
                    self.turn_index += 1
                    continue
                return
            self.turn_index += 1
        self.finish_round()

    def finish_round(self) -> None:
        self.game._grant_passive_titanium()
        if self._step_armed:
            self._dist_iter = self.game.iter_distribute_resources()
            self.consume_game_log()
            self.append_log("[step] paused at distribution. Press / to step, Enter to flush remaining.")
            self.redraw()
            self.refresh_side_panel()
            return
        self.game.distribute_resources()
        self.game._end_round_post_distribute()
        self.consume_game_log()
        self.append_log(f"--- End round; now round {self.game.round} / bot turn {self.bot_turn()} ---")
        self.turn_order = []
        self.turn_index = 0
        self.round_active = False
        self.start_round()

    def _finalize_after_step(self) -> None:
        self.game._end_round_post_distribute()
        self.consume_game_log()
        self.append_log(f"--- End round; now round {self.game.round} / bot turn {self.bot_turn()} ---")
        self.turn_order = []
        self.turn_index = 0
        self.round_active = False
        self._step_armed = False
        self._dist_iter = None
        self.start_round()

    def enter_unit_turn(self) -> None:
        actor = self.active_unit()
        if actor is not None:
            self.selected_tile = actor.pos
            self.mode = "tile"
            self.bridge_source = None
        self.status_var.set(self.status_for_active())

    def status_for_active(self) -> str:
        actor = self.active_unit()
        if actor is None:
            return "no active unit"
        return f"active: #{actor.eid} {actor.typ.value} at ({actor.pos.x},{actor.pos.y})"

    def _auto_end_if_needed(self, actor: Optional[Entity]) -> None:
        """End the active unit's turn automatically when its cooldowns leave
        nothing it could legally do this turn:
          - core: action_cd > 0 (just spawned a builder)
          - builder bot: action_cd > 0 AND move_cd > 0
        """
        if actor is None or not actor.alive:
            return
        # Only auto-end when the actor that just acted is still the active one;
        # otherwise advance_turn would advance past the wrong unit.
        current = self.active_unit()
        if current is None or current.eid != actor.eid:
            return
        if actor.typ == EntityType.CORE:
            if actor.action_cd > 0:
                self.advance_turn(actor)
            return
        if actor.typ == EntityType.BUILDER:
            if actor.action_cd > 0 and actor.move_cd > 0:
                self.advance_turn(actor)

    def advance_turn(self, actor: Optional[Entity]) -> None:
        # Always record at least an empty wait block so the exporter knows this
        # unit was active on this turn.
        self.exporter.record_wait_turn(actor, self.bot_turn())
        self.export_generated_code("turn finished")
        self.pending_building = None
        self.turn_index += 1
        self.skip_to_live_or_end()
        self.enter_unit_turn()
        self.record_snapshot()
        self.redraw()
        self.refresh_side_panel()

    def consume_game_log(self) -> None:
        if not self.game.log:
            return
        for line in self.game.log:
            self.append_log(line)
        self.game.log.clear()

    def append_log(self, msg: str) -> None:
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    # ----------------------------- snapshot / undo -----------------------------

    def capture(self) -> dict:
        return {
            "game": copy.deepcopy(self.game),
            "exporter_actions": copy.deepcopy(self.exporter.actions),
            "exporter_eid_map": dict(self.exporter.builder_spawn_turn_by_eid),
            "exporter_export_count": self.exporter.export_count,
            "turn_order": list(self.turn_order),
            "turn_index": self.turn_index,
            "round_active": self.round_active,
            "round_convert_total": dict(self.round_convert_total),
            "turn_id": (self.game.round, self.turn_index),
        }

    def restore(self, snap: dict) -> None:
        self.game = copy.deepcopy(snap["game"])
        self.exporter.actions = copy.deepcopy(snap["exporter_actions"])
        self.exporter.builder_spawn_turn_by_eid = dict(snap["exporter_eid_map"])
        self.exporter.export_count = snap["exporter_export_count"]
        self.turn_order = list(snap["turn_order"])
        self.turn_index = snap["turn_index"]
        self.round_active = snap["round_active"]
        self.round_convert_total = dict(snap["round_convert_total"])
        actor = self.active_unit()
        self.selected_tile = actor.pos if actor is not None else None
        self.mode = "tile"
        self.bridge_source = None
        self.pending_building = None

    def record_snapshot(self) -> None:
        # Drop any redo branch first; the new snapshot starts a fresh future.
        self.history = self.history[: self.cursor + 1]
        self.history.append(self.capture())
        self.cursor = len(self.history) - 1
        self._save_state()

    def truncate_redo(self) -> None:
        if self.cursor + 1 < len(self.history):
            self.history = self.history[: self.cursor + 1]

    # ----------------------------- persistence -----------------------------

    def _save_state(self) -> None:
        try:
            data = {
                "version": self.SAVE_VERSION,
                "title": self.title,
                "history": self.history,
                "cursor": self.cursor,
                "bot_dir": self.exporter.bot_dir,
                "zoom": self.zoom,
            }
            tmp = self.SAVE_FILE + ".tmp"
            with open(tmp, "wb") as f:
                pickle.dump(data, f)
            os.replace(tmp, self.SAVE_FILE)
        except Exception as e:
            # Persistence is best-effort; never break the UI on save failure.
            try:
                self.status_var.set(f"Save failed: {e}")
            except Exception:
                pass

    def _load_state(self) -> bool:
        if not os.path.exists(self.SAVE_FILE):
            return False
        try:
            with open(self.SAVE_FILE, "rb") as f:
                data = pickle.load(f)
        except Exception as e:
            self.append_log(f"[load] could not read save file: {e}")
            return False
        if not isinstance(data, dict) or data.get("version") != self.SAVE_VERSION:
            return False
        history = data.get("history")
        cursor = data.get("cursor", -1)
        if not history or cursor < 0 or cursor >= len(history):
            return False
        try:
            self.history = history
            self.cursor = cursor
            self.title = data.get("title", self.title)
            saved_bot_dir = data.get("bot_dir")
            if saved_bot_dir and os.path.isdir(os.path.dirname(saved_bot_dir) or "."):
                self.exporter.bot_dir = saved_bot_dir
                self.bot_dir_var.set(saved_bot_dir)
            elif saved_bot_dir:
                self.append_log(
                    f"[load] dropped corrupt saved bot_dir: {saved_bot_dir!r}; using {self.exporter.bot_dir}"
                )
            self.restore(self.history[self.cursor])
            self.base_cell_size = cell_size_for(self.game.h, self.game.w)
            self.zoom = float(data.get("zoom", 1.0) or 1.0)
            self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom))
            self.cell_size = max(8, round(self.base_cell_size * self.zoom))
            viewport_w = self.game.w * self.base_cell_size
            viewport_h = self.game.h * self.base_cell_size
            self.canvas.config(
                width=viewport_w,
                height=viewport_h,
                scrollregion=self._scrollregion(),
            )
            self.root.title(f"Herbert's Hardcoding - {self.title}")
            self.append_log(f"[load] resumed saved progress ({len(self.history)} snapshots, cursor {self.cursor})")
            return True
        except Exception as e:
            self.append_log(f"[load] failed to restore saved state: {e}")
            self.history = []
            self.cursor = -1
            return False

    def _on_close(self) -> None:
        self._save_state()
        self.root.destroy()

    # ----------------------------- auto-convert -----------------------------

    def auto_convert_for(self, team: int, cost: Cost) -> int:
        """If the team cannot afford `cost` due to a Ti shortfall, convert just enough Ax->Ti."""
        ts = self.game.teams[team]
        if ts.titanium >= cost.ti and ts.axionite >= cost.ax:
            return 0
        if ts.titanium >= cost.ti:
            return 0  # only Ax shortfall - conversion produces Ti, not Ax
        ti_short = cost.ti - ts.titanium
        ax_to_convert = (ti_short + 3) // 4  # ceil division
        if ts.axionite < ax_to_convert + cost.ax:
            return 0  # not enough Ax even after conversion to also pay cost.ax
        core = self.find_my_core()
        if core is None:
            return 0
        self.game.convert_axionite(core.eid, ax_to_convert)
        self.record_round_convert(team, ax_to_convert)
        return ax_to_convert

    def record_round_convert(self, team: int, amount: int) -> None:
        if team != 0 or amount <= 0:
            return
        bot_turn = self.bot_turn()
        total = self.round_convert_total.get(bot_turn, 0) + amount
        self.round_convert_total[bot_turn] = total
        # Maintain at most one convert block in the core's actions for this round.
        blocks = self.exporter.actions.setdefault("core", {}).setdefault(bot_turn, [])
        new_block = action_convert_lines(total)
        for i, b in enumerate(blocks):
            if b and any("c.convert(" in line for line in b):
                blocks[i] = new_block
                return
        # Run conversion before any other core action that depends on the resulting Ti.
        blocks.insert(0, new_block)

    # ----------------------------- input handlers -----------------------------

    def on_click(self, event: tk.Event) -> None:
        pos = self.pos_from_event(event)
        if pos is None:
            return
        if self.mode == "direction":
            return  # arrows control direction, clicking is ignored mid-direction-pick
        if self.mode != "bridge_target":
            actor = self.active_unit()
            if actor is not None and pos.distance_squared(actor.pos) > 2:
                return
        self.selected_tile = pos
        self.refresh_status_after_selection()
        self.redraw()
        self.refresh_side_panel()

    def on_motion(self, event: tk.Event) -> None:
        pos = self.pos_from_event(event)
        if pos is not None:
            self.root.title(f"Herbert's Hardcoding - {self.title}   hover=({pos.x},{pos.y})")

    def _is_text_focus(self) -> bool:
        try:
            w = self.root.focus_get()
        except (tk.TclError, KeyError):
            return False
        return isinstance(w, (tk.Entry, tk.Text))

    def on_arrow(self, event: tk.Event) -> str:
        if self._is_text_focus():
            return ""
        d = KEY_TO_DIR.get(event.keysym)
        if d is None:
            return ""
        if self.mode == "direction":
            self.pending_dir = d
            self.redraw()
            self.refresh_side_panel()
        else:
            # tile / bridge_target: arrows move the cursor.
            actor = self.active_unit()
            if self.selected_tile is None:
                self.selected_tile = actor.pos if actor is not None else Pos(0, 0)
            nx = self.selected_tile.x + d.dxdy[0]
            ny = self.selected_tile.y + d.dxdy[1]
            if self.mode != "bridge_target" and actor is not None:
                nx = max(actor.pos.x - 1, min(actor.pos.x + 1, nx))
                ny = max(actor.pos.y - 1, min(actor.pos.y + 1, ny))
            new_pos = Pos(
                max(0, min(self.game.w - 1, nx)),
                max(0, min(self.game.h - 1, ny)),
            )
            self.selected_tile = new_pos
            self.refresh_status_after_selection()
            self.redraw()
            self.refresh_side_panel()
        return "break"

    def on_space(self, event: tk.Event) -> str:
        if self._is_text_focus():
            return ""
        self.space_pressed()
        return "break"

    def on_toggle_skip_core(self, event: tk.Event) -> str:
        if self._is_text_focus():
            return ""
        self._skip_core = not self._skip_core
        msg = "core turns: SKIPPED" if self._skip_core else "core turns: ON"
        self.append_log(f"[toggle] {msg}")
        self.status_var.set(msg)
        # If we're sitting on the core's turn right now, skip past it.
        actor = self.active_unit()
        if self._skip_core and actor is not None and actor.typ == EntityType.CORE:
            self.skip_to_live_or_end()
            self.enter_unit_turn()
            self.redraw()
            self.refresh_side_panel()
        return "break"

    def on_slash(self, event: tk.Event) -> str:
        if self._is_text_focus():
            return ""
        if self._dist_iter is not None:
            try:
                ev = next(self._dist_iter)
                self.append_log(f"[step] {ev}")
                self.consume_game_log()
                self.redraw()
                self.refresh_side_panel()
            except StopIteration:
                self._finalize_after_step()
            return "break"
        self._step_armed = not self._step_armed
        msg = "armed: next round-end will pause at distribution" if self._step_armed else "disarmed"
        self.append_log(f"[step mode] {msg}")
        self.status_var.set(f"step mode {'ON' if self._step_armed else 'off'}")
        return "break"

    def on_enter(self, event: tk.Event) -> str:
        if self._is_text_focus():
            return ""
        if self._dist_iter is not None:
            for _ in self._dist_iter:
                pass
            self.consume_game_log()
            self._finalize_after_step()
            return "break"
        actor = self.active_unit()
        if actor is None:
            return "break"
        self.advance_turn(actor)
        return "break"

    def on_digit(self, event: tk.Event) -> str:
        if self._is_text_focus():
            return ""
        ch = event.char
        if ch == "0":
            self.pending_building = None
            self.mode = "tile"
            self.bridge_source = None
            self.refresh_side_panel()
            self.redraw()
            return "break"
        choice = BUILD_KEYS.get(ch)
        if choice is None:
            return ""
        self.pending_building = choice[1]
        self.mode = "tile"
        self.bridge_source = None
        self.refresh_side_panel()
        self.redraw()
        return "break"

    def on_delete(self, event: tk.Event) -> str:
        if self._is_text_focus():
            return ""
        actor = self.active_unit()
        if actor is None or self.selected_tile is None:
            return "break"
        if actor.typ == EntityType.BUILDER and self.selected_tile == actor.pos:
            self.do_self_destruct()
        else:
            self.do_destroy(self.selected_tile)
        return "break"

    def on_escape(self, event: tk.Event) -> str:
        if self._is_text_focus():
            return ""
        if self.mode != "tile":
            self.mode = "tile"
            self.bridge_source = None
            self.refresh_side_panel()
            self.redraw()
        return "break"

    def on_undo(self, event: tk.Event) -> str:
        if self._is_text_focus():
            return ""
        if self.cursor <= 0:
            return "break"
        self.cursor -= 1
        self.restore(self.history[self.cursor])
        self.status_var.set("undo")
        self._save_state()
        self.redraw()
        self.refresh_side_panel()
        return "break"

    def on_redo(self, event: tk.Event) -> str:
        if self._is_text_focus():
            return ""
        if self.cursor + 1 >= len(self.history):
            return "break"
        self.cursor += 1
        self.restore(self.history[self.cursor])
        self.status_var.set("redo")
        self._save_state()
        self.redraw()
        self.refresh_side_panel()
        return "break"

    # ----------------------------- space dispatch -----------------------------

    def space_pressed(self) -> None:
        actor = self.active_unit()
        if actor is None or self.selected_tile is None:
            return

        if self.mode == "direction":
            self.do_directional_build(actor, self.selected_tile, self.pending_dir)
            return

        if self.mode == "bridge_target":
            if self.bridge_source is None:
                self.mode = "tile"
                return
            self.do_bridge_build(actor, self.bridge_source, self.selected_tile)
            return

        # tile mode dispatch.
        if self.pending_building is None:
            if actor.typ == EntityType.CORE:
                self.do_spawn_builder(actor, self.selected_tile)
            elif actor.typ == EntityType.BUILDER:
                self.do_move_or_road_then_move(actor, self.selected_tile)
            return

        if self.pending_building in DIRECTIONAL_BUILDS:
            self.mode = "direction"
            self.refresh_side_panel()
            self.redraw()
            return

        if self.pending_building == EntityType.BRIDGE:
            self.bridge_source = self.selected_tile
            self.mode = "bridge_target"
            self.refresh_side_panel()
            self.redraw()
            return

        # Non-directional, non-bridge: harvester / foundry.
        self.do_simple_build(actor, self.pending_building, self.selected_tile)

    # ----------------------------- action wrappers -----------------------------

    def do_with(self, fn) -> None:
        try:
            self.truncate_redo()
            fn()
            self.consume_game_log()
            self.record_snapshot()
            self.export_generated_code("action")
            self.redraw()
            self.refresh_side_panel()
        except RuleError as e:
            self.status_var.set(f"RuleError: {e}")
            self.append_log(f"RuleError: {e}")
        except (ValueError, IndexError) as e:
            self.status_var.set(f"Bad input: {e}")
            self.append_log(f"Bad input: {e}")

    def do_spawn_builder(self, core: Entity, target: Pos) -> None:
        def go() -> None:
            cost = self.game.cost_for(core.team, EntityType.BUILDER)
            self.auto_convert_for(core.team, cost)
            bot = self.game.spawn_builder(core.eid, target)
            self.exporter.register_builder(bot, write=False)
            self.exporter.record_core(self.bot_turn(), action_spawn_builder_lines(target))
        self.do_with(go)
        self._auto_end_if_needed(core)

    def do_move_or_road_then_move(self, builder: Entity, target: Pos) -> None:
        if not self.game.in_bounds(target):
            self.status_var.set("target out of bounds")
            return
        d = self.adjacent_dir(builder.pos, target)
        if d is None:
            self.status_var.set("target is not adjacent to the builder")
            return

        def go() -> None:
            walkable = self.game.is_walkable_for_builder(builder.team, target)
            if not walkable:
                if builder.action_cd != 0:
                    raise RuleError("destination is not walkable and builder has no action available")
                terrain = self.game.terrain_at(target)
                if terrain == Terrain.WALL:
                    raise RuleError("destination is a wall - cannot lay road")
                if self.game.building_at_pos(target) is not None:
                    raise RuleError("destination already has a non-walkable building")
                cost = self.game.cost_for(builder.team, EntityType.ROAD)
                self.auto_convert_for(builder.team, cost)
                self.game.build(builder.eid, EntityType.ROAD, target)
                self.exporter.record_builder(
                    builder, self.bot_turn(),
                    action_build_lines(EntityType.ROAD, target),
                )
            self.game.move_builder(builder.eid, d)
            self.exporter.record_builder(builder, self.bot_turn(), action_move_lines(d))
        self.do_with(go)
        self._auto_end_if_needed(builder)

    def do_simple_build(self, builder: Entity, typ: EntityType, target: Pos) -> None:
        if builder.typ != EntityType.BUILDER:
            self.status_var.set("build requires a builder")
            return

        def go() -> None:
            cost = self.game.cost_for(builder.team, typ)
            self.auto_convert_for(builder.team, cost)
            self.game.build(builder.eid, typ, target)
            self.exporter.record_builder(builder, self.bot_turn(), action_build_lines(typ, target))
            self.pending_building = None
        self.do_with(go)
        self._auto_end_if_needed(builder)

    def do_directional_build(self, actor: Entity, target: Pos, direction: Dir) -> None:
        typ = self.pending_building
        if typ not in DIRECTIONAL_BUILDS:
            self.mode = "tile"
            return
        if actor.typ != EntityType.BUILDER:
            self.status_var.set("build requires a builder")
            return

        def go() -> None:
            cost = self.game.cost_for(actor.team, typ)
            self.auto_convert_for(actor.team, cost)
            self.game.build(actor.eid, typ, target, direction=direction)
            self.exporter.record_builder(
                actor, self.bot_turn(),
                action_build_lines(typ, target, direction=direction),
            )
            self.pending_building = None
        self.do_with(go)
        # Return to tile mode regardless of success.
        self.mode = "tile"
        self.refresh_side_panel()
        self.redraw()
        self._auto_end_if_needed(actor)

    def do_bridge_build(self, actor: Entity, source: Pos, target: Pos) -> None:
        if actor.typ != EntityType.BUILDER:
            self.status_var.set("build requires a builder")
            return

        def go() -> None:
            cost = self.game.cost_for(actor.team, EntityType.BRIDGE)
            self.auto_convert_for(actor.team, cost)
            self.game.build(actor.eid, EntityType.BRIDGE, source, bridge_target=target)
            self.exporter.record_builder(
                actor, self.bot_turn(),
                action_build_lines(EntityType.BRIDGE, source, bridge_target=target),
            )
            self.pending_building = None
        self.do_with(go)
        self.selected_tile = source
        self.bridge_source = None
        self.mode = "tile"
        self.refresh_side_panel()
        self.redraw()
        self._auto_end_if_needed(actor)

    def do_destroy(self, target: Pos) -> None:
        actor = self.active_unit()
        if actor is None:
            return

        def go() -> None:
            self.game.destroy_building(actor.eid, target)
            self.exporter.record_builder(actor, self.bot_turn(), action_destroy_lines(target))
        self.do_with(go)
        self._auto_end_if_needed(actor)

    def do_self_destruct(self) -> None:
        actor = self.active_unit()
        if actor is None or actor.typ != EntityType.BUILDER:
            return

        def go() -> None:
            self.game.self_destruct(actor.eid)
            self.exporter.record_builder(actor, self.bot_turn(), action_self_destruct_lines())
        self.do_with(go)
        self.advance_turn(actor)

    # ----------------------------- geometry helpers -----------------------------

    def adjacent_dir(self, src: Pos, dst: Pos) -> Optional[Dir]:
        dx = dst.x - src.x
        dy = dst.y - src.y
        if dx == 0 and dy == 0:
            return None
        if abs(dx) > 1 or abs(dy) > 1:
            return None
        for direction in Dir:
            if direction.dxdy == (dx, dy):
                return direction
        return None

    def pos_from_event(self, event: tk.Event) -> Optional[Pos]:
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        x = int(cx) // self.cell_size
        y = int(cy) // self.cell_size
        p = Pos(x, y)
        if self.game.in_bounds(p):
            return p
        return None

    # ----------------------------- zoom / pan -----------------------------

    MIN_ZOOM = 0.4
    MAX_ZOOM = 4.0

    def _pan_start(self, event: tk.Event) -> None:
        self.canvas.scan_mark(event.x, event.y)

    def _pan_drag(self, event: tk.Event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _scrollregion(self) -> Tuple[int, int, int, int]:
        """Scrollregion that includes generous padding around the map so the
        user can pan well past its edges."""
        w = self.game.w * self.cell_size
        h = self.game.h * self.cell_size
        pad_x = max(w, 600)
        pad_y = max(h, 600)
        return (-pad_x, -pad_y, w + pad_x, h + pad_y)

    def _zoom_step(self, factor: float) -> str:
        self._set_zoom(self.zoom * factor)
        return "break"

    def _set_zoom(self, zoom: float) -> bool:
        zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))
        new_cell = max(8, round(self.base_cell_size * zoom))
        if new_cell == self.cell_size:
            return False
        self.cell_size = new_cell
        self.zoom = new_cell / self.base_cell_size
        self.canvas.config(scrollregion=self._scrollregion())
        self.redraw()
        return True

    def refresh_status_after_selection(self) -> None:
        if self.selected_tile is None:
            return
        self.status_var.set(
            f"cursor=({self.selected_tile.x},{self.selected_tile.y})  {self.status_for_active()}"
        )

    # ----------------------------- side panel -----------------------------

    def refresh_side_panel(self) -> None:
        ts = self.game.teams.get(0)
        if ts is None:
            self.state_var.set("(no team 0)")
        else:
            unit_count = self.game.unit_count(0)
            scale_factor = max(0.01, ts.scale_percent / 100.0)
            effective_ti = (ts.titanium + ts.axionite * 4) / scale_factor
            lines = [
                f"Round: {self.game.round}  (bot turn {self.bot_turn()})",
                f"Units: {unit_count}",
                f"Ti: {ts.titanium}",
                f"Ax: {ts.axionite}",
                f"Effective Ti: {effective_ti:.0f}",
                f"Scale: {ts.scale_percent:.1f}%",
                f"Ti collected: {ts.ti_collected}",
                f"Ax collected: {ts.ax_collected}",
            ]
            convert_now = self.round_convert_total.get(self.bot_turn(), 0)
            if convert_now:
                lines.append(f"Auto-converted this round: {convert_now} Ax")
            self.state_var.set("\n".join(lines))

        actor = self.active_unit()
        if actor is None:
            self.active_var.set("No active unit")
        else:
            extra = [
                f"#{actor.eid} {actor.typ.value}",
                f"pos=({actor.pos.x},{actor.pos.y}) hp={actor.hp}/{actor.max_hp}",
                f"action_cd={actor.action_cd} move_cd={actor.move_cd}",
                f"turn {self.turn_index + 1}/{max(1, len(self.turn_order))}",
            ]
            self.active_var.set("\n".join(extra))

        if self.pending_building is None:
            label = "(none)"
        else:
            label = self.pending_building.value
        mode_suffix = ""
        if self.mode == "direction":
            mode_suffix = f"  [direction-pick: {self.pending_dir.name}]"
        elif self.mode == "bridge_target":
            mode_suffix = "  [bridge-target-pick]"
        self.build_var.set(
            "Selected: " + label + mode_suffix
            + "\nkeys: " + BUILD_KEYS_LEGEND
        )

    # ----------------------------- map / export buttons -----------------------------

    def change_bot_dir(self) -> None:
        self.exporter.bot_dir = os.path.abspath(self.bot_dir_var.get().strip() or "pong_bot")
        self.bot_dir_var.set(self.exporter.bot_dir)
        self.status_var.set(f"Bot dir set to {self.exporter.bot_dir}")

    def export_now(self) -> None:
        self.export_generated_code("manual export")

    def export_generated_code(self, reason: str = "") -> None:
        self.exporter.bot_dir = os.path.abspath(self.bot_dir_var.get().strip() or "pong_bot")
        ok, msg = self.exporter.try_write_files()
        if ok:
            suffix = f" ({reason})" if reason else ""
            self.append_log(f"[export] {msg}{suffix}")
            self.status_var.set(msg)
        else:
            self.append_log(f"[export error] {msg}")
            self.status_var.set(f"Export error: {msg}")

    def load_map_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Load map26",
            initialfile="pong.map26",
            filetypes=[("Battlecode map26", "*.map26"), ("All files", "*")],
        )
        if not path:
            return
        try:
            map_data = load_map26(path)
            self.replace_game(game_from_map_data(map_data), os.path.basename(path))
        except Exception as e:
            self.status_var.set(f"Could not load map: {e}")
            self.append_log(f"Could not load map: {e}")

    def reset_sample(self) -> None:
        self.replace_game(Game.from_int_grid(SAMPLE_GRID), "sample grid")

    def reset_progress(self) -> None:
        if not self.history:
            return
        if not messagebox.askyesno(
            "Reset progress",
            "Wipe all progress and undo/redo history for the current map and start over?",
        ):
            return
        initial = self.history[0]
        self.history = [initial]
        self.cursor = 0
        self.restore(initial)
        self.round_convert_total = dict(initial.get("round_convert_total", {}))
        self.log_text.delete("1.0", tk.END)
        self.append_log("[reset] progress cleared")
        self.status_var.set("Progress reset")
        self._save_state()
        self.redraw()
        self.refresh_side_panel()

    def replace_game(self, game: Game, title: str) -> None:
        self.game = game
        self.title = title
        self.exporter.reset_for_game(game)
        self.base_cell_size = cell_size_for(game.h, game.w)
        self.cell_size = self.base_cell_size
        self.zoom = 1.0
        viewport_w = self.game.w * self.cell_size
        viewport_h = self.game.h * self.cell_size
        self.canvas.config(
            width=viewport_w,
            height=viewport_h,
            scrollregion=self._scrollregion(),
        )
        self.canvas.xview_moveto(0.0)
        self.canvas.yview_moveto(0.0)
        self.selected_tile = None
        self.mode = "tile"
        self.pending_building = None
        self.bridge_source = None
        self.history = []
        self.cursor = -1
        self.round_convert_total = {}
        self.log_text.delete("1.0", tk.END)
        self.start_round()
        self.record_snapshot()
        self.redraw()
        self.refresh_side_panel()

    # ----------------------------- drawing -----------------------------

    def redraw(self) -> None:
        self.canvas.delete("all")
        self._core_tile_set: Set[Pos] = set()
        for ent in self.game.entities.values():
            if ent.alive and ent.typ == EntityType.CORE:
                self._core_tile_set.update(ent.occupied)
        for y in range(self.game.h):
            for x in range(self.game.w):
                self.draw_base_cell(Pos(x, y))

        # Cores first, then other buildings, then builders on top.
        for ent in self.game.entities.values():
            if ent.alive and ent.typ == EntityType.CORE:
                self.draw_core(ent)

        for ent in self.game.entities.values():
            if not ent.alive or ent.typ in (EntityType.CORE, EntityType.BUILDER):
                continue
            self.draw_building(ent)

        for ent in self.game.entities.values():
            if ent.alive and ent.typ == EntityType.BUILDER:
                self.draw_builder(ent)

        if self.mode == "bridge_target" and self.bridge_source is not None:
            self.outline_cell(self.bridge_source, "#1a73e8", 3)
        if self.selected_tile is not None:
            self.outline_cell(self.selected_tile, "#fbbc04", 3)
            if self.mode == "direction":
                self.draw_direction_indicator(self.selected_tile, self.pending_dir)

        actor = self.active_unit()
        if actor is not None and actor.typ != EntityType.CORE:
            for p in actor.occupied:
                self.outline_cell(p, "#e91e63", 4)

    def draw_base_cell(self, p: Pos) -> None:
        x0, y0, x1, y1 = self.cell_box(p)
        terrain = self.game.terrain_at(p)
        fill = TERRAIN_COLORS.get(terrain, EMPTY_TILE_COLOR)
        outline = "" if p in self._core_tile_set else "#4a3936"
        self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline)
        ore_asset = None
        if terrain == Terrain.TITANIUM_ORE:
            ore_asset = "titanium-ore"
        elif terrain == Terrain.AXIONITE_ORE:
            ore_asset = "axionite-ore"
        if ore_asset is not None:
            photo = self.assets.get(ore_asset, max(1, self.cell_size - 2))
            if photo is not None:
                cx, cy = self.cell_center(p)
                self.canvas.create_image(cx, cy, image=photo)

    def draw_core(self, ent: Entity) -> None:
        xs = [p.x for p in ent.occupied]
        ys = [p.y for p in ent.occupied]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        w = max_x - min_x + 1
        h = max_y - min_y + 1

        x0 = min_x * self.cell_size
        y0 = min_y * self.cell_size
        x1 = (max_x + 1) * self.cell_size
        y1 = (max_y + 1) * self.cell_size

        size = max(w, h) * self.cell_size
        photo = self.assets.get("core", size)
        if photo is not None and ent.team == 0:
            cx = (x0 + x1) // 2
            cy = (y0 + y1) // 2
            self.canvas.create_image(cx, cy, image=photo)
        else:
            fill = MY_TEAM_COLOR if ent.team == 0 else ENEMY_TEAM_COLOR
            for p in ent.occupied:
                bx0, by0, bx1, by1 = self.cell_box(p)
                self.canvas.create_rectangle(bx0, by0, bx1, by1, fill=fill, outline="#5f3c21")
            self.draw_text(ent.pos, f"C{ent.team}", "#ffffff")
        self.draw_hp_bar(x0, y0, x1, y0 + max(4, self.cell_size // 6), ent.hp, ent.max_hp)

    def draw_building(self, ent: Entity) -> None:
        x0, y0, x1, y1 = self.cell_box(ent.pos, pad=2)
        cx, cy = self.cell_center(ent.pos)
        size = self.cell_size - 4
        photo = None
        if ent.typ == EntityType.ROAD:
            photo = self.assets.get("road", size)
            if photo is None:
                self.canvas.create_rectangle(x0, y0, x1, y1, fill="#aeb4bd", outline="#70757d")
                self.draw_text(ent.pos, "R", "#202124")
        elif ent.typ == EntityType.CONVEYOR:
            photo = self.assets.get("conveyor", size, ent.direction or Dir.E)
            if photo is None:
                self.canvas.create_rectangle(x0, y0, x1, y1, fill="#2bb3bd", outline="#116269")
                self.draw_arrow(ent.pos, ent.direction or Dir.E)
        elif ent.typ == EntityType.SPLITTER:
            photo = self.assets.get("splitter", size, ent.direction or Dir.E)
            if photo is None:
                self.canvas.create_rectangle(x0, y0, x1, y1, fill="#00897b", outline="#005c51")
                self.draw_arrow(ent.pos, ent.direction or Dir.E)
        elif ent.typ == EntityType.HARVESTER:
            photo = self.assets.get("harvester", size)
            if photo is None:
                self.canvas.create_rectangle(x0, y0, x1, y1, fill="#30a46c", outline="#17633f")
                self.draw_text(ent.pos, "H", "#ffffff")
        elif ent.typ == EntityType.FOUNDRY:
            photo = self.assets.get("foundry", size)
            if photo is None:
                self.canvas.create_rectangle(x0, y0, x1, y1, fill="#8f63d8", outline="#4d2f88")
                self.draw_text(ent.pos, "F", "#ffffff")
        elif ent.typ == EntityType.BRIDGE:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#4068d4", outline="#193b91")
            self.draw_text(ent.pos, "B", "#ffffff")
            if ent.bridge_target is not None:
                tx, ty = self.cell_center(ent.bridge_target)
                self.canvas.create_line(cx, cy, tx, ty, fill="#193b91", width=2)
                self.canvas.create_oval(tx - 4, ty - 4, tx + 4, ty + 4, outline="#193b91", width=2)
        elif ent.typ == EntityType.ARMOURED_CONVEYOR:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#5f7f8f", outline="#344955")
            self.draw_arrow(ent.pos, ent.direction or Dir.E)

        if photo is not None:
            self.canvas.create_image(cx, cy, image=photo)

        if ent.stored is not None:
            asset_name = RESOURCE_ASSET_NAME.get(ent.stored.kind)
            if asset_name is not None:
                res_size = max(8, self.cell_size // 2)
                res_photo = self.assets.get(asset_name, res_size)
                if res_photo is not None:
                    self.canvas.create_image(cx, cy, image=res_photo)
                else:
                    self.draw_text_xy(cx, cy, ent.stored.kind.value, "#ffffff", size_delta=-6)
        elif ent.typ == EntityType.FOUNDRY:
            inputs = []
            if ent.foundry_ti:
                inputs.append("titanium")
            if ent.foundry_raw:
                inputs.append("axionite-raw")
            if inputs:
                small = max(6, self.cell_size // 3)
                step = small + 2
                start_x = cx - (step * (len(inputs) - 1)) / 2
                for i, name in enumerate(inputs):
                    res_photo = self.assets.get(name, small)
                    px = start_x + step * i
                    py = cy + self.cell_size * 0.28
                    if res_photo is not None:
                        self.canvas.create_image(px, py, image=res_photo)
                    else:
                        label = "Ti" if name == "titanium" else "RawAx"
                        self.draw_text_xy(px, py, label, "#ffffff", size_delta=-7)

        self.draw_hp_bar(x0, y0 - 3, x1, y0 - 1, ent.hp, ent.max_hp)

    def draw_builder(self, ent: Entity) -> None:
        x0, y0, x1, y1 = self.cell_box(ent.pos, pad=max(2, self.cell_size // 8))
        size = max(8, x1 - x0)
        photo = self.assets.get("builder-bot", size)
        cx, cy = self.cell_center(ent.pos)
        if photo is not None:
            self.canvas.create_image(cx, cy, image=photo)
        else:
            fill = "#f4b400" if ent.team == 0 else "#db4437"
            self.canvas.create_oval(x0, y0, x1, y1, fill=fill, outline="#202124", width=2)
            self.draw_text(ent.pos, f"b{ent.eid}", "#202124", size_delta=-5)
        self.draw_hp_bar(x0, y0 - 3, x1, y0 - 1, ent.hp, ent.max_hp)

    def storage_label(self, ent: Entity) -> str:
        if ent.stored:
            return ent.stored.kind.value
        if ent.typ == EntityType.FOUNDRY:
            parts = []
            if ent.foundry_ti:
                parts.append("Ti")
            if ent.foundry_raw:
                parts.append("Raw")
            return "+".join(parts)
        return ""

    def draw_arrow(self, p: Pos, direction: Dir) -> None:
        cx, cy = self.cell_center(p)
        dx, dy = direction.dxdy
        length = max(5, self.cell_size // 3)
        self.canvas.create_line(
            cx - dx * length, cy - dy * length,
            cx + dx * length, cy + dy * length,
            fill="#ffffff", width=2, arrow=tk.LAST,
        )

    def draw_direction_indicator(self, p: Pos, direction: Dir) -> None:
        cx, cy = self.cell_center(p)
        dx, dy = direction.dxdy
        length = max(8, self.cell_size // 2)
        self.canvas.create_line(
            cx, cy, cx + dx * length, cy + dy * length,
            fill="#ea4335", width=4, arrow=tk.LAST,
        )

    def draw_hp_bar(self, x0: int, y0: int, x1: int, y1: int, hp: int, max_hp: int) -> None:
        if max_hp <= 0:
            return
        ratio = max(0.0, min(1.0, hp / max_hp))
        if ratio >= 1.0:
            return
        bar_w = max(0, x1 - x0)
        fill_w = int(bar_w * ratio)
        self.canvas.create_rectangle(x0, y0, x0 + bar_w, y1, fill="#aaaaaa", outline="")
        self.canvas.create_rectangle(x0, y0, x0 + fill_w, y1, fill="#34a853", outline="")

    def outline_cell(self, p: Pos, color: str, width: int) -> None:
        x0, y0, x1, y1 = self.cell_box(p, pad=1)
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=width)

    def cell_box(self, p: Pos, pad: int = 0) -> Tuple[int, int, int, int]:
        x0 = p.x * self.cell_size + pad
        y0 = p.y * self.cell_size + pad
        x1 = (p.x + 1) * self.cell_size - pad
        y1 = (p.y + 1) * self.cell_size - pad
        return x0, y0, x1, y1

    def cell_center(self, p: Pos) -> Tuple[float, float]:
        return (p.x + 0.5) * self.cell_size, (p.y + 0.5) * self.cell_size

    def draw_text(self, p: Pos, text: str, fill: str, size_delta: int = 0) -> None:
        x, y = self.cell_center(p)
        self.draw_text_xy(x, y, text, fill, size_delta)

    def draw_text_xy(self, x: float, y: float, text: str, fill: str, size_delta: int = 0) -> None:
        size = max(7, self.cell_size // 2 + size_delta)
        self.canvas.create_text(x, y, text=text, fill=fill, font=("TkDefaultFont", size, "bold"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keyboard-driven self-play UI for hardcoding bots.")
    parser.add_argument("--map", default="maps/pong.map26", help="map26 file to load; default keeps pong.map26")
    parser.add_argument("--sample", action="store_true", help="use a tiny built-in sample grid instead of loading --map")
    parser.add_argument("--check", action="store_true", help="load the map and print a summary without opening the UI")
    parser.add_argument("--bot-dir", default="pong_bot", help="directory to rewrite generated bot files into")
    return parser.parse_args()


def make_initial_game(args: argparse.Namespace) -> Tuple[Game, str]:
    if args.sample:
        return Game.from_int_grid(SAMPLE_GRID), "sample grid"
    map_data = load_map26(args.map)
    return game_from_map_data(map_data), os.path.basename(args.map)


def main() -> None:
    args = parse_args()
    if args.check:
        if args.sample:
            g = Game.from_int_grid(SAMPLE_GRID)
            print(f"Loaded sample grid: {g.w}x{g.h}, {len(g.teams)} team(s)")
        else:
            md = load_map26(args.map)
            print(f"Loaded {args.map}: {md.width}x{md.height}, {len(md.cores)} core(s)")
        return

    game, title = make_initial_game(args)
    root = tk.Tk()
    HardcodeApp(root, game, title=title, bot_dir=args.bot_dir)
    root.mainloop()


if __name__ == "__main__":
    main()
