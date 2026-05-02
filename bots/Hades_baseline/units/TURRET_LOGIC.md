# Hades turret logic

How sentinels and gunners decide what (and whether) to shoot. Both share the
target-priority engine in [`turret_priority.py`](turret_priority.py); the
turrets differ only in how they enumerate candidate tiles.

## Shared candidate tuple

Every candidate flows through `select_best` as a tuple whose **first five
elements** are fixed:

```
(tile: Position, n: int, weight: int, hp: int, etype: EntityType, ...extras)
```

- `tile` — the position to fire at (sentinel) or the obstruction position
  inferred from a directional ray (gunner).
- `n` — `tile.x + tile.y * width`, the bitmask index.
- `weight` — `_WEIGHTS.get(etype, 0)`. Used only as a final tiebreak.
- `hp` — current HP at `tile`. Used by the one-shot tiebreak.
- `etype` — the **enemy** EntityType motivating the shot. For gunner-rotation
  candidates this is the etype detected along the ray, even if `fire_at` is a
  friendly road we'd be sacrificing to clear.
- Extras — turret-specific (e.g. the gunner appends `direction` at index 5).

## Priority tiers (`compute_priority_sets`)

Returned per turn as `{1: mask, 2: mask, 3: mask, 4: mask, 5: mask}`. Lower
number = higher priority. Tier 6 is implicit ("any positive-weight tile not
in 1–5").

| # | Set                                                                                                |
|---|----------------------------------------------------------------------------------------------------|
| 1 | Enemy foundries that feed ≥1 enemy turret AND 0 of mine                                            |
| 2 | Enemy harvesters that feed >1 enemy turret AND 0 of mine                                           |
| 3 | Enemy turrets that can hit one of my turrets, the enemy conveyors that feed them, and harvesters whose chain reaches them (and don't feed mine) |
| 4 | Same as 3 but without the "threatens my turret" guard — i.e. every enemy turret + its enemy-side feeder chain + harvesters feeding them |
| 5 | Enemy roads / conveyor types / barriers cardinally adjacent to ANY harvester (own or enemy)        |
| 6 | Anything else with positive weight                                                                 |

Plus a special **`protected`** mask (not a priority bucket): conveyors that
feed any of my turrets, plus harvesters / foundries cardinally adjacent to
those conveyors or directly adjacent to my turrets. Anything in `protected`
is filtered out before bucketing — we never shoot our own pipeline, even
when the enemy owns the tile.

### Helpers used to build these masks

- `_threatening_enemy_turrets(rc, enemy_turrets, my_turrets)` — enemy turrets
  that have a legal shot at any of my turrets *right now*. Uses
  `rc.can_fire_from(pos, direction, etype, my_pos)`. For gunners this enforces
  first-obstruction LOS through the current map (walls and buildings block
  correctly); sentinels and breaches still resolve geometrically per the
  CAMBC API.
- `_reverse_conveyor_feeders(seeds, conv_filter)` — walk upstream via
  `_conv_reverse[n]`, restricted to a conveyor filter (e.g. enemy-team
  conveyors). Used to grow the chain feeding any seed turret.
- `_adj_seed_conveyors(src)` — cardinal-adjacent conveyors **not pointing
  back** toward `src`. Mirrors the engine's "what feeds this tile" rule.
- `_forward_reach(seeds)` — propagate forward through conveyor flow plus
  bridge outputs, to count how many enemy turrets a harvester ultimately
  reaches.
- `direct = expand_manhattan(lsb)` — covers the case where a harvester /
  foundry sits directly cardinally adjacent to a turret with no conveyor
  between them; both `feeds_enemy` and `feeds_mine` checks include this.

## Selection (`select_best`)

```
select_best(candidates, priority_sets, nav, one_shot_hp) -> tuple | None
```

1. **Friendly-bot safety.** Drop *any* candidate (bot or non-bot) whose tile
   is in `map_info._bm_friendly_bots`. Belt-and-suspenders — the per-turret
   resolvers also screen these out, but `select_best` is the last gate.
2. **Pipeline-protection filter.** Drop any non-bot candidate whose tile is
   in `priority_sets['protected']` (anything feeding one of my turrets).
3. **Bot-adjacency demotion.** Compute
   `bot_ring = expand_chebyshev(enemy_bots) & ~enemy_bots` (8-neighbor ring of
   any enemy builder bot, **excluding** the bot tiles themselves). A non-bot
   candidate whose `n` is in `bot_ring` is **never dropped** — it's pushed
   into a separate `fallback` pool, tried only after every normal pool
   (priorities 1–6 + bots) has been exhausted. The intent: bot adjacency
   means heal risk, but if we have nothing else worth shooting we still
   want a target.

   Behavior per caller's `bot_ring_mode`:
   - **Sentinel (`'one_shot_override'`)** — demote a `bot_ring` candidate
     unless one of:
     - the candidate is a one-shot (`hp ≤ one_shot_hp = 18`), or
     - the candidate's bit is set in `ring_override_mask` (see below).
     In either case it stays in the normal pool.
   - **Gunner (`'off'`)** — never demote. Gunners already pay a per-shot
     cooldown and a 10 Ti rotate, so we keep their candidate pool wide.
   - **`'strict'` (legacy default)** — demote unconditionally.

   The sentinel populates `ring_override_mask` via `_kill_assist_mask`:
   for each candidate tile, sum this sentinel's 18 damage with the base
   damage of every *other* allied turret (gunner = 10, sentinel = 18,
   breach = 40 direct) whose `rc.can_fire_from` is true at the tile. If
   the total reaches `hp + 4` (the "−4 HP grace"), the tile bypasses
   demotion. `can_fire_from` ignores ammo/cooldown, so this is a damage-
   potential proxy, not a real-fire forecast.

   Bot tiles themselves are not affected by this rule (the furthest-from-bot
   tiebreak handles isolation later).
4. **Bucket non-bots.** For each surviving non-bot candidate, find the
   smallest `p` such that `priority_sets[p] & (1 << n)` is set. Otherwise
   `bucket = 6` if `weight > 0`, else discard.
5. **Pool order:**
   ```
   priority 1 → priority 2 → priority 3 → priority 4
                          → enemy builder bots (bot_pool)
                          → priority 5 → priority 6
                          → fallback p1 → fallback p2 → ... → fallback p6
   ```
   Bots fire only when no enemy-foundry / harvester / turret-chain target
   is available; bots take precedence over generic priority-5/6 hits.
   Fallback pools (bot-adjacent demoted candidates, bucketed by the same
   priority tiers) only fire when *every* normal pool is empty.
6. **Within each pool, `_apply_tiebreaks`:**
   1. **One-shot filter:** keep only candidates with `hp ≤ one_shot_hp` if
      any exist. (`one_shot_hp` = 18 for sentinel, 10 for gunner.)
   2. **Furthest from nearest enemy bot:** of the survivors, retain only
      those tied for max `nav.closest(enemy_bots, pos=tile)` distance
      (unreachable = ∞). Skipped if there are no enemy bots.
   3. **Final order:** if any candidate is one-shot, sort by `(-weight,
      -hp)` (kill the bigger one); otherwise `(-weight, hp)` (chip the
      weakest). Pick the first.

If every pool is empty, returns `None`.

`select_best` logs every step via `log()` — raw candidate count, prefilter
counts, bucket sizes, the case label being tried, the pool contents,
distance-to-enemy-bot per candidate, the tiebreak path, and the final
winner. Useful for debugging "why did the turret pick X" questions.

## Sentinel — [`turret_sentinel.py`](turret_sentinel.py)

Sentinels are stationary AOE shooters with a fixed +/-1 band attack pattern.

### Each turn

1. **`map_info.update()`** refreshes shared bitmasks.
2. **Self-destruct gates:**
   - `_invalid_upstream_turns` — if `turret_could_possibly_be_fed(my_pos)` is
     False for ≥4 turns, no ammo, and `_should_stay()` says no, kill self.
   - `_no_ammo_turns` — if ammo < 10 for ≥16 turns and `_should_stay()` says
     no, kill self.
   - `_should_stay()` keeps the turret alive when an enemy builder bot is
     within Chebyshev 2, or when no friendly bot is closer than the nearest
     enemy bot (we'd be giving our pathing a free shortcut by leaving).
3. **Action gates:** require `action_cooldown == 0` and `ammo ≥ 5`.
4. **Build candidate list:** for every tile in `rc.get_attackable_tiles()`:
   - skip if the tile is in `_ally_feeder_mask()` (a friendly conveyor
     upstream of any of *my* turrets — never shoot our own supply chain).
   - skip if `rc.can_fire(tile)` is False.
   - skip if `_resolve_target_on_tile(tile)` returns None (empty / friendly /
     marker — also models the engine's "bot beats building on the same
     tile").
   - otherwise append `(tile, n, weight, hp, etype)`.
5. **No candidates** → `_should_stay()` check, possibly self-destruct.
6. **Run `select_best`** with `ONE_SHOT_HP = SENTINEL_DAMAGE = 18`. Fire at
   the chosen tile, or self-destruct (if `_should_stay()` is False) when the
   selector returns None.

## Gunner — [`turret_gunner.py`](turret_gunner.py)

Gunners are directional single-target shooters that can rotate (10 Ti, 1
turn cooldown). Their geometry is precomputed at `init` into
`_attackable_by_dir`.

### Ray scanning (`_scan_ray`)

`_scan_ray(direction, attackable, feeder_mask, allow_builder_bots,
bot_must_be_on_my_conveyor=False)` walks one tile at a time forward from
`my_pos` along `direction`:

| Tile contents              | Behavior                                                                  |
|----------------------------|---------------------------------------------------------------------------|
| Out of bounds / not in pattern | Stop (no target)                                                      |
| Wall                       | Stop (no target)                                                          |
| Empty                      | Pass through                                                              |
| Marker (no bot)            | Pass through (`fire_at` not yet set)                                      |
| Friendly road              | Pass through, `passed_road = True`, `fire_at` records this tile           |
| Friendly non-road building / friendly bot | Stop, no fire                                              |
| Tile in `feeder_mask`      | Stop, no fire (don't shoot our own supply chain)                          |
| Enemy bot                  | Fire only if `allow_builder_bots`, no friendly road already passed, and (if `bot_must_be_on_my_conveyor`) bot stands on my conveyor, the conveyor is damaged (HP < max), AND no friendly bot is within Chebyshev 1 of the tile |
| Enemy building             | Fire — return `(bid_etype, fire_at)`                                      |

`fire_at` is set on the **first real obstruction** so the engine resolves
fire to the friendly road we're sacrificing if we choose to shoot through
it.

### Firing the current direction (`_decide_fire`)

`_scan_ray(current_dir, ..., allow_builder_bots=True)` — bots are valid in
the current facing. Returns the `fire_at` position to pass to `rc.fire`.

### Choosing a rotate direction (`_choose_rotate_dir`)

For each non-current facing `d`, run
`_scan_ray(d, ..., allow_builder_bots=True, bot_must_be_on_my_conveyor=True)`
and append `(fire_at, n, weight, hp, etype, d)`. Bots are eligible **only**
when:

- the bot stands on one of my conveyors (the "bot trespassing on my line"
  fallback),
- that conveyor is already damaged (HP < max — if it's pristine, the bot
  isn't actually hurting our pipeline yet), AND
- no friendly builder bot is within Chebyshev 1 of the tile (a nearby
  friendly can heal/repair, so we'd rather let it deal with the trespasser
  than spend a 10 Ti rotate).

Surviving rotation candidates reach the bot pool in `select_best`, so they
only win the rotation when priorities 1–4 are empty.

The rotation candidate's etype reflects the enemy thing on the ray — a
friendly road sacrificed to reach an enemy harvester is still scored as a
harvester for priority bucketing.

### Each turn

1. `map_info.update()`.
2. **Self-destruct gates** (mirrors sentinel, with `≥3` invalid-upstream
   turns and `ammo == 0` for `_no_ammo_turns`).
3. Require `action_cooldown == 0` and `ammo ≥ 2`.
4. **Try firing forward** — `_decide_fire()` then `rc.can_fire(target)`.
5. **Else try rotating** — `_choose_rotate_dir()`, gated on
   `global_titanium ≥ 60` and `rc.can_rotate(d)`. The 60 Ti gate is a
   discretionary buffer over the 10 Ti rotate cost.
6. If both fail and `_should_stay()` is False, self-destruct.

## Tuning knobs

| Knob                   | Where                       | Effect                                              |
|------------------------|-----------------------------|-----------------------------------------------------|
| `_WEIGHTS`             | sentinel & gunner           | Final-tiebreak preference among same-bucket targets |
| `ONE_SHOT_HP`          | sentinel = 18, gunner = 10  | Threshold for the one-shot tiebreak                 |
| `_no_ammo_turns` cap   | 16 in both                  | How long to wait for ammo before self-destruct      |
| `_invalid_upstream_turns` cap | 4 sentinel, 3 gunner | How long to wait when no possible feeder           |
| Rotate Ti gate         | 60 in gunner                | Min global Ti before spending 10 Ti on a rotate     |
| Bot-ring filter shape  | `expand_chebyshev` (8-neighbor) | Which non-bot tiles get rejected near enemy bots |
