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
    def __init__(self, terrain: List[List[int]]) -> None:
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
        self.next_eid = 1
        self.next_rid = 1
        self.log: List[str] = []

        self._place_cores_from_terrain()

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
        if stat.is_unit:
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
        self.log.append(f"T{core.team} core #{core.eid} spawned builder #{bot.eid} at {pos} for {cost.short()}")
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


# ----------------------------- sample main -----------------------------


SAMPLE_GRID = [
    # 0 empty, 1 core tile, 2 titanium ore, 3 axionite ore, 4 wall
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 1, 1, 1, 0, 0, 2, 0, 0, 3, 0, 0],
    [0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 1, 1, 1, 0, 0, 0, 4, 4, 4, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 2, 0, 0, 0, 3, 0, 0],
]


def main() -> None:
    game = Game.from_int_grid(SAMPLE_GRID)
    print("Cambridge Battlecode self-play sandbox")
    print("Type 'help' during a unit turn for commands. Ctrl-D/Ctrl-C exits.")
    try:
        while True:
            game.run_one_round_interactive()
    except (EOFError, KeyboardInterrupt):
        print("\nExiting.")


if __name__ == "__main__":
    main()
