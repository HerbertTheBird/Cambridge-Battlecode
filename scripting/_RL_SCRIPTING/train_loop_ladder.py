import subprocess
import random
import re
import time

# Define vowels + y
VOWELS = ['a', 'e', 'i', 'o', 'u', 'y']

# Pool of bots
BOTS_POOL = ['RL_AGENT_LADDER', 'RL_TARGET', 'rush', 'starter', 'Artemis_v0_1']

# Minimum rating threshold
MIN_RATING = 1700

BLUE = "\033[34m"
RESET = "\033[0m"
counter = 0

def run_command(cmd):
    """Run a shell command, print it in blue, and return combined stdout+stderr."""
    print(f"Running command: {BLUE}{cmd}{RESET}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    output = result.stdout + result.stderr
    output = output.strip()
    return output

def parse_team_ids(output):
    """Parse team IDs from cambc search output with rating >= MIN_RATING."""
    team_ids = []
    for line in output.splitlines():
        # Match lines like: │ <uuid> │ ... │   <rating> │
        match = re.search(
            r'│\s*([0-9a-f-]{36})\s*│.*?│\s*(\d+)\s*│', 
            line
        )
        if match:
            team_id = match.group(1)
            rating = int(match.group(2))
            if rating >= MIN_RATING:
                team_ids.append(team_id)
    return team_ids

def biased_reward():
    """Return a float between -0.4 and 20, biased so >80% are below 0."""
    if random.random() < 0.8:
        return round(random.uniform(-0.4, 0), 3)
    else:
        return round(random.uniform(0.01, 20), 3)

def fake_rl_training(iterations=5):
    global counter
    """Deterministic fake RL output for each iteration."""
    time.sleep(random.uniform(1, 5))
    for i in range(1, iterations + 1):
        counter += 1
        print(f"Processing iteration {counter}...")
        time.sleep(random.uniform(0.2, 0.4))
        print(f"Iteration {counter} complete{RESET}")
        time.sleep(random.uniform(0.1, 0.2))
        print(f"Processing replay...{RESET}")
        time.sleep(random.uniform(0.1, 0.3))
        game_num = random.randint(0, 15)
        print(f"Replay processed and stored at game_{game_num}.log{RESET}")
        total_turns = random.randint(500, 2000)
        print(f"Total turns collected: {total_turns}{RESET}")
        avg_reward = biased_reward()
        print(f"Average reward: {BLUE}{avg_reward}{RESET}\n")
        time.sleep(random.uniform(0.3, 0.6))

def main():
    eligible_team_ids = []

    # Search for teams using vowels + y
    for vowel in VOWELS:
        print(f"Searching for teams with '{vowel}'...{RESET}")
        output = run_command(f"cambc team search {vowel}")

        # Print the raw table output in blue
        # print(f"OUTPUT: {output}{RESET}")

        ids = parse_team_ids(output)
        print(f"Found {len(ids)} eligible teams.{RESET}")
        eligible_team_ids.extend(ids)

    print()
    if not eligible_team_ids:
        print(f"{BLUE}No eligible teams found.{RESET}")
        return
    while True:
        
        fake_rl_training(iterations=5)

        # Pick random team and bot
        chosen_team = random.choice(eligible_team_ids)
        chosen_bot = random.choice(BOTS_POOL)

        print(f"{BLUE}Chosen team: {chosen_team}{RESET}")
        print(f"{BLUE}Chosen bot: {chosen_bot}{RESET}\n")

        # Run the sequence of cambc commands
        try:
            # 1. Submit bot1 replacement
            subprocess.run(f"cambc submit bots/{chosen_bot}", shell=True)
            # 2. Unrated match against chosen team
            subprocess.run(f"cambc match unrated {chosen_team}", shell=True)

        finally:
            # Always submit Artemis_v0_2 at the end
            subprocess.run("cambc submit bots/Artemis_v0_2", shell=True)
            print(f"{BLUE}Done.{RESET}")

if __name__ == "__main__":
    main()