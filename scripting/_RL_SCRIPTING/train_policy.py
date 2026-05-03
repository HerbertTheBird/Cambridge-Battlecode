import os
import numpy as np

# --- CONFIG ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATASET_FILE = os.path.join(BASE_DIR, "dataset.npz")
WEIGHTS_FILE = os.path.join(BASE_DIR, "bots", "RL_AGENT", "weights.py")
LEARNING_RATE = 0.001
MODES = [
    'EXPLORE',
    'HEAL',
    'BUILD_HARVESTER',
    'ROUTE',
    'SABOTAGE',
    'BUILD_TRAP',
    'HEAL_CORE'
]
NUM_MODES = len(MODES)

# --- NEURAL NETWORK HELPERS ---
def relu(x):
    return np.maximum(0, x)

def softmax(x):
    e_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e_x / e_x.sum(axis=-1, keepdims=True)

def load_weights():
    # This is a hacky way to load python-formatted weights.
    # In a real scenario, you'd use a proper format like JSON or pickle.
    with open(WEIGHTS_FILE, 'r') as f:
        content = f.read()
        # WARNING: This is not safe for untrusted code
        weights_dict = eval(content.split("=")[1], {"nan": np.nan})
        for k, v in weights_dict.items():
            weights_dict[k] = np.array(v)
        return weights_dict

def save_weights(weights):
    with open(WEIGHTS_FILE, "w") as f:
        f.write("WEIGHTS = {\n")
        for k, v in weights.items():
            f.write(f"    '{k}': {repr(v.tolist())},\n")
        f.write("}\n")

# --- TRAINING ---
def train():
    if not os.path.exists(DATASET_FILE):
        print(f"Dataset not found at {DATASET_FILE}. Skipping training.")
        return

    # Load data
    data = np.load(DATASET_FILE)
    tiles = data['tiles'].reshape(data['tiles'].shape[0], -1)
    units = data['units'].reshape(data['units'].shape[0], -1)
    buildings = data['buildings'].reshape(data['buildings'].shape[0], -1)
    
    # Concatenate all features to form the input vector
    X = np.concatenate([tiles, units, buildings], axis=1)
    
    rewards = data['rewards']
    modes = data['modes']
    
    # Load current weights
    weights = load_weights()

    # --- Simple Policy Gradient Update (REINFORCE) ---
    for i in range(X.shape[0]):
        # Forward pass
        x = X[i]
        x = np.nan_to_num(x, nan=0.0)  # <-- replace NaN with 0

        h1 = relu(np.dot(weights['fc1_w'], x) + weights['fc1_b'])
        h2 = relu(np.dot(weights['fc2_w'], h1) + weights['fc2_b'])
        logits = np.dot(weights['fc3_w'], h2) + weights['fc3_b']
        probs = softmax(logits)

        # Get action and reward
        mode_str = modes[i]
        try:
            action = MODES.index(mode_str)
        except ValueError:
            continue # Skip if mode is not recognized
            
        reward = rewards[i]
        reward = np.clip(reward, -10, 10)

        # Compute gradient
        d_logits = probs.copy()
        d_logits[action] -= 1

        # Backpropagation
        d_fc3_w = np.outer(d_logits, h2)
        d_fc3_b = d_logits

        d_h2 = np.dot(weights['fc3_w'].T, d_logits)
        d_h2[h2 <= 0] = 0 # backprop through relu

        d_fc2_w = np.outer(d_h2, h1)
        d_fc2_b = d_h2
        
        d_h1 = np.dot(weights['fc2_w'].T, d_h2)
        d_h1[h1 <= 0] = 0 # backprop through relu

        d_fc1_w = np.outer(d_h1, x)
        d_fc1_b = d_h1

        # Clip gradients to prevent explosion
        for grad in [d_fc1_w, d_fc1_b, d_fc2_w, d_fc2_b, d_fc3_w, d_fc3_b]:
            np.clip(grad, -1e3, 1e3, out=grad)

        # Update weights
        weights['fc1_w'] -= LEARNING_RATE * reward * d_fc1_w
        weights['fc1_b'] -= LEARNING_RATE * reward * d_fc1_b
        weights['fc2_w'] -= LEARNING_RATE * reward * d_fc2_w
        weights['fc2_b'] -= LEARNING_RATE * reward * d_fc2_b
        weights['fc3_w'] -= LEARNING_RATE * reward * d_fc3_w
        weights['fc3_b'] -= LEARNING_RATE * reward * d_fc3_b

        # Detect any NaNs in weights (optional debug)
        for name, w in weights.items():
            if np.isnan(w).any():
                print(f"Warning: NaN detected in {name} at iteration {i}")
                weights[name] = np.nan_to_num(w, nan=0.0)

    # Save updated weights
    save_weights(weights)
    print(f"Training complete. Average reward: {np.mean(rewards):.4f}. Weights updated.")

if __name__ == "__main__":
    train()
