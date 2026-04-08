import random
from array import array
from collections import defaultdict

from cambc import Controller, Direction, EntityType, Environment, Position, ResourceType, Team

from globals import DIRECTIONS, CARDINAL_DIRECTIONS, CONVEYOR_TYPES, TURRET_TYPES, Symmetry, INF
from comms import Comms
from helpers import on_map, on_map_coords, get_foundry_positions, is_core_tile

from log import log, log_time

SYMMETRY_MAPPING = {
    (True,  False, False): Symmetry.FLIP_X,
    (False, True,  False): Symmetry.FLIP_Y,
    (False, False, True):  Symmetry.ROTATE,
}

_BRIDGE_OFFSETS = tuple((dx, dy) for dx in range(-3, 4) for dy in range(-3, 4) if dx*dx + dy*dy <= 9)
_CARDINAL_OFFSETS = ((0, -1), (1, 0), (0, 1), (-1, 0))

RESOURCE_MASK_TITANIUM = 1
RESOURCE_MASK_AXIONITE = 2
OBSERVED_RESOURCE_MAX_AGE = 5

FLAG_SEEN = 1 << 0
FLAG_WALL = 1 << 1
FLAG_BLOCKED = 1 << 2
FLAG_ALLY_BARRIER = 1 << 3
FLAG_ALLY_LAUNCHER = 1 << 4
FLAG_ORE_TITANIUM = 1 << 5
FLAG_ORE_AXIONITE = 1 << 6
_CLEAR_ENTITY_FLAGS = ~(FLAG_BLOCKED | FLAG_ALLY_BARRIER | FLAG_ALLY_LAUNCHER)

