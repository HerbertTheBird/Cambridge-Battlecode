import random
import os

# --- sizes ---
MAX_TILES = 69
MAX_UNITS = 10
MAX_BUILDINGS = 20
input_size = MAX_TILES*3 + MAX_UNITS*5 + MAX_BUILDINGS*7

hidden1_size = 64
hidden2_size = 32
num_modes = 7  # your Mode enum has 7 entries

# --- helper functions ---
def random_matrix(rows, cols):
    return [[round(random.uniform(-0.1, 0.1), 5) for _ in range(cols)] for _ in range(rows)]

def random_vector(size):
    return [round(random.uniform(-0.1, 0.1), 5) for _ in range(size)]

# --- create weights dict ---
WEIGHTS = {
    "fc1_w": random_matrix(hidden1_size, input_size),
    "fc1_b": random_vector(hidden1_size),
    "fc2_w": random_matrix(hidden2_size, hidden1_size),
    "fc2_b": random_vector(hidden2_size),
    "fc3_w": random_matrix(num_modes, hidden2_size),
    "fc3_b": random_vector(num_modes)
}

# --- determine base directory ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  # base project dir
bot_dir = os.path.join(BASE_DIR, "bots", "RL_AGENT")
os.makedirs(bot_dir, exist_ok=True)
weights_file = os.path.join(bot_dir, "weights.py")

# --- write the file ---
with open(weights_file, "w") as f:
    f.write("WEIGHTS = {\n")
    for k, v in WEIGHTS.items():
        f.write(f"    {repr(k)}: {repr(v)},\n")
    f.write("}\n")

print(f"Dummy weights written to {weights_file}")