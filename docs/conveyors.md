> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Conveyors

> Transport resources across the map — conveyors, splitters, bridges, and armoured conveyors.

All conveyors can hold **one stack** of any resource, and both accept input and produce output. Basic conveyors, splitters, and armoured conveyors point in one of the **cardinal directions**.

You can inspect the stack currently stored in one of these buildings with `c.get_stored_resource(...)` and `c.get_stored_resource_id(...)`.

## Conveyor

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/conveyor.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=487e50d068b9c397ad4c16a993f6a257" alt="Conveyor" style={{ width: 64, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/entities/conveyor.png" />

Accepts resources from any of its three non-output directions. Sends its contents in the direction it is pointing if that tile can accept a resource.

| Property  | Value |
| --------- | ----- |
| HP        | 20    |
| Base cost | 3 Ti  |
| Scaling   | 1%    |

```python  theme={"dark"}
# Build a conveyor pointing south
c.build_conveyor(pos, Direction.SOUTH)

# Inspect the stack currently on a conveyor
resource_type = c.get_stored_resource(conveyor_id)
resource_id = c.get_stored_resource_id(conveyor_id)
```

## Splitter

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/splitter.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=4e711af42fbce57a77c38341f7f2da39" alt="Splitter" style={{ width: 64, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/entities/splitter.png" />

Alternates between outputting in three directions: the primary output and the two adjacent directions. **Only accepts input from the back.** Prioritises directions used least recently.

| Property  | Value |
| --------- | ----- |
| HP        | 20    |
| Base cost | 6 Ti  |
| Scaling   | 1%    |

## Bridge

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/bridge.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=662d002ce068c838da058b5924ed9772" alt="Bridge" style={{ width: 64, float: "right", marginLeft: 16 }} width="1536" height="512" data-path="images/entities/bridge.png" />

Outputs its contents to a **specific tile within Euclidean distance 3** (distance² ≤ 9), chosen when built. Bridges bypass directional restrictions — they can feed any building that accepts resources. Accepts input from all directions.

| Property  | Value |
| --------- | ----- |
| HP        | 20    |
| Base cost | 20 Ti |
| Scaling   | 10%   |

```python  theme={"dark"}
# Build a bridge that outputs to a target position
c.build_bridge(bridge_pos, target_pos)
```

## Armoured Conveyor

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/armoured-conveyor.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=c706382c9d7e9718c9a2d3e41322a373" alt="Armoured conveyor" style={{ width: 64, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/entities/armoured-conveyor.png" />

Same function as a basic conveyor but with **much more HP**. Requires refined axionite to build.

| Property  | Value      |
| --------- | ---------- |
| HP        | 50         |
| Base cost | 5 Ti, 5 Ax |
| Scaling   | 1%         |

## Resource distribution

At the end of each round (after all units have acted), resources are distributed. Buildings that have resources to output attempt to send them to adjacent buildings that can accept them.

Key rules:

* Resources are always moved in **stacks of 10**
* **Resources can be sent to enemy buildings** — be careful with conveyor placement near opponents
* Harvesters and splitters prioritise outputting in directions **used least recently**
* Foundries require one stack each of titanium and raw axionite before outputting one stack of refined axionite
* Turrets only accept resources when completely empty


Built with [Mintlify](https://mintlify.com).