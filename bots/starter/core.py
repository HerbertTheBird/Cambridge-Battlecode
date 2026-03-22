from cambc import Controller, Position
import random
class Core:
    def random_spawn_tile(self) -> Position | None:
        rc = self.rc
        core_pos = rc.get_position()
        candidates = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                p = Position(core_pos.x + dx, core_pos.y + dy)
                candidates.append(p)

        random.shuffle(candidates)

        for p in candidates:
            if rc.can_spawn(p):
                return p

        return None
    def run(self):
        rc = self.rc
        if self.num_spawned < 6:
                spawn_pos = self.random_spawn_tile()
                if spawn_pos is not None:
                    self.rc.spawn_builder(spawn_pos)
                    self.num_spawned += 1
    def __init__(self, c: Controller):
        self.rc = c
        self.num_spawned = 0