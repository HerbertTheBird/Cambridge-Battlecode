#!/usr/bin/env python3
"""
bc_selfplay_tool.py

A small, standard-library-only Cambridge Battlecode-style self-play sandbox.

Scope implemented:
  - Terrain input as a 2D int grid:
      0 empty
      1 core tile
      2 titanium ore
      3 axionite ore
      4 wall
  - Core, builder bots, roads, conveyors, splitters, bridges, armoured conveyors,
    harvesters, and axionite foundries.
  - Global Ti/Ax resources, passive Ti income, cost scaling, unit/action/move
    cooldowns, spawn order, one-hop end-of-round resource movement, harvesting,
    and foundry refining.
  - Interactive stepping so a human can choose each unit's action.

Intentionally ignored for now:
  - Turrets, barriers, markers, true combat system, fog/vision, enemy teams beyond
    separate core regions, exact engine timing/performance behavior.

Run:
    python bc_selfplay_tool.py

Or import Game and use Game.from_int_grid(grid).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
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
        else:
            if terrain != Terrain.EMPTY:
                raise RuleError("only harvesters may be built on ore tiles in this sandbox")
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
            if stack.kind == Resource.TITANIUM:
                return dst.foundry_ti is None
            if stack.kind == Resource.RAW_AXIONITE:
                return dst.foundry_raw is None
            return False
        return False

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
                    ent.next_harvest_round = self.round + 4
            elif ent.typ in (EntityType.CONVEYOR, EntityType.SPLITTER, EntityType.BRIDGE,
                             EntityType.ARMOURED_CONVEYOR, EntityType.FOUNDRY):
                if ent.stored is not None:
                    outputs.append((eid, ent.stored))

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
            elif not moved and ent.typ == EntityType.HARVESTER:
                self.log.append(f"harvester #{ent.eid} output was blocked/skipped")

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

    # ----------------------------- round stepping -----------------------------

    def end_round(self) -> None:
        # Passive titanium every 4 rounds. Round 0 is the first player-action round,
        # so award after rounds 3, 7, 11, ... have completed.
        if (self.round + 1) % 4 == 0:
            for team, ts in self.teams.items():
                ts.titanium += 10
                ts.ti_collected += 10
                self.log.append(f"T{team} gained 10 passive Ti")

        self.distribute_resources()

        for ent in self.entities.values():
            if not ent.alive:
                continue
            if ent.action_cd > 0:
                ent.action_cd -= 1
            if ent.move_cd > 0:
                ent.move_cd -= 1
        self.round += 1

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
import tkinter as tk
from tkinter import filedialog

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

GENERATED_HEADER = "# AUTO-GENERATED by bc_selfplay_ui.py. Manual edits may be overwritten."


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

MOVE_BUTTONS = [
    ["NW", "N", "NE"],
    ["W", "WAIT", "E"],
    ["SW", "S", "SE"],
]

BUILD_KIND_LABELS = [
    "road",
    "conveyor",
    "splitter",
    "bridge",
    "harvester",
    "foundry",
    "armoured_conveyor",
]

TERRAIN_COLORS = {
    Terrain.EMPTY: "#ffffff",
    Terrain.TITANIUM_ORE: "#b8e6ff",
    Terrain.AXIONITE_ORE: "#ffc36b",
    Terrain.WALL: "#202124",
}

TEAM_COLORS = ["#8a5a35", "#6b4aa0", "#386641", "#9a3412"]


class SelfPlayApp:
    def __init__(self, root: tk.Tk, game: Game, *, title: str = "Self Play", bot_dir: str = "pong_bot") -> None:
        self.root = root
        self.game = game
        self.title = title
        self.cell_size = auto_cell_size(game.h, game.w)
        self.target_cell: Optional[Pos] = None
        self.bridge_target_cell: Optional[Pos] = None
        self.turn_order: List[int] = []
        self.turn_index = 0
        self.round_active = False
        self.exporter = BotCodeExporter(bot_dir)
        self.exporter.reset_for_game(game)

        self.status_var = tk.StringVar(value="Left-click selects an action target; right-click selects a bridge target.")
        self.state_var = tk.StringVar(value="")
        self.active_var = tk.StringVar(value="")
        self.build_kind_var = tk.StringVar(value="road")
        self.build_dir_var = tk.StringVar(value="E")
        self.convert_var = tk.StringVar(value="10")
        self.bot_dir_var = tk.StringVar(value=self.exporter.bot_dir)

        root.title(f"Battlecode Self Play - {title}")
        root.geometry("")
        self.build_ui()
        self.start_round()
        self.redraw()
        self.refresh_side_panel()

    def build_ui(self) -> None:
        outer = tk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        left = tk.Frame(outer)
        left.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        legend = tk.Label(
            left,
            anchor="w",
            text="Black=wall  Brown/Purple=core  Blue=Ti ore  Orange=Ax ore  b=builder  H=harvester  C=conveyor  B=bridge  F=foundry  R=road",
        )
        legend.pack(fill="x", pady=(0, 4))

        self.canvas = tk.Canvas(
            left,
            width=self.game.w * self.cell_size,
            height=self.game.h * self.cell_size,
            bg="#ffffff",
            highlightthickness=0,
        )
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.on_left_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Motion>", self.on_motion)

        tk.Label(left, textvariable=self.status_var, anchor="w").pack(fill="x", pady=(4, 0))

        right = tk.Frame(outer, width=360)
        right.pack(side="right", fill="y", padx=(0, 8), pady=8)
        right.pack_propagate(False)

        tk.Label(right, text="Game State", font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill="x")
        tk.Label(right, textvariable=self.state_var, justify="left", anchor="w").pack(fill="x", pady=(0, 8))

        tk.Label(right, text="Active Unit", font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill="x")
        tk.Label(right, textvariable=self.active_var, justify="left", anchor="w", wraplength=340).pack(fill="x", pady=(0, 8))

        turn_frame = tk.Frame(right)
        turn_frame.pack(fill="x", pady=(0, 8))
        tk.Button(turn_frame, text="Wait / Finish Turn", command=self.wait_active).pack(side="left", expand=True, fill="x", padx=(0, 4))
        tk.Button(turn_frame, text="End Round Now", command=self.force_end_round).pack(side="left", expand=True, fill="x")

        self.core_frame = tk.LabelFrame(right, text="Core Actions", padx=6, pady=6)
        self.core_frame.pack(fill="x", pady=(0, 8))
        tk.Button(self.core_frame, text="Spawn builder on selected tile", command=self.spawn_selected).pack(fill="x")
        conv_row = tk.Frame(self.core_frame)
        conv_row.pack(fill="x", pady=(6, 0))
        tk.Label(conv_row, text="Convert Ax:").pack(side="left")
        tk.Entry(conv_row, textvariable=self.convert_var, width=6).pack(side="left", padx=4)
        tk.Button(conv_row, text="Convert", command=self.convert_ax).pack(side="left")

        self.builder_frame = tk.LabelFrame(right, text="Builder Actions", padx=6, pady=6)
        self.builder_frame.pack(fill="x", pady=(0, 8))

        move_grid = tk.Frame(self.builder_frame)
        move_grid.pack(pady=(0, 6))
        for r, row in enumerate(MOVE_BUTTONS):
            for c, label in enumerate(row):
                if label == "WAIT":
                    cmd = self.wait_active
                    text = "·"
                else:
                    cmd = lambda d=label: self.move_active(d)
                    text = label
                tk.Button(move_grid, text=text, width=5, command=cmd).grid(row=r, column=c, padx=1, pady=1)

        build_row1 = tk.Frame(self.builder_frame)
        build_row1.pack(fill="x", pady=(2, 0))
        tk.Label(build_row1, text="Build:").pack(side="left")
        tk.OptionMenu(build_row1, self.build_kind_var, *BUILD_KIND_LABELS).pack(side="left", padx=4)
        tk.Label(build_row1, text="Dir:").pack(side="left")
        tk.OptionMenu(build_row1, self.build_dir_var, "N", "E", "S", "W").pack(side="left", padx=4)

        tk.Button(self.builder_frame, text="Build at selected tile", command=self.build_selected).pack(fill="x", pady=(6, 0))

        act_row = tk.Frame(self.builder_frame)
        act_row.pack(fill="x", pady=(6, 0))
        tk.Button(act_row, text="Heal selected", command=self.heal_selected).pack(side="left", expand=True, fill="x", padx=(0, 3))
        tk.Button(act_row, text="Destroy selected", command=self.destroy_selected).pack(side="left", expand=True, fill="x", padx=(3, 0))

        act_row2 = tk.Frame(self.builder_frame)
        act_row2.pack(fill="x", pady=(6, 0))
        tk.Button(act_row2, text="Attack under bot", command=self.attack_active).pack(side="left", expand=True, fill="x", padx=(0, 3))
        tk.Button(act_row2, text="Self-destruct", command=self.self_destruct_active).pack(side="left", expand=True, fill="x", padx=(3, 0))

        misc = tk.LabelFrame(right, text="Map / Session / Export", padx=6, pady=6)
        misc.pack(fill="x", pady=(0, 8))
        tk.Button(misc, text="Load map26…", command=self.load_map_dialog).pack(fill="x")
        tk.Button(misc, text="Reset sample grid", command=self.reset_sample).pack(fill="x", pady=(4, 0))
        tk.Button(misc, text="Print text map to log", command=self.print_text_map).pack(fill="x", pady=(4, 0))
        tk.Label(misc, text="Bot dir:", anchor="w").pack(fill="x", pady=(6, 0))
        tk.Entry(misc, textvariable=self.bot_dir_var).pack(fill="x")
        tk.Button(misc, text="Use bot dir", command=self.change_bot_dir).pack(fill="x", pady=(4, 0))
        tk.Button(misc, text="Export generated bot files now", command=self.export_now).pack(fill="x", pady=(4, 0))

        tk.Label(right, text="Log", font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill="x")
        self.log_text = tk.Text(right, width=44, height=18, wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def bot_turn(self) -> int:
        # CambC rounds start at 1; the sandbox starts at 0.
        return self.game.round + 1

    def active_unit(self) -> Optional[Entity]:
        while self.turn_index < len(self.turn_order):
            eid = self.turn_order[self.turn_index]
            ent = self.game.entities.get(eid)
            if ent is not None and ent.alive and ent.team == 0:
                return ent
            self.turn_index += 1
        return None

    def start_round(self) -> None:
        # Team/core 1 is the opponent and is intentionally skipped.
        # Snapshot only units that existed before this round began. Newly spawned
        # builders are appended to global spawn order immediately, but they are
        # not eligible to act until the following round.
        self.turn_order = [e.eid for e in self.game.alive_units_eligible_this_round() if e.team == 0]
        self.turn_index = 0
        self.round_active = True
        self.append_log(f"=== Round {self.game.round} / bot turn {self.bot_turn()} ===")
        self.skip_to_live_or_end()

    def skip_to_live_or_end(self) -> None:
        while self.turn_index < len(self.turn_order):
            eid = self.turn_order[self.turn_index]
            ent = self.game.entities.get(eid)
            if ent is not None and ent.alive and ent.team == 0:
                return
            self.turn_index += 1
        self.finish_round()

    def finish_round(self) -> None:
        self.game.end_round()
        self.consume_game_log()
        self.append_log(f"--- End round; now round {self.game.round} / bot turn {self.bot_turn()} ---")
        self.turn_order = []
        self.turn_index = 0
        self.round_active = False
        self.start_round()

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

    def finish_active_turn(self, actor: Optional[Entity]) -> None:
        self.exporter.record_wait_turn(actor, self.bot_turn())
        self.export_generated_code("turn finished")
        self.advance_turn()

    def advance_turn(self) -> None:
        self.turn_index += 1
        self.skip_to_live_or_end()
        self.consume_game_log()
        self.redraw()
        self.refresh_side_panel()

    def current_target_required(self) -> Pos:
        if self.target_cell is None:
            raise RuleError("left-click a target tile first")
        return self.target_cell

    def current_bridge_target_required(self) -> Pos:
        if self.bridge_target_cell is None:
            raise RuleError("right-click a bridge target tile first")
        return self.bridge_target_cell

    def actor_or_error(self, typ: Optional[EntityType] = None) -> Entity:
        actor = self.active_unit()
        if actor is None:
            raise RuleError("no active unit")
        if typ is not None and actor.typ != typ:
            raise RuleError(f"active unit is not a {typ.value}")
        return actor

    def run_action(self, fn: Callable[[], Optional[bool]]) -> None:
        actor_before = self.active_unit()
        try:
            should_advance = fn()
            self.consume_game_log()
            if should_advance:
                self.finish_active_turn(actor_before)
            else:
                self.redraw()
                self.refresh_side_panel()
        except RuleError as e:
            self.status_var.set(f"RuleError: {e}")
            self.append_log(f"RuleError: {e}")
        except (ValueError, IndexError) as e:
            self.status_var.set(f"Bad input: {e}")
            self.append_log(f"Bad input: {e}")

    def wait_active(self) -> None:
        self.run_action(lambda: True)

    def force_end_round(self) -> None:
        self.exporter.record_wait_turn(self.active_unit(), self.bot_turn())
        self.export_generated_code("round forced")
        self.turn_index = len(self.turn_order)
        self.finish_round()
        self.redraw()
        self.refresh_side_panel()

    def spawn_selected(self) -> None:
        def do() -> bool:
            core = self.actor_or_error(EntityType.CORE)
            target = self.current_target_required()
            bot = self.game.spawn_builder(core.eid, target)
            self.exporter.register_builder(bot, write=False)
            self.exporter.record_core(self.bot_turn(), action_spawn_builder_lines(target))
            return True
        self.run_action(do)

    def convert_ax(self) -> None:
        def do() -> bool:
            core = self.actor_or_error(EntityType.CORE)
            amount = int(self.convert_var.get())
            self.game.convert_axionite(core.eid, amount)
            self.exporter.record_core(self.bot_turn(), action_convert_lines(amount))
            # Conversion does not cost action cooldown; keep the core's turn active.
            return False
        self.run_action(do)

    def move_active(self, direction: str) -> None:
        def do() -> bool:
            builder = self.actor_or_error(EntityType.BUILDER)
            d = parse_dir(direction)
            self.game.move_builder(builder.eid, d)
            self.exporter.record_builder(builder, self.bot_turn(), action_move_lines(d))
            # Builders can still build/action after moving, so do not auto-finish.
            return False
        self.run_action(do)

    def build_selected(self) -> None:
        def do() -> bool:
            builder = self.actor_or_error(EntityType.BUILDER)
            target = self.current_target_required()
            kind = self.build_kind_var.get()
            direction = parse_dir(self.build_dir_var.get())
            built_type: EntityType
            bridge_target: Optional[Pos] = None
            if kind == "road":
                built_type = EntityType.ROAD
                self.game.build(builder.eid, built_type, target)
            elif kind == "conveyor":
                built_type = EntityType.CONVEYOR
                self.game.build(builder.eid, built_type, target, direction=direction)
            elif kind == "splitter":
                built_type = EntityType.SPLITTER
                self.game.build(builder.eid, built_type, target, direction=direction)
            elif kind == "bridge":
                built_type = EntityType.BRIDGE
                bridge_target = self.current_bridge_target_required()
                self.game.build(builder.eid, built_type, target, bridge_target=bridge_target)
            elif kind == "harvester":
                built_type = EntityType.HARVESTER
                self.game.build(builder.eid, built_type, target)
            elif kind == "foundry":
                built_type = EntityType.FOUNDRY
                self.game.build(builder.eid, built_type, target)
            elif kind == "armoured_conveyor":
                built_type = EntityType.ARMOURED_CONVEYOR
                self.game.build(builder.eid, built_type, target, direction=direction)
            else:
                raise RuleError(f"unknown build kind {kind}")
            self.exporter.record_builder(
                builder,
                self.bot_turn(),
                action_build_lines(built_type, target, direction=direction, bridge_target=bridge_target),
            )
            # Builders can still move after building, so do not auto-finish.
            return False
        self.run_action(do)

    def heal_selected(self) -> None:
        def do() -> bool:
            builder = self.actor_or_error(EntityType.BUILDER)
            target = self.current_target_required()
            self.game.heal(builder.eid, target)
            self.exporter.record_builder(builder, self.bot_turn(), action_heal_lines(target))
            # Heal uses action cooldown, but the builder may still move.
            return False
        self.run_action(do)

    def destroy_selected(self) -> None:
        def do() -> bool:
            builder = self.actor_or_error(EntityType.BUILDER)
            target = self.current_target_required()
            self.game.destroy_building(builder.eid, target)
            self.exporter.record_builder(builder, self.bot_turn(), action_destroy_lines(target))
            # Destroy is free and does not end the turn.
            return False
        self.run_action(do)

    def attack_active(self) -> None:
        def do() -> bool:
            builder = self.actor_or_error(EntityType.BUILDER)
            self.game.builder_attack_tile(builder.eid)
            self.exporter.record_builder(builder, self.bot_turn(), action_fire_lines())
            # Attack uses action cooldown, but the builder may still move.
            return False
        self.run_action(do)

    def self_destruct_active(self) -> None:
        def do() -> bool:
            actor = self.actor_or_error(EntityType.BUILDER)
            self.game.self_destruct(actor.eid)
            self.exporter.record_builder(actor, self.bot_turn(), action_self_destruct_lines())
            return True
        self.run_action(do)

    def on_left_click(self, event: tk.Event) -> None:
        pos = self.pos_from_event(event)
        if pos is None:
            return
        self.target_cell = pos
        self.status_var.set(f"Selected target ({pos.x}, {pos.y}). Right-click sets bridge target.")
        self.redraw()
        self.refresh_side_panel()

    def on_right_click(self, event: tk.Event) -> None:
        pos = self.pos_from_event(event)
        if pos is None:
            return
        self.bridge_target_cell = pos
        self.status_var.set(f"Selected bridge target ({pos.x}, {pos.y}).")
        self.redraw()
        self.refresh_side_panel()

    def on_motion(self, event: tk.Event) -> None:
        pos = self.pos_from_event(event)
        if pos is not None:
            self.root.title(f"Battlecode Self Play - {self.title}   hover=({pos.x},{pos.y})")

    def pos_from_event(self, event: tk.Event) -> Optional[Pos]:
        x = event.x // self.cell_size
        y = event.y // self.cell_size
        p = Pos(x, y)
        if self.game.in_bounds(p):
            return p
        return None

    def change_bot_dir(self) -> None:
        self.exporter.bot_dir = os.path.abspath(self.bot_dir_var.get().strip() or "pong_bot")
        self.bot_dir_var.set(self.exporter.bot_dir)
        self.status_var.set(f"Bot dir set to {self.exporter.bot_dir}")
        self.append_log(f"[export] bot dir set to {self.exporter.bot_dir}")

    def export_now(self) -> None:
        self.export_generated_code("manual export")

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

    def replace_game(self, game: Game, title: str) -> None:
        self.game = game
        self.title = title
        self.exporter.reset_for_game(game)
        self.cell_size = auto_cell_size(game.h, game.w)
        self.canvas.config(width=self.game.w * self.cell_size, height=self.game.h * self.cell_size)
        self.target_cell = None
        self.bridge_target_cell = None
        self.turn_order = []
        self.turn_index = 0
        self.log_text.delete("1.0", tk.END)
        self.start_round()
        self.redraw()
        self.refresh_side_panel()

    def print_text_map(self) -> None:
        self.append_log(self.game.render())

    def consume_game_log(self) -> None:
        if not self.game.log:
            return
        for line in self.game.log:
            self.append_log(line)
        self.game.log.clear()

    def append_log(self, msg: str) -> None:
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def refresh_side_panel(self) -> None:
        lines = [f"Round {self.game.round}"]
        for team in sorted(self.game.teams):
            ts = self.game.teams[team]
            lines.append(
                f"T{team}: Ti={ts.titanium} Ax={ts.axionite} scale={ts.scale_percent:.1f}% "
                f"collected Ti={ts.ti_collected} Ax={ts.ax_collected}"
            )
        if self.target_cell is not None:
            lines.append(f"target=({self.target_cell.x},{self.target_cell.y})")
        if self.bridge_target_cell is not None:
            lines.append(f"bridge target=({self.bridge_target_cell.x},{self.bridge_target_cell.y})")
        lines.append(f"bot dir={self.exporter.bot_dir}")
        lines.append(f"exports={self.exporter.export_count}")
        self.state_var.set("\n".join(lines))

        actor = self.active_unit()
        if actor is None:
            self.active_var.set("No active unit")
        else:
            extra = []
            extra.append(f"#{actor.eid} {actor.typ.value} team {actor.team}")
            extra.append(f"pos=({actor.pos.x},{actor.pos.y}) hp={actor.hp}/{actor.max_hp}")
            extra.append(f"action_cd={actor.action_cd} move_cd={actor.move_cd}")
            extra.append(f"turn {self.turn_index + 1}/{max(1, len(self.turn_order))}")
            self.active_var.set("\n".join(extra))

        is_core = actor is not None and actor.typ == EntityType.CORE
        is_builder = actor is not None and actor.typ == EntityType.BUILDER
        self.set_frame_enabled(self.core_frame, is_core)
        self.set_frame_enabled(self.builder_frame, is_builder)

    def set_frame_enabled(self, frame: tk.Widget, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for child in frame.winfo_children():
            try:
                child.configure(state=state)
            except tk.TclError:
                pass
            # OptionMenus are Menubuttons with a child menu; disabling the shell is enough.
            if isinstance(child, tk.Frame):
                self.set_frame_enabled(child, enabled)

    def redraw(self) -> None:
        self.canvas.delete("all")
        for y in range(self.game.h):
            for x in range(self.game.w):
                self.draw_base_cell(Pos(x, y))

        for eid in sorted(self.game.entities):
            ent = self.game.entities[eid]
            if ent.alive and ent.typ != EntityType.BUILDER:
                self.draw_building(ent)

        for eid in sorted(self.game.entities):
            ent = self.game.entities[eid]
            if ent.alive and ent.typ == EntityType.BUILDER:
                self.draw_builder(ent)

        if self.target_cell is not None:
            self.outline_cell(self.target_cell, "#fbbc04", 3)
        if self.bridge_target_cell is not None:
            self.outline_cell(self.bridge_target_cell, "#1a73e8", 3)

        actor = self.active_unit()
        if actor is not None:
            for p in actor.occupied:
                self.outline_cell(p, "#e91e63", 3)

    def draw_base_cell(self, p: Pos) -> None:
        x0, y0, x1, y1 = self.cell_box(p)
        fill = TERRAIN_COLORS.get(self.game.terrain_at(p), "#ffffff")
        self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="#d5d7dc")

    def draw_building(self, ent: Entity) -> None:
        if ent.typ == EntityType.CORE:
            for p in ent.occupied:
                x0, y0, x1, y1 = self.cell_box(p)
                fill = TEAM_COLORS[ent.team % len(TEAM_COLORS)]
                self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="#5f3c21")
            self.draw_text(ent.pos, f"C{ent.team}", "#ffffff")
            return

        x0, y0, x1, y1 = self.cell_box(ent.pos, pad=3)
        cx, cy = self.cell_center(ent.pos)
        if ent.typ == EntityType.ROAD:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#aeb4bd", outline="#70757d")
            self.draw_text(ent.pos, "R", "#202124")
        elif ent.typ == EntityType.CONVEYOR:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#2bb3bd", outline="#116269")
            self.draw_conveyor_arrow(ent.pos, ent.direction or Dir.E)
        elif ent.typ == EntityType.ARMOURED_CONVEYOR:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#5f7f8f", outline="#344955")
            self.draw_conveyor_arrow(ent.pos, ent.direction or Dir.E)
            self.draw_text_xy(cx, cy + self.cell_size * 0.24, "A", "#ffffff", size_delta=-4)
        elif ent.typ == EntityType.SPLITTER:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#00897b", outline="#005c51")
            self.draw_conveyor_arrow(ent.pos, ent.direction or Dir.E)
            self.draw_text_xy(cx, cy + self.cell_size * 0.24, "S", "#ffffff", size_delta=-4)
        elif ent.typ == EntityType.BRIDGE:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#4068d4", outline="#193b91")
            self.draw_text(ent.pos, "B", "#ffffff")
            if ent.bridge_target is not None:
                tx, ty = self.cell_center(ent.bridge_target)
                self.canvas.create_line(cx, cy, tx, ty, fill="#193b91", width=2)
                self.canvas.create_oval(tx - 4, ty - 4, tx + 4, ty + 4, outline="#193b91", width=2)
        elif ent.typ == EntityType.HARVESTER:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#30a46c", outline="#17633f")
            self.draw_text(ent.pos, "H", "#ffffff")
        elif ent.typ == EntityType.FOUNDRY:
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#8f63d8", outline="#4d2f88")
            self.draw_text(ent.pos, "F", "#ffffff")

        stored_label = self.storage_label(ent)
        if stored_label:
            self.draw_text_xy(cx, cy + self.cell_size * 0.34, stored_label, "#111111", size_delta=-7)

    def draw_builder(self, ent: Entity) -> None:
        x0, y0, x1, y1 = self.cell_box(ent.pos, pad=max(3, self.cell_size // 6))
        fill = "#f4b400" if ent.team == 0 else "#db4437"
        self.canvas.create_oval(x0, y0, x1, y1, fill=fill, outline="#202124", width=2)
        self.draw_text(ent.pos, f"b{ent.eid}", "#202124", size_delta=-5)

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

    def draw_conveyor_arrow(self, p: Pos, direction: Dir) -> None:
        cx, cy = self.cell_center(p)
        dx, dy = direction.dxdy
        length = max(5, self.cell_size // 3)
        self.canvas.create_line(
            cx - dx * length,
            cy - dy * length,
            cx + dx * length,
            cy + dy * length,
            fill="#ffffff",
            width=2,
            arrow=tk.LAST,
        )
        self.draw_text_xy(cx, cy + self.cell_size * 0.22, direction.name, "#ffffff", size_delta=-5)

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
    parser = argparse.ArgumentParser(description="Tkinter self-play tool for Cambridge Battlecode maps.")
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
    SelfPlayApp(root, game, title=title, bot_dir=args.bot_dir)
    root.mainloop()


if __name__ == "__main__":
    main()
