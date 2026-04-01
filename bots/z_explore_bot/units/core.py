from cambc import Controller, Position
import comms
import random
rc = None
num_spawned = 0
def random_spawn_tile() -> Position | None:
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
def run():
    # comms.communicate(rc)
    global num_spawned
    if num_spawned < 2 or rc.get_global_resources()[0] > 1000 + 50*rc.get_scale_percent():
            spawn_pos = random_spawn_tile()
            if spawn_pos is not None:
                rc.spawn_builder(spawn_pos)
                num_spawned += 1
def init(c: Controller):
    global rc, num_spawned
    comms.init()
    rc = c
    num_spawned = 0