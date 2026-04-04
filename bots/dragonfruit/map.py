import random
from collections import defaultdict

from cambc import Controller, Direction, EntityType, Environment, Position, ResourceType, Team

from globals import DIRECTIONS, CARDINAL_DIRECTIONS, CONVEYOR_TYPES, TURRET_TYPES, Symmetry, INF
from comms import Comms
from helpers import on_map, on_map_coords, get_foundry_positions, is_core_tile

SYMMETRY_MAPPING = {
    (True,  False, False): Symmetry.FLIP_X,
    (False, True,  False): Symmetry.FLIP_Y,
    (False, False, True):  Symmetry.ROTATE,
}

_BRIDGE_OFFSETS = tuple((dx, dy) for dx in range(-3, 4) for dy in range(-3, 4) if dx*dx + dy*dy <= 9)

RESOURCE_MASK_TITANIUM = 1
RESOURCE_MASK_AXIONITE = 2
OBSERVED_RESOURCE_MAX_AGE = 5

class Map:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.env: list[list[Environment | None]] = [[None] * height for _ in range(width)]
        self.entities: list[list[tuple[int, EntityType, Team] | None]] = [[None] * height for _ in range(width)]
        self.ore_ti = set()
        self.ore_ax = set()
        self.unserviced_harvesters: set[Position] = set()
        self.unreachable_harvesters: set[Position] = set()
        self.conveyor_outputs: dict[Position, Position] = {}  # build_pos -> output_pos
        self.conveyor_inputs: defaultdict[Position, set[Position]] = defaultdict(set)  # output_pos -> set of build_pos
        self.conveyor_resources: defaultdict[Position, set[ResourceType]] = defaultdict(set)  # build_pos -> observed resource types
        self.conveyor_resources_last_seen: defaultdict[Position, dict[ResourceType, int]] = defaultdict(dict)
        self.input_chain_valid: list[list[bool]] = [[False] * height for _ in range(width)]
        self.input_resource_masks: list[list[int]] = [[0] * height for _ in range(width)]
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
        
    def get_symmetric_pos(self, pos: Position, symmetry: Symmetry):
        if symmetry == Symmetry.FLIP_X:
            return Position(self.width - 1 - pos.x, pos.y)
        elif symmetry == Symmetry.FLIP_Y:
            return Position(pos.x, self.height - 1 - pos.y)
        elif symmetry == Symmetry.ROTATE:
            return Position(self.width - 1 - pos.x, self.height - 1 - pos.y)
        return pos
        
    def check_symmetry(self, pos: Position, env: Environment):
        if self.symmetry != Symmetry.UNKNOWN:
            return
        if self.can_flip_x:
            sym_pos = self.get_symmetric_pos(pos, Symmetry.FLIP_X)
            sym_env = self.env[sym_pos.x][sym_pos.y]
            if sym_env is not None and sym_env != env:
                self.can_flip_x = False
        if self.can_flip_y:
            sym_pos = self.get_symmetric_pos(pos, Symmetry.FLIP_Y)
            sym_env = self.env[sym_pos.x][sym_pos.y]
            if sym_env is not None and sym_env != env:
                self.can_flip_y = False
        if self.can_rotate:
            sym_pos = self.get_symmetric_pos(pos, Symmetry.ROTATE)
            sym_env = self.env[sym_pos.x][sym_pos.y]
            if sym_env is not None and sym_env != env:
                self.can_rotate = False
                
    def check_core_symmetry(self, pos: Position):
        if self.symmetry != Symmetry.UNKNOWN:
            return
        if self.can_flip_x:
            sym_pos = self.get_symmetric_pos(pos, Symmetry.FLIP_X)
            sym_entity = self.entities[sym_pos.x][sym_pos.y]
            if sym_entity is not None and sym_entity[1] != EntityType.CORE:
                self.can_flip_x = False
        if self.can_flip_y:
            sym_pos = self.get_symmetric_pos(pos, Symmetry.FLIP_Y)
            sym_entity = self.entities[sym_pos.x][sym_pos.y]
            if sym_entity is not None and sym_entity[1] != EntityType.CORE:
                self.can_flip_y = False
        if self.can_rotate:
            sym_pos = self.get_symmetric_pos(pos, Symmetry.ROTATE)
            sym_entity = self.entities[sym_pos.x][sym_pos.y]
            if sym_entity is not None and sym_entity[1] != EntityType.CORE:
                self.can_rotate = False

    def get_symmetry_key(self):
        return (self.can_flip_x, self.can_flip_y, self.can_rotate)    
    
    def update_symmetry(self):
        if self.symmetry != Symmetry.UNKNOWN:
            return
        key = self.get_symmetry_key()
        if key in SYMMETRY_MAPPING:
            self.symmetry = SYMMETRY_MAPPING[key]

    def _resource_to_mask(self, resource: ResourceType | None) -> int:
        if resource == ResourceType.TITANIUM:
            return RESOURCE_MASK_TITANIUM
        if resource == ResourceType.RAW_AXIONITE:
            return RESOURCE_MASK_AXIONITE
        return 0

    def _resource_mask_to_set(self, mask: int) -> set[ResourceType]:
        resources = set()
        if mask & RESOURCE_MASK_TITANIUM:
            resources.add(ResourceType.TITANIUM)
        if mask & RESOURCE_MASK_AXIONITE:
            resources.add(ResourceType.RAW_AXIONITE)
        return resources

    def _get_cached_resource_mask(self, pos: Position) -> int:
        return self.input_resource_masks[pos.x][pos.y]

    def _set_cached_resource_mask(self, pos: Position, mask: int):
        self.input_resource_masks[pos.x][pos.y] = mask

    def _get_cached_chain_valid(self, pos: Position) -> bool:
        return self.input_chain_valid[pos.x][pos.y]

    def _set_cached_chain_valid(self, pos: Position, valid: bool):
        self.input_chain_valid[pos.x][pos.y] = valid

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
        return resource in self.get_recent_conveyor_resources(pos, max_age=max_age)

    def get_cached_conveyor_resources(self, pos: Position) -> set[ResourceType]:
        """Return cached resource evidence for a conveyor chain position."""
        resources = set(self.get_recent_conveyor_resources(pos))
        resources.update(self._resource_mask_to_set(self._get_cached_resource_mask(pos)))
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
        for input_pos in self.conveyor_inputs.get(output_pos, ()):
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
        for input_pos in self.conveyor_inputs.get(output_pos, ()):
            cached_resources.update(self.get_cached_conveyor_resources(input_pos))
        if len(cached_resources) == 1:
            return next(iter(cached_resources))
        return None

    def _has_ore_harvester_at(self, pos: Position) -> bool:
        entity = self.entities[pos.x][pos.y]
        if entity is None or entity[1] != EntityType.HARVESTER:
            return False
        env = self.env[pos.x][pos.y]
        return env == Environment.ORE_TITANIUM or env == Environment.ORE_AXIONITE

    def _collect_downstream_positions(self, dirty_roots: set[Position]) -> list[Position]:
        positions = []
        seen = set()
        stack = list(dirty_roots)
        while stack:
            pos = stack.pop()
            if pos in seen or not on_map(pos, self.width, self.height):
                continue
            seen.add(pos)
            positions.append(pos)
            next_pos = self.conveyor_outputs.get(pos)
            if next_pos is not None:
                stack.append(next_pos)
        return positions

    def _compute_cached_resource_mask(self, pos: Position) -> int:
        mask = 0

        for d in CARDINAL_DIRECTIONS:
            adj = pos.add(d)
            if not on_map(adj, self.width, self.height):
                continue
            entity = self.entities[adj.x][adj.y]
            if entity is None or entity[1] != EntityType.HARVESTER:
                continue
            env = self.env[adj.x][adj.y]
            if env == Environment.ORE_TITANIUM:
                mask |= RESOURCE_MASK_TITANIUM
            elif env == Environment.ORE_AXIONITE:
                mask |= RESOURCE_MASK_AXIONITE

        for input_pos in self.conveyor_inputs.get(pos, ()):
            if not on_map(input_pos, self.width, self.height):
                continue

            if not self.is_visited(input_pos):
                continue

            entity = self.entities[input_pos.x][input_pos.y]
            if entity is None or entity[1] not in CONVEYOR_TYPES:
                continue
            if not self._get_cached_chain_valid(input_pos):
                continue

            mask |= self._get_cached_resource_mask(input_pos)
            if mask == (RESOURCE_MASK_TITANIUM | RESOURCE_MASK_AXIONITE):
                break

        return mask

    def _compute_cached_chain_valid(self, pos: Position) -> bool:
        has_valid_feeder = False

        for d in CARDINAL_DIRECTIONS:
            adj = pos.add(d)
            if not on_map(adj, self.width, self.height):
                continue
            entity = self.entities[adj.x][adj.y]
            if entity is None or entity[1] != EntityType.HARVESTER:
                continue
            env = self.env[adj.x][adj.y]
            if env == Environment.ORE_TITANIUM or env == Environment.ORE_AXIONITE:
                has_valid_feeder = True

        for input_pos in self.conveyor_inputs.get(pos, ()):
            if not on_map(input_pos, self.width, self.height):
                continue
            if not self.is_visited(input_pos):
                # Unvisited input — optimistically assume valid
                has_valid_feeder = True
                continue
            entity = self.entities[input_pos.x][input_pos.y]
            if entity is None or entity[1] not in CONVEYOR_TYPES:
                continue  # broken input, but other inputs may still be valid
            if not self._get_cached_chain_valid(input_pos):
                continue  # invalid upstream, but other inputs may still be valid
            has_valid_feeder = True

        return has_valid_feeder

    def _recompute_input_chain_cache(self, dirty_roots: set[Position]):
        positions = self._collect_downstream_positions(dirty_roots)
        if not positions:
            return

        pos_set = set(positions)

        # Build in-degree map (only counting edges within pos_set)
        in_degree: dict[Position, int] = {pos: 0 for pos in positions}
        for pos in positions:
            for input_pos in self.conveyor_inputs.get(pos, ()):
                if input_pos in pos_set:
                    in_degree[pos] += 1

        # Topological sort via Kahn's algorithm
        queue = [pos for pos in positions if in_degree[pos] == 0]
        topo_order: list[Position] = []
        qi = 0
        while qi < len(queue):
            pos = queue[qi]
            qi += 1
            topo_order.append(pos)
            output = self.conveyor_outputs.get(pos)
            if output is not None and output in pos_set:
                in_degree[output] -= 1
                if in_degree[output] == 0:
                    queue.append(output)

        # Positions not in topo_order are in cycles — mark invalid
        for pos in positions:
            if pos not in in_degree or in_degree[pos] != 0:
                self._set_cached_chain_valid(pos, False)
                self._set_cached_resource_mask(pos, 0)

        # Single pass in topological order (upstream first)
        for pos in topo_order:
            new_valid = self._compute_cached_chain_valid(pos)
            new_mask = self._compute_cached_resource_mask(pos) if new_valid else 0
            self._set_cached_chain_valid(pos, new_valid)
            self._set_cached_resource_mask(pos, new_mask)

    def _remove_conveyor_tracking(self, pos: Position):
        """Remove conveyor output/input/resource tracking for a position."""
        old_output = self.conveyor_outputs.pop(pos, None)
        if old_output is not None:
            self.conveyor_inputs[old_output].discard(pos)
            if not self.conveyor_inputs[old_output]:
                del self.conveyor_inputs[old_output]
        self.conveyor_resources.pop(pos, None)
        self.conveyor_resources_last_seen.pop(pos, None)

    def update_vision(self, ct: Controller, comms: Comms):
        self.current_round = ct.get_current_round()
        self._feeds_turret_cache.clear()
        self._feeds_building_cache.clear()
        self._feeds_building_in_vision_cache.clear()
        self._sabotage_downstream_cache.clear()
        self._chain_terminal_cache.clear()
        self._chain_last_visible_cache.clear()
        my_team = ct.get_team()
        nearby = ct.get_nearby_tiles()
        dirty_cache_positions: set[Position] = set()

        # First pass: update env and entities
        for pos in nearby:
            prev_output = self.conveyor_outputs.get(pos)
            prev_was_ore_harvester = self._has_ore_harvester_at(pos) if self.is_visited(pos) else False

            env = ct.get_tile_env(pos)
            if self.env[pos.x][pos.y] is None:
                self.env[pos.x][pos.y] = env
                if env == Environment.ORE_TITANIUM:
                    self.ore_ti.add(pos)
                elif env == Environment.ORE_AXIONITE:
                    self.ore_ax.add(pos)
                self.check_symmetry(pos, env)

            bid = ct.get_tile_building_id(pos)
            if bid is not None:
                etype = ct.get_entity_type(bid)
                team = ct.get_team(bid)
                if etype == EntityType.MARKER:
                    if team == my_team:
                        comms.read_marker(ct.get_marker_value(bid))
                    self.entities[pos.x][pos.y] = None
                    if pos in self.conveyor_outputs:
                        self._remove_conveyor_tracking(pos)
                else:
                    if etype == EntityType.CORE:
                        if self.entities[pos.x][pos.y] is None:
                            self.check_core_symmetry(pos)
                    self.entities[pos.x][pos.y] = (bid, etype, team)

                    # Track conveyor outputs and resources
                    if etype in CONVEYOR_TYPES:
                        new_output = ct.get_bridge_target(bid) if etype == EntityType.BRIDGE else pos.add(ct.get_direction(bid))
                        old_output = self.conveyor_outputs.get(pos)
                        if old_output != new_output:
                            if old_output is not None:
                                self.conveyor_inputs[old_output].discard(pos)
                                if not self.conveyor_inputs[old_output]:
                                    del self.conveyor_inputs[old_output]
                            self.conveyor_outputs[pos] = new_output
                            self.conveyor_inputs[new_output].add(pos)
                        resource = ct.get_stored_resource(bid)
                        if resource is not None:
                            self._record_conveyor_resource(pos, resource)
                    elif pos in self.conveyor_outputs:
                        self._remove_conveyor_tracking(pos)
            else:
                self.entities[pos.x][pos.y] = None
                if pos in self.conveyor_outputs:
                    self._remove_conveyor_tracking(pos)

            new_output = self.conveyor_outputs.get(pos)
            if prev_output is not None:
                dirty_cache_positions.add(prev_output)
            if new_output is not None:
                dirty_cache_positions.add(new_output)

            new_is_ore_harvester = self._has_ore_harvester_at(pos)
            if prev_was_ore_harvester != new_is_ore_harvester:
                for d in CARDINAL_DIRECTIONS:
                    adj = pos.add(d)
                    if not on_map(adj, self.width, self.height):
                        continue
                    dirty_cache_positions.add(adj)

        self.update_symmetry()
        self._recompute_input_chain_cache(dirty_cache_positions)
        
        # Second pass: maintain unserviced_harvesters
        for pos in nearby:
            entity = self.entities[pos.x][pos.y]
            if entity is not None and entity[1] == EntityType.HARVESTER:
                has_ally_conveyor = False
                for d in CARDINAL_DIRECTIONS:
                    n = pos.add(d)
                    if not on_map(n, self.width, self.height):
                        continue
                    n_entity = self.entities[n.x][n.y]
                    if n_entity is not None and n_entity[1] in CONVEYOR_TYPES and n_entity[2] == my_team:
                        has_ally_conveyor = True
                        break
                if has_ally_conveyor:
                    self.unserviced_harvesters.discard(pos)
                else:
                    self.unserviced_harvesters.add(pos)
            else:
                self.unserviced_harvesters.discard(pos)
    
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
            next_pos = self.conveyor_outputs.get(cur)
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
            next_pos = self.conveyor_outputs.get(cur)
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

    def would_create_loop(self, build_pos: Position, output_pos: Position) -> bool:
        """Return True if placing a conveyor at build_pos pointing to output_pos
        would create a loop (i.e. following the chain from output_pos eventually
        reaches build_pos)."""
        cur = self.conveyor_outputs.get(output_pos)
        if cur is None:
            return False

        seen = {build_pos}
        while cur is not None:
            if cur in seen:
                return cur == build_pos
            seen.add(cur)
            cur = self.conveyor_outputs.get(cur)
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
            entity = self.entities[adj.x][adj.y]
            if entity is None or entity[1] != EntityType.HARVESTER:
                continue
            env = self.env[adj.x][adj.y]
            if env == Environment.ORE_TITANIUM or env == Environment.ORE_AXIONITE:
                feeders.append((adj, EntityType.HARVESTER))

        for input_pos in self.conveyor_inputs.get(output_pos, ()):
            if not on_map(input_pos, self.width, self.height):
                continue
            if self.is_visited(input_pos):
                entity = self.entities[input_pos.x][input_pos.y]
                if entity is None or entity[1] not in CONVEYOR_TYPES:
                    continue
                etype = entity[1]
            else:
                etype = EntityType.CONVEYOR
            feeders.append((input_pos, etype))

        return feeders

    def has_adjacent_ore_harvester(self, output_pos: Position) -> bool:
        """True if a cardinally adjacent harvester on ore feeds output_pos."""
        for d in CARDINAL_DIRECTIONS:
            adj = output_pos.add(d)
            if not on_map(adj, self.width, self.height):
                continue
            entity = self.entities[adj.x][adj.y]
            if entity is None or entity[1] != EntityType.HARVESTER:
                continue
            env = self.env[adj.x][adj.y]
            if env == Environment.ORE_TITANIUM or env == Environment.ORE_AXIONITE:
                return True
        return False

    def has_valid_input_chain(self, pos: Position) -> bool:
        """Return True if all known upstream branches remain valid."""
        return self._get_cached_chain_valid(pos)

    def feeds_ally_building(self, pos: Position, my_team: Team) -> bool:
        """Follow conveyor_outputs from pos. Returns True if the chain
        eventually reaches an ally building (any type)."""
        key = (pos, my_team)
        cached = self._feeds_building_cache.get(key)
        if cached is not None:
            return cached
        cur = pos
        visited = set()
        while cur in self.conveyor_outputs:
            if cur in visited:
                self._feeds_building_cache[key] = False
                return False  # cycle
            visited.add(cur)
            cur = self.conveyor_outputs[cur]
            if not self.is_visited(cur):
                self._feeds_building_cache[key] = False
                return False
            entity = self.entities[cur.x][cur.y]
            if entity is None:
                self._feeds_building_cache[key] = False
                return False
            _, etype, team = entity
            if team == my_team:
                self._feeds_building_cache[key] = True
                return True
            if etype not in CONVEYOR_TYPES:
                self._feeds_building_cache[key] = False
                return False  # enemy non-conveyor terminal
        self._feeds_building_cache[key] = False
        return False

    def feeds_ally_building_in_vision(self, pos: Position, my_team: Team, ct: Controller, core_pos: Position | None = None) -> bool:
        """Follow conveyor_outputs while outputs stay in current vision.
        Returns True iff the visible chain clearly reaches an allied terminal."""
        key = (pos, my_team)
        cached = self._feeds_building_in_vision_cache.get(key)
        if cached is not None:
            return cached
        cur = pos
        visited = set()
        while cur in self.conveyor_outputs:
            if cur in visited:
                self._feeds_building_in_vision_cache[key] = False
                return False
            visited.add(cur)
            cur = self.conveyor_outputs[cur]
            if not ct.is_in_vision(cur):
                self._feeds_building_in_vision_cache[key] = False
                return False
            if is_core_tile(core_pos, cur):
                self._feeds_building_in_vision_cache[key] = True
                return True
            if not self.is_visited(cur):
                self._feeds_building_in_vision_cache[key] = False
                return False
            entity = self.entities[cur.x][cur.y]
            if entity is None:
                self._feeds_building_in_vision_cache[key] = False
                return False
            _, etype, team = entity
            if team == my_team:
                self._feeds_building_in_vision_cache[key] = True
                return True
            if etype not in CONVEYOR_TYPES:
                self._feeds_building_in_vision_cache[key] = False
                return False
        self._feeds_building_in_vision_cache[key] = False
        return False

    def feeds_ally_turret(self, pos: Position, my_team: Team) -> bool:
        """Follow conveyor_outputs from pos. Returns True if the chain
        eventually reaches an ally turret (SENTINEL, GUNNER, BREACH)."""
        key = (pos, my_team)
        cached = self._feeds_turret_cache.get(key)
        if cached is not None:
            return cached
        cur = pos
        visited = set()
        while cur in self.conveyor_outputs:
            if cur in visited:
                self._feeds_turret_cache[key] = False
                return False  # cycle
            visited.add(cur)
            cur = self.conveyor_outputs[cur]
            if not self.is_visited(cur):
                self._feeds_turret_cache[key] = False
                return False
            entity = self.entities[cur.x][cur.y]
            if entity is None:
                self._feeds_turret_cache[key] = False
                return False
            _, etype, team = entity
            if team == my_team and etype in TURRET_TYPES:
                self._feeds_turret_cache[key] = True
                return True
            if etype not in CONVEYOR_TYPES:
                self._feeds_turret_cache[key] = False
                return False
        self._feeds_turret_cache[key] = False
        return False

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

        while cur in self.conveyor_outputs:
            if cur in visited:
                break
            visited.add(cur)
            path.append(cur)

            next_pos = self.conveyor_outputs[cur]
            if not self.is_visited(next_pos):
                break

            entity = self.entities[next_pos.x][next_pos.y]
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
        best_ti = None
        best_ti_dist = INF
        best_ax = None
        best_ax_dist = INF
        for hpos in self.unserviced_harvesters:
            if hpos in self.unreachable_harvesters:
                continue
            dist = pos.distance_squared(hpos)
            if hpos in self.ore_ti:
                if dist < best_ti_dist:
                    best_ti_dist = dist
                    best_ti = hpos
            else:
                if dist < best_ax_dist:
                    best_ax_dist = dist
                    best_ax = hpos
        if best_ti is not None:
            return best_ti
        if ct.get_global_resources()[0] >= 1500 and best_ax is not None:
            return best_ax
        return None

    def get_tile_env(self, pos: Position) -> Environment | None:
        return self.env[pos.x][pos.y]
    
    def get_tile_entity(self, pos: Position) -> tuple[int, EntityType, Team] | None:
        return self.entities[pos.x][pos.y]
    
    def is_visited(self, pos: Position) -> bool:
        return self.env[pos.x][pos.y] is not None
        
    def get_random_tile(self) -> Position:
        return Position(random.randint(0, self.width - 1), random.randint(0, self.height - 1))
    
    def get_nearest_ore_without_harvester(self, pos: Position, ct: Controller) -> Position | None:
        best_ti = None
        best_ti_dist = INF
        best_ax = None
        best_ax_dist = INF

        for ore_pos in self.ore_ti:
            entity = self.get_tile_entity(ore_pos)
            if entity is not None and entity[1] == EntityType.HARVESTER:
                continue
            dist = pos.distance_squared(ore_pos)
            if dist < best_ti_dist:
                best_ti_dist = dist
                best_ti = ore_pos

        for ore_pos in self.ore_ax:
            entity = self.get_tile_entity(ore_pos)
            if entity is not None and entity[1] == EntityType.HARVESTER:
                continue
            dist = pos.distance_squared(ore_pos)
            if dist < best_ax_dist:
                best_ax_dist = dist
                best_ax = ore_pos

        if best_ti is not None:
            return best_ti
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
        entity = self.entities[terminal.x][terminal.y]
        return (
            entity is not None
            and entity[1] == EntityType.FOUNDRY
            and entity[2] == my_team
            and terminal != target_foundry
        )

    def is_single_input_foundry(self, pos: Position, my_team) -> bool:
        """True if pos has an ally foundry with at most 1 input and no titanium input."""
        entity = self.entities[pos.x][pos.y]
        if entity is None or entity[1] != EntityType.FOUNDRY or entity[2] != my_team:
            return False
        input_positions = self.conveyor_inputs.get(pos, set())
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
        return pos in self.ore_ax or pos in self.ore_ti

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
    
    def has_conflict(self, resource: ResourceType | None, pos: Position, ct: Controller) -> bool:
        if resource is None:
            return False
        return self._get_conveyor_resource_state(pos, ct, resource) == 2

    def has_input_conflict(self, resource: ResourceType | None, pos: Position, ct: Controller) -> bool:
        """True if pos is the output of a known conveyor carrying the opposite resource.
        Use this for empty tiles that don't have a conveyor yet but are fed by one."""
        if resource is None:
            return False
        for input_pos in self.conveyor_inputs.get(pos, ()):
            if self._get_conveyor_resource_state(input_pos, ct, resource) == 2:
                return True
        return False

    def terminal_distance_squared(self, pos: Position, core_pos: Position | None, end_positions: set | None = None) -> int | float:
        """Distance metric toward the current terminal objective."""
        if end_positions:
            return min(pos.distance_squared(end_pos) for end_pos in end_positions)
        if core_pos is None:
            return INF
        return pos.distance_squared(core_pos)


    def get_best_conveyor_output(self, build_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None) -> tuple[Direction, Position] | None:
        """Find the best cardinal-adjacent tile for a conveyor at build_pos.
        Priority: terminal > best by effective distance (ally chain-followed or empty).
        Returns (direction, next_pos) or None."""
        if core_pos is None:
            return None

        if end_positions:
            terminal_positions = tuple((p.x, p.y) for p in end_positions)

            def dist_to_terminal(pos: Position) -> int | float:
                px, py = pos
                best = INF
                for tx, ty in terminal_positions:
                    dx = px - tx
                    dy = py - ty
                    dist = dx * dx + dy * dy
                    if dist < best:
                        best = dist
                return best
        else:
            cx, cy = core_pos

            def dist_to_terminal(pos: Position) -> int | float:
                dx = pos.x - cx
                dy = pos.y - cy
                return dx * dx + dy * dy

        build_dist = dist_to_terminal(build_pos)
        best_terminal = None
        best_terminal_dist = INF
        best_next = None
        best_next_dist = INF
        print(f"  conv_output: build={build_pos} core={core_pos} term_dist²={build_dist} res={resource}")

        for d in CARDINAL_DIRECTIONS:
            adj = build_pos.add(d)
            if not on_map(adj, self.width, self.height):
                print(f"    {adj} ({d}): SKIP off map")
                continue
            dist = dist_to_terminal(adj)
            if dist >= build_dist:
                print(f"    {adj} ({d}): SKIP not closer to terminal (dist²={dist} >= {build_dist})")
                continue
            if self.would_create_loop(build_pos, adj):
                print(f"    {adj} ({d}): SKIP would create loop")
                continue
            if self.is_ore(adj):
                print(f"    {adj} ({d}): SKIP is ore")
                continue

            # Terminal position (core tile or end position)
            is_terminal = (adj in end_positions) if end_positions is not None else (core_pos is not None and abs(adj.x - core_pos.x) <= 1 and abs(adj.y - core_pos.y) <= 1)
            if is_terminal:
                if dist < best_terminal_dist:
                    print(f"    {adj} ({d}): TERMINAL (new best, dist²={dist})")
                    best_terminal_dist = dist
                    best_terminal = (d, adj)
                else:
                    print(f"    {adj} ({d}): TERMINAL (worse, dist²={dist} >= {best_terminal_dist})")
                continue
        
            if self.is_adjacent_to_opposite_ore(adj, resource):
                print(f"    {adj} ({d}): SKIP adjacent to opposite ore")
                continue


            if not self.is_visited(adj):
                print(f"    {adj} ({d}): SKIP not visited")
                continue
            if self.env[adj.x][adj.y] == Environment.WALL:
                print(f"    {adj} ({d}): SKIP wall")
                continue

            entity = self.entities[adj.x][adj.y]
            if entity is not None:
                _, etype, eteam = entity
                # Ally conveyor/bridge carrying matching resource — chain into it
                if etype in CONVEYOR_TYPES and my_team is not None and eteam == my_team:
                    # Non-bridge types can only feed a splitter from the back
                    if etype == EntityType.SPLITTER:
                        splitter_output = self.conveyor_outputs.get(adj)
                        if splitter_output is not None:
                            splitter_dir = adj.direction_to(splitter_output)
                            if d != splitter_dir:
                                print(f"    {adj} ({d}): SKIP ally splitter not feeding from back (faces {splitter_dir})")
                                continue
                    if not self.has_conflict(resource, adj, ct):
                        # Check the ally conveyor's own output is closer to core than build_pos
                        ally_output = self.conveyor_outputs.get(adj)
                        if ally_output is not None and dist_to_terminal(ally_output) >= build_dist:
                            print(f"    {adj} ({d}): SKIP ally {etype} output not closer to terminal")
                            continue
                        # Follow chain to terminal and verify it reaches the right destination
                        terminal = self.follow_chain_terminal(adj)
                        if end_positions is not None and terminal not in end_positions:
                            is_core = core_pos is not None and abs(terminal.x - core_pos.x) <= 1 and abs(terminal.y - core_pos.y) <= 1
                            if is_core or (on_map(terminal, self.width, self.height) and self.entities[terminal.x][terminal.y] is not None):
                                print(f"    {adj} ({d}): SKIP ally {etype} chain ends at {terminal} (wrong dest)")
                                continue
                        # Use the last visible output as the effective distance
                        effective_pos = self.follow_chain_last_visible(adj)
                        if effective_pos is None:
                            print(f"    {adj} ({d}): SKIP ally {etype} chain leaves vision immediately")
                            continue
                        effective_dist = dist_to_terminal(effective_pos)
                        if effective_dist < best_next_dist:
                            print(f"    {adj} ({d}): CHAIN ally {etype} eff_dist²={effective_dist} (new best)")
                            best_next_dist = effective_dist
                            best_next = (d, adj)
                        else:
                            print(f"    {adj} ({d}): CHAIN ally {etype} eff_dist²={effective_dist} (worse, best={best_next_dist})")
                    else:
                        print(f"    {adj} ({d}): SKIP ally {etype} wrong/no resource")
                elif etype == EntityType.MARKER or (etype == EntityType.ROAD and my_team is not None and eteam == my_team):
                    if self.has_input_conflict(resource, adj, ct):
                        print(f"    {adj} ({d}): SKIP road/marker has opposite-resource input")
                        continue
                    if dist < best_next_dist:
                        print(f"    {adj} ({d}): ALLY ROAD dist²={dist} (new best)")
                        best_next_dist = dist
                        best_next = (d, adj)
                    else:
                        print(f"    {adj} ({d}): ALLY ROAD dist²={dist} (worse, best={best_next_dist})")
                else:
                    print(f"    {adj} ({d}): SKIP occupied by {eteam} {etype}")
            else:
                if self.has_input_conflict(resource, adj, ct):
                    print(f"    {adj} ({d}): SKIP empty tile has opposite-resource input")
                    continue
                if dist < best_next_dist:
                    print(f"    {adj} ({d}): EMPTY dist²={dist} (new best)")
                    best_next_dist = dist
                    best_next = (d, adj)
                else:
                    print(f"    {adj} ({d}): EMPTY dist²={dist} (worse, best={best_next_dist})")

        result = best_terminal if best_terminal is not None else best_next
        print(f"  conv_output result: {result}")
        if best_terminal is not None:
            return best_terminal
        return best_next

    def get_best_bridge_output(self, bridge_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None) -> Position | None:
        """Find the best output tile for a bridge at bridge_pos, targeting core_pos.
        Prefers any core tile reachable within dist² ≤ 9; otherwise the visited,
        empty, non-wall tile closest to the core."""
        if core_pos is None:
            return None

        if end_positions:
            terminal_positions = tuple((p.x, p.y) for p in end_positions)

            def dist_to_terminal(pos: Position) -> int | float:
                px, py = pos
                best = INF
                for tx, ty in terminal_positions:
                    dx = px - tx
                    dy = py - ty
                    dist = dx * dx + dy * dy
                    if dist < best:
                        best = dist
                return best
        else:
            cx, cy = core_pos

            def dist_to_terminal(pos: Position) -> int | float:
                dx = pos.x - cx
                dy = pos.y - cy
                return dx * dx + dy * dy

        best_terminal = None
        best_terminal_dist = INF
        best_next = None
        best_next_dist = INF
        bridge_dist_to_terminal = dist_to_terminal(bridge_pos)
        print(f"  bridge_output: pos={bridge_pos} core={core_pos} bridge_term_dist²={bridge_dist_to_terminal} res={resource}")
        for dx, dy in _BRIDGE_OFFSETS:
            x, y = bridge_pos.x + dx, bridge_pos.y + dy
            candidate = Position(x, y)
            if not on_map(candidate, self.width, self.height):
                continue
            candidate_dist = dist_to_terminal(candidate)
            if self.would_create_loop(bridge_pos, candidate):
                print(f"    {candidate}: SKIP loop")
                continue
            if self.is_ore(candidate):
                print(f"    {candidate}: SKIP ore")
                continue
            if self.is_adjacent_to_opposite_ore(candidate, resource):
                print(f"    {candidate}: SKIP adj opposite ore")
                continue
            is_terminal = (candidate in end_positions) if end_positions is not None else (core_pos is not None and abs(x - core_pos.x) <= 1 and abs(y - core_pos.y) <= 1)
            if is_terminal:
                if candidate_dist < best_terminal_dist:
                    print(f"    {candidate}: TERMINAL (new best, dist²={candidate_dist})")
                    best_terminal_dist = candidate_dist
                    best_terminal = candidate
                else:
                    print(f"    {candidate}: TERMINAL (worse, dist²={candidate_dist} >= {best_terminal_dist})")
            elif candidate_dist >= bridge_dist_to_terminal:
                print(f"    {candidate}: SKIP not closer to terminal (dist²={candidate_dist} >= {bridge_dist_to_terminal})")
            elif not self.is_visited(candidate):
                print(f"    {candidate}: SKIP not visited")
            elif self.env[x][y] == Environment.WALL:
                print(f"    {candidate}: SKIP wall")
            else:
                entity = self.entities[x][y]
                if entity is not None:
                    _, etype, eteam = entity
                    if etype in CONVEYOR_TYPES and my_team is not None and eteam == my_team:
                        if not self.has_conflict(resource, candidate, ct):
                            # Check the ally conveyor's own output is closer to core than bridge_pos
                            ally_output = self.conveyor_outputs.get(candidate)
                            if ally_output is not None and dist_to_terminal(ally_output) >= bridge_dist_to_terminal:
                                print(f"    {candidate}: SKIP ally {etype} output not closer to terminal")
                                continue
                            # Follow chain to terminal and verify it reaches the right destination
                            terminal = self.follow_chain_terminal(candidate)
                            if end_positions is not None and terminal not in end_positions:
                                is_core = core_pos is not None and abs(terminal.x - core_pos.x) <= 1 and abs(terminal.y - core_pos.y) <= 1
                                if is_core or (on_map(terminal, self.width, self.height) and self.entities[terminal.x][terminal.y] is not None):
                                    print(f"    {candidate}: SKIP ally {etype} chain ends at {terminal} (wrong dest)")
                                    continue
                            # Use the last visible output as the effective distance
                            effective_pos = self.follow_chain_last_visible(candidate)
                            if effective_pos is None:
                                print(f"    {candidate}: SKIP ally {etype} chain leaves vision immediately")
                                continue
                            effective_dist = dist_to_terminal(effective_pos)
                            if effective_dist < best_next_dist:
                                print(f"    {candidate}: CHAIN ally {etype} eff_dist²={effective_dist} (new best)")
                                best_next_dist = effective_dist
                                best_next = candidate
                            else:
                                print(f"    {candidate}: CHAIN ally {etype} eff_dist²={effective_dist} (worse, best={best_next_dist})")
                        else:
                            print(f"    {candidate}: SKIP ally {etype} wrong/no resource")
                    elif etype == EntityType.MARKER or etype == EntityType.ROAD and my_team is not None and eteam == my_team:
                        if self.has_input_conflict(resource, candidate, ct):
                            print(f"    {candidate}: SKIP road/marker has opposite-resource input")
                            continue
                        if candidate_dist < best_next_dist:
                            print(f"    {candidate}: ALLY ROAD dist²={candidate_dist} (new best)")
                            best_next_dist = candidate_dist
                            best_next = candidate
                        else:
                            print(f"    {candidate}: ALLY ROAD dist²={candidate_dist} (worse, best={best_next_dist})")
                    else:
                        print(f"    {candidate}: SKIP occupied by {eteam} {etype}")
                else:
                    if self.has_input_conflict(resource, candidate, ct):
                        print(f"    {candidate}: SKIP empty tile has opposite-resource input")
                        continue
                    if candidate_dist < best_next_dist:
                        print(f"    {candidate}: EMPTY dist²={candidate_dist} (new best)")
                        best_next_dist = candidate_dist
                        best_next = candidate
                    else:
                        print(f"    {candidate}: EMPTY dist²={candidate_dist} (worse, best={best_next_dist})")
        result = best_terminal if best_terminal is not None else best_next
        print(f"  bridge_output result: {result}")
        if best_terminal is not None:
            return best_terminal
        return best_next

    def indicate_entity_map(self, ct: Controller, my_team: Team):
        """Draw colored indicator dots for all tracked entities.
        Red=enemy units, Orange=enemy conveyors, Yellow=other enemy non-road,
        Green=ally units, Blue=ally conveyors, Purple=other ally non-road."""
        _UNIT_TYPES = (EntityType.CORE, EntityType.BUILDER_BOT, *TURRET_TYPES, EntityType.LAUNCHER)
        for x in range(self.width):
            for y in range(self.height):
                entity = self.entities[x][y]
                if entity is None:
                    continue
                _, etype, team = entity
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
