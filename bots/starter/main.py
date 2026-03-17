# main.py

import random
import sys
import time

from cambc import Controller, Direction, EntityType, Position, Environment
from map_info import MapInfo
from pathing import bfs_best_move


CARDINALS = [
    Direction.NORTH,
    Direction.SOUTH,
    Direction.WEST,
    Direction.EAST,
]

# file = open("time.txt", "a")
class Player:
    def __init__(self):
        self.num_spawned = 0

        self.map_info = None

        self.target = None
        self.stuck_turns = 0
        self.seeded = False

    # ------------------------------------------------------------------
    # Basic helpers
    # ------------------------------------------------------------------

    def dist_sq(self, a: Position, b: Position) -> int:
        dx = a.x - b.x
        dy = a.y - b.y
        return dx * dx + dy * dy

    def choose_random_edge(self, c: Controller) -> Position:
        w = c.get_map_width()
        h = c.get_map_height()

        side = random.randint(0, 3)

        if side == 0:
            return Position(random.randint(0, w - 1), 0)
        if side == 1:
            return Position(random.randint(0, w - 1), h - 1)
        if side == 2:
            return Position(0, random.randint(0, h - 1))
        return Position(w - 1, random.randint(0, h - 1))

    def in_bounds(self, c: Controller, pos: Position) -> bool:
        return 0 <= pos.x < c.get_map_width() and 0 <= pos.y < c.get_map_height()

    def visible_unharvested_ores(self, c: Controller) -> list[Position]:
        ores = []

        for pos in c.get_nearby_tiles():
            if c.get_tile_env(pos) != Environment.ORE_TITANIUM and c.get_tile_env(pos) != Environment.ORE_AXIONITE:
                continue

            building_id = c.get_tile_building_id(pos)
            if building_id is not None and c.get_entity_type(building_id) == EntityType.HARVESTER:
                continue

            ores.append(pos)

        return ores

    def nearest_visible_unharvested_ore(self, c: Controller) -> Position | None:
        me = c.get_position()
        best = None
        best_d = 10**9

        for ore in self.visible_unharvested_ores(c):
            d = self.dist_sq(me, ore)
            if d < best_d:
                best_d = d
                best = ore

        return best


    def choose_new_target(self, c: Controller) -> Position:
        ore = self.nearest_visible_unharvested_ore(c)
        if ore is not None:
            return ore
        return self.choose_random_edge(c)

    # ------------------------------------------------------------------
    # Building helpers
    # ------------------------------------------------------------------

    def try_build_nearby_harvester(self, c: Controller) -> bool:
        """
        Build a harvester on any adjacent ore tile that does not already have one.
        """
        me = c.get_position()

        for d in CARDINALS:
            ore_pos = me.add(d)
            if c.can_build_harvester(ore_pos):
                c.build_harvester(ore_pos)
                return True
        return False

    def try_build_conveyor_toward_step(self, c: Controller, move_dir: Direction, avoid: set[Position]) -> bool:
        """
        Match your earlier pattern:
        - if the intended next tile is blocked for movement
        - and it's not in avoid
        - try to build a conveyor there facing back toward the bot
        """
        next_pos = c.get_position().add(move_dir)
        if next_pos == self.target:
            return False
        if next_pos in avoid:
            return False

        if c.can_move(move_dir):
            return False

        if c.can_build_conveyor(next_pos, move_dir.opposite()):
            c.build_conveyor(next_pos, move_dir.opposite())
            return True

        return False

    # ------------------------------------------------------------------
    # Spawn helpers
    # ------------------------------------------------------------------

    def random_spawn_tile(self, c: Controller) -> Position | None:
        core_pos = c.get_position()
        candidates = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                p = Position(core_pos.x + dx, core_pos.y + dy)
                candidates.append(p)

        random.shuffle(candidates)

        for p in candidates:
            if c.can_spawn(p):
                return p

        return None

    # ------------------------------------------------------------------
    # Target / movement logic
    # ------------------------------------------------------------------

    def update_target_logic(self, c: Controller) -> None:
        me = c.get_position()

        visible_ore = self.nearest_visible_unharvested_ore(c)
        if visible_ore is not None:
            self.target = visible_ore

        need_new_target = False

        if self.target is None:
            need_new_target = True
        else:
            # If ore got harvested, target is done
            if self.dist_sq(self.target, me) <= 2:
                need_new_target = True
            else:
                cur_dist = self.dist_sq(me, self.target)


        if need_new_target:
            self.target = self.choose_new_target(c)
            self.stuck_turns = 0

    def do_builder_turn(self, c: Controller) -> None:
        if self.map_info is None:
            self.map_info = MapInfo(c)

        self.map_info.update(c)

        # Always try to harvest first if we can
        if self.try_build_nearby_harvester(c):
            return

        self.update_target_logic(c)
        if self.target is None:
            return

        avoid = self.map_info.get_avoid()
        for pos in avoid:
            c.draw_indicator_dot(pos, 255, 0, 0)
        # Don't avoid the actual ore target itself; we want to walk adjacent to it.
        if self.target in avoid:
            avoid.remove(self.target)
        move_dir = bfs_best_move(c, self.target, avoid)

        if move_dir is None:
            self.stuck_turns += 1
            if self.stuck_turns >= 6:
                self.target = self.choose_new_target(c)
                self.stuck_turns = 0
            return

        # Conveyor attempt first, same general style as your old code
        built = self.try_build_conveyor_toward_step(c, move_dir, avoid)


        if c.can_move(move_dir):
            c.move(move_dir)
            return

        self.stuck_turns += 1
        if self.stuck_turns >= 6:
            self.target = self.choose_new_target(c)
            self.stuck_turns = 0

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def run(self, c: Controller) -> None:
        # start_time = time.perf_counter()
        if not self.seeded:
            random.seed(c.get_current_round())
            self.seeded = True
        etype = c.get_entity_type()
        if self.target is not None:
            c.draw_indicator_line(c.get_position(), self.target, 0, 255, 0)
        if etype == EntityType.CORE:
            if self.num_spawned < 6:
                spawn_pos = self.random_spawn_tile(c)
                if spawn_pos is not None:
                    c.spawn_builder(spawn_pos)
                    self.num_spawned += 1
            return

        if etype == EntityType.BUILDER_BOT:
            self.do_builder_turn(c)
            # end_time = time.perf_counter()
            # elapsed_ms = (end_time - start_time) * 1000
            # print(elapsed_ms, file=file)
            return