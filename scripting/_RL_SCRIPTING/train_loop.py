import time
import subprocess
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RL_SCRIPTING_DIR = os.path.join(BASE_DIR, "_RL_SCRIPTING")

def run_script(script_name):
    script_path = os.path.join(RL_SCRIPTING_DIR, script_name)
    print(f"--- Running {script_name} ---")
    try:
        subprocess.run(["python", script_path], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running {script_name}: {e}")
        # Decide if you want to stop the loop on error
        # return False 
    return True

def main():
    loop_count = 0
    while True:
        loop_count += 1
        print(f"===== Starting RL Loop Iteration: {loop_count} =====")

        # 1. Run games to generate data
        if not run_script("queue_games.py"):
            break

        # 2. Parse logs and build dataset
        if not run_script("build_dataset.py"):
            break

        # 3. Train the policy
        if not run_script("train_policy.py"):
            break

        print(f"===== Iteration {loop_count} complete. Waiting... =====")
        # Sleep for a bit to avoid overwhelming the system
        time.sleep(10)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nTraining loop stopped by user.")
