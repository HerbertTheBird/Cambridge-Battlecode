> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Running Matches

> Run local matches with the CLI and view replays in the visualiser.

## Run a local match

```bash  theme={"dark"}
cambc run <bot_a> <bot_b> [map]
```

This runs the full game engine locally with **no time limits** — ideal for rapid iteration. The engine outputs a `replay.replay26` file.

Bot paths can be a directory containing `main.py`, a `.py` file, or a bot name from your `bots_dir` (set in `cambc.toml`). The optional map argument is a `.map26` file — if omitted, the first map in your `maps_dir` is used.

```bash  theme={"dark"}
cambc run starter starter                        # bot vs itself
cambc run my_bot opponent --seed 42              # deterministic seed
cambc run my_bot opponent maps/custom.map26      # custom map
cambc run my_bot opponent --replay out.replay26  # custom replay path
```

## View a replay

```bash  theme={"dark"}
cambc watch replay.replay26
```

Opens the visualiser in your browser. Supports play/pause, round scrubbing, speed control, and keyboard navigation.

### Run + watch in one command

```bash  theme={"dark"}
cambc run --watch starter starter
```

### View a platform match

```bash  theme={"dark"}
cambc watch --match <match_id>
cambc watch --match <match_id> --game 3
```

## Remote test runs

Remote commands require authentication — run `cambc login` first if you haven't already.

Test your bots on the **same hardware** that runs ladder matches, with full time limit enforcement:

```bash  theme={"dark"}
cambc test-run <bot_a> <bot_b> [map]
```

This uploads both bots and runs a match on AWS Graviton3 instances with the 2ms CPU time limit enforced. Use this to catch performance issues before submitting.

Bot paths for `test-run` must be a directory containing `main.py` or a `.zip` file (unlike `cambc run`, arbitrary `.py` files are not accepted).

```bash  theme={"dark"}
cambc test-run my_bot opponent
cambc test-run my_bot opponent maps/custom.map26
```

<Warning>
  Remote test runs are rate-limited: max 10 test/unrated matches per 5 minutes. Unrated matches also have a 5-minute cooldown per specific matchup.
</Warning>

You can also challenge another team to an unrated match using both teams' latest submissions:

```bash  theme={"dark"}
cambc unrated <opponent_team_id>
```

## Debugging

* **stdout** via `print("msg")` is captured and saved to the replay — view it per-unit in the visualiser
* **stderr** prints to your console in real time
* Use `c.draw_indicator_line()` and `c.draw_indicator_dot()` to draw debug overlays on the map

## Next steps

<CardGroup cols={2}>
  <Card title="Submit your bot" icon="cloud-arrow-up" href="/getting-started/submitting">
    Upload your bot to compete on the ladder.
  </Card>

  <Card title="CLI reference" icon="terminal" href="/getting-started/cli">
    Full reference for all CLI commands.
  </Card>
</CardGroup>


Built with [Mintlify](https://mintlify.com).