import os
import random
import subprocess

# === CONFIG ===
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAPS_DIR = os.path.join(BASE_DIR, "maps")
BOTS_DIR = os.path.join(BASE_DIR, "bots")
RL_AGENT = os.path.join(BOTS_DIR, "RL_AGENT")    # your RL bot
RL_TARGET = os.path.join(BOTS_DIR, "Artemis_v0_1")  # expert bot to imitate
NUM_GAMES = 1
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# === Get all maps ===
all_maps = [f for f in os.listdir(MAPS_DIR) if f.endswith(".map26")]

# === Queue and run games locally ===
for i in range(NUM_GAMES):
    game_map = random.choice(all_maps)
    seed = random.randint(1, 10000)

    print(f"Running game {i} on map {game_map} with seed {seed}...")

    # Command to run RL_AGENT vs RL_TARGET
    cmd = [
        "cambc", "run",
        RL_AGENT,
        RL_TARGET,
        os.path.join(MAPS_DIR, game_map),
        "--seed", str(seed)
    ]

    # Save output to log file for replay scraping
    log_file = os.path.join(LOGS_DIR, f"game_{i}.log")
    with open(log_file, "w") as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)

    print(f"Game {i} completed, log saved to {log_file}")