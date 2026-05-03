import os
import numpy as np
from parse_logs import parse_log_file  # import your parser

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
OUTPUT_FILE = os.path.join(BASE_DIR, "dataset.npz")

# Max counts for padding
MAX_TILES = 69
MAX_UNITS = 10
MAX_BUILDINGS = 20

TEAM_MAP = {"Team.A": 0, "Team.B": 1}

def build_dataset(delete_logs=True):
    all_obs = []
    all_rewards = []

    # Find all game log files
    log_files = [f for f in os.listdir(LOGS_DIR) if f.startswith("game_") and f.endswith(".log")]

    if not log_files:
        print("No game logs found.")
        return

    # Pick the highest numbered game
    log_files.sort(key=lambda f: int(f.replace("game_", "").replace(".log", "")))
    latest_file = log_files[-1]
    file_path = os.path.join(LOGS_DIR, latest_file)

    print(f"Processing latest log: {latest_file}")
    obs_list = parse_log_file(file_path)

    # --- Reward Calculation ---
    game_rewards = []
    last_titanium = {}  # {unit_id: titanium}
    for obs in obs_list:
        reward = 0
        unit_id = obs["unit_id"]
        current_titanium = obs.get("titanium", 0)

        # Reward for titanium gain
        if unit_id in last_titanium:
            if current_titanium - last_titanium[unit_id] > 10:
                reward += 1
        last_titanium[unit_id] = current_titanium

        # Penalty for mode
        if obs["mode"] == "EXPLORE":
            reward -= 0.4
        else:
            reward -= 0.5
        game_rewards.append(reward)

    all_obs.extend(obs_list)
    all_rewards.extend(game_rewards)

    if delete_logs:
        try:
            os.remove(file_path)
            print(f"Deleted {latest_file} after parsing.")
        except Exception as e:
            print(f"Failed to delete {latest_file}: {e}")

    print(f"Total turns collected: {len(all_obs)}")

    # Convert lists to arrays
    n_turns = len(all_obs)

    tiles_arr = np.zeros((n_turns, MAX_TILES, 3), dtype=np.int32)
    units_arr = np.zeros((n_turns, MAX_UNITS, 5), dtype=np.int32)       # matches parser shape
    buildings_arr = np.zeros((n_turns, MAX_BUILDINGS, 7), dtype=np.int32) # matches parser shape
    turns = np.zeros((n_turns,), dtype=np.int32)
    unit_ids = np.zeros((n_turns,), dtype=np.int32)
    titanium = np.zeros((n_turns,), dtype=np.int32)
    modes = []
    rewards = np.array(all_rewards, dtype=np.float32)

    for i, obs in enumerate(all_obs):
        titanium[i] = obs.get("titanium", 0)  # match parser key
        tiles_arr[i] = obs["tiles"]
        units_arr[i] = obs["units"]
        buildings_arr[i] = obs["buildings"]
        turns[i] = obs["turn"]
        unit_ids[i] = obs["unit_id"]
        modes.append(obs["mode"])

    # Save everything
    np.savez_compressed(
        OUTPUT_FILE,
        tiles=tiles_arr,
        units=units_arr,
        buildings=buildings_arr,
        turns=turns,
        unit_ids=unit_ids,
        modes=np.array(modes),
        titanium=titanium,
        rewards=rewards
    )
    print(f"Dataset saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    build_dataset(delete_logs=True)