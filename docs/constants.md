> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Game Constants

> All numeric constants available via GameConstants.

Access constants via `GameConstants`:

```python  theme={"dark"}
from cambc import GameConstants

max_turns = GameConstants.MAX_TURNS  # 2000
```

## General

| Constant                            | Value | Description                                       |
| ----------------------------------- | ----- | ------------------------------------------------- |
| `MAX_TURNS`                         | 2000  | Maximum number of turns per game                  |
| `MAX_TEAM_UNITS`                    | 50    | Maximum living units per team, including the core |
| `STACK_SIZE`                        | 10    | Resources are moved in stacks of 10               |
| `STARTING_TITANIUM`                 | 500   | Each team's initial titanium                      |
| `STARTING_AXIONITE`                 | 0     | Each team's initial axionite                      |
| `PASSIVE_TITANIUM_AMOUNT`           | 10    | Titanium granted passively each interval          |
| `PASSIVE_TITANIUM_INTERVAL`         | 4     | Rounds between passive titanium grants            |
| `AXIONITE_CONVERSION_TITANIUM_RATE` | 4     | Titanium gained per refined axionite converted    |

## Radii (squared)

| Constant                       | Value | Description                      |
| ------------------------------ | ----- | -------------------------------- |
| `ACTION_RADIUS_SQ`             | 2     | Default action radius for units  |
| `CORE_ACTION_RADIUS_SQ`        | 8     | Core action radius (from centre) |
| `CORE_SPAWNING_RADIUS_SQ`      | 2     | Core spawning radius             |
| `CORE_VISION_RADIUS_SQ`        | 36    | Core vision                      |
| `BUILDER_BOT_VISION_RADIUS_SQ` | 20    | Builder bot vision               |
| `GUNNER_VISION_RADIUS_SQ`      | 13    | Gunner vision                    |
| `SENTINEL_VISION_RADIUS_SQ`    | 32    | Sentinel vision                  |
| `BREACH_VISION_RADIUS_SQ`      | 13    | Breach vision                    |
| `BREACH_ATTACK_RADIUS_SQ`      | 5     | Breach attack cone               |
| `LAUNCHER_VISION_RADIUS_SQ`    | 26    | Launcher vision + throw range    |
| `BRIDGE_TARGET_RADIUS_SQ`      | 9     | Max bridge output distance²      |

## Base costs (titanium, axionite)

| Constant                      | Value    |
| ----------------------------- | -------- |
| `BUILDER_BOT_BASE_COST`       | (30, 0)  |
| `CONVEYOR_BASE_COST`          | (3, 0)   |
| `SPLITTER_BASE_COST`          | (6, 0)   |
| `BRIDGE_BASE_COST`            | (20, 0)  |
| `ARMOURED_CONVEYOR_BASE_COST` | (5, 5)   |
| `HARVESTER_BASE_COST`         | (20, 0)  |
| `ROAD_BASE_COST`              | (1, 0)   |
| `BARRIER_BASE_COST`           | (3, 0)   |
| `FOUNDRY_BASE_COST`           | (40, 0)  |
| `GUNNER_BASE_COST`            | (10, 0)  |
| `SENTINEL_BASE_COST`          | (30, 0)  |
| `BREACH_BASE_COST`            | (15, 10) |
| `LAUNCHER_BASE_COST`          | (20, 0)  |

## Max HP

| Constant                   | Value |
| -------------------------- | ----- |
| `CORE_MAX_HP`              | 500   |
| `BUILDER_BOT_MAX_HP`       | 30    |
| `CONVEYOR_MAX_HP`          | 20    |
| `SPLITTER_MAX_HP`          | 20    |
| `BRIDGE_MAX_HP`            | 20    |
| `ARMOURED_CONVEYOR_MAX_HP` | 50    |
| `HARVESTER_MAX_HP`         | 30    |
| `ROAD_MAX_HP`              | 5     |
| `BARRIER_MAX_HP`           | 30    |
| `FOUNDRY_MAX_HP`           | 50    |
| `MARKER_MAX_HP`            | 1     |
| `GUNNER_MAX_HP`            | 40    |
| `SENTINEL_MAX_HP`          | 30    |
| `BREACH_MAX_HP`            | 60    |
| `LAUNCHER_MAX_HP`          | 30    |

## Combat

| Constant                           | Value   | Description                                     |
| ---------------------------------- | ------- | ----------------------------------------------- |
| `BUILDER_BOT_ATTACK_DAMAGE`        | 2       | Builder bot own-tile attack damage              |
| `BUILDER_BOT_ATTACK_COST`          | (2, 0)  | Cost per builder bot attack                     |
| `BUILDER_BOT_HEAL_COST`            | (1, 0)  | Cost per builder bot heal                       |
| `BUILDER_BOT_SELF_DESTRUCT_DAMAGE` | 0       | Damage on self-destruct                         |
| `HEAL_AMOUNT`                      | 4       | HP restored per heal action                     |
| `GUNNER_DAMAGE`                    | 10      | Gunner base damage per shot                     |
| `GUNNER_AXIONITE_DAMAGE`           | 30      | Gunner damage when loaded with refined axionite |
| `GUNNER_FIRE_COOLDOWN`             | 1       | Turns between gunner shots                      |
| `GUNNER_AMMO_COST`                 | 2       | Resources consumed per shot                     |
| `GUNNER_ROTATE_COST`               | (10, 0) | Cost to rotate a gunner                         |
| `GUNNER_ROTATE_COOLDOWN`           | 1       | Action cooldown applied after rotating          |
| `SENTINEL_DAMAGE`                  | 18      | Sentinel damage per shot                        |
| `SENTINEL_FIRE_COOLDOWN`           | 3       | Turns between sentinel shots                    |
| `SENTINEL_AMMO_COST`               | 10      | Resources consumed per shot                     |
| `SENTINEL_STUN_DURATION`           | 5       | Cooldown added by refined axionite stun         |
| `BREACH_DAMAGE`                    | 40      | Breach direct hit damage                        |
| `BREACH_SPLASH_DAMAGE`             | 20      | Breach splash damage                            |
| `BREACH_FIRE_COOLDOWN`             | 1       | Turns between breach shots                      |
| `BREACH_AMMO_COST`                 | 5       | Refined axionite per shot                       |
| `LAUNCHER_FIRE_COOLDOWN`           | 1       | Turns between launcher throws                   |


Built with [Mintlify](https://mintlify.com).