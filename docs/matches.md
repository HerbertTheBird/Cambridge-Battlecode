> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# How Matches Work

> How ladder matches, Elo ratings, and the 5-game format work.

## Match format

Every match consists of **5 games**. Each game is played on a different map with a different random seed. The team that wins more games wins the match.

### Win conditions per game

A game ends when:

| Condition          | Description                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------------- |
| **Core destroyed** | One team's core reaches 0 HP                                                                      |
| **Resources**      | After 2000 rounds, the [tiebreaker sequence](/spec/overview#win-conditions) determines the winner |
| **Timeout**        | After 2000 rounds with equal tiebreakers — decided by coinflip                                    |

## Ladder

The [ladder](https://game.battlecode.cam/ladder) ranks all teams by **Elo** rating. New teams start unrated and are seeded to 1500 when they upload their first ready submission.

### Scheduling

Every **10 minutes**, the scheduler:

1. Pairs each team with one similarly-rated opponent (greedy nearest-rating matching with small random jitter to avoid repetitive matchups)
2. Avoids rematches from the last hour
3. Submits matches to the runner infrastructure

With N teams, each cycle creates floor(N/2) matches — one match per team.

### Rating updates

Ratings are updated using **Elo** immediately after each match completes. Match outcomes use fractional scoring based on the game score (e.g., a 5-0 win counts more than a 3-2 win).

Each team has a single Elo rating that moves up or down after each match.

## Unrated matches

You can challenge any team to an unrated match that doesn't affect ratings:

```bash  theme={"dark"}
cambc unrated <opponent_team_id>
```

Unrated matches use the same infrastructure and time limits as ladder matches but are prioritised for faster execution.

## Test runs

Test runs let you upload two local bots and run them against each other on the same hardware as ladder matches:

```bash  theme={"dark"}
cambc test-run my_bot opponent
```

This is the best way to verify your bot works within the 2ms CPU time limit before submitting to the ladder.

<Warning>
  Rate limits: max 10 test/unrated matches per 5 minutes. Unrated matches also have a 5-minute cooldown per specific matchup.
</Warning>


Built with [Mintlify](https://mintlify.com).