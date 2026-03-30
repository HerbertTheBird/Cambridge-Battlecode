import os
import numpy as np

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# Max counts for padding
MAX_TILES = 69
MAX_UNITS = 10
MAX_BUILDINGS = 20
TEAM_MAP = {"Team.A": 0, "Team.B": 1}

# Map env types and entity types to integers
ENV_TYPES = {e: i for i, e in enumerate([
    'EMPTY', 'ORE_TITANIUM', 'ORE_AXIONITE', 'WALL'
])}
ENTITY_TYPES = {e: i for i, e in enumerate([
    'BUILDER_BOT','BARRIER','HARVESTER','LAUNCHER',
    'CONVEYOR','BRIDGE','ARMORED_CONVEYER','SPLITTER','SENTINEL','GUNNER','BREACH'
])}

def parse_log_line(line):
    """Parse a single RL log line into structured observation."""
    tiles = []
    units = []
    buildings = []
    turn = 0
    unit_id = 0
    mode_name = ""
    titanium = 0

    entries = line.strip().split(" | ")
    for entry in entries:
        if entry.startswith("T="):
            parts = entry.split("|")
            turn = int(parts[0][2:])
            unit_id = int(parts[1][3:])
            mode_name = parts[2].split("=")[1]  # M=EXPLORE
            titanium = int(parts[3].split("=")[1])
        elif entry.startswith("TILE"):
            _, xy, env = entry.split("|")
            x, y = map(int, xy.split(","))
            env_idx = ENV_TYPES.get(env.split("=")[1], 0)
            tiles.append([x, y, env_idx])
        elif entry.startswith("UNIT"):
            # UNIT|TEAM=team|POS=x,y
            fields = {kv.split("=")[0]: kv.split("=")[1] for kv in entry.split("|")[1:]}
            x, y = map(int, fields['POS'].split(","))
            units.append([
                TEAM_MAP.get(fields['TEAM'], 0),  # default to 0 if unknown
                x, y,
                0, 0  # placeholder padding if needed for extra columns
            ])
        elif entry.startswith("BUILDING"):
            # BUILDING|TEAM=team|TYPE=etype|POS=x,y|HP=hp
            fields = {kv.split("=")[0]: kv.split("=")[1] for kv in entry.split("|")[1:]}
            x, y = map(int, fields['POS'].split(","))
            etype_idx = ENTITY_TYPES.get(fields['TYPE'], 0)
            hp = int(fields['HP'])
            buildings.append([
                TEAM_MAP.get(fields['TEAM'], 0),  # default to 0 if unknown
                etype_idx,
                x, y,
                hp,
                0, 0  # placeholder padding if needed for fixed width
            ])

    # Pad arrays
    # Tiles
    tiles_arr = np.zeros((MAX_TILES, 3), dtype=np.int32)
    if len(tiles) > 0:
        arr = np.array(tiles[:MAX_TILES], dtype=np.int32)
        tiles_arr[:len(arr), :arr.shape[1]] = arr

    # Units
    units_arr = np.zeros((MAX_UNITS, 5), dtype=np.int32)
    if len(units) > 0:
        arr = np.array(units[:MAX_UNITS], dtype=np.int32)
        units_arr[:len(arr), :arr.shape[1]] = arr

    # Buildings
    buildings_arr = np.zeros((MAX_BUILDINGS, 7), dtype=np.int32)
    if len(buildings) > 0:
        arr = np.array(buildings[:MAX_BUILDINGS], dtype=np.int32)
        buildings_arr[:len(arr), :arr.shape[1]] = arr

    return {
        "turn": turn,
        "unit_id": unit_id,
        "mode": mode_name,
        "titanium": titanium,
        "tiles": tiles_arr,
        "units": units_arr,
        "buildings": buildings_arr
    }


def parse_log_file(file_path):
    """Parse an entire log file into a list of observations."""
    obs_list = []
    with open(file_path, "r") as f:
        for line in f:
            obs = parse_log_line(line)
            obs_list.append(obs)
    return obs_list