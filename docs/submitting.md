> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Submitting

> Upload your bot to the platform to compete on the ladder.

## Log in

Before submitting, authenticate the CLI with your platform account:

```bash  theme={"dark"}
cambc login
```

This opens a browser window to complete the OAuth flow. Your session persists until it expires or you run `cambc logout`.

## Via CLI

```bash  theme={"dark"}
cambc submit ./my_bot/
```

If you pass a directory, the CLI auto-zips it. You can also submit a zip file directly.

## Via the platform

1. Go to [game.battlecode.cam](https://game.battlecode.cam)
2. Navigate to **Submissions** in the sidebar
3. Upload your bot zip

## Bot requirements

Your submission must contain a `main.py` file with a `Player` class. The file can be at the root of the zip or inside a single top-level directory.

| Constraint        | Limit                                                  |
| ----------------- | ------------------------------------------------------ |
| Zip size          | 5 MB max                                               |
| Decompressed size | 50 MB max                                              |
| File count        | 500 files max                                          |
| Native extensions | **Not allowed** (`.so`, `.pyd`, `.dylib`, `.dll`)      |
| Imports           | Must be top-level (file I/O is blocked during `run()`) |

<Warning>
  All imports must happen at the top of your file, not inside `run()`. The sandbox blocks file reads during gameplay for security.
</Warning>

## What happens after upload

1. Your zip is validated (structure, size, no native extensions)
2. Status is set to **ready**
3. Your latest ready submission becomes your active bot on the ladder
4. The scheduler pairs you against other teams every 10 minutes

## Ladder

The [ladder](https://game.battlecode.cam/ladder) ranks all teams by Elo rating. Every 10 minutes, the scheduler creates one match per team, pairing you with a nearby-rated opponent using greedy nearest-rating matching with small random jitter. Each match consists of **5 games** — the team that wins more games wins the match. Ratings update immediately after each match. See [How matches work](/getting-started/matches) for details.

<Tip>
  Use `cambc test-run` to test your bot with full time limits on the same hardware as the ladder before submitting. Use `cambc unrated` to challenge specific teams without affecting your rating.
</Tip>


Built with [Mintlify](https://mintlify.com).