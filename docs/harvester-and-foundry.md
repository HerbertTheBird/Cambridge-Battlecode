> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Harvester & Foundry

> Resource production — mining ore and refining axionite.

## Harvester

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/harvester.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=992df718cfea7d1bdf21c47ed90faddb" alt="Harvester" style={{ width: 64, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/entities/harvester.png" />

Must be placed on an **ore deposit**. Outputs one stack of the corresponding resource to an adjacent building every **4 rounds**. The first output happens immediately on the round the harvester is built.

Prioritises outputting in directions used least recently.

| Property        | Value    |
| --------------- | -------- |
| HP              | 30       |
| Base cost       | 20 Ti    |
| Scaling         | 5%       |
| Output interval | 4 rounds |

```python  theme={"dark"}
# Build a harvester on an ore tile
if c.can_build_harvester(ore_pos):
    c.build_harvester(ore_pos)
```

<Tip>
  Harvesters are now cheap enough to get online early. A base harvester costs
  only 20 Ti and adds just +5% scaling.
</Tip>

## Axionite Foundry

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/foundry.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=9661cc6db96a60c933245749ab6f843a" alt="Axionite foundry" style={{ width: 64, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/entities/foundry.png" />

Takes one stack each of **titanium and raw axionite**, then outputs one stack of **refined axionite**. Accepts input and produces output from any side.

| Property  | Value |
| --------- | ----- |
| HP        | 50    |
| Base cost | 40 Ti |
| Scaling   | 100%  |

<Warning>
  Foundries have the highest scaling contribution at +100% each. Building one
  adds 100% to your cost multiplier (e.g. 1.0x -> 2.0x if it's your first
  build, but 1.5x -> 2.5x if you've already built other things). Plan
  carefully before committing 40 Ti.
</Warning>

### Refining process

1. Feed titanium (via conveyor) → foundry stores it
2. Feed raw axionite (via conveyor) → foundry combines them
3. Foundry outputs one stack of refined axionite to an adjacent accepting building


Built with [Mintlify](https://mintlify.com).