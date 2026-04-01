> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Your First Bot

> Write a basic bot that spawns builder bots and starts harvesting resources.

## Get started with `cambc starter`

If you haven't already, run `cambc starter` to scaffold your project. When prompted, choose to create the starter bot — it gives you a working bot to build on.

```bash  theme={"dark"}
cambc starter
```

The starter bot demonstrates core mechanics: the core spawns builder bots, builders explore by laying roads, and when they find ore they build harvesters on it. Run it against itself to see it in action:

```bash  theme={"dark"}
cambc run starter starter --watch
```

<Info>
  Teams can have at most **50 living units total**, including the core. In larger bots, use `c.get_unit_count()` with `GameConstants.MAX_TEAM_UNITS` if you want the exact numbers. `c.can_spawn()` and any unit-producing `c.can_build_*()` method already account for the cap.
</Info>

## Bot structure

Every bot is a Python file containing a `Player` class with a `run` method. The engine creates one `Player` instance per unit and calls `run(controller)` once per round.

```python main.py theme={"dark"}
"""Starter bot - a simple example to demonstrate usage of the Controller API.

Each unit gets its own Player instance; the engine calls run() once per round.
Use Controller.get_entity_type() to branch on what kind of unit you are.

This bot:
  - Core: spawns up to 3 builder bots on random adjacent tiles
  - Builder bot: builds a harvester on any adjacent ore tile, then moves in a
    random direction (laying a road first so the tile is passable), and places
    a marker recording the current round number
"""

import random

from cambc import Controller, Direction, EntityType, Environment, Position

# non-centre directions
DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]

class Player:
    def __init__(self):
        self.num_spawned = 0 # number of builder bots spawned so far (core)

    def run(self, ct: Controller) -> None:
        etype = ct.get_entity_type()
        if etype == EntityType.CORE:
            if self.num_spawned < 3:
                # if we haven't spawned 3 builder bots yet, try to spawn one on a random tile
                spawn_pos = ct.get_position().add(random.choice(DIRECTIONS))
                if ct.can_spawn(spawn_pos):
                    ct.spawn_builder(spawn_pos)
                    self.num_spawned += 1
        elif etype == EntityType.BUILDER_BOT:
            # if we are adjacent to an ore tile, build a harvester on it
            for d in Direction:
                check_pos = ct.get_position().add(d)
                if ct.can_build_harvester(check_pos):
                    ct.build_harvester(check_pos)
                    break

            # move in a random direction
            move_dir = random.choice(DIRECTIONS)
            move_pos = ct.get_position().add(move_dir)
            # we need to place a conveyor or road to stand on, before we can move onto a tile
            if ct.can_build_road(move_pos):
                ct.build_road(move_pos)
            if ct.can_move(move_dir):
                ct.move(move_dir)

            # place a marker on an adjacent tile with the current round number
            marker_pos = ct.get_position().add(random.choice(DIRECTIONS))
            if ct.can_place_marker(marker_pos):
                ct.place_marker(marker_pos, ct.get_current_round())
```

## Key concepts

<AccordionGroup>
  <Accordion title="One Player instance per unit">
    Each unit (core, builder bot, turret) gets its own `Player` instance. Instance variables persist across rounds for that unit, but are **not shared** between units. Use [markers](/spec/other-buildings#marker) for inter-unit communication.
  </Accordion>

  <Accordion title="The Controller object">
    The `controller` argument passed to `run()` provides all game queries and actions. See the full [Controller API reference](/api/controller).
  </Accordion>

  <Accordion title="Imports from cambc">
    `from cambc import *` gives you all game types: `Team`, `EntityType`, `Direction`, `Position`, `ResourceType`, `Environment`, `GameConstants`, `GameError`, and `Controller`.
  </Accordion>

  <Accordion title="Time limit">
    Each unit gets **2ms of CPU time** per round, plus a 5% buffer that refills when you use less. Locally there are no time limits — use remote test runs to check performance on the actual hardware.
  </Accordion>

  <Accordion title="No external packages">
    Only Python standard library modules are available. External packages like `numpy` or `scipy` cannot be imported — bots run in a sandboxed environment with no `pip install`.
  </Accordion>
</AccordionGroup>

## Next steps

<CardGroup cols={2}>
  <Card title="Run a local match" icon="play" href="/getting-started/running-matches">
    Test your bot against itself or an example opponent.
  </Card>

  <Card title="Game rules" icon="book" href="/spec/overview">
    Understand the full game mechanics before optimising.
  </Card>

  <Card title="API reference" icon="rectangle-terminal" href="/api/controller">
    Every method available via the Controller object.
  </Card>

  <Card title="Types and enums" icon="list" href="/api/types">
    All game types: Team, EntityType, Direction, Position, and more.
  </Card>
</CardGroup>


Built with [Mintlify](https://mintlify.com).