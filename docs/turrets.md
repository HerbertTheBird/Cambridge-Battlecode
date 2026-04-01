> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Turrets

> Defensive and offensive combat units — gunner, sentinel, breach, and launcher.

Every turret **except the launcher** faces in one of **8 directions**. Ammo must be fed to turrets via conveyors, from any direction except the direction the turret is facing. Diagonal turrets can be fed from all four sides.

Ammo-based turrets can hold up to one stack of one resource type and only accept incoming resources when completely empty.

You can inspect raw turret geometry with `c.get_attackable_tiles()` on a real
turret or `c.get_attackable_tiles_from(position, direction, turret_type)` from
any controller. These queries ignore ammo, cooldown, occupancy, blockers, and
other legality checks. Use `c.can_fire()` to check a real turret's current shot
legality, or `c.can_fire_from(...)` to test a hypothetical shot against the
current map's range and obstruction rules.

<Info>
  If a builder bot is standing on a building, turret attacks on that tile hit
  **only the builder bot**.
</Info>

<Info>
  Raw axionite fed into a turret is **destroyed**. Only the ammo types listed
  below have any effect.
</Info>

## Gunner

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/gunner.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=62439f66dff4e5aa36645340d4daad02" alt="Gunner" style={{ width: 64, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/entities/gunner.png" />

Has a vision radius of √13. Fires along the forward ray up to range. Empty
tiles and markers do **not** block line of sight. Markers are still targetable.
Walls block the ray but are not targetable. Builder bots and non-marker
buildings are both targetable and blocking, so nothing beyond the first such
blocker is legal. Using refined axionite as ammo deals **30 damage** instead of 10.

<Info>
  Markers are the only occupied tiles that do **not** block a gunner. Walls
  block but are not targetable. Builder bots and non-marker buildings are both
  targetable and blocking.
</Info>

<Tip>
  `c.get_gunner_target()` returns the closest **targetable** occupied tile on
  the forward line. It may return a marker even if a farther legal target also
  exists behind that marker.
</Tip>

<Info>
  Gunners can rotate to any direction with `c.rotate(direction)`. This costs
  **10 Ti** from the global pool and applies a **1-turn cooldown**. Use
  `c.can_rotate(direction)` to preflight the move.
</Info>

| Property      | Value                         |
| ------------- | ----------------------------- |
| HP            | 40                            |
| Base cost     | 10 Ti                         |
| Scaling       | 10%                           |
| Damage        | 10 (30 with refined axionite) |
| Reload        | 1 round                       |
| Ammo per shot | 2                             |
| Vision r²     | 13                            |
| Attack r²     | 13 (same as vision)           |

<Tabs>
  <Tab title="Cardinal">
        <img src="https://mintcdn.com/cambridgebattlecode/sOfFkEKzv7YbWA_S/images/ranges/gunner-cardinal.png?fit=max&auto=format&n=sOfFkEKzv7YbWA_S&q=85&s=77b1386259e4c53e81aca66c783e18d9" alt="Gunner range — cardinal direction" width="1175" height="1182" data-path="images/ranges/gunner-cardinal.png" />
  </Tab>

  <Tab title="Diagonal">
        <img src="https://mintcdn.com/cambridgebattlecode/sOfFkEKzv7YbWA_S/images/ranges/gunner-diagonal.png?fit=max&auto=format&n=sOfFkEKzv7YbWA_S&q=85&s=9566451973643d6ba88ec8745d8137b1" alt="Gunner range — diagonal direction" width="1175" height="1182" data-path="images/ranges/gunner-diagonal.png" />
  </Tab>
</Tabs>

## Sentinel

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/sentinel.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=d9e59a60e07e843f244324b5a144cb5f" alt="Sentinel" style={{ width: 64, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/entities/sentinel.png" />

High range support turret. Can hit all tiles within **1 king move** (Chebyshev distance) of the straight line in its facing direction, within vision range.

Using refined axionite instead of titanium as ammo adds **+5 to the action and move cooldown** of any unit directly hit — acting as a stun.

| Property      | Value               |
| ------------- | ------------------- |
| HP            | 30                  |
| Base cost     | 30 Ti               |
| Scaling       | 20%                 |
| Damage        | 18                  |
| Reload        | 3 rounds            |
| Ammo per shot | 10                  |
| Vision r²     | 32                  |
| Attack r²     | 32 (same as vision) |

