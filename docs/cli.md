> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# CLI reference

> Every command available in the cambc CLI.

The `cambc` CLI is your main tool for local development, testing, and interacting with the platform. Install it with `pip install cambc`.

## Project setup

### `cambc starter`

Scaffold a new Cambridge Battlecode project. Run this first after installing.

```bash  theme={"dark"}
cambc starter
```

Creates the following project structure:

```
your-project/
├── cambc.toml          # Project configuration
├── .gitignore          # Ignores replays, __pycache__, venvs
├── bots/
│   └── starter/
│       └── main.py     # Starter bot (optional, prompted)
└── maps/               # Custom maps (optional, prompted)
```

The starter bot demonstrates core gameplay: the core spawns builder bots, builders explore by laying roads, and when they find ore they build harvesters on it.

### `cambc.toml`

The config file created by `cambc starter`. All fields have defaults and all CLI options override config values.

```toml  theme={"dark"}
bots_dir = "bots"           # Where to find bots
maps_dir = "maps"           # Where to find maps
replay = "replay.replay26"  # Default replay output path
seed = 1                    # Default map seed
```

Bot paths in `cambc run` are resolved by first checking the raw path, then checking inside `bots_dir`. So `cambc run starter opponent` resolves to `bots/starter` and `bots/opponent`.

## Local development

### `cambc run`

Run a local match between two bots. No time limits are enforced locally.

```bash  theme={"dark"}
cambc run <bot_a> <bot_b> [map]
```

**Arguments:**

| Argument | Description                                                                         |
| -------- | ----------------------------------------------------------------------------------- |
| `bot_a`  | First bot — a directory containing `main.py`, a `.py` file, or a name in `bots_dir` |
| `bot_b`  | Second bot — same formats as `bot_a`                                                |
| `map`    | Optional `.map26` map file. If omitted, uses the first map in `maps_dir`            |

**Options:**

| Option          | Description                                              |
| --------------- | -------------------------------------------------------- |
| `--replay PATH` | Output replay file path (overrides `cambc.toml` default) |
| `--seed N`      | Map seed (overrides `cambc.toml` default)                |
| `--watch`       | Open the visualiser automatically after the match        |

```bash  theme={"dark"}
cambc run starter starter                           # bot vs itself
cambc run my_bot opponent --seed 42                 # fixed seed
cambc run my_bot opponent maps/custom.map26         # custom map
cambc run --watch my_bot opponent                   # run + auto-open visualiser
cambc run my_bot opponent --replay out.replay26     # custom replay path
```

After the match completes, `cambc run` prints a summary showing the winner, win condition, turn count, and a resource breakdown (titanium, axionite, units, and buildings) for each team.

### `cambc watch`

View a replay in the browser-based visualiser.

```bash  theme={"dark"}
cambc watch [replay_file]
cambc watch --match <match_id> [--game <n>]
```

**Local replay:** Serves the visualiser on `localhost` and opens your browser. Press `Ctrl+C` to stop the server.

```bash  theme={"dark"}
cambc watch replay.replay26
```

**Platform match:** Opens the platform visualiser in your browser for a specific match.

```bash  theme={"dark"}
cambc watch --match abc123          # opens match on platform
cambc watch --match abc123 --game 3 # specific game within the match
```

### `cambc map-editor`

Open the map editor to create custom `.map26` files.

```bash  theme={"dark"}
cambc map-editor              # local map editor
cambc map-editor --platform   # open map editor on the platform
```

## Platform commands

These commands interact with the online platform at [game.battlecode.cam](https://game.battlecode.cam). Most require authentication via `cambc login`.

### `cambc login`

Authenticate with the platform. Opens a browser window for OAuth login and stores your session locally.

```bash  theme={"dark"}
cambc login
```

The session persists across CLI invocations until it expires or you run `cambc logout`.

### `cambc logout`

Clear stored credentials.

```bash  theme={"dark"}
cambc logout
```

### `cambc submit`

Upload a bot to compete on the ladder.

```bash  theme={"dark"}
cambc submit <path>
```

The path can be a directory containing `main.py`, a single `.py` file, or a `.zip`. Directories are auto-zipped before upload. See [submission requirements](/getting-started/submitting#bot-requirements) for constraints.

```bash  theme={"dark"}
cambc submit ./my_bot/       # directory (auto-zipped)
cambc submit my_bot.py       # single file
cambc submit my_bot.zip      # pre-zipped
```

### `cambc status`

Show your current team, rating, rank, and member list.

```bash  theme={"dark"}
cambc status
```

Displays your username, team name, category, Elo rating, matches played, and team members with roles.

### `cambc unrated`

Challenge another team to an unrated match using both teams' latest submissions.

```bash  theme={"dark"}
cambc unrated <opponent_team_id>
cambc unrated <opponent_team_id> --match <source_match_id>
```

| Option       | Description                                                                              |
| ------------ | ---------------------------------------------------------------------------------------- |
| `--match ID` | Use the opponent's submission version from a specific past match instead of their latest |

Unrated matches run on the same AWS infrastructure as ladder matches with full time limit enforcement but do not affect ratings. They are prioritised over ladder matches for faster results.

<Warning>
  Rate limits apply: max 10 test/unrated matches per 5 minutes, plus a 5-minute cooldown per matchup.
</Warning>

### `cambc test-run`

Upload two local bots and run a remote match with full time limit enforcement on AWS Graviton3 hardware.

```bash  theme={"dark"}
cambc test-run <bot_a> <bot_b> [map]
```

Both bots are packaged and uploaded to the server. Unlike `cambc run`, this enforces the 2ms CPU time limit per round — use this to check your bot's performance before submitting.

```bash  theme={"dark"}
cambc test-run my_bot opponent                    # test two bots remotely
cambc test-run my_bot opponent maps/custom.map26  # with a custom map
```

### `cambc matches`

View recent match history.

```bash  theme={"dark"}
cambc matches [options]
```

| Option                     | Description                                     |
| -------------------------- | ----------------------------------------------- |
| `--type {ladder\|unrated}` | Filter by match type                            |
| `--team NAME`              | Filter by team name (substring match)           |
| `--limit N`                | Number of matches to show (default 20, max 100) |
| `--cursor CURSOR`          | Pagination cursor from previous results         |

### `cambc match`

View details of a specific match including per-game results.

```bash  theme={"dark"}
cambc match <match_id>
```

Shows match status, teams, score, rating delta, timestamps, and a table of individual games with map, winner, win condition, and turns played.

### `cambc test-matches`

View your remote test run history.

```bash  theme={"dark"}
cambc test-matches [--limit N]
```

### `cambc teams`

Search for teams or view team details.

```bash  theme={"dark"}
cambc teams search <query>    # search by name
cambc teams info <team_id>    # view team details
```


Built with [Mintlify](https://mintlify.com).