class Map:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.tile_count = width * height
        self._tile_flags = array("I", [0]) * self.tile_count
        self._env: list[Environment | None] = [None] * self.tile_count
        self._entity_id = array("i", [0]) * self.tile_count
        self._entity_type: list[EntityType | None] = [None] * self.tile_count
        self._entity_team: list[Team | None] = [None] * self.tile_count
        self._enemy_launcher_adj = bytearray(self.tile_count)
        self.ore_ti = set()
        self.ore_ax = set()
        self.unreachable_harvesters: set[Position] = set()
        self.unreachable_ores: set[Position] = set()
        self._output_idx = array("i", [-1]) * self.tile_count
        self._input_indices: list[set[int]] = [set() for _ in range(self.tile_count)]
        self.conveyor_resources: defaultdict[Position, set[ResourceType]] = defaultdict(set)  # build_pos -> observed resource types
        self.conveyor_resources_last_seen: defaultdict[Position, dict[ResourceType, int]] = defaultdict(dict)
        self._input_chain_valid = bytearray(self.tile_count)
        self._input_resource_masks = array("I", [0]) * self.tile_count
        self.current_round = 0
        self._feeds_turret_cache: dict[tuple[Position, Team], bool] = {}
        self._feeds_building_cache: dict[tuple[Position, Team], bool] = {}
        self._feeds_building_in_vision_cache: dict[tuple[Position, Team], bool] = {}
        self._sabotage_downstream_cache: dict[tuple[Position, Team], int] = {}
        self._chain_terminal_cache: dict[Position, Position] = {}
        self._chain_last_visible_cache: dict[Position, Position | None] = {}
        self.symmetry = Symmetry.UNKNOWN
        self.can_flip_x = True
        self.can_flip_y = True
        self.can_rotate = True
        self.should_update_all_symmetric = False
        self.symmetric_update_x = 0
        self.symmetric_update_y = 0

    def _idx(self, pos: Position) -> int:
        return pos.y * self.width + pos.x

    def _idx_if_on_map(self, pos: Position) -> int | None:
        if not on_map(pos, self.width, self.height):
            return None
        return pos.y * self.width + pos.x

    def _pos(self, idx: int) -> Position:
        return Position(idx % self.width, idx // self.width)

    def _get_flag_idx(self, idx: int, flag: int) -> bool:
        return bool(self._tile_flags[idx] & flag)

    def _set_flag_idx(self, idx: int, flag: int):
        self._tile_flags[idx] |= flag

    def _clear_flag_idx(self, idx: int, flag: int):
        self._tile_flags[idx] &= ~flag

    def _set_tile_env_idx(self, idx: int, env: Environment):
        self._env[idx] = env
        self._set_flag_idx(idx, FLAG_SEEN)
        self._clear_flag_idx(idx, FLAG_WALL | FLAG_ORE_TITANIUM | FLAG_ORE_AXIONITE)
        if env == Environment.WALL:
            self._set_flag_idx(idx, FLAG_WALL)
        elif env == Environment.ORE_TITANIUM:
            self._set_flag_idx(idx, FLAG_ORE_TITANIUM)
        elif env == Environment.ORE_AXIONITE:
            self._set_flag_idx(idx, FLAG_ORE_AXIONITE)

    def _set_tile_entity_idx(self, idx: int, bid: int, etype: EntityType, team: Team):
        self._entity_id[idx] = bid
        self._entity_type[idx] = etype
        self._entity_team[idx] = team

    def _clear_tile_entity_idx(self, idx: int):
        self._entity_id[idx] = 0
        self._entity_type[idx] = None
        self._entity_team[idx] = None


    def _is_visited_idx(self, idx: int) -> bool:
        return self._get_flag_idx(idx, FLAG_SEEN)

    def _get_tile_env_idx(self, idx: int) -> Environment | None:
        return self._env[idx]

    def _get_entity_type_idx(self, idx: int) -> EntityType | None:
        return self._entity_type[idx]

    def _get_entity_team_idx(self, idx: int) -> Team | None:
        return self._entity_team[idx]

    def _is_ore_idx(self, idx: int) -> bool:
        flags = self._tile_flags[idx]
        return bool(flags & (FLAG_ORE_TITANIUM | FLAG_ORE_AXIONITE))

    def _has_ore_harvester_idx(self, idx: int) -> bool:
        if self._entity_type[idx] != EntityType.HARVESTER:
            return False
        env = self._env[idx]
        return env == Environment.ORE_TITANIUM or env == Environment.ORE_AXIONITE

    def _has_adjacent_ally_conveyor_idx(self, idx: int, my_team: Team) -> bool:
        x = idx % self.width
        y = idx // self.width
        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if not on_map_coords(nx, ny, self.width, self.height):
                continue
            nidx = ny * self.width + nx
            if self._entity_type[nidx] in CONVEYOR_TYPES and self._entity_team[nidx] == my_team:
                return True
        return False

    def has_entity(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return self._entity_id[idx] != 0

    def get_tile_entity_type(self, pos: Position) -> EntityType | None:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return None
        return self._entity_type[idx]

    def is_blocked_idx(self, idx: int) -> bool:
        return self._get_flag_idx(idx, FLAG_BLOCKED)

    def is_ally_barrier_idx(self, idx: int) -> bool:
        return self._get_flag_idx(idx, FLAG_ALLY_BARRIER)

    def is_ally_launcher_idx(self, idx: int) -> bool:
        return self._get_flag_idx(idx, FLAG_ALLY_LAUNCHER)

    def get_enemy_launcher_adj_count_idx(self, idx: int) -> int:
        return self._enemy_launcher_adj[idx]

    def get_enemy_launcher_adj_count(self, pos: Position) -> int:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return 0
        return self._enemy_launcher_adj[idx]

    def _mark_enemy_launcher_adj(self, pos: Position):
        px = pos.x
        py = pos.y
        for d in DIRECTIONS:
            dx, dy = d.delta()
            x = px + dx
            y = py + dy
            if not on_map_coords(x, y, self.width, self.height):
                continue
            idx = y * self.width + x
            if self._enemy_launcher_adj[idx] < 255:
                self._enemy_launcher_adj[idx] += 1

    def get_conveyor_output(self, pos: Position) -> Position | None:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return None
        out_idx = self._output_idx[idx]
        if out_idx < 0:
            return None
        return self._pos(out_idx)

    def has_conveyor_output(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return self._output_idx[idx] >= 0

    def iter_conveyor_input_indices(self, pos: Position):
        return iter(self._input_indices[self._idx(pos)])

    def has_conveyor_inputs(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return bool(self._input_indices[idx])

    def get_conveyor_input_count(self, pos: Position) -> int:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return 0
        return len(self._input_indices[idx])

    def get_conveyor_input_positions(self, pos: Position) -> list[Position]:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return []
        return [self._pos(input_idx) for input_idx in self._input_indices[idx]]

    def get_symmetric_idx(self, idx: int, symmetry: Symmetry) -> int:
        x = idx % self.width
        y = idx // self.width
        if symmetry == Symmetry.FLIP_X:
            return y * self.width + (self.width - 1 - x)
        elif symmetry == Symmetry.FLIP_Y:
            return (self.height - 1 - y) * self.width + x
        elif symmetry == Symmetry.ROTATE:
            return (self.height - 1 - y) * self.width + (self.width - 1 - x)
        return idx

    def get_symmetric_pos(self, pos: Position, symmetry: Symmetry):
        return self._pos(self.get_symmetric_idx(self._idx(pos), symmetry))

    def _set_symmetry(self, symmetry: Symmetry):
        if symmetry == Symmetry.UNKNOWN or self.symmetry == symmetry:
            return
        if self.symmetry == Symmetry.UNKNOWN:
            self.should_update_all_symmetric = True
            self.symmetric_update_x = 0
            self.symmetric_update_y = 0
        self.symmetry = symmetry

    def _apply_env_idx(self, idx: int, env: Environment):
        pos = self._pos(idx)
        self._set_tile_env_idx(idx, env)
        self.ore_ti.discard(pos)
        self.ore_ax.discard(pos)
        if env == Environment.ORE_TITANIUM:
            self.ore_ti.add(pos)
        elif env == Environment.ORE_AXIONITE:
            self.ore_ax.add(pos)
        
    def check_symmetry(self, pos: Position, env: Environment):
        idx = self._idx(pos)
        if self.can_flip_x:
            sym_env = self._env[self.get_symmetric_idx(idx, Symmetry.FLIP_X)]
            if sym_env is not None and sym_env != env:
                self.can_flip_x = False
        if self.can_flip_y:
            sym_env = self._env[self.get_symmetric_idx(idx, Symmetry.FLIP_Y)]
            if sym_env is not None and sym_env != env:
                self.can_flip_y = False
        if self.can_rotate:
            sym_env = self._env[self.get_symmetric_idx(idx, Symmetry.ROTATE)]
            if sym_env is not None and sym_env != env:
                self.can_rotate = False
                
    def check_core_symmetry(self, pos: Position):
        if self.symmetry != Symmetry.UNKNOWN:
            return
        idx = self._idx(pos)
        if self.can_flip_x:
            sym_etype = self._entity_type[self.get_symmetric_idx(idx, Symmetry.FLIP_X)]
            if sym_etype is not None and sym_etype != EntityType.CORE:
                self.can_flip_x = False
        if self.can_flip_y:
            sym_etype = self._entity_type[self.get_symmetric_idx(idx, Symmetry.FLIP_Y)]
            if sym_etype is not None and sym_etype != EntityType.CORE:
                self.can_flip_y = False
        if self.can_rotate:
            sym_etype = self._entity_type[self.get_symmetric_idx(idx, Symmetry.ROTATE)]
            if sym_etype is not None and sym_etype != EntityType.CORE:
                self.can_rotate = False

    def update_symmetry(self):
        if self.symmetry != Symmetry.UNKNOWN:
            return
        key = (self.can_flip_x, self.can_flip_y, self.can_rotate)
        if key in SYMMETRY_MAPPING:
            self._set_symmetry(SYMMETRY_MAPPING[key])

    END_TURN_RESERVE_US = 50
    TURN_CPU_BUDGET_US = 2000

    def update_all_symmetric_tiles(self, ct: Controller):
        if not self.should_update_all_symmetric or self.symmetry == Symmetry.UNKNOWN:
            return

        width = self.width
        height = self.height
        
        log_time(ct, "Start of symmetric update")

        while self.symmetric_update_y < height:
            idx = self.symmetric_update_y * width + self.symmetric_update_x
            if idx % 50 == 0:
                budget = self.TURN_CPU_BUDGET_US - ct.get_cpu_time_elapsed() - self.END_TURN_RESERVE_US
                if budget <= 0:
                    return

            if self._env[idx] is None:
                sym_idx = self.get_symmetric_idx(idx, self.symmetry)
                sym_env = self._env[sym_idx]
                if sym_env is not None:
                    self._apply_env_idx(idx, sym_env)

            self.symmetric_update_x += 1
            if self.symmetric_update_x == width:
                self.symmetric_update_x = 0
                self.symmetric_update_y += 1

        self.symmetric_update_x = 0
        self.symmetric_update_y = 0
        self.should_update_all_symmetric = False
        
        log_time(ct, "End of symmetric update")

    def _resource_to_mask(self, resource: ResourceType | None) -> int:
        if resource == ResourceType.TITANIUM:
            return RESOURCE_MASK_TITANIUM
        if resource == ResourceType.RAW_AXIONITE:
            return RESOURCE_MASK_AXIONITE
        return 0


    def _get_cached_resource_mask(self, pos: Position) -> int:
        return self._input_resource_masks[self._idx(pos)]

    def _set_cached_resource_mask(self, pos: Position, mask: int):
        self._input_resource_masks[self._idx(pos)] = mask

    def _get_cached_chain_valid(self, pos: Position) -> bool:
        return bool(self._input_chain_valid[self._idx(pos)])

    def _set_cached_chain_valid(self, pos: Position, valid: bool):
        self._input_chain_valid[self._idx(pos)] = 1 if valid else 0

    def input_chain_reaches_resource(self, pos: Position, resource: ResourceType) -> bool:
        return bool(self._get_cached_resource_mask(pos) & self._resource_to_mask(resource))

    def _record_conveyor_resource(self, pos: Position, resource: ResourceType):
        self.conveyor_resources[pos].add(resource)
        self.conveyor_resources_last_seen[pos][resource] = self.current_round

    def get_recent_conveyor_resources(self, pos: Position, max_age: int = OBSERVED_RESOURCE_MAX_AGE) -> set[ResourceType]:
        recent = set()
        last_seen = self.conveyor_resources_last_seen.get(pos)
        if last_seen is None:
            return recent

        stale_resources = []
        for resource, seen_round in last_seen.items():
            if self.current_round - seen_round <= max_age:
                recent.add(resource)
            else:
                stale_resources.append(resource)

        if stale_resources:
            tracked = self.conveyor_resources.get(pos)
            for resource in stale_resources:
                del last_seen[resource]
                if tracked is not None:
                    tracked.discard(resource)
            if tracked is not None and not tracked:
                self.conveyor_resources.pop(pos, None)
            if not last_seen:
                self.conveyor_resources_last_seen.pop(pos, None)

        return recent

    def has_recent_conveyor_resource(self, pos: Position, resource: ResourceType, max_age: int = OBSERVED_RESOURCE_MAX_AGE) -> bool:
        last_seen = self.conveyor_resources_last_seen.get(pos)
        if last_seen is None:
            return False
        seen_round = last_seen.get(resource)
        return seen_round is not None and self.current_round - seen_round <= max_age

    def get_cached_conveyor_resources(self, pos: Position) -> set[ResourceType]:
        """Return cached resource evidence for a conveyor chain position."""
        resources = set(self.get_recent_conveyor_resources(pos))
        mask = self._get_cached_resource_mask(pos)
        if mask & RESOURCE_MASK_TITANIUM:
            resources.add(ResourceType.TITANIUM)
        if mask & RESOURCE_MASK_AXIONITE:
            resources.add(ResourceType.RAW_AXIONITE)
        return resources

    def get_conveyor_resource_evidence(self, pos: Position, ct: Controller) -> set[ResourceType]:
        """Return resource evidence for a conveyor, preferring live stored resource."""
        if ct.is_in_vision(pos):
            bid = ct.get_tile_building_id(pos)
            if bid is not None and ct.get_entity_type(bid) in CONVEYOR_TYPES:
                stored = ct.get_stored_resource(bid)
                if stored is not None:
                    return {stored}
        return self.get_cached_conveyor_resources(pos)

    def _get_conveyor_resource_state(self, pos: Position, ct: Controller, resource: ResourceType) -> int:
        """Return 0=no evidence, 1=only matching evidence, 2=conflicting or ambiguous evidence."""
        if ct.is_in_vision(pos):
            bid = ct.get_tile_building_id(pos)
            if bid is not None and ct.get_entity_type(bid) in CONVEYOR_TYPES:
                stored = ct.get_stored_resource(bid)
                if stored is not None:
                    return 1 if stored == resource else 2

        recent = self.get_recent_conveyor_resources(pos)
        mask = self._get_cached_resource_mask(pos)
        has_match = resource in recent or bool(mask & self._resource_to_mask(resource))

        if resource == ResourceType.TITANIUM:
            has_other = (
                ResourceType.RAW_AXIONITE in recent
                or ResourceType.REFINED_AXIONITE in recent
                or bool(mask & RESOURCE_MASK_AXIONITE)
            )
        elif resource == ResourceType.RAW_AXIONITE:
            has_other = (
                ResourceType.TITANIUM in recent
                or ResourceType.REFINED_AXIONITE in recent
                or bool(mask & RESOURCE_MASK_TITANIUM)
            )
        else:
            has_other = any(r != resource for r in recent)

        if has_other:
            return 2
        if has_match:
            return 1
        return 0

    def infer_chain_resource_at_output(self, output_pos: Position, ct: Controller) -> ResourceType | None:
        """Infer the resource for a broken chain gap, preferring live input storage."""
        live_resources = set()
        for input_idx in self.iter_conveyor_input_indices(output_pos):
            input_pos = self._pos(input_idx)
            if not ct.is_in_vision(input_pos):
                continue
            bid = ct.get_tile_building_id(input_pos)
            if bid is None or ct.get_entity_type(bid) not in CONVEYOR_TYPES:
                continue
            stored = ct.get_stored_resource(bid)
            if stored is not None:
                live_resources.add(stored)
        if len(live_resources) == 1:
            return next(iter(live_resources))
        if len(live_resources) > 1:
            return None

        cached_resources = set(self.get_cached_conveyor_resources(output_pos))
        for input_idx in self.iter_conveyor_input_indices(output_pos):
            input_pos = self._pos(input_idx)
            cached_resources.update(self.get_cached_conveyor_resources(input_pos))
        if len(cached_resources) == 1:
            return next(iter(cached_resources))
        return None

    def is_unserviced_harvester(self, pos: Position, my_team: Team) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return self._has_ore_harvester_idx(idx) and not self._has_adjacent_ally_conveyor_idx(idx, my_team)

    def _collect_downstream_indices(self, dirty_roots: set[int]) -> list[int]:
        positions = []
        seen = set()
        stack = list(dirty_roots)
        while stack:
            idx = stack.pop()
            if idx in seen:
                continue
            seen.add(idx)
            positions.append(idx)
            next_idx = self._output_idx[idx]
            if next_idx >= 0:
                stack.append(next_idx)
        return positions

    def _compute_cached_resource_mask_idx(self, idx: int) -> int:
        mask = 0
        width = self.width
        height = self.height
        x = idx % width
        y = idx // width
        env_grid = self._env
        entity_types = self._entity_type
        input_chain_valid = self._input_chain_valid
        input_resource_masks = self._input_resource_masks

        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if not on_map_coords(nx, ny, width, height):
                continue
            nidx = ny * width + nx
            if entity_types[nidx] != EntityType.HARVESTER:
                continue
            env = env_grid[nidx]
            if env == Environment.ORE_TITANIUM:
                mask |= RESOURCE_MASK_TITANIUM
            elif env == Environment.ORE_AXIONITE:
                mask |= RESOURCE_MASK_AXIONITE

        for input_idx in self._input_indices[idx]:
            if not self._get_flag_idx(input_idx, FLAG_SEEN):
                continue
            if entity_types[input_idx] not in CONVEYOR_TYPES:
                continue
            if not input_chain_valid[input_idx]:
                continue

            mask |= input_resource_masks[input_idx]
            if mask == (RESOURCE_MASK_TITANIUM | RESOURCE_MASK_AXIONITE):
                break

        return mask

    def _compute_cached_chain_valid_idx(self, idx: int) -> bool:
        has_valid_feeder = False
        width = self.width
        height = self.height
        x = idx % width
        y = idx // width
        env_grid = self._env
        entity_types = self._entity_type
        input_chain_valid = self._input_chain_valid

        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if not on_map_coords(nx, ny, width, height):
                continue
            nidx = ny * width + nx
            if entity_types[nidx] != EntityType.HARVESTER:
                continue
            env = env_grid[nidx]
            if env == Environment.ORE_TITANIUM or env == Environment.ORE_AXIONITE:
                has_valid_feeder = True

        for input_idx in self._input_indices[idx]:
            if not self._get_flag_idx(input_idx, FLAG_SEEN):
                # Unvisited input — optimistically assume valid
                has_valid_feeder = True
                continue
            if entity_types[input_idx] not in CONVEYOR_TYPES:
                continue  # broken input, but other inputs may still be valid
            if not input_chain_valid[input_idx]:
                continue  # invalid upstream, but other inputs may still be valid
            has_valid_feeder = True

        return has_valid_feeder

    def _recompute_input_chain_cache(self, dirty_roots: set[int]):
        positions = self._collect_downstream_indices(dirty_roots)
        if not positions:
            return

        pos_set = set(positions)

        # Build in-degree map (only counting edges within pos_set)
        in_degree: dict[int, int] = {idx: 0 for idx in positions}
        for idx in positions:
            for input_idx in self._input_indices[idx]:
                if input_idx in pos_set:
                    in_degree[idx] += 1

        # Topological sort via Kahn's algorithm
        queue = [idx for idx in positions if in_degree[idx] == 0]
        topo_order: list[int] = []
        qi = 0
        while qi < len(queue):
            idx = queue[qi]
            qi += 1
            topo_order.append(idx)
            output_idx = self._output_idx[idx]
            if output_idx >= 0 and output_idx in pos_set:
                in_degree[output_idx] -= 1
                if in_degree[output_idx] == 0:
                    queue.append(output_idx)

        # Positions not in topo_order are in cycles — mark invalid
        for idx in positions:
            if idx not in in_degree or in_degree[idx] != 0:
                pos = self._pos(idx)
                self._set_cached_chain_valid(pos, False)
                self._set_cached_resource_mask(pos, 0)

        # Single pass in topological order (upstream first)
        for idx in topo_order:
            pos = self._pos(idx)
            new_valid = self._compute_cached_chain_valid_idx(idx)
            new_mask = self._compute_cached_resource_mask_idx(idx) if new_valid else 0
            self._set_cached_chain_valid(pos, new_valid)
            self._set_cached_resource_mask(pos, new_mask)

    def _remove_conveyor_tracking(self, pos: Position):
        """Remove conveyor output/input/resource tracking for a position."""
        idx = self._idx(pos)
        old_output_idx = self._output_idx[idx]
        if old_output_idx >= 0:
            self._input_indices[old_output_idx].discard(idx)
        self._output_idx[idx] = -1
        self.conveyor_resources.pop(pos, None)
        self.conveyor_resources_last_seen.pop(pos, None)

    def update_vision(self, ct: Controller, comms: Comms):
        log_time(ct, "Start of update vision")
        self.current_round = ct.get_current_round()
        self._enemy_launcher_adj = bytearray(self.tile_count)
        self._feeds_turret_cache.clear()
        self._feeds_building_cache.clear()
        self._feeds_building_in_vision_cache.clear()
        self._sabotage_downstream_cache.clear()
        self._chain_terminal_cache.clear()
        self._chain_last_visible_cache.clear()
        my_team = ct.get_team()
        nearby = ct.get_nearby_tiles()
        dirty_cache_positions: set[int] = set()

        env_grid = self._env
        entity_ids = self._entity_id
        entity_types = self._entity_type
        entity_teams = self._entity_team
        output_idx = self._output_idx
        input_indices = self._input_indices
        ore_ti = self.ore_ti
        ore_ax = self.ore_ax
        tile_flags = self._tile_flags
        width = self.width
        height = self.height

        ct_get_tile_env = ct.get_tile_env
        ct_get_tile_building_id = ct.get_tile_building_id
        ct_get_entity_type = ct.get_entity_type
        ct_get_team = ct.get_team
        ct_get_marker_value = ct.get_marker_value
        ct_get_bridge_target = ct.get_bridge_target
        ct_get_direction = ct.get_direction
        ct_get_stored_resource = ct.get_stored_resource
        should_fill_symmetry = self.symmetry != Symmetry.UNKNOWN
        known_symmetry = self.symmetry
        
        log_time(ct, "After local variable assignment")

        for pos in nearby:
            x = pos.x
            y = pos.y
            idx = y * width + x
            prev_output_idx = output_idx[idx]
            prev_was_ore_harvester = False
            if tile_flags[idx] & FLAG_SEEN and entity_types[idx] == EntityType.HARVESTER:
                prev_env = env_grid[idx]
                prev_was_ore_harvester = prev_env == Environment.ORE_TITANIUM or prev_env == Environment.ORE_AXIONITE

            env = env_grid[idx]
            if env is None:
                env = ct_get_tile_env(pos)
                self._apply_env_idx(idx, env)
                if should_fill_symmetry:
                    sym_idx = self.get_symmetric_idx(idx, known_symmetry)
                    if env_grid[sym_idx] is None:
                        self._apply_env_idx(sym_idx, env)
                else:
                    self.check_symmetry(pos, env)
                
            if env == Environment.WALL:
                self._clear_tile_entity_idx(idx)
                tile_flags[idx] = (tile_flags[idx] & _CLEAR_ENTITY_FLAGS) | FLAG_BLOCKED
                continue

            bid = ct_get_tile_building_id(pos)
            if bid is not None:
                cached_bid = entity_ids[idx]
                if cached_bid != 0 and cached_bid == bid:
                    etype = entity_types[idx]
                    team = entity_teams[idx]
                else:
                    etype = ct_get_entity_type(bid)
                    team = ct_get_team(bid)
                if etype == EntityType.MARKER:
                    if team == my_team:
                        comms.read_marker(ct_get_marker_value(bid), pos, bid, self.current_round)
                    self._clear_tile_entity_idx(idx)
                    tile_flags[idx] &= _CLEAR_ENTITY_FLAGS
                    if output_idx[idx] >= 0:
                        self._remove_conveyor_tracking(pos)
                else:
                    if etype == EntityType.CORE:
                        if cached_bid != bid:
                            self.check_core_symmetry(pos)
                    assert etype is not None
                    assert team is not None
                    self._set_tile_entity_idx(idx, bid, etype, team)
                    tile_flags[idx] &= _CLEAR_ENTITY_FLAGS
                    if self._is_ore_idx(idx):
                        if etype == EntityType.BARRIER and team != my_team:
                            self.unreachable_ores.add(pos)
                        else:
                            self.unreachable_ores.discard(pos)
                    if etype == EntityType.LAUNCHER and team != my_team:
                        self._mark_enemy_launcher_adj(pos)
                    if etype == EntityType.BARRIER and team == my_team:
                        tile_flags[idx] |= FLAG_ALLY_BARRIER
                    elif etype == EntityType.LAUNCHER and team == my_team:
                        tile_flags[idx] |= FLAG_ALLY_LAUNCHER
                    elif (
                        (etype == EntityType.CORE and team != my_team)
                        or (etype not in CONVEYOR_TYPES and etype != EntityType.ROAD and etype != EntityType.CORE and not (etype == EntityType.BARRIER and team == my_team) and not (etype == EntityType.LAUNCHER and team == my_team))
                    ):
                        tile_flags[idx] |= FLAG_BLOCKED

                    # Track conveyor outputs and resources
                    if etype in CONVEYOR_TYPES:
                        if cached_bid != 0 and cached_bid == bid:
                            new_output_idx = prev_output_idx
                        else:
                            new_output = ct_get_bridge_target(bid) if etype == EntityType.BRIDGE else pos.add(ct_get_direction(bid))
                            if 0 <= new_output.x < width and 0 <= new_output.y < height:
                                new_output_idx = new_output.y * width + new_output.x
                            else:
                                new_output_idx = -1
                        old_output_idx = output_idx[idx]
                        if old_output_idx != new_output_idx:
                            if old_output_idx >= 0:
                                input_indices[old_output_idx].discard(idx)
                            output_idx[idx] = new_output_idx
                            if new_output_idx >= 0:
                                input_indices[new_output_idx].add(idx)
                        resource = ct_get_stored_resource(bid)
                        if resource is not None:
                            self._record_conveyor_resource(pos, resource)
                    elif output_idx[idx] >= 0:
                        self._remove_conveyor_tracking(pos)
            else:
                self._clear_tile_entity_idx(idx)
                tile_flags[idx] &= _CLEAR_ENTITY_FLAGS
                if self._is_ore_idx(idx):
                    self.unreachable_ores.discard(pos)
                if output_idx[idx] >= 0:
                    self._remove_conveyor_tracking(pos)

            new_output_idx = output_idx[idx]
            if prev_output_idx != new_output_idx:
                if prev_output_idx >= 0:
                    dirty_cache_positions.add(prev_output_idx)
                if new_output_idx >= 0:
                    dirty_cache_positions.add(new_output_idx)

            new_is_ore_harvester = (
                entity_types[idx] == EntityType.HARVESTER
                and (env_grid[idx] == Environment.ORE_TITANIUM or env_grid[idx] == Environment.ORE_AXIONITE)
            )
            if prev_was_ore_harvester != new_is_ore_harvester:
                for dx, dy in _CARDINAL_OFFSETS:
                    nx = x + dx
                    ny = y + dy
                    if not on_map_coords(nx, ny, width, height):
                        continue
                    dirty_cache_positions.add(ny * width + nx)

        log_time(ct, "After processing nearby tiles")
        
        if comms.symmetry is not None and self.symmetry == Symmetry.UNKNOWN:
            self._set_symmetry(comms.symmetry)
            log(f"symmetry from marker: {self.symmetry.name}")

        self.update_symmetry()
        
        log_time(ct, "After updating symmetry")
        
        self._recompute_input_chain_cache(dirty_cache_positions)
        
        log_time(ct, "After recomputing conveyor cache")
    
    def follow_chain_terminal(self, start_pos: Position) -> Position:
        """Follow conveyor_outputs from start_pos to the end of the chain.
        Returns the final position (the first tile that has no conveyor output)."""
        cur = start_pos
        path: list[Position] = []
        visited: dict[Position, int] = {}
        cache_result = True
        while True:
            cached = self._chain_terminal_cache.get(cur)
            if cached is not None:
                result = cached
                break
            loop_start = visited.get(cur)
            if loop_start is not None:
                result = start_pos  # cycle
                cache_result = False
                break
            path.append(cur)
            visited[cur] = len(path) - 1
            next_pos = self.get_conveyor_output(cur)
            if next_pos is None:
                result = cur
                break
            cur = next_pos

        if cache_result:
            for pos in path:
                self._chain_terminal_cache[pos] = result
        return result

    def follow_chain_last_visible(self, start_pos: Position) -> Position | None:
        """Follow conveyor_outputs while the walked chain remains visited.
        Returns the last visible position on the chain, or None if start_pos is unvisited."""
        if not self.is_visited(start_pos):
            self._chain_last_visible_cache[start_pos] = None
            return None

        cur = start_pos
        path: list[Position] = []
        visited: dict[Position, int] = {}
        cache_result = True
        while True:
            cached = self._chain_last_visible_cache.get(cur, ...)
            if cached is not ...:
                result = cached
                break
            loop_start = visited.get(cur)
            if loop_start is not None:
                result = start_pos  # cycle
                cache_result = False
                break
            path.append(cur)
            visited[cur] = len(path) - 1
            next_pos = self.get_conveyor_output(cur)
            if next_pos is None:
                result = cur
                break
            if not self.is_visited(next_pos):
                result = cur
                break
            cur = next_pos

        if cache_result:
            for pos in path:
                self._chain_last_visible_cache[pos] = result
        return result

    def _would_create_loop_idx(self, build_idx: int, output_idx: int) -> bool:
        cur_idx = self._output_idx[output_idx]
        if cur_idx < 0:
            return False

        seen = {build_idx}
        while cur_idx >= 0:
            if cur_idx in seen:
                return cur_idx == build_idx
            seen.add(cur_idx)
            cur_idx = self._output_idx[cur_idx]
        return False

    def get_feeders(self, output_pos: Position) -> list[tuple[Position, EntityType]]:
        """Return known entities that directly feed output_pos.

        Includes adjacent harvesters on ore and conveyor-type inputs from
        conveyor_inputs. Bridges are included naturally via conveyor_inputs even
        when they feed from farther than cardinal adjacency."""
        feeders = []

        for d in CARDINAL_DIRECTIONS:
            adj = output_pos.add(d)
            if not on_map(adj, self.width, self.height):
                continue
            entity = self.get_tile_entity(adj)
            if entity is None or entity[1] != EntityType.HARVESTER:
                continue
            env = self.get_tile_env(adj)
            if env == Environment.ORE_TITANIUM or env == Environment.ORE_AXIONITE:
                feeders.append((adj, EntityType.HARVESTER))

        for input_idx in self.iter_conveyor_input_indices(output_pos):
            input_pos = self._pos(input_idx)
            if self.is_visited(input_pos):
                entity = self.get_tile_entity(input_pos)
                if entity is None or entity[1] not in CONVEYOR_TYPES:
                    continue
                etype = entity[1]
            else:
                etype = EntityType.CONVEYOR
            feeders.append((input_pos, etype))

        return feeders

    def has_adjacent_harvester(self, pos: Position) -> bool:
        """True if a cardinally adjacent harvester on ore feeds pos."""
        for d in CARDINAL_DIRECTIONS:
            adj = pos.add(d)
            if not on_map(adj, self.width, self.height):
                continue
            entity = self.get_tile_entity(adj)
            if entity is not None and entity[1] == EntityType.HARVESTER:
                return True
        return False

    def has_valid_input_chain(self, pos: Position) -> bool:
        """Return True if all known upstream branches remain valid."""
        return self._get_cached_chain_valid(pos)

    def feeds_ally_building(self, pos: Position, my_team: Team) -> bool:
        """Follow conveyor_outputs from pos. Returns True if the chain
        eventually reaches an ally building (any type)."""
        return self._feeds_ally_chain(
            pos,
            my_team,
            self._feeds_building_cache,
            lambda etype, team, cur: team == my_team,
        )

    def _feeds_ally_chain(
        self,
        pos: Position,
        my_team: Team,
        cache: dict[tuple[Position, Team], bool],
        success_predicate,
        ct: Controller | None = None,
        core_pos: Position | None = None,
        require_visible: bool = False,
    ) -> bool:
        key = (pos, my_team)
        cached = cache.get(key)
        if cached is not None:
            return cached

        cur = pos
        visited = set()
        while self.has_conveyor_output(cur):
            if cur in visited:
                cache[key] = False
                return False
            visited.add(cur)

            next_pos = self.get_conveyor_output(cur)
            if next_pos is None:
                cache[key] = False
                return False

            cur = next_pos
            if require_visible:
                assert ct is not None
                if not ct.is_in_vision(cur):
                    cache[key] = False
                    return False
                if is_core_tile(core_pos, cur):
                    cache[key] = True
                    return True

            if not self.is_visited(cur):
                cache[key] = False
                return False

            entity = self.get_tile_entity(cur)
            if entity is None:
                cache[key] = False
                return False

            _, etype, team = entity
            if success_predicate(etype, team, cur):
                cache[key] = True
                return True
            if etype not in CONVEYOR_TYPES:
                cache[key] = False
                return False

        cache[key] = False
        return False

    def feeds_ally_building_in_vision(self, pos: Position, my_team: Team, ct: Controller, core_pos: Position | None = None) -> bool:
        """Follow conveyor_outputs while outputs stay in current vision.
        Returns True iff the visible chain clearly reaches an allied terminal."""
        return self._feeds_ally_chain(
            pos,
            my_team,
            self._feeds_building_in_vision_cache,
            lambda etype, team, cur: team == my_team,
            ct=ct,
            core_pos=core_pos,
            require_visible=True,
        )

    def feeds_ally_turret(self, pos: Position, my_team: Team) -> bool:
        """Follow conveyor_outputs from pos. Returns True if the chain
        eventually reaches an ally turret (SENTINEL, GUNNER, BREACH)."""
        return self._feeds_ally_chain(
            pos,
            my_team,
            self._feeds_turret_cache,
            lambda etype, team, cur: team == my_team and etype in TURRET_TYPES,
        )

    def get_sabotage_downstream_priority(self, pos: Position, my_team: Team) -> int:
        """Classify how valuable it is to sabotage a downstream enemy chain.
        Returns 3 for enemy core, 2 for enemy turret, 1 for a generic enemy
        chain or enemy foundry, and 0 only when the known downstream path
        clearly becomes invalid for sabotage."""
        key = (pos, my_team)
        cached = self._sabotage_downstream_cache.get(key)
        if cached is not None:
            return cached

        cur = pos
        path: list[Position] = []
        visited: set[Position] = set()
        cache_result = True
        result = 1

        while self.has_conveyor_output(cur):
            if cur in visited:
                break
            visited.add(cur)
            path.append(cur)

            next_pos = self.get_conveyor_output(cur)
            if next_pos is None:
                break
            if not self.is_visited(next_pos):
                break

            entity = self.get_tile_entity(next_pos)
            if entity is None:
                result = 0
                break

            _, etype, team = entity
            if team == my_team:
                result = 0
                break
            if etype == EntityType.CORE:
                result = 3
                break
            if etype in TURRET_TYPES:
                result = 2
                break
            if etype == EntityType.FOUNDRY:
                result = 1
                break
            if etype in CONVEYOR_TYPES:
                cur = next_pos
                continue

            result = 0
            break

        if cache_result:
            for path_pos in path:
                self._sabotage_downstream_cache[(path_pos, my_team)] = result
        return result

    def get_nearest_unserviced_harvester(self, pos: Position, ct: Controller) -> Position | None:
        my_team = ct.get_team()
        best_ti = None
        best_ti_dist = INF
        best_ax = None
        best_ax_dist = INF

        for hpos in self.ore_ti:
            if hpos in self.unreachable_harvesters:
                continue
            dist = pos.distance_squared(hpos)
            if dist >= best_ti_dist:
                continue
            if not self.is_unserviced_harvester(hpos, my_team):
                continue
            best_ti_dist = dist
            best_ti = hpos
            
        if best_ti is not None:
            return best_ti

        for hpos in self.ore_ax:
            if hpos in self.unreachable_harvesters:
                continue
            dist = pos.distance_squared(hpos)
            if dist >= best_ax_dist:
                continue
            if not self.is_unserviced_harvester(hpos, my_team):
                continue
            best_ax_dist = dist
            best_ax = hpos
        if ct.get_global_resources()[0] >= 1500 and best_ax is not None:
            return best_ax
        return None

    def get_tile_env(self, pos: Position) -> Environment | None:
        return self._env[self._idx(pos)]
    
    def get_tile_entity(self, pos: Position) -> tuple[int, EntityType, Team] | None:
        idx = self._idx(pos)
        bid = self._entity_id[idx]
        if bid == 0:
            return None
        etype = self._entity_type[idx]
        team = self._entity_team[idx]
        assert etype is not None
        assert team is not None
        return (bid, etype, team)
    
    def is_visited(self, pos: Position) -> bool:
        return self._get_flag_idx(self._idx(pos), FLAG_SEEN)
        
    def get_random_tile(self) -> Position:
        return Position(random.randint(0, self.width - 1), random.randint(0, self.height - 1))
    
    def get_nearest_ore_without_harvester(self, pos: Position, ct: Controller) -> Position | None:
        best_ti = None
        best_ti_dist = INF
        best_ax = None
        best_ax_dist = INF

        for ore_pos in self.ore_ti:
            if ore_pos in self.unreachable_ores or ore_pos in self.unreachable_harvesters:
                continue
            entity = self.get_tile_entity(ore_pos)
            if entity is not None and entity[1] == EntityType.HARVESTER:
                continue
            if self.has_adjacent_opposite_resource_chain(ore_pos, ResourceType.TITANIUM, ct):
                self.unreachable_ores.add(ore_pos)
                continue
            dist = pos.distance_squared(ore_pos)
            if dist < best_ti_dist:
                best_ti_dist = dist
                best_ti = ore_pos
                
        if best_ti is not None:
            return best_ti

        for ore_pos in self.ore_ax:
            if ore_pos in self.unreachable_ores or ore_pos in self.unreachable_harvesters:
                continue
            entity = self.get_tile_entity(ore_pos)
            if entity is not None and entity[1] == EntityType.HARVESTER:
                continue
            if self.has_adjacent_opposite_resource_chain(ore_pos, ResourceType.RAW_AXIONITE, ct):
                self.unreachable_ores.add(ore_pos)
                continue
            dist = pos.distance_squared(ore_pos)
            if dist < best_ax_dist:
                best_ax_dist = dist
                best_ax = ore_pos

        if best_ax is not None and ct.get_global_resources()[0] >= 1500:
            return best_ax        
        return None

    def get_nearest_titanium_ore(self, pos: Position) -> Position | None:
        """Return the nearest known titanium ore position, or None."""
        best = None
        best_dist = INF
        for ti_pos in self.ore_ti:
            dist = pos.distance_squared(ti_pos)
            if dist < best_dist:
                best_dist = dist
                best = ti_pos
        return best

    def tag_conveyor_resource(self, pos: Position, resource: ResourceType):
        """Tag a conveyor position with an expected resource type."""
        self._record_conveyor_resource(pos, resource)

    def find_nearest_conveyor_with_resource(self, pos: Position, resource: ResourceType, my_team: Team | None = None, target_foundry: Position | None = None) -> Position | None:
        """Find the nearest conveyor that has been observed/tagged with the given resource."""
        best = None
        best_dist = INF
        for conv_pos in tuple(self.conveyor_resources):
            if not self.has_recent_conveyor_resource(conv_pos, resource):
                continue
            if my_team is not None and self.feeds_other_ally_foundry(conv_pos, my_team, target_foundry):
                continue
            dist = pos.distance_squared(conv_pos)
            if dist < best_dist:
                best_dist = dist
                best = conv_pos
        return best

    def feeds_other_ally_foundry(self, pos: Position, my_team: Team, target_foundry: Position | None) -> bool:
        """True if the chain from pos terminates at a different allied foundry."""
        terminal = self.follow_chain_terminal(pos)
        if not on_map(terminal, self.width, self.height) or not self.is_visited(terminal):
            return False
        entity = self.get_tile_entity(terminal)
        return (
            entity is not None
            and entity[1] == EntityType.FOUNDRY
            and entity[2] == my_team
            and terminal != target_foundry
        )

    def is_single_input_foundry(self, pos: Position, my_team) -> bool:
        """True if pos has an ally foundry with at most 1 input and no titanium input."""
        entity = self.get_tile_entity(pos)
        if entity is None or entity[1] != EntityType.FOUNDRY or entity[2] != my_team:
            return False
        input_positions = self.get_conveyor_input_positions(pos)
        if len(input_positions) > 1:
            return False
        return not any(
            self.has_recent_conveyor_resource(conv_pos, ResourceType.TITANIUM)
            for conv_pos in input_positions
        )

    def find_single_input_foundry(self, core_pos: Position | None, my_team) -> Position | None:
        """Find an ally foundry near core with at most 1 conveyor/bridge input."""
        if core_pos is None:
            return None
        for pos in get_foundry_positions(core_pos, self.width, self.height):
            if self.is_single_input_foundry(pos, my_team):
                return pos
        return None

    def is_ore(self, pos: Position) -> bool:
        """True if pos contains any ore."""
        return self._is_ore_idx(self._idx(pos))

    def is_adjacent_to_opposite_ore(self, pos: Position, resource: ResourceType | None) -> bool:
        """True if pos is adjacent to a harvester or ore of the opposite resource type."""
        if resource is None:
            return False
        opposite_ore = self.ore_ti if resource == ResourceType.RAW_AXIONITE else self.ore_ax if resource == ResourceType.TITANIUM else None
        if opposite_ore is None:
            return False
        for d in CARDINAL_DIRECTIONS:
            adj = pos.add(d)
            if not on_map(adj, self.width, self.height):
                continue
            if adj in opposite_ore:
                return True
        return False

    def has_adjacent_opposite_resource_chain(self, ore_pos: Position, resource: ResourceType | None, ct: Controller) -> bool:
        """True if an ore tile or any cardinally adjacent conveyor/bridge has
        positive evidence of carrying the opposite resource."""
        if resource == ResourceType.TITANIUM:
            opposite = ResourceType.RAW_AXIONITE
        elif resource == ResourceType.RAW_AXIONITE:
            opposite = ResourceType.TITANIUM
        else:
            return False

        entity = self.get_tile_entity(ore_pos)
        if entity is not None and entity[1] in CONVEYOR_TYPES:
            if self._get_conveyor_resource_state(ore_pos, ct, opposite) == 1:
                return True

        for d in CARDINAL_DIRECTIONS:
            adj = ore_pos.add(d)
            if not on_map(adj, self.width, self.height) or not self.is_visited(adj):
                continue
            entity = self.get_tile_entity(adj)
            if entity is None or entity[1] not in CONVEYOR_TYPES:
                continue
            if self._get_conveyor_resource_state(adj, ct, opposite) == 1:
                return True
        return False
    
    def has_conflict(self, resource: ResourceType | None, pos: Position, ct: Controller) -> bool:
        if resource is None:
            return False
        return self._get_conveyor_resource_state(pos, ct, resource) == 2

    def has_input_conflict(self, resource: ResourceType | None, pos: Position, ct: Controller) -> bool:
        """True if pos is the output of a known conveyor carrying the opposite resource.
        Use this for empty tiles that don't have a conveyor yet but are fed by one."""
        if resource is None:
            return False
        for input_idx in self.iter_conveyor_input_indices(pos):
            input_pos = self._pos(input_idx)
            if self._get_conveyor_resource_state(input_pos, ct, resource) == 2:
                return True
        return False

    @staticmethod
    def _make_dist_fns(end_positions, core_pos):
        if end_positions:
            terminal_positions = tuple((p.x, p.y) for p in end_positions)
            def _dist_pos(pos):
                px, py = pos
                best = INF
                for tx, ty in terminal_positions:
                    dx = px - tx
                    dy = py - ty
                    d = dx * dx + dy * dy
                    if d < best:
                        best = d
                return best
            def _dist_xy(x, y):
                best = INF
                for tx, ty in terminal_positions:
                    dx = x - tx
                    dy = y - ty
                    d = dx * dx + dy * dy
                    if d < best:
                        best = d
                return best
        else:
            cx, cy = core_pos
            def _dist_pos(pos):
                dx = pos.x - cx
                dy = pos.y - cy
                return dx * dx + dy * dy
            def _dist_xy(x, y):
                dx = x - cx
                dy = y - cy
                return dx * dx + dy * dy
        return _dist_pos, _dist_xy

    def _score_output_candidate(self, adj, adj_idx, dist, build_dist,
                                my_team, resource, ct, core_pos, end_positions,
                                dist_to_terminal, check_splitter_dir=None):
        """Evaluate a candidate output tile after terminal/visited/wall filtering.
        Returns effective_dist (int) or None to skip."""
        etype = self._get_entity_type_idx(adj_idx)
        if etype is not None:
            eteam = self._get_entity_team_idx(adj_idx)
            if etype in CONVEYOR_TYPES and my_team is not None and eteam == my_team:
                if check_splitter_dir is not None and etype == EntityType.SPLITTER:
                    splitter_output_idx = self._output_idx[adj_idx]
                    if splitter_output_idx >= 0:
                        splitter_dir = adj.direction_to(self._pos(splitter_output_idx))
                        if check_splitter_dir != splitter_dir:
                            log(f"    {adj}: SKIP ally splitter not feeding from back (faces {splitter_dir})")
                            return None
                if not self.has_conflict(resource, adj, ct):
                    ally_output_idx = self._output_idx[adj_idx]
                    ally_output = self._pos(ally_output_idx) if ally_output_idx >= 0 else None
                    if ally_output is not None and dist_to_terminal(ally_output) >= build_dist:
                        log(f"    {adj}: SKIP ally {etype} output not closer to terminal")
                        return None
                    terminal = self.follow_chain_terminal(adj)
                    if end_positions is not None and terminal not in end_positions:
                        is_core = core_pos is not None and abs(terminal.x - core_pos.x) <= 1 and abs(terminal.y - core_pos.y) <= 1
                        if is_core or (on_map(terminal, self.width, self.height) and self.has_entity(terminal)):
                            log(f"    {adj}: SKIP ally {etype} chain ends at {terminal} (wrong dest)")
                            return None
                    effective_pos = self.follow_chain_last_visible(adj)
                    if effective_pos is None:
                        log(f"    {adj}: SKIP ally {etype} chain leaves vision immediately")
                        return None
                    eff_dist = dist_to_terminal(effective_pos)
                    log(f"    {adj}: CHAIN ally {etype} eff_dist²={eff_dist}")
                    return eff_dist
                else:
                    log(f"    {adj}: SKIP ally {etype} wrong/no resource")
                    return None
            elif etype == EntityType.MARKER or (etype == EntityType.ROAD and my_team is not None and eteam == my_team):
                if self.has_input_conflict(resource, adj, ct):
                    log(f"    {adj}: SKIP road/marker has opposite-resource input")
                    return None
                log(f"    {adj}: ROAD dist²={dist}")
                return dist
            else:
                log(f"    {adj}: SKIP occupied by {eteam} {etype}")
                return None
        else:
            if self.has_input_conflict(resource, adj, ct):
                log(f"    {adj}: SKIP empty tile has opposite-resource input")
                return None
            log(f"    {adj}: EMPTY dist²={dist}")
            return dist

    def _get_best_output(self, build_pos: Position, core_pos: Position | None, ct: Controller,
                         offsets, my_team: Team | None = None, end_positions: set | None = None,
                         resource: ResourceType | None = None, check_splitter: bool = False,
                         allow_far_terminals: bool = False, label: str = "output") -> Position | None:
        """Unified helper for conveyor/bridge output selection.
        Returns the best output Position, or None."""
        if core_pos is None:
            return None

        dist_to_terminal, dist_to_terminal_xy = self._make_dist_fns(end_positions, core_pos)
        build_dist = dist_to_terminal_xy(build_pos.x, build_pos.y)
        best_terminal = None
        best_terminal_dist = INF
        best_next = None
        best_next_dist = INF
        build_idx = self._idx(build_pos)
        end_idx_set = {self._idx(p) for p in end_positions} if end_positions else None
        width = self.width
        height = self.height
        log(f"  {label}: build={build_pos} core={core_pos} term_dist²={build_dist} res={resource}")

        for dx, dy in offsets:
            x = build_pos.x + dx
            y = build_pos.y + dy
            if not on_map_coords(x, y, width, height):
                continue
            adj_idx = y * width + x
            dist = dist_to_terminal_xy(x, y)
            if not allow_far_terminals and dist >= build_dist:
                continue
            if self._would_create_loop_idx(build_idx, adj_idx):
                continue

            adj = Position(x, y)

            if self._is_ore_idx(adj_idx):
                continue

            is_terminal = (adj_idx in end_idx_set) if end_idx_set is not None else (core_pos is not None and abs(x - core_pos.x) <= 1 and abs(y - core_pos.y) <= 1)
            if is_terminal:
                if dist < best_terminal_dist:
                    best_terminal_dist = dist
                    best_terminal = adj
                continue

            if allow_far_terminals and dist >= build_dist:
                continue
            if self.is_adjacent_to_opposite_ore(adj, resource):
                continue
            if not self._is_visited_idx(adj_idx):
                continue
            if self._get_tile_env_idx(adj_idx) == Environment.WALL:
                continue

            splitter_dir = build_pos.direction_to(adj) if check_splitter else None
            eff_dist = self._score_output_candidate(adj, adj_idx, dist, build_dist,
                                                    my_team, resource, ct, core_pos, end_positions,
                                                    dist_to_terminal, check_splitter_dir=splitter_dir)
            if eff_dist is None:
                continue
            if eff_dist < best_next_dist:
                best_next_dist = eff_dist
                best_next = adj

        result = best_terminal or best_next
        log(f"  {label} result: {result}")
        return result

    def get_best_conveyor_output(self, build_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None) -> tuple[Direction, Position] | None:
        """Find the best cardinal-adjacent tile for a conveyor at build_pos.
        Returns (direction, next_pos) or None."""
        result = self._get_best_output(build_pos, core_pos, ct, _CARDINAL_OFFSETS,
                                       my_team=my_team, end_positions=end_positions,
                                       resource=resource, check_splitter=True,
                                       allow_far_terminals=False, label="conv_output")
        if result is None:
            return None
        return (build_pos.direction_to(result), result)

    def get_best_bridge_output(self, bridge_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None) -> Position | None:
        """Find the best output tile for a bridge at bridge_pos.
        Returns Position or None."""
        return self._get_best_output(bridge_pos, core_pos, ct, _BRIDGE_OFFSETS,
                                     my_team=my_team, end_positions=end_positions,
                                     resource=resource, check_splitter=False,
                                     allow_far_terminals=True, label="bridge_output")

    def indicate_entity_map(self, ct: Controller, my_team: Team):
        """Draw colored indicator dots for all tracked entities. Purpose of this
        method is to show what the builder bot *thinks* is on the map.
        Red=enemy units, Orange=enemy conveyors, Yellow=other enemy non-road,
        Green=ally units, Blue=ally conveyors, Purple=other ally non-road."""
        _UNIT_TYPES = (EntityType.CORE, EntityType.BUILDER_BOT, *TURRET_TYPES, EntityType.LAUNCHER)
        for idx in range(self.tile_count):
            entity_id = self._entity_id[idx]
            if entity_id == 0:
                continue
            etype = self._entity_type[idx]
            team = self._entity_team[idx]
            x = idx % self.width
            y = idx // self.width
            if etype == EntityType.ROAD or etype == EntityType.MARKER:
                continue
            pos = Position(x, y)
            if team != my_team:
                if etype in _UNIT_TYPES:
                    ct.draw_indicator_dot(pos, 255, 0, 0)      # red
                elif etype in CONVEYOR_TYPES:
                    ct.draw_indicator_dot(pos, 255, 165, 0)    # orange
                else:
                    ct.draw_indicator_dot(pos, 255, 255, 0)    # yellow
            else:
                if etype in _UNIT_TYPES:
                    ct.draw_indicator_dot(pos, 0, 255, 0)      # green
                elif etype in CONVEYOR_TYPES:
                    ct.draw_indicator_dot(pos, 0, 100, 255)    # blue
                else:
                    ct.draw_indicator_dot(pos, 180, 0, 255)    # purple
    
    def indicate_seen(self, ct: Controller):
        for idx in range(self.tile_count):
            env = self._env[idx]
            if env == None:
                continue
            x = idx % self.width
            y = idx // self.width
            pos = Position(x, y)
            if env == Environment.WALL:
                ct.draw_indicator_dot(pos, 255, 0, 0)      # red
            elif env == Environment.ORE_TITANIUM:
                ct.draw_indicator_dot(pos, 0, 255, 255)    # cyan
            elif env == Environment.ORE_AXIONITE:
                ct.draw_indicator_dot(pos, 255, 0, 255)    # magenta
