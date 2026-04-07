import random
import sys

from cambc import Controller, Direction, EntityType, Environment, Position, ResourceType, Team

from globals import *
from nav import Navigator
from a_star_nav import AStarNavigator
from map import Map
from comms import Comms, LAUNCH_ORDER_ID_MASK
from combat import *
from helpers import *
from vision import VisionCache
from log import log, log_time

TURN_CPU_BUDGET_US = 2000
BUGNAV_RESERVE_US = 200
END_TURN_RESERVE_US = 50

class Player:
    def __init__(self):
        self.nav = Navigator()
        self.a_star_nav = AStarNavigator()
        self.comms = Comms()
        self.vc = VisionCache()
        self.map: Map
        self.my_team: Team
        self.etype: EntityType
        self.my_id: int
        self.path_color: tuple[int, int, int] = (0, 100, 255)

        self.num_spawned = 0
        self.turns_since_wealthy_spawn = SPAWN_WEALTHY_INTERVAL
        self.core_pos: Position | None = None
        self.enemy_core_pos: Position | None = None
        self.predicted_enemy_core_pos: Position | None = None

        self.state: State = State.EXPLORE
        self.timeout_turns = 0
        self.has_explored_first_destination = False
        self.last_fired_round = 0
        self.harvest_ore_type: ResourceType | None = None
        self.harvest_ore_pos: Position | None = None   # position of the ore/harvester we're chaining from
        self.foundry_pos: Position | None = None
        self.foundry_positions: set | None = None
        
        self.initial_spawn_plan = None
        self.broken_chains: dict = {}  # output_pos -> resource type
        self.health = 0
        self.prev_health = 0
        self.global_titanium = 0
        self.global_axionite = 0
        self.prev_global_titanium = -1
        self.prev_global_axionite = -1
        self.last_global_titanium_increase = -2000
        self.last_global_axionite_increase = -2000

        self.attack_target: Position | None = None
        self.attack_reason: str | None = None
        
        self.last_seen_builder_bot_round = 0
        self.last_support_launcher_round = -2000
        self.rush_enemy_core = False

    def decideState(self, ct: Controller, my_pos: Position, vc: VisionCache) -> State:
        # Scan for broken ally chains (conveyors with input but no ally output)
        if self.map is not None:
            for (bid, etype, pos) in vc.ally_conveyors:
                if etype == EntityType.BRIDGE:
                    output_pos = ct.get_bridge_target(bid)
                else:
                    output_pos = pos.add(ct.get_direction(bid))
                if not on_map(output_pos, self.map.width, self.map.height) or not ct.is_in_vision(output_pos):
                    continue
                if not self.map.has_conveyor_inputs(pos):
                    has_adj_harvester = False
                    for d in CARDINAL_DIRECTIONS:
                        adj = pos.add(d)
                        if not on_map(adj, self.map.width, self.map.height):
                            continue
                        adj_entity = self.map.get_tile_entity(adj)
                        if adj_entity is not None and adj_entity[1] == EntityType.HARVESTER:
                            has_adj_harvester = True
                            break
                    if not has_adj_harvester:
                        self.broken_chains.pop(output_pos, None)
                        continue
                if is_core_tile(self.core_pos, output_pos):
                    self.broken_chains.pop(output_pos, None)
                    continue
                out_bid = ct.get_tile_building_id(output_pos)
                if out_bid is not None:
                    out_etype = ct.get_entity_type(out_bid)
                    out_team = ct.get_team(out_bid)
                    if out_team == self.my_team and (out_etype in CONVEYOR_TYPES or out_etype == EntityType.FOUNDRY):
                        self.broken_chains.pop(output_pos, None)
                        continue
                    if out_team != self.my_team and out_etype in CONVEYOR_TYPES:
                        if self.map.feeds_ally_building_in_vision(output_pos, self.my_team, ct, core_pos=self.core_pos):
                            self.broken_chains.pop(output_pos, None)
                            continue
                resource = ct.get_stored_resource(bid)
                if resource is None:
                    has_axionite_input = self.map.input_chain_reaches_resource(pos, ResourceType.RAW_AXIONITE)
                    has_titanium_input = self.map.input_chain_reaches_resource(pos, ResourceType.TITANIUM)
                    if has_axionite_input and not has_titanium_input:
                        resource = ResourceType.RAW_AXIONITE
                    elif has_titanium_input and not has_axionite_input:
                        resource = ResourceType.TITANIUM
                    else:
                        resources = self.map.get_recent_conveyor_resources(pos)
                        if ResourceType.RAW_AXIONITE in resources and ResourceType.TITANIUM not in resources:
                            resource = ResourceType.RAW_AXIONITE
                        elif ResourceType.TITANIUM in resources and ResourceType.RAW_AXIONITE not in resources:
                            resource = ResourceType.TITANIUM
                        else:
                            resource = ResourceType.TITANIUM
                self.broken_chains[output_pos] = resource
        for broken_chain in list(self.broken_chains):
            if not ct.is_in_vision(broken_chain):
                continue
            entity = self.map.get_tile_entity(broken_chain)
            if entity is not None and entity[2] == self.my_team and (entity[1] in CONVEYOR_TYPES or entity[1] == EntityType.FOUNDRY):
                self.broken_chains.pop(broken_chain, None)
        log(f"broken chains: {self.broken_chains}")
        
        log_time(ct, "After broken chain scan")

        enemy_core_anchor = get_enemy_core_anchor(self)

        if self.rush_enemy_core:
            if self.state in (State.INTERCEPT, State.SABOTAGE):
                return self.state

            threat_result = get_nearest_enemy_threat_pos(vc, my_pos) if vc.enemy_units else None
            if threat_result is None:
                threat_result = get_known_core_intercept_threat(self, my_pos, "rush synthetic threat")
            min_turret_cost = min(ct.get_gunner_cost()[0], ct.get_sentinel_cost()[0])
            if threat_result is not None and self.global_titanium >= min_turret_cost * 2:
                threat_pos, _ = threat_result
                if enemy_core_anchor is None or threat_pos.distance_squared(enemy_core_anchor) <= 100:
                    intercept = find_intercept_pos(
                        ct, my_pos, self.my_team, vc, threat_pos, self.map,
                        enemy_only=True,
                        global_titanium=self.global_titanium,
                        enemy_core_pos=enemy_core_anchor,
                    )
                    log_time(ct, "After rush find intercept pos")
                    if intercept is not None:
                        log(f"rush intercept target at {intercept}")
                        self.nav.set_destination(intercept, "adjacent")
                        return State.INTERCEPT

            if self.global_titanium >= 20:
                sd_result = find_sabotage_target(self, ct, my_pos, vc)
                log(f"rush sabotage target: {sd_result}")
                log_time(ct, "After rush finding sabotage target")
                if sd_result is not None:
                    sd_target, prio = sd_result
                    if enemy_core_anchor is None or sd_target.distance_squared(enemy_core_anchor) <= 128:
                        if prio >= 2 or (prio > 0 and self.global_titanium >= 100):
                            self.nav.set_destination(sd_target, "exact")
                            self.attack_target = sd_target
                            self.attack_reason = "sabotage"
                            return State.SABOTAGE

            if enemy_core_anchor is not None:
                self.nav.set_destination(enemy_core_anchor, "exact")
            return State.EXPLORE
        
        if self.state == State.INTERCEPT:
            return self.state

        # Priority 0: intercept enemy conveyors if threat detected
        threat_result = get_nearest_enemy_threat_pos(vc, my_pos) if should_intercept(vc, my_pos, self.core_pos) else None
        if threat_result is None:
            threat_result = get_known_core_intercept_threat(self, my_pos, "synthetic threat")
        threat_pos = None
        threat_is_core = False
        cost_mult = 3
        if threat_result is not None:
            threat_pos, threat_is_core = threat_result
            cost_mult = 2 if threat_is_core else 3
        min_turret_cost = min(ct.get_gunner_cost()[0], ct.get_sentinel_cost()[0])
        
        log_time(ct, "After checking threats")
        
        if threat_pos is not None and self.global_titanium >= min_turret_cost * cost_mult:
            if threat_is_core or count_ally_turrets_covering(ct, vc, threat_pos) < 2:
                log("trying to find intercept pos")
                intercept = find_intercept_pos(ct, my_pos, self.my_team, vc, threat_pos, self.map, enemy_only=False, global_titanium=self.global_titanium, enemy_core_pos=enemy_core_anchor)
                log_time(ct, "After find intercept pos")
                if intercept is not None:
                    log(f"intercept target at {intercept}")
                    self.nav.set_destination(intercept, "adjacent")
                    return State.INTERCEPT
            
        # Priority 0.5: heal damaged ally conveyors that enemy bots are standing on
        best_heal_pos = None
        best_heal_dist = INF
        for (_eid, etype, epos) in vc.enemy_units:
            if etype != EntityType.BUILDER_BOT:
                continue
            bid = ct.get_tile_building_id(epos)
            if bid is None:
                continue
            if ct.get_team(bid) != self.my_team:
                continue
            btype = ct.get_entity_type(bid)
            if btype not in CONVEYOR_TYPES:
                continue
            if ct.get_hp(bid) >= ct.get_max_hp(bid):
                continue
            dist = my_pos.distance_squared(epos)
            if dist < best_heal_dist:
                best_heal_dist = dist
                best_heal_pos = epos
        if best_heal_pos is not None:
            # Only go if we are the closest ally bot
            closer_ally = False
            for (_eid, apos) in vc.ally_builder_bots:
                if apos.distance_squared(best_heal_pos) < best_heal_dist:
                    closer_ally = True
                    break
            if not closer_ally:
                log(f"heal target at {best_heal_pos}")
                self.nav.set_destination(best_heal_pos, "adjacent")
                return State.HEAL

        # Priority 0.6: if we can see a damaged ally core, go heal it.
        # Do not require this bot to be the closest ally since core loss ends the game.
        if vc.core_pos is not None and ct.is_in_vision(vc.core_pos):
            core_id = ct.get_tile_building_id(vc.core_pos)
            if (
                core_id is not None
                and ct.get_team(core_id) == self.my_team
                and ct.get_entity_type(core_id) == EntityType.CORE
                and ct.get_hp(core_id) < ct.get_max_hp(core_id) - 40 # Arbitrary threshold to prevent bots getting stuck healing core
            ):
                log(f"heal core at {vc.core_pos}")
                self.nav.set_destination(vc.core_pos, "adjacent")
                return State.HEAL
                
        log_time(ct, "After checking heals")

        if self.state != State.EXPLORE and self.state != State.HEAL:
            return self.state

        # Priority 1: unserviced harvester; Priority 2: unharvested ore
        target = self.map.get_nearest_unserviced_harvester(my_pos, ct)
        if target is None:
            target = self.map.get_nearest_ore_without_harvester(my_pos, ct)

        log_time(ct, "After finding ore/harvest target")

        if target is not None:
            # Allow one closer ally in case it is busy with something else.
            if (
                count_closer_allies(self, target, my_pos, vc) < 2
                and can_start_harvest_chain_now(self, ct, my_pos, target, vc)
            ):
                log(f"new harvest target at {target}")
                self.timeout_turns = 0
                self.harvest_ore_pos = target
                self.nav.set_destination(target, "adjacent")
                return State.START_HARVEST_CHAIN

        # Priority 3: reroute titanium to foundry with single input
        if self.core_pos is not None and self.global_titanium >= 1500:
            foundry = self.map.find_single_input_foundry(self.core_pos, self.my_team)
            if foundry is not None:
                self.foundry_pos = foundry
                return State.REROUTE_TITANIUM

        # Priority 3.5: upgrade visible axionite placeholder conveyors to foundries
        if self.core_pos is not None and self.global_titanium >= 1500:
            foundry_placeholder = find_upgradeable_axionite_placeholder(self, ct, my_pos, vc)
            if (foundry_placeholder is not None
                and count_closer_allies(self, foundry_placeholder, my_pos, vc) < 2):
                log(f"upgrade foundry placeholder at {foundry_placeholder}")
                self.nav.set_destination(foundry_placeholder, "adjacent")
                self.harvest_ore_type = ResourceType.RAW_AXIONITE
                self.harvest_ore_pos = None
                return State.EXTEND_HARVEST_CHAIN

        # Sabotage: spot a core-feeding conveyor/bridge nearby with 500 resources
        if self.global_titanium >= 20:
            sd_result = find_sabotage_target(self, ct, my_pos, vc)
            log(f"sabotage target: {sd_result}")
            log_time(ct, "After finding sabotage target")
            if sd_result is not None:
                sd_target, prio = sd_result
                log(f"sabotage target priority: {prio}")
                if (prio >= 2 or prio > 0 and self.global_titanium >= 100):
                    log(f"sabotage: spotted core or turret feeding building at {sd_target}")
                    self.nav.set_destination(sd_target, "exact")
                    self.attack_target = sd_target
                    self.attack_reason = "sabotage"
                    return State.SABOTAGE
            
        # Defend: protect harvesters with infrastructure, or block bare titanium ore
        defend_target = find_defend_target(self, ct, my_pos, vc)
        if defend_target is not None:
            log(f"defend target at {defend_target}")
            self.harvest_ore_pos = defend_target
            self.nav.set_destination(defend_target, "adjacent")
            return State.DEFEND

        # Extend broken ally chains: pick nearest from pre-scanned broken_chains
        if self.broken_chains:
            best_chain_pos = None
            best_chain_dist = INF
            best_chain_resource = None
            for output_pos, resource in self.broken_chains.items():
                dist = my_pos.distance_squared(output_pos)
                if dist >= best_chain_dist:
                    continue
                if not can_repair_broken_chain_now(self, ct, output_pos, vc):
                    continue
                if count_closer_allies(self, output_pos, my_pos, vc) >= 2:
                    continue
                best_chain_dist = dist
                best_chain_pos = output_pos
                best_chain_resource = resource
            if best_chain_pos is not None:
                log(f"extending broken chain at {best_chain_pos} on turn {ct.get_current_round()}")
                self.nav.set_destination(best_chain_pos, "adjacent")
                self.harvest_ore_type = best_chain_resource
                return State.EXTEND_HARVEST_CHAIN

        # Explore
        if (not self.has_explored_first_destination
            and self.core_pos is not None
            and ct.get_current_round() < 100):
            self.has_explored_first_destination = True
            spawn_dir = self.core_pos.direction_to(my_pos)
            dx, dy = spawn_dir.delta()

            ex, ey = my_pos.x, my_pos.y

            while True:
                nx, ny = ex + dx, ey + dy
                if nx < 0 or nx >= self.map.width or ny < 0 or ny >= self.map.height:
                    break
                ex, ey = nx, ny

            self.nav.set_destination(Position(ex, ey), "visited")
            log(f"initial explore dest=({ex},{ey}) using direction {spawn_dir}")
            log(f"initial explore dest=({ex},{ey}) from core offset=({dx},{dy})")
        else:
            max_iters = 10
            while max_iters > 0 and self.nav.is_destination_reached(ct, self.map):
                self.nav.set_destination(self.map.get_random_tile(), "sensed")
                max_iters -= 1
            log(f"explore target={self.nav.destination} type={self.nav.destination_type}")
        return State.EXPLORE
    
    def run_core(self, ct: Controller, my_pos: Position, vc) -> None:
        builder_cost = ct.get_builder_bot_cost()[0]
        bridge_cost = ct.get_bridge_cost()[0]

        _CORE = EntityType.CORE
        sees_enemy = any(etype is not _CORE for (_eid, etype, _pos) in vc.enemy_units)

        self.turns_since_wealthy_spawn += 1
        
        wealthy = (
            self.turns_since_wealthy_spawn >= SPAWN_WEALTHY_INTERVAL and
            self.global_titanium >= bridge_cost * SPAWN_WEALTHY_BRIDGE_MULT and
            self.global_titanium >= builder_cost * SPAWN_WEALTHY_BUILDER_MULT and
            self.global_titanium >= SPAWN_WEALTHY_RESOURCE_THRESHOLD
        )
        threatened = (
            sees_enemy and
            (self.global_titanium >= builder_cost * SPAWN_THREATENED_BUILDER_MULT or self.health - self.prev_health < 0) and
            (len(vc.ally_builder_bots) == 0)
        )
        
        if len(vc.ally_builder_bots) > 0:
            self.last_seen_builder_bot_round = ct.get_current_round()

        if ct.get_unit_count() <= 1 or self.num_spawned < SPAWN_INITIAL_COUNT or (self.num_spawned < SPAWN_LATER_COUNT and self.global_titanium - ct.get_builder_bot_cost()[0] > 200 and ct.get_current_round() - self.last_global_titanium_increase < 10) or wealthy or threatened or (ct.get_current_round() - self.last_seen_builder_bot_round > 30 and self.global_titanium - ct.get_builder_bot_cost()[0] > 200):
            if sees_enemy:
                nearest_enemy_pos = min(
                    (pos for (_eid, etype, pos) in vc.enemy_units if etype is not _CORE),
                    key=lambda p: my_pos.distance_squared(p)
                )
                spawn_dir = my_pos.direction_to(nearest_enemy_pos)
                spawn_pos = my_pos.add(spawn_dir)
            else:
                if self.initial_spawn_plan is None:
                    valid_dirs = get_valid_directions(ct, my_pos, self.map.width, self.map.height)
                    rotational_core_dir = my_pos.direction_to(self.map.get_symmetric_pos(my_pos, Symmetry.ROTATE))

                    if len(valid_dirs) == 0:
                        # fallback (shouldn't happen, but safe)
                        self.initial_spawn_plan = prioritize_direction(random.sample(DIRECTIONS, 3), rotational_core_dir)
                    else:
                        chosen = pick_three_directions(my_pos, self.map.width, self.map.height, valid_dirs)
                        self.initial_spawn_plan = prioritize_direction([d for (d, _) in chosen], rotational_core_dir)

                if self.num_spawned < len(self.initial_spawn_plan):
                    spawn_dir = self.initial_spawn_plan[self.num_spawned]
                    spawn_pos = my_pos.add(spawn_dir)
                    
                    for d in self.initial_spawn_plan:
                        endpoint = get_ray_endpoint(my_pos, d, self.map.width, self.map.height)
                        ct.draw_indicator_line(my_pos, endpoint, 0, 255, 0)
                else:
                    spawn_dir = random.choice(DIRECTIONS)
                    spawn_pos = my_pos.add(spawn_dir)
            if ct.can_spawn(spawn_pos):
                ct.spawn_builder(spawn_pos)
                self.num_spawned += 1
                if wealthy:
                    self.turns_since_wealthy_spawn = 0
        
    def run_builder(self, ct: Controller, my_pos: Position, vc) -> None:
        self.attack_target: Position | None = None  # set by state logic, attacked at the end
        self.attack_reason = ""
        self.comms.reset_turn(ct.get_current_round())
        self.map.update_vision(ct, self.comms)
        
        log_time(ct, "After vision update")
        
        if self.foundry_positions is None and self.core_pos is not None and self.map is not None:
            self.foundry_positions = {p for p in get_foundry_positions(self.core_pos, self.map.width, self.map.height)
                                      if self.map.get_tile_env(p) != Environment.WALL}

        if self.comms.symmetry is not None and self.map.symmetry == Symmetry.UNKNOWN:
            self.map.symmetry = self.comms.symmetry
            log(f"symmetry from marker: {self.map.symmetry.name}")
        previous_enemy_core = self.enemy_core_pos
        if vc.enemy_core_pos is not None:
            self.enemy_core_pos = vc.enemy_core_pos
        predicted_enemy_core = get_predicted_enemy_core_pos(self)
        if predicted_enemy_core != self.predicted_enemy_core_pos:
            self.predicted_enemy_core_pos = predicted_enemy_core
            if self.predicted_enemy_core_pos is not None:
                log(f"predicted enemy core position at {self.predicted_enemy_core_pos}")
        if self.enemy_core_pos != previous_enemy_core and self.enemy_core_pos is not None:
            log(f"confirmed enemy core position at {self.enemy_core_pos}")
        # if self.map is not None and self.my_team is not None:
        #     self.map.indicate_entity_map(ct, self.my_team)
            
            
        log_time(ct, "After map checks")

        if RUSH_CORE and ct.get_current_round() == 1:
            self.rush_enemy_core = True
            
        # Check for axionite conveyors at foundry-eligible positions to upgrade to foundry
        if self.global_titanium >= 1500 and self.core_pos is not None:
            for d in DIRECTIONS:
                adj = my_pos.add(d)
                if not on_map(adj, self.map.width, self.map.height) or not ct.is_in_vision(adj) or not is_foundry_position(self.core_pos, adj):
                    continue
                bid = ct.get_tile_building_id(adj)
                if bid is None:
                    continue
                if ct.get_team(bid) != self.my_team:
                    continue
                etype = ct.get_entity_type(bid)
                if etype != EntityType.CONVEYOR:
                    continue
                is_axionite = (ct.get_stored_resource(bid) == ResourceType.RAW_AXIONITE or
                                self.map.has_recent_conveyor_resource(adj, ResourceType.RAW_AXIONITE) or
                                self.map.input_chain_reaches_resource(adj, ResourceType.RAW_AXIONITE))
                is_titanium = (ct.get_stored_resource(bid) == ResourceType.TITANIUM or
                                self.map.has_recent_conveyor_resource(adj, ResourceType.TITANIUM) or
                                self.map.input_chain_reaches_resource(adj, ResourceType.TITANIUM))
                if (is_axionite and not is_titanium) and ct.can_destroy(adj):
                    if ct.get_tile_builder_bot_id(adj) is not None:
                        continue
                    # Check if there's already an adjacent foundry — if so, build splitter toward it
                    adjacent_foundry_dir = None
                    for fd in CARDINAL_DIRECTIONS:
                        fpos = adj.add(fd)
                        if not on_map(fpos, self.map.width, self.map.height) or not ct.is_in_vision(fpos):
                            continue
                        fbid = ct.get_tile_building_id(fpos)
                        if fbid is not None and ct.get_entity_type(fbid) == EntityType.FOUNDRY and ct.get_team(fbid) == self.my_team:
                            adjacent_foundry_dir = fd
                            break
                    safe_destroy(self, ct, adj, vc)
                    log(f"destroyed to build foundry")
                    if adjacent_foundry_dir is not None and can_build_conveyor_here(adj, adjacent_foundry_dir, ct, my_pos, self.my_team, self.map, vc=vc):
                        safe_build_conveyor(self, ct, adj, adjacent_foundry_dir)
                        log(f"upgraded axionite conveyor to splitter at {adj} -> foundry")
                    else:
                        safe_build_foundry(self, ct, adj)
                        log(f"upgraded axionite conveyor to foundry at {adj}")
                    break
                
        log_time(ct, "After checking foundry upgrades")

        prev_state = self.state
        self.state = self.decideState(ct, my_pos, vc)
        log(f"state={self.state}")
        
        log_time(ct, "After decideState")

        if self.state == State.START_HARVEST_CHAIN:
            nearest_unserviced = self.map.get_nearest_unserviced_harvester(my_pos, ct)
            if nearest_unserviced is not None:
                self.harvest_ore_pos = nearest_unserviced
            else:
                nearest_without_harvester = self.map.get_nearest_ore_without_harvester(my_pos, ct)
                if self.harvest_ore_pos is not None and my_pos.distance_squared(self.harvest_ore_pos) <= 2:
                    pass  # keep current target if we are adjacent
                else:
                    self.harvest_ore_pos = nearest_without_harvester
            ore_pos = self.harvest_ore_pos
            
            if ore_pos is None:
                log("no harvest target found on START_HARVEST_CHAIN")
                clear_state(self, )
            
            if ore_pos is not None and count_closer_allies(self, ore_pos, my_pos, vc) >= 2:
                log(f"2+ closer allies to {ore_pos} -> abandoning harvest")
                clear_state(self, )
                
            if ore_pos is not None and ct.is_in_vision(ore_pos) and self.state == State.START_HARVEST_CHAIN:
                ore_entity = self.map.get_tile_entity(ore_pos)

                # If we see a harvester on the target ore...
                if ore_entity is not None and ore_entity[1] == EntityType.HARVESTER:

                    # Abandon if there is already an adjacent ally bridge
                    if not self.map.is_unserviced_harvester(ore_pos, self.my_team):
                        log(f"ore {ore_pos} already serviced -> done")
                        clear_state(self, )

                    # Otherwise, start bridge chain from this harvester
                    else:
                        opposite_ore = self.map.ore_ti if ore_pos in self.map.ore_ax else self.map.ore_ax
                        best_build_pos = get_best_bridge_build_pos(ore_pos, self.core_pos, ct, self.my_team, self.map, vc, opposite_ore=opposite_ore)
                        if best_build_pos is None:
                            self.timeout_turns += 1
                            if self.timeout_turns >= TIMEOUT_TURNS:
                                log(f"timeout trying to build bridge from {ore_pos} -> abandoning")
                                clear_state(self, )
                                self.timeout_turns = 0
                                self.map.unreachable_harvesters.add(ore_pos)
                        else:
                            self.nav.set_destination(best_build_pos, "adjacent")
                            self.state = State.EXTEND_HARVEST_CHAIN
                            self.harvest_ore_type = ResourceType.RAW_AXIONITE if ore_pos in self.map.ore_ax else ResourceType.TITANIUM
                            self.harvest_ore_pos = ore_pos

                # If we don't see a harvester — barrier first, then build harvester
                else:
                    is_titanium_ore = self.map.get_tile_env(ore_pos) == Environment.ORE_TITANIUM
                    
                    # TODO: Add better reachability check
                    barrier_count = 0
                    for d in CARDINAL_DIRECTIONS:
                        adj = ore_pos.add(d)
                        if not on_map(adj, self.map.width, self.map.height) or not ct.is_in_vision(adj):
                            continue
                        env = self.map.get_tile_env(adj)
                        adj_bid = ct.get_tile_building_id(adj)
                        if env == Environment.WALL or adj_bid is not None and ct.get_team(adj_bid) != self.my_team and ct.get_entity_type(adj_bid) == EntityType.BARRIER:
                            barrier_count += 1
                            
                    if barrier_count == 4:                            
                        clear_state(self, )
                        self.map.unreachable_harvesters.add(ore_pos)
                        log(f"marked {ore_pos} as unreachable due to barriers")
                    
                    # Mark enemy building covering ore for destruction
                    if (ore_entity is not None and ore_entity[2] != self.my_team
                        and ore_entity[1] != EntityType.MARKER
                        and self.global_titanium >= 100
                        and (ct.is_tile_passable(ore_pos) or my_pos == ore_pos)):
                        self.attack_target = ore_pos
                        self.attack_reason = "ore covered"

                    # Compute bridge build direction to leave open
                    opposite_ore = self.map.ore_ti if ore_pos in self.map.ore_ax else self.map.ore_ax
                    bridge_pos = get_best_bridge_build_pos(ore_pos, self.core_pos, ct, self.my_team, self.map, vc, opposite_ore=opposite_ore)

                    bbid = ct.get_tile_builder_bot_id(ore_pos)
                    if (bbid is None or bbid == ct.get_id()) and ct.can_destroy(ore_pos):
                        safe_destroy(self, ct, ore_pos, vc)

                    # Find barrier targets (cardinal sides minus bridge direction) once
                    # we are close enough to act on the ore.
                    barrier_targets = []
                    if is_titanium_ore and bridge_pos is not None and my_pos.distance_squared(ore_pos) <= 2:
                        for d in CARDINAL_DIRECTIONS:
                            adj = ore_pos.add(d)
                            if adj == bridge_pos:
                                continue
                            if not on_map(adj, self.map.width, self.map.height) or not ct.is_in_vision(adj):
                                continue
                            if self.map.get_tile_env(adj) == Environment.WALL:
                                continue
                            bbid_adj = ct.get_tile_builder_bot_id(adj)
                            if bbid_adj is not None and bbid_adj != ct.get_id():
                                continue
                            bid_adj = ct.get_tile_building_id(adj)
                            if bid_adj is not None:
                                etype = ct.get_entity_type(bid_adj)
                                team = ct.get_team(bid_adj)
                                if not (etype == EntityType.MARKER or (etype == EntityType.ROAD and team == self.my_team)):
                                    continue
                            barrier_targets.append(adj)

                    # Skip barriers if we can't afford them all plus the harvester
                    barrier_cost = len(barrier_targets) * ct.get_barrier_cost()[0]
                    harvester_cost = ct.get_harvester_cost()[0]
                    if self.global_titanium < barrier_cost + harvester_cost:
                        barrier_targets = []

                    # If barriers are already settled and we can reach the bridge side,
                    # do that directly instead of staging on the ore first.
                    if (
                        my_pos.distance_squared(ore_pos) <= 2
                        and my_pos != ore_pos
                        and bridge_pos is not None
                        and not barrier_targets
                    ):
                        self.nav.set_destination(bridge_pos, "exact")

                    # Stand on ore and place barriers one per turn
                    if my_pos == ore_pos and barrier_targets:
                        target = barrier_targets[0]
                        bid_t = ct.get_tile_building_id(target)
                        if bid_t is not None and not is_marker_building(ct, bid_t) and ct.can_destroy(target):
                            safe_destroy(self, ct, target, vc)
                            log(f"START_CHAIN: destroyed at {target} for barrier")
                        elif safe_build_barrier(self, ct, target):
                            log(f"START_CHAIN: barrier at {target} (protecting {ore_pos})")

                    # All barriers placed (or none needed) — move to bridge side
                    elif my_pos == ore_pos and not barrier_targets:
                        if bridge_pos is not None:
                            # Destroy ally barrier at bridge_pos if present (free destruct)
                            bp_bid = ct.get_tile_building_id(bridge_pos)
                            bbid = ct.get_tile_builder_bot_id(bridge_pos)
                            if bp_bid is not None and ct.get_team(bp_bid) == self.my_team and ct.get_entity_type(bp_bid) == EntityType.BARRIER:
                                if ct.can_destroy(bridge_pos) and (bbid is None or bbid == ct.get_id()):
                                    safe_destroy(self, ct, bridge_pos, vc)
                                    log(f"START_CHAIN: destroyed ally barrier at {bridge_pos} to reach bridge side")
                            self.nav.set_destination(bridge_pos, "exact")
                        else:
                            # No bridge pos, just build harvester from here
                            if safe_build_harvester(self, ct, ore_pos):
                                log(f"built harvester at {ore_pos}")

                    # On the bridge side, destroy blocker and build harvester
                    elif my_pos == bridge_pos:
                        bbid = ct.get_tile_builder_bot_id(ore_pos)
                        if (ore_entity is not None
                            and ore_entity[1] not in (EntityType.HARVESTER, EntityType.MARKER)
                            and self.global_titanium >= ct.get_harvester_cost()[0]
                            and ct.can_destroy(ore_pos)
                            and (bbid is None or bbid == ct.get_id())):
                            log(f"destroyed {ore_pos} to build harvester")
                            safe_destroy(self, ct, ore_pos, vc)
                        if safe_build_harvester(self, ct, ore_pos):
                            log(f"built harvester at {ore_pos}")

                    # Navigate onto the ore tile first
                    elif self.nav.original_destination != ore_pos or self.nav.destination_type != "exact":
                        bid = ct.get_tile_building_id(ore_pos)
                        etype = ct.get_entity_type(bid) if bid is not None else None
                        if bid is None or etype in CONVEYOR_TYPES or etype == EntityType.ROAD or etype == EntityType.MARKER:
                            self.nav.set_destination(ore_pos, "exact")
                        else:
                            self.nav.set_destination(ore_pos, "adjacent")

        if self.state == State.EXTEND_HARVEST_CHAIN:
            dest = self.nav.original_destination

            # Recalculate first bridge position as we get closer and see more tiles
            if dest is not None and self.harvest_ore_pos is not None and ct.is_in_vision(self.harvest_ore_pos):
                opposite_ore = self.map.ore_ti if self.harvest_ore_type == ResourceType.RAW_AXIONITE else self.map.ore_ax
                new_pos = get_best_bridge_build_pos(self.harvest_ore_pos, self.core_pos, ct, self.my_team, self.map, vc, opposite_ore=opposite_ore)
                if new_pos is not None and new_pos != dest:
                    log(f"recalculated first bridge pos: {dest} -> {new_pos}")
                    dest = new_pos
                    self.nav.set_destination(new_pos, "adjacent")

            dest_entity = self.map.get_tile_entity(dest) if dest is not None else None

            # If we are chaining but don't have valid target, abandon
            if dest is None:
                log(f"error: no destination for harvest chain")
                clear_state(self, )

            # If we are chaining and reach the core, done
            elif is_core_tile(self.core_pos, dest):
                log(f"chain reaches core -> done")
                clear_state(self, )

            # If we reach an ally foundry, done
            elif dest_entity is not None and dest_entity[1] == EntityType.FOUNDRY and dest_entity[2] == self.my_team:
                log(f"chain reaches foundry -> done")
                clear_state(self, )

            # If axionite chain reaches a foundry-eligible position and we're adjacent, build foundry or conveyor
            elif (self.harvest_ore_type == ResourceType.RAW_AXIONITE and is_foundry_position(self.core_pos, dest)
                    and ct.is_in_vision(dest) and my_pos.distance_squared(dest) <= 2):
                builder_on_dest = ct.get_tile_builder_bot_id(dest) is not None
                if (self.global_titanium >= 1500
                    and not builder_on_dest
                    and can_build_foundry_here(dest, ct, my_pos, self.my_team, self.map, vc=vc)):
                    bid = ct.get_tile_building_id(dest)
                    bbid = ct.get_tile_builder_bot_id(dest)
                    if bid is not None and (bbid is None or bbid == ct.get_id()) and safe_destroy(self, ct, dest, vc):
                        log(f"destroyed to build foundry")
                    if safe_build_foundry(self, ct, dest):
                        log(f"BUILT foundry at {dest}")
                    clear_state(self, )
                else:
                    # Place a conveyor toward core as placeholder until we can afford foundry
                    existing_bid = ct.get_tile_building_id(dest)
                    existing_etype = ct.get_entity_type(existing_bid) if existing_bid is not None else None
                    core_dir = get_cardinal_direction_into_core(self.core_pos, dest)
                    if (existing_etype not in CONVEYOR_TYPES
                        and core_dir is not None
                        and self.global_titanium >= ct.get_conveyor_cost()[0]
                        and can_build_conveyor_here(dest, core_dir, ct, my_pos, self.my_team, self.map, vc=vc)):
                        if existing_bid is not None and safe_destroy(self, ct, dest, vc):
                            log(f"destroyed to build conveyor")
                        if safe_build_conveyor(self, ct, dest, core_dir):
                            if self.harvest_ore_type is not None:
                                self.map.tag_conveyor_resource(dest, self.harvest_ore_type)
                            log(f"placed axionite conveyor at {dest} as foundry placeholder")
                        else:
                            log(f"failed to place axionite conveyor at {dest} as foundry placeholder")
                    clear_state(self, )

            # Otherwise, keep chaining towards the core/foundry
            elif ct.is_in_vision(dest):
                build_pos = dest
                harvest_anchor = self.harvest_ore_pos
                self.harvest_ore_pos = None  # first bridge committed, stop recalculating

                built_support_launcher = False
                if USE_LAUNCHERS:
                    launcher_anchors = [build_pos]
                    if harvest_anchor is not None:
                        launcher_anchors.append(harvest_anchor)
                    built_support_launcher = try_build_support_launcher(self,
                        ct, my_pos, vc, launcher_anchors, self.core_pos, min_spacing_sq=8
                    )

                if not built_support_launcher:
                    inferred_resource = self.map.infer_chain_resource_at_output(build_pos, ct)
                    if inferred_resource is not None and inferred_resource != self.harvest_ore_type:
                        log(f"updated chain resource at {build_pos}: {self.harvest_ore_type} -> {inferred_resource}")
                        self.harvest_ore_type = inferred_resource

                    # Compute end positions based on ore type
                    end_positions = None
                    if self.harvest_ore_type == ResourceType.RAW_AXIONITE:
                        end_positions = set()
                        if self.foundry_positions is not None:
                            for p in self.foundry_positions:
                                has_titanium = (
                                    self.map.has_recent_conveyor_resource(p, ResourceType.TITANIUM)
                                    or self.map.input_chain_reaches_resource(p, ResourceType.TITANIUM)
                                )
                                if not has_titanium and ct.is_in_vision(p):
                                    p_bid = ct.get_tile_building_id(p)
                                    if p_bid is not None and ct.get_entity_type(p_bid) in CONVEYOR_TYPES:
                                        has_titanium = ct.get_stored_resource(p_bid) == ResourceType.TITANIUM
                                if not has_titanium:
                                    end_positions.add(p)
                    elif self.harvest_ore_type == ResourceType.TITANIUM and self.core_pos is not None:
                        if self.foundry_pos is not None:
                            # Rerouting titanium to a specific foundry — re-check it still needs input
                            if not self.map.is_single_input_foundry(self.foundry_pos, self.my_team):
                                log(f"foundry at {self.foundry_pos} no longer needs titanium reroute -> redirecting to core")
                                self.foundry_pos = None
                            else:
                                end_positions = {self.foundry_pos}
                        else:
                            # Normal titanium chain - check for single-input foundry
                            foundry_target = self.map.find_single_input_foundry(self.core_pos, self.my_team)
                            if foundry_target is not None:
                                end_positions = set()
                                for dx in range(-1, 2):
                                    for dy in range(-1, 2):
                                        end_positions.add(Position(self.core_pos.x + dx, self.core_pos.y + dy))
                                end_positions.add(foundry_target)

                    # Check for existing conveyor/bridge we can follow
                    existing_bid = ct.get_tile_building_id(build_pos)
                    existing_etype = ct.get_entity_type(existing_bid) if existing_bid is not None else None
                    existing_team = ct.get_team(existing_bid) if existing_bid is not None else None
                    if existing_bid is not None and existing_etype in CONVEYOR_TYPES:
                        if existing_etype == EntityType.BRIDGE:
                            next_pos = ct.get_bridge_target(existing_bid)
                        else:
                            next_pos = build_pos.add(ct.get_direction(existing_bid))
                        if existing_team == self.my_team:
                            # Check if following leads to a terminal — done immediately
                            if is_core_tile(self.core_pos, next_pos):
                                log(f"ally {existing_etype.name} at {build_pos} feeds core -> done")
                                clear_state(self, )
                            else:
                                next_entity = self.map.get_tile_entity(next_pos)
                                if next_entity is not None and next_entity[1] == EntityType.FOUNDRY and next_entity[2] == self.my_team:
                                    log(f"ally {existing_etype.name} at {build_pos} feeds foundry -> done")
                                    clear_state(self, )
                                else:
                                    self.nav.set_destination(next_pos, "adjacent")
                                    log(f"ally {existing_etype.name} at {build_pos} -> following to {next_pos}")
                        elif self.core_pos is not None and next_pos.distance_squared(self.core_pos) < build_pos.distance_squared(self.core_pos):
                            # Enemy conveyor/bridge outputting closer to our core - follow it
                            self.nav.set_destination(next_pos, "adjacent")
                            log(f"enemy {existing_etype.name} at {build_pos} outputs toward core -> following to {next_pos}")
                        elif (ct.is_tile_passable(build_pos) or my_pos == build_pos) and (len(vc.enemy_units) == 0 or not self.map.feeds_ally_turret(build_pos, self.my_team)):
                            # Enemy conveyor/bridge going away from core - fire on it to build over
                            self.attack_target = build_pos
                            self.attack_reason = "enemy conveyor blocking chain"
                            log(f"enemy {existing_etype.name} at {build_pos} blocks chain -> firing to destroy")
                        else:
                            log(f"enemy {existing_etype.name} at {build_pos} blocks chain -> abandoning")
                            clear_state(self, )

                    # Build ourselves if tile is empty or has a destroyable building
                    elif existing_bid is None or existing_etype in (EntityType.ROAD, EntityType.MARKER) or (existing_etype == EntityType.BARRIER and existing_team == self.my_team) or (existing_etype in TURRET_TYPES and existing_team == self.my_team and len(vc.enemy_units) == 0) or (existing_etype == EntityType.LAUNCHER and existing_team == self.my_team and len(vc.enemy_units) == 0):
                        chain_resource = self.harvest_ore_type
                        allow_launcher_replacement = existing_etype == EntityType.LAUNCHER and existing_team == self.my_team and len(vc.enemy_units) == 0
                        conveyor_info = self.map.get_best_conveyor_output(build_pos, self.core_pos, ct, self.my_team, end_positions=end_positions, resource=chain_resource)

                        # Prefer conveyor; only consider bridge when conveyor can't get us closer
                        if conveyor_info is None:
                            bridge_output_pos = self.map.get_best_bridge_output(build_pos, self.core_pos, ct, self.my_team, end_positions=end_positions, resource=chain_resource)
                        else:
                            bridge_output_pos = None

                        # Determine what to build and where to target
                        built = False
                        if conveyor_info is not None:
                            conv_dir, conv_target = conveyor_info
                            # Use splitter for titanium feeding into a foundry
                            target_entity = self.map.get_tile_entity(conv_target)
                            feeds_foundry = (
                                (target_entity is not None and target_entity[1] == EntityType.FOUNDRY) or
                                (end_positions is not None and conv_target in end_positions and is_foundry_position(self.core_pos, conv_target))
                            )
                            # Determine splitter facing: face away from non-bridge feeder,
                            # but the 3 non-back sides must not have conveyors/bridges of opposite resource
                            splitter_dir = None
                            if feeds_foundry:
                                chain_resource = self.harvest_ore_type
                                foundry_dir = build_pos.direction_to(conv_target)

                                def _splitter_sides_clear(candidate_dir):
                                    """Check that the 3 non-back sides have no opposite-resource conveyors/bridges."""
                                    back = candidate_dir.opposite()
                                    for sd in CARDINAL_DIRECTIONS:
                                        if sd == back:
                                            continue
                                        side_pos = build_pos.add(sd)
                                        if self.map.has_conflict(chain_resource, side_pos, ct):
                                            return False
                                    return True

                                # Try facing away from each non-bridge feeder on a non-foundry side
                                for check_d in CARDINAL_DIRECTIONS:
                                    if check_d == foundry_dir:
                                        continue
                                    adj_entity = self.map.get_tile_entity(build_pos.add(check_d))
                                    if (adj_entity is not None
                                            and adj_entity[1] in CONVEYOR_TYPES
                                            and adj_entity[1] != EntityType.BRIDGE
                                            and adj_entity[2] == self.my_team):
                                        feeder_output = self.map.get_conveyor_output(build_pos.add(check_d))
                                        if feeder_output == build_pos:
                                            candidate = check_d.opposite()
                                            if _splitter_sides_clear(candidate):
                                                splitter_dir = candidate
                                            break
                                # Fallback: use conv_dir if no feeder found, but still check sides
                                if splitter_dir is None and _splitter_sides_clear(conv_dir):
                                    splitter_dir = conv_dir
                            use_splitter = (splitter_dir is not None
                                            and self.harvest_ore_type == ResourceType.TITANIUM
                                            and can_build_splitter_here(build_pos, splitter_dir, ct, my_pos, self.my_team, self.map, vc=vc, allow_launchers=allow_launcher_replacement))
                            can_build = use_splitter or can_build_conveyor_here(build_pos, conv_dir, ct, my_pos, self.my_team, self.map, vc=vc, allow_launchers=allow_launcher_replacement)
                            if can_build:
                                if ct.get_tile_building_id(build_pos) is not None:
                                    safe_destroy(self, ct, build_pos, vc)
                                if use_splitter:
                                    if safe_build_splitter(self, ct, build_pos, splitter_dir):
                                        log(f"BUILT splitter at {build_pos} facing {splitter_dir}")
                                        built = True
                                        self.nav.set_destination(conv_target, "adjacent")
                                else:
                                    if safe_build_conveyor(self, ct, build_pos, conv_dir):
                                        log(f"BUILT conveyor at {build_pos} -> {conv_target}")
                                        built = True
                                        self.nav.set_destination(conv_target, "adjacent")

                        elif bridge_output_pos and can_build_bridge_here(build_pos, bridge_output_pos, ct, my_pos, self.my_team, self.map, vc=vc, allow_launchers=allow_launcher_replacement):
                            if ct.get_tile_building_id(build_pos) is not None:
                                safe_destroy(self, ct, build_pos, vc)
                            if safe_build_bridge(self, ct, build_pos, bridge_output_pos):
                                log(f"BUILT bridge at {build_pos} -> {bridge_output_pos}")
                                built = True
                                self.nav.set_destination(bridge_output_pos, "adjacent")

                        if built and self.harvest_ore_type is not None:
                            self.map.tag_conveyor_resource(build_pos, self.harvest_ore_type)

                        # If we didn't build but are adjacent, stand on the tile to block enemies
                        if not built and my_pos.distance_squared(build_pos) <= 2 and ct.is_tile_empty(build_pos):
                            move_dir = my_pos.direction_to(build_pos)
                            if ct.can_move(move_dir):
                                ct.move(move_dir)
                                my_pos = ct.get_position()
                                log(f"moved onto {build_pos} to block enemies")

                    # Unhandled building type blocks chain - abandon
                    else:
                        log(f"{existing_etype.name if existing_etype else 'unknown'} at {build_pos} blocks chain -> abandoning")
                        clear_state(self, )

            # If we updated destination but it turns out to be invalid, abandon
            if self.nav.original_destination is None:
                log(f"error after updating: no destination for harvest chain")
                clear_state(self, )

        if self.state == State.REROUTE_TITANIUM:
            # Verify foundry still needs titanium input
            foundry_inputs = self.map.get_conveyor_input_count(self.foundry_pos) if self.foundry_pos and self.map else 0
            if self.foundry_pos is None or foundry_inputs >= 2:
                log(f"foundry reroute no longer needed -> done")
                clear_state(self, )
            else:
                # First try the simple local reroute case: break an adjacent titanium conveyor/bridge
                # and rebuild it to face the foundry.
                ti_source = find_adjacent_foundry_reroute_source(self, ct, my_pos, self.foundry_pos)
                # Otherwise fall back to the broader "pick a titanium source and extend from there" logic.
                if ti_source is None:
                    ti_source = find_nearest_titanium_conveyor(ct, my_pos, vc, map_obj=self.map, my_team=self.my_team, target_foundry=self.foundry_pos)
                if ti_source is None:
                    ti_source = self.map.find_nearest_conveyor_with_resource(my_pos, ResourceType.TITANIUM, my_team=self.my_team, target_foundry=self.foundry_pos)
                if ti_source is not None:
                    ti_pos = ti_source
                    if my_pos.distance_squared(ti_pos) <= 2:
                        # Adjacent - destroy and start chain from here to foundry
                        if safe_destroy(self, ct, ti_pos, vc):
                            log(f"destroyed titanium conveyor at {ti_pos} for foundry reroute")
                            self.nav.set_destination(ti_pos, "adjacent")
                            self.state = State.EXTEND_HARVEST_CHAIN
                            self.harvest_ore_type = ResourceType.TITANIUM
                    else:
                        self.nav.set_destination(ti_pos, "adjacent")
                else:
                    # Walk toward nearest titanium ore to find conveyors
                    nearest_ti = self.map.get_nearest_titanium_ore(my_pos)
                    if nearest_ti is not None:
                        self.nav.set_destination(nearest_ti, "adjacent")
                    if self.nav.is_destination_reached(ct, self.map):
                        clear_state(self, )

        if self.state == State.INTERCEPT:
            enemy_core_anchor = get_enemy_core_anchor(self)
            enemy_result = get_nearest_enemy_threat_pos(vc, my_pos)
            if enemy_result is None:
                enemy_result = get_known_core_intercept_threat(self, my_pos, "intercept synthetic threat")
            if enemy_result is None:
                log("no visible or synthetic enemies to intercept -> abandoning")
                clear_state(self, )
            elif not enemy_result[1] and count_ally_turrets_covering(ct, vc, enemy_result[0]) >= 2:
                log("enough ally turrets covering threat -> abandoning intercept")
                clear_state(self, )
            else:
                # Revalidate intercept pos once per turn (skip if we just entered this state)
                if prev_state == State.INTERCEPT:
                    if self.rush_enemy_core:
                        threat_result = get_nearest_enemy_threat_pos(vc, my_pos) if vc.enemy_units else None
                    else:
                        threat_result = get_nearest_enemy_threat_pos(vc, my_pos) if should_intercept(vc, my_pos, self.core_pos) else None
                    if threat_result is None:
                        threat_result = get_known_core_intercept_threat(self,
                            self.nav.original_destination if self.nav.original_destination is not None else my_pos,
                            "recalculated intercept synthetic threat"
                        )
                    threat_pos = threat_result[0] if threat_result is not None else None
                    if threat_pos is None:
                        log("recalculated intercept: no threat -> abandoning")
                        clear_state(self, )
                    elif self.nav.original_destination is not None and is_valid_intercept_pos(
                            self.nav.original_destination, ct, self.my_team, threat_pos, my_pos,
                            map_obj=self.map, global_titanium=self.global_titanium, enemy_core_pos=enemy_core_anchor):
                        pass  # existing intercept pos still valid, keep it
                    else:
                        # Existing pos invalid, do full recalculation
                        self.state = State.EXPLORE # Clear state in the meantime to prevent getting stuck if find_intercept_pos TLEs
                        new_intercept = find_intercept_pos(ct, my_pos, self.my_team, vc, threat_pos, self.map, enemy_only=self.rush_enemy_core, global_titanium=self.global_titanium, enemy_core_pos=enemy_core_anchor)
                        if new_intercept is not None:
                            self.state = State.INTERCEPT
                            log(f"recalculated intercept: {self.nav.original_destination} -> {new_intercept}")
                            self.nav.set_destination(new_intercept, "adjacent")
                        else:
                            log("recalculated intercept: no valid pos -> abandoning")
                            clear_state(self, )
                intercept_pos = self.nav.original_destination
                log(f"intercepting at {intercept_pos}")
                # Revalidate: check input chain is still intact
                if intercept_pos is not None and ct.is_in_vision(intercept_pos):
                    # Check if position is still fed by an adjacent harvester on ore
                    # or by a valid input chain terminating at intercept_pos.
                    still_valid = (
                        self.map is not None
                        and (
                            self.map.has_adjacent_ore_harvester(intercept_pos)
                            or self.map.has_valid_input_chain(intercept_pos)
                        )
                    )
                    if not still_valid:
                        log(f"intercept at {intercept_pos}: input chain broken, abandoning")
                        clear_state(self, )
                if intercept_pos is not None and my_pos.distance_squared(intercept_pos) <= 2:
                    enemy_result = get_nearest_enemy_threat_pos(vc, my_pos)
                    if enemy_result is None:
                        enemy_result = get_known_core_intercept_threat(self, intercept_pos)
                    if enemy_result is not None:
                        log(f"threat at {enemy_result[0]} -> trying to intercept")
                        enemy_pos = enemy_result[0]
                        direction = get_sentinel_direction(intercept_pos, enemy_pos, ct, self.map)
                        if direction is not None:
                            bid = ct.get_tile_building_id(intercept_pos)
                            if bid is not None:
                                bid_team = ct.get_team(bid)
                                bid_etype = ct.get_entity_type(bid)
                                # Abort if the same allied turret we would build is already here.
                                if (bid_team == self.my_team
                                    and bid_etype in TURRET_TYPES
                                    and bid_etype == get_best_turret_type(intercept_pos, enemy_core_anchor)
                                    and ct.get_direction(bid) == direction):
                                    clear_state(self, )
                                # Skip if the building feeds one of our turrets
                                elif (self.map is not None
                                      and bid_team != self.my_team
                                      and self.map.feeds_ally_turret(intercept_pos, self.my_team)):
                                    log(f"intercept at {intercept_pos}: feeds ally turret, abandoning")
                                    clear_state(self, )
                                # Destroy enemy building if present and we can afford to kill it
                                elif (bid_team != self.my_team
                                      and bid_etype != EntityType.MARKER
                                      and (ct.is_tile_passable(intercept_pos) or my_pos == intercept_pos)):
                                    kill_cost = attack_cost_to_destroy(ct, bid)
                                    if self.global_titanium >= kill_cost:
                                        self.attack_target = intercept_pos
                                        self.attack_reason = "intercept enemy passable"
                                    else:
                                        log(f"intercept: can't afford to kill at {intercept_pos} (need {kill_cost}, have {self.global_titanium})")
                                        clear_state(self, )
                                else:
                                    bbid = ct.get_tile_builder_bot_id(intercept_pos)
                                    if (bbid is None or bbid == ct.get_id()) and ct.can_destroy(intercept_pos) and safe_destroy(self, ct, intercept_pos, vc):
                                        log("destroyed to build turret")
                                    if build_best_turret(ct, intercept_pos, direction, enemy_core_anchor):
                                        clear_state(self, )
                            else:
                                if build_best_turret(ct, intercept_pos, direction, enemy_core_anchor):
                                    clear_state(self, )


        if self.state == State.DEFEND:
            ore_pos = self.harvest_ore_pos
            log(f"defending {ore_pos}")
            if ore_pos is None:
                clear_state(self, )
            elif self.map.get_tile_env(ore_pos) == Environment.ORE_AXIONITE:
                clear_state(self, )
            elif ct.is_in_vision(ore_pos):
                ore_bid = ct.get_tile_building_id(ore_pos)
                ore_etype = ct.get_entity_type(ore_bid) if ore_bid is not None else None

                if ore_etype is None or ore_etype == EntityType.MARKER:
                    # No building on ore — place barrier directly
                    if my_pos.distance_squared(ore_pos) <= 2:
                        if ct.can_build_barrier(ore_pos):
                            ct.build_barrier(ore_pos)
                            log(f"DEFEND: barrier on bare ore at {ore_pos}")
                        clear_state(self, )

                elif ore_etype == EntityType.HARVESTER:
                    # Barrier unprotected cardinal sides, farthest from core first
                    targets = get_barrier_targets(ore_pos, self.core_pos, ct, self.map)
                    log(f"DEFEND: barrier targets for {ore_pos} are {targets}")
                    if not targets:
                        built_support_launcher = (
                            USE_LAUNCHERS
                            and try_build_support_launcher(self, ct, my_pos, vc, [ore_pos], self.core_pos, min_spacing_sq=8)
                        )
                        if not built_support_launcher:
                            log(f"DEFEND: all sides protected at {ore_pos}")
                            clear_state(self, )
                    else:
                        target = targets[0]
                        self.nav.set_destination(target, "adjacent")
                        log(f"DEFEND: navigating to {target}")
                        if my_pos.distance_squared(target) <= 2:
                            bid = ct.get_tile_building_id(target)
                            bbid = ct.get_tile_builder_bot_id(target)
                            if bid is not None and (bbid is None or bbid == ct.get_id()) and not is_marker_building(ct, bid) and ct.get_team(bid) == self.my_team:
                                safe_destroy(self, ct, target, vc)
                                log(f"DEFEND: destroyed road at {target} to build barrier")
                            if safe_build_barrier(self, ct, target):
                                log(f"DEFEND: barrier at {target} (protecting {ore_pos})")
                            # Re-check; if can't build (e.g. no resources), move on
                            remaining = get_barrier_targets(ore_pos, self.core_pos, ct, self.map)
                            if not remaining:
                                clear_state(self, )

                else:
                    # Something unexpected on ore (barrier already placed, etc.)
                    clear_state(self, )

        if (USE_LAUNCHERS
            and self.state == State.EXPLORE
            and len(vc.enemy_units) == 0
            and self.global_titanium >= max(120, ct.get_launcher_cost()[0] * 4)
            and ct.get_current_round() - self.last_support_launcher_round >= 20):
            explore_objective = self.nav.original_destination if self.nav.destination_type == "adjacent" else self.nav.destination
            try_build_support_launcher(self, ct, my_pos, vc, [my_pos], explore_objective, min_spacing_sq=20)

        if self.state == State.SABOTAGE:
            enemy_core_anchor = get_enemy_core_anchor(self)
            dest = self.nav.original_destination
            need_retarget = dest is None or dest == enemy_core_anchor
            # If targeting a specific building, check it's still valid
            if not need_retarget and dest is not None and ct.is_in_vision(dest):
                if not get_sabotage_target_priority(self, ct, dest, vc):
                    need_retarget = True
            # When close to enemy core, look for a specific building to destroy
            if need_retarget:
                sd_result = find_sabotage_target(self, ct, my_pos, vc)
                if sd_result is not None:
                    sd_target = sd_result[0]
                    log(f"sabotage: targeting enemy building at {sd_target}")
                    self.nav.set_destination(sd_target, "exact")
                elif enemy_core_anchor is None:
                    self.state = State.EXPLORE
                    self.nav.clear_destination()
                elif dest != enemy_core_anchor:
                    self.nav.set_destination(enemy_core_anchor, "exact")

            # Scan adjacent tiles for sabotage target
            if self.global_titanium < 20 or (not self.rush_enemy_core and self.attack_target is None and self.global_titanium < 100):
                clear_state(self, )
                log(f"no resources to sabotage -> abandoning")
            elif self.attack_target is None:
                for d in ALL_DIRECTIONS:
                    if d != Direction.CENTRE and not ct.can_move(d):
                        continue
                    target_pos = my_pos.add(d)
                    if not on_map(target_pos, self.map.width, self.map.height) or not ct.is_in_vision(target_pos):
                        continue
                    sabotage_priority = get_sabotage_target_priority(self, ct, target_pos, vc)
                    if sabotage_priority > 0:
                        self.attack_target = target_pos
                        self.attack_reason = "sabotage"
                        log(f"sabotage: targeting enemy building at {self.attack_target}")
                        break

        if self.rush_enemy_core and self.nav.destination is None and self.predicted_enemy_core_pos is not None:
            self.nav.set_destination(self.predicted_enemy_core_pos, "exact")
                    
        log_time(ct, "After executing state")

        # Fire on enemy buildings/roads blocking harvest chain, or destroy ally barriers in the way
        if self.attack_target is None and self.state in (State.START_HARVEST_CHAIN, State.EXTEND_HARVEST_CHAIN):
            harvest_dest = self.nav.original_destination
            if harvest_dest is not None and ct.is_in_vision(harvest_dest):
                bid = ct.get_tile_building_id(harvest_dest)
                if bid is not None:
                    bid_team = ct.get_team(bid)
                    bid_etype = ct.get_entity_type(bid)
                    if (bid_team != self.my_team
                        and not is_marker_building(ct, bid)
                        and (ct.is_tile_passable(harvest_dest) or my_pos == harvest_dest)
                        and (self.map is None or not self.map.feeds_ally_turret(harvest_dest, self.my_team))):
                        self.attack_target = harvest_dest
                        self.attack_reason = "chain blocked by enemy passable building"
                    elif bid_team == self.my_team and bid_etype == EntityType.BARRIER:
                        self.attack_target = harvest_dest
                        self.attack_reason = "ally barrier blocking chain"

        # --- Unified fire logic ---
        # Move onto target and fire if adjacent; otherwise nav will get us closer
        attacked = False
        if self.attack_target is not None and my_pos.distance_squared(self.attack_target) <= 2:
            if my_pos != self.attack_target:
                move_dir = my_pos.direction_to(self.attack_target)
                if ct.can_move(move_dir):
                    ct.move(move_dir)
                    my_pos = ct.get_position()
            if ct.can_destroy(self.attack_target):
                bbid = ct.get_tile_builder_bot_id(self.attack_target)
                if (bbid is None or bbid == ct.get_id()) and safe_destroy(self, ct, self.attack_target, vc):
                    log(f"Destroyed ally {self.attack_target} for reason: {self.attack_reason}")
                    attacked = True
            if ct.can_fire(self.attack_target):
                ct.fire(self.attack_target)
                log(f"ATTACK ({self.attack_reason}) at {self.attack_target}")
                attacked = True
                
        log_time(ct, "After attack logic")

        # Nav to destination (skip if we just attacked)
        issued_launcher_order = False
        if not attacked:
            if USE_LAUNCHERS:
                issued_launcher_order = try_issue_launcher_order(self, ct, my_pos)
            self.nav.refresh_adjacent(ct, self.map)
            log_time(ct, "After refresh adjacent")
            if issued_launcher_order:
                log_time(ct, "After launcher request")
            elif self.nav.destination is not None:
                a_star_target = self.nav.original_destination if self.nav.destination_type == "adjacent" else self.nav.destination
                self.a_star_nav.set_destination(a_star_target, self.nav.destination_type)
                pre_nav_budget = max(0, TURN_CPU_BUDGET_US - ct.get_cpu_time_elapsed() - BUGNAV_RESERVE_US)
                if pre_nav_budget > 0:
                    self.a_star_nav.advance_compute(ct, self.map, pre_nav_budget, draw=False)
                
                log_time(ct, "After possible A* compute")
                
                if not self.a_star_nav.step_if_ready(ct):
                    self.nav.go_to(ct, self.map)
                    log_time(ct, "After bugnav")
                else:
                    log_time(ct, "After A* step")
                    
                my_pos = ct.get_position()
                log(f"destination={self.nav.destination}")
            else:
                self.a_star_nav.clear_destination()

        try_heal(ct, my_pos, self.my_team, self.map.width, self.map.height)
        
        log_time(ct, "After heal")
        
        # Make sure to update important info before A* final compute
        self.prev_health = self.health
        self.prev_global_titanium = self.global_titanium
        self.prev_global_axionite = self.global_axionite

        # Place a marker encoding symmetry on the first empty adjacent tile
        if not issued_launcher_order and self.map.symmetry != Symmetry.UNKNOWN:
            marker_value = self.comms.encode_symmetry(self.map.symmetry)
            for d in DIRECTIONS:
                marker_pos = my_pos.add(d)
                if on_map(marker_pos, self.map.width, self.map.height) and safe_place_marker(self, ct, marker_pos, marker_value):
                    break
                
        log_time(ct, "After marker spam")
        
        self.nav.refresh_adjacent(ct, self.map)
        if self.nav.destination is not None:
            a_star_target = self.nav.original_destination if self.nav.destination_type == "adjacent" else self.nav.destination
            self.a_star_nav.set_destination(a_star_target, self.nav.destination_type)
            end_turn_budget = max(0, TURN_CPU_BUDGET_US - ct.get_cpu_time_elapsed() - END_TURN_RESERVE_US)
            if end_turn_budget > 0:
                self.a_star_nav.advance_compute(ct, self.map, end_turn_budget, draw=True)
        else:
            self.a_star_nav.clear_destination()
            
        log_time(ct, "After end-turn A* compute")

    def run_turret(self, ct: Controller, my_pos: Position, vc) -> None:
        if self.last_fired_round == 0:
             self.last_fired_round = ct.get_current_round()
        target = choose_target(ct, my_pos, vc)
        log("turret target:", target)
        
        if target is None:
            target = choose_passive_target(ct, my_pos, self.my_team, vc, map_obj=self.map)
            log("turret passive target:", target)
        if target is not None:
            if ct.can_fire(target):
                ct.fire(target)
                log(f"turret fired at {target}")
                self.last_fired_round = ct.get_current_round()
        
        if ct.get_current_round() - self.last_fired_round >= 20:
            if len(vc.enemy_units) > 0:
                self.last_fired_round = ct.get_current_round()
                return
            if ct.get_scale_percent() > 500:
                ct.self_destruct()

    def run_gunner(self, ct: Controller, my_pos: Position, vc) -> None:
        if self.last_fired_round == 0:
             self.last_fired_round = ct.get_current_round()

        target = choose_gunner_target(ct, my_pos, self.my_team)
        log("gunner target:", target)

        if target is not None and ct.can_fire(target):
            ct.fire(target)
            log(f"gunner fired at {target}")
            self.last_fired_round = ct.get_current_round()
        else:
            if self.global_titanium <= GameConstants.GUNNER_ROTATE_COST[0] + 50:
                current_dir = ct.get_direction()
                rotate_dir = None
                rotate_dist = INF
                for (_eid, etype, pos) in vc.enemy_units:
                    if etype not in TURRET_TYPES:
                        continue
                    dist = my_pos.distance_squared(pos)
                    if dist > 2:
                        continue
                    desired_dir = my_pos.direction_to(pos)
                    if desired_dir == current_dir:
                        continue
                    if dist < rotate_dist:
                        rotate_dist = dist
                        rotate_dir = desired_dir
                if rotate_dir is not None and ct.can_rotate(rotate_dir):
                    ct.rotate(rotate_dir)
                    log(f"gunner rotated toward adjacent enemy turret: {rotate_dir}")

        if ct.get_current_round() - self.last_fired_round >= 20:
            if len(vc.enemy_units) > 0:
                self.last_fired_round = ct.get_current_round()
                return
            if ct.get_scale_percent() > 500:
                ct.self_destruct()
                
    def run_launcher(self, ct: Controller, my_pos: Position, vc) -> None:
        self.comms.reset_turn(ct.get_current_round())
        self.map.update_vision(ct, self.comms)

        adjacent_ally_builders = []
        for d in DIRECTIONS:
            adj = my_pos.add(d)
            bid = ct.get_tile_builder_bot_id(adj)
            if bid is not None and ct.get_team(bid) == self.my_team:
                adjacent_ally_builders.append((bid, adj))

        for target, builder_id_tag, marker_pos, marker_id, _created_round in tuple(self.comms.launch_orders):
            matched = None
            for bid, bot_pos in adjacent_ally_builders:
                if (bid & LAUNCH_ORDER_ID_MASK) == builder_id_tag:
                    matched = (bid, bot_pos)
                    break

            if matched is None:
                continue

            builder_id, bot_pos = matched
            if ct.can_launch(bot_pos, target):
                ct.launch(bot_pos, target)
                self.comms.remove_launch_order(marker_id)
                if marker_pos is not None:
                    safe_place_marker(self, ct, marker_pos, 0)
                log(f"launcher executed order for {builder_id} to {target}")
                return

        attack_range = ct.get_attackable_tiles()
        
        defending_tiles = []
        ally_targets = []
        enemy_targets = []
        
        for d in DIRECTIONS:
            adj = my_pos.add(d)
            bid = ct.get_tile_builder_bot_id(adj)
            if bid is not None:
                team = ct.get_team(bid)
                if team == self.my_team:
                    ally_targets.append((adj, bid))
                else:
                    enemy_targets.append((adj, bid))

        if len(enemy_targets) == 0:
            return

        if self.core_pos is not None:
            defending_tiles.append(self.core_pos)
        else:
            for (eid, etype, pos) in vc.ally_conveyors:
                defending_tiles.append(pos)

        gunner_front_tiles = set()
        sentinel_tiles = []
        for (eid, etype, pos) in vc.ally_turrets:
            if etype == EntityType.GUNNER:
                gunner_front_tiles.add(pos.add(ct.get_direction(eid)))
            elif etype == EntityType.SENTINEL:
                sentinel_tiles.append((pos, ct.get_direction(eid)))

        best_pos = None
        best_score = None
        
        for tile in attack_range:
            if not ct.is_tile_passable(tile):
                continue
            total_dist = 0
            for defend in defending_tiles:
                dist = tile.distance_squared(defend)
                total_dist += dist

            gunner_front_bonus = 1 if tile in gunner_front_tiles else 0
            sentinel_cover_count = 0
            for sentinel_pos, sentinel_dir in sentinel_tiles:
                if tile in ct.get_attackable_tiles_from(sentinel_pos, sentinel_dir, EntityType.SENTINEL):
                    sentinel_cover_count += 1

            score = (
                total_dist,
                gunner_front_bonus,
                sentinel_cover_count,
                tile.distance_squared(my_pos),
            )
            if best_score is None or score > best_score:
                best_pos = tile
                best_score = score
        
        if best_pos is not None and ct.can_launch(enemy_targets[0][0], best_pos):
            ct.launch(enemy_targets[0][0], best_pos)
            log(f"launched at {best_pos} targeting {enemy_targets[0][0]}")
            ct.draw_indicator_dot(enemy_targets[0][0], 255, 0, 0)
            ct.draw_indicator_line(best_pos, enemy_targets[0][0], 255, 255, 0)

    def run(self, ct: Controller) -> None:
        try:
            log_time(ct, "Start")
            # Init info that depends on ct
            if not hasattr(self, 'my_id'):
                self.my_id = ct.get_id()
                self.path_color = bot_path_color(self.my_id)
                self.a_star_nav.path_color = self.path_color
            if not hasattr(self, 'map'):
                self.map = Map(ct.get_map_width(), ct.get_map_height())
                self.nav.set_statics(self.map.width, self.map.height, self.my_id)
                self.a_star_nav.set_statics(self.map.width, self.map.height, self.my_id, ct.get_team())
            if not hasattr(self, 'my_team'):
                self.my_team = ct.get_team()
            if not hasattr(self, 'etype'):
                self.etype = ct.get_entity_type()
            
            log_time(ct, "After init")
                
            # Update info that could change each turn
                
            self.health = ct.get_hp()
            if self.prev_health == 0:
                self.prev_health = self.health
                
            self.global_titanium, self.global_axionite = ct.get_global_resources()
            
            if self.prev_global_titanium == -1:
                self.prev_global_titanium = self.global_titanium
            if self.prev_global_axionite == -1:
                self.prev_global_axionite = self.global_axionite
                
            # We gain passive titanium income every 4 rounds, so ignore for inferring harvest success
            if ct.get_current_round() % 4 != 0:
                if self.global_titanium > self.prev_global_titanium:
                    self.last_global_titanium_increase = ct.get_current_round()
                if self.global_axionite > self.prev_global_axionite:
                    self.last_global_axionite_increase = ct.get_current_round()

            my_pos = ct.get_position()
            vc = self.vc
            
            vc.refresh(ct, self.my_team)
            
            if self.core_pos is None and vc.core_pos is not None:
                self.core_pos = vc.core_pos
                log("core position at", self.core_pos)
                
            log(f"pos={my_pos}")

            if self.etype == EntityType.CORE:
                self.run_core(ct, my_pos, vc)

            elif self.etype == EntityType.BUILDER_BOT:
                self.run_builder(ct, my_pos, vc)

            elif self.etype == EntityType.GUNNER:
                self.run_gunner(ct, my_pos, vc)

            elif self.etype in TURRET_TYPES:
                self.run_turret(ct, my_pos, vc)
                
            elif self.etype == EntityType.LAUNCHER:
                self.run_launcher(ct, my_pos, vc)
            
            self.prev_health = self.health
            self.prev_global_titanium = self.global_titanium
            self.prev_global_axionite = self.global_axionite

        except Exception as e:
            print(f"Error: {e} on turn {ct.get_current_round()} by {self.etype}, ID: {ct.get_id()}", file=sys.stderr)
