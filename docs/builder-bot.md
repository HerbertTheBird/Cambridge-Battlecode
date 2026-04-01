> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Builder Bot

> The only mobile unit — responsible for constructing all buildings.

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/builder-bot.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=b0b8f534c879d31c95e22691fccade5b" alt="Builder bot" style={{ width: 64, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/entities/builder-bot.png" />

Builder bots are the **only mobile unit**. They construct buildings, heal friendly entities on a chosen tile, and can make a weak attack against the building under them.

## Properties

| Property             | Value |
| -------------------- | ----- |
| HP                   | 30    |
| Base cost            | 30 Ti |
| Scaling contribution | 20%   |
| Vision radius²       | 20    |
| Action radius²       | 2     |

<img src="https://mintcdn.com/cambridgebattlecode/sOfFkEKzv7YbWA_S/images/ranges/builder-bot.png?fit=max&auto=format&n=sOfFkEKzv7YbWA_S&q=85&s=98ae316919ebf6354d162d4e56bf589f" alt="Builder bot range — blue is vision, red is action radius" width="1287" height="1283" data-path="images/ranges/builder-bot.png" />

## Movement

Builder bots can move to an adjacent tile (including diagonals) if their move cooldown is 0. Moving increases the cooldown by 1.

<Warning>
  Builder bots can **only walk on**:

  * Conveyors (any variant, any direction, either team)
  * Roads (either team)
  * The allied core

  These are called **walkable** tiles. The direction of the conveyor does not matter, and neither does the presence of resources on the tile.
</Warning>

```python  theme={"dark"}
# Move towards a target
direction = c.get_position().direction_to(target)
if c.can_move(direction):
    c.move(direction)
```

## Actions

When action cooldown is 0, a builder bot can perform one of:

### Build

Build any building or turret on a tile within action radius that doesn't
already have a building.

<Info>
  If a tile already contains a builder bot, only walkable buildings
  (conveyors and roads) can be built on that tile.
</Info>

### Heal

Spend **1 Ti** to heal **4 HP** to all friendly entities on a tile within action radius. If a friendly builder bot and a friendly building share the chosen tile, both are healed. The action fails if nothing on that tile would gain HP.

```python  theme={"dark"}
if c.can_heal(target_pos):
    c.heal(target_pos)
```

### Attack

Spend **2 Ti** to deal **2 damage** to the building on the tile the builder bot is standing on. This reuses the standard `can_fire()` / `fire()` combat API.

```python  theme={"dark"}
my_pos = c.get_position()
if c.can_fire(my_pos):
    c.fire(my_pos)
```

### Destroy

Destroy any allied building within action radius. This can be done **any number of times per round** and does **not** cost action cooldown.

```python  theme={"dark"}
if c.can_destroy(building_pos):
    c.destroy(building_pos)
```

## Self-destruct

A builder bot can self-destruct at any time. It does **not** deal damage.

```python  theme={"dark"}
c.self_destruct()
```

## Markers

Builder bots (like all units) can place one [marker](/spec/other-buildings#marker) per round within action radius, separate from the action cooldown.


Built with [Mintlify](https://mintlify.com).