<Tabs>
  <Tab title="Cardinal">
        <img src="https://mintcdn.com/cambridgebattlecode/sOfFkEKzv7YbWA_S/images/ranges/sentinel-cardinal.png?fit=max&auto=format&n=sOfFkEKzv7YbWA_S&q=85&s=20f20ba8f92bf979e16c04f59693a25b" alt="Sentinel range — cardinal direction" width="1428" height="1325" data-path="images/ranges/sentinel-cardinal.png" />
  </Tab>

  <Tab title="Diagonal">
        <img src="https://mintcdn.com/cambridgebattlecode/sOfFkEKzv7YbWA_S/images/ranges/sentinel-diagonal.png?fit=max&auto=format&n=sOfFkEKzv7YbWA_S&q=85&s=a729fde3208ca6ef064c5474960dcc26" alt="Sentinel range — diagonal direction" width="1428" height="1325" data-path="images/ranges/sentinel-diagonal.png" />
  </Tab>
</Tabs>

<Tip>
  Sentinels with refined axionite ammo still disrupt builder bots by delaying
  both movement and actions.
</Tip>

## Breach

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/breach.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=f1d1f22a28280a787eb9ef85f4a4f145" alt="Breach" style={{ width: 64, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/entities/breach.png" />

Very high damage with **splash**. Attacks in a **180° cone** in the facing direction.

| Property      | Value                                       |
| ------------- | ------------------------------------------- |
| HP            | 60                                          |
| Base cost     | 15 Ti, 10 Ax                                |
| Scaling       | 10%                                         |
| Damage        | 40 direct + 20 splash (8 surrounding tiles) |
| Reload        | 1 round                                     |
| Ammo per shot | 5 (refined axionite only)                   |
| Vision r²     | 13                                          |
| Attack r²     | 5                                           |

<Tabs>
  <Tab title="Cardinal">
        <img src="https://mintcdn.com/cambridgebattlecode/jkHPwcNhhgR_-bsi/images/ranges/breach-cardinal.png?fit=max&auto=format&n=jkHPwcNhhgR_-bsi&q=85&s=401c6ed47480bff1a0791b8b80e25bf9" alt="Breach range — cardinal direction" width="1024" height="984" data-path="images/ranges/breach-cardinal.png" />
  </Tab>

  <Tab title="Diagonal">
        <img src="https://mintcdn.com/cambridgebattlecode/HREr2plTj9cAMxXJ/images/ranges/breach-diagonal.png?fit=max&auto=format&n=HREr2plTj9cAMxXJ&q=85&s=abf61b4bda33d930891fe73a6d9379c8" alt="Breach range — diagonal direction" width="1024" height="986" data-path="images/ranges/breach-diagonal.png" />
  </Tab>
</Tabs>

<Warning>
  Breach turrets have **friendly fire** on the splash damage (8 surrounding
  tiles). They do not damage themselves.
</Warning>

## Launcher

<img src="https://mintcdn.com/cambridgebattlecode/W9OYBDP1YcA3tc0W/images/entities/launcher.png?fit=max&auto=format&n=W9OYBDP1YcA3tc0W&q=85&s=0f147103b2279e8b33821454d9377b40" alt="Launcher" style={{ width: 64, float: "right", marginLeft: 16 }} width="512" height="512" data-path="images/entities/launcher.png" />

Picks up and **throws adjacent builder bots** to a target tile within range. The target tile must be bot-passable. Unlike other turrets, launchers have **no facing direction** and do not use ammo.

| Property  | Value               |
| --------- | ------------------- |
| HP        | 30                  |
| Base cost | 20 Ti               |
| Scaling   | 10%                 |
| Reload    | 1 round             |
| Vision r² | 26                  |
| Attack r² | 26 (same as vision) |

<img src="https://mintcdn.com/cambridgebattlecode/sNfop_mvBaJIfJyf/images/ranges/launcher.png?fit=max&auto=format&n=sNfop_mvBaJIfJyf&q=85&s=6ad5adfed217c4f27cf37e1a9feb323f" alt="Launcher range — blue is vision, red is throw range" width="1287" height="1283" data-path="images/ranges/launcher.png" />

```python  theme={"dark"}
# Build a launcher (no direction needed)
c.build_launcher(pos)

# Launch a builder bot to a distant position
if c.can_launch(bot_pos, target_pos):
    c.launch(bot_pos, target_pos)
```


Built with [Mintlify](https://mintlify.com).