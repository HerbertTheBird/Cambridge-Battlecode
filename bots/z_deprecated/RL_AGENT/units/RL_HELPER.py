import math

TEAM_MAP = {
    "Team.A": 0,
    "Team.B": 1
}

def relu(xs):
    n = len(xs)
    for i in range(n):
        xs[i] = xs[i] if xs[i] > 0 else 0.0
    return xs

def matvec_dot(W, x, b):
    """y = W*x + b (fast, pure Python)"""
    n_rows = len(W)
    n_cols = len(W[0])
    y = [0.0] * n_rows
    for i in range(n_rows):
        s = b[i]
        row = W[i]
        for j in range(n_cols):
            s += row[j] * x[j]
        y[i] = s
    return y

def forward(x, weights):
    h1 = matvec_dot(weights["fc1_w"], x, weights["fc1_b"])
    relu(h1)

    h2 = matvec_dot(weights["fc2_w"], h1, weights["fc2_b"])
    relu(h2)

    out = matvec_dot(weights["fc3_w"], h2, weights["fc3_b"])

    # argmax manually to avoid extra list ops
    max_val = out[0]
    max_idx = 0
    for i in range(1, len(out)):
        if out[i] > max_val:
            max_val = out[i]
            max_idx = i
    return max_idx


def parse_log_line_py(line, max_tiles=69, max_units=10, max_buildings=30):
    """ convert one log line from inference_state() into an observation dict """
    tiles = [[0,0,0] for _ in range(max_tiles)]
    units = [[0,0] for _ in range(max_units)]
    buildings = [[0,0,0,0] for _ in range(max_buildings)]
    
    turn = 0
    unit_id = 0
    mode_name = ""
    
    ENV_TYPES = {'PLAIN':0,'ORE_TITANIUM':1,'ORE_SILVER':2,'WATER':3,'FOREST':4}
    ENTITY_TYPES = {'BUILDER_BOT':0,'BARRIER':1,'HARVESTER':2,'LAUNCHER':3,
                    'CONVEYOR':4,'BRIDGE':4,'ARMORED_CONVEYER':4,'SPLITTER':4,'SENTINEL':5,'GUNNER':5,'BREACH':5}
    
    t_idx = 0
    u_idx = 0
    b_idx = 0

    entries = line.strip().split(" | ")
    for entry in entries:
        if entry.startswith("T="):
            parts = entry.split("|")
            turn = int(parts[0][2:])
            unit_id = int(parts[1][3:])
            mode_name = parts[2][2:]
        elif entry.startswith("TILE"):
            _, xy, env = entry.split("|")
            x, y = map(int, xy.split(","))
            env_idx = ENV_TYPES.get(env.split("=")[1], 0)
            if t_idx < max_tiles:
                tiles[t_idx] = [x, y, env_idx]
                t_idx += 1
        elif entry.startswith("UNIT"):
            fields = {kv.split("=")[0]: kv.split("=")[1] for kv in entry.split("|")[1:]}
            x, y = map(int, fields['POS'].split(","))
            if u_idx < max_units:
                units[u_idx] = [
                    TEAM_MAP.get(fields['TEAM'], 0),
                    x * 200 + y
                ]
                u_idx += 1
        elif entry.startswith("BUILDING"):
            fields = {kv.split("=")[0]: kv.split("=")[1] for kv in entry.split("|")[1:]}
            x, y = map(int, fields['POS'].split(","))
            if b_idx < max_buildings:
                buildings[b_idx] = [
                    TEAM_MAP.get(fields['TEAM'], 0),
                    ENTITY_TYPES.get(fields['TYPE'], 0),
                    x * 200 + y,
                    int(fields['HP']),
                ]
                b_idx += 1

    return {
        "turn": turn,
        "unit_id": unit_id,
        "mode": mode_name,
        "tiles": tiles,
        "units": units,
        "buildings": buildings
    }
    
def flatten_observation_py(obs):
    """
    Pure Python flatten function for RL inference.
    """
    flat_list = []
    for tile in obs["tiles"]:
        flat_list.extend(tile)
    for unit in obs["units"]:
        flat_list.extend(unit)
    for building in obs["buildings"]:
        flat_list.extend(building)
    return flat_list