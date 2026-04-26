# Hades vs Hades_markerless

Both bots disable `comms.mark()` (Hades has a `return` at the top of the function — the marker-placement code is dead). The names are misleading: the meaningful split is **how each one substitutes for the marker channel**, not whether markers are placed.

## TL;DR

- **Hades** keeps the original marker-based comms architecture intact and just stubs out `mark()`. State coordination is per-bot ("my single tile claims this target"), with no inference about teammates beyond what `_bm_friendly_bots` reveals. Failure caches (`cant_harvest`, `unpathable`, `cant_attack`) are mostly monotonic global ints.
- **Hades_markerless** rebuilds coordination from scratch around **positional inference + Voronoi claims**. With markers gone, every bot looks at its visible friendlies and *classifies them* into states by what they're plausibly heading toward, then treats those positions as virtual senders for `claim_subset` / `voronoi_claim`. It also adds a sabotage state, a sticky-target memory, TTL'd failure caches, and instrumentation hooks.

## Coordination model

The most important difference. In `units/builder.py`:

- **Hades**: `handle_comms()` decodes incoming marker messages, populates `claimed_targets[flag]` and `claimed_senders[flag]` from real broadcasts, and ages them out by round. Each state's `_my_claims()` uses a single-tile `my_mask = 1 << (my_pos)` for the BFS seed.
- **Hades_markerless**: `handle_comms()` is a **positional classifier**. It builds candidate sets for each state (attack/route/harvest/disrupt/sabotage), Chebyshev-expands them by `_CLASSIFY_REACH = 8`, and assigns each visible friendly to the highest-priority state-zone whose reach contains it. Those friend positions become `claimed_senders[flag]`, simulating the marker-derived claims. Explore additionally extrapolates each friend's vector outward from the core to a map-edge "virtual destination," giving the explore BFS a teammate-corridor to avoid.
- States in markerless replace `my_mask = 1 << my_pos` with `units.builder.my_voronoi_mask(comm_flag)`, which adds **stickiness**: last turn's active target (preserved per-flag with `STICKY_TARGET_TTL = 8`) gets included in the BFS seed so a bot doesn't surrender a target after a one-turn detour through heal.

## State roster

- **Hades** runs: explore, disrupt, harvest, route, heal, attack, secure (7 states).
- **Hades_markerless** adds **sabotage** (8 states). Sabotage is a low-priority (`MAX_SCORE = 1.5`) stealth state that targets enemy non-armoured conveyors/splitters/bridges that aren't in turret threat, launcher adjacency, or near enemy bots — chewed down by builder bots that have nothing better to do. Uses `voronoi_claim` like all other markerless coordinated states.

## Failure caches

- **Hades**: `cant_harvest`, `unpathable`, `cant_attack` are append-only global ints. `secure._cant_secure_map` already has TTL (100 rounds). Cost-too-expensive uses TTL'd `_cost_map` per state.
- **Hades_markerless**: every `cant_X` cache is TTL'd with helpers `_mark_cant_X(bits)` and `_expire_cant_X()` (typically 60 rounds for harvest/route/attack). A tile that was unrouteable 60 turns ago gets re-tried — useful when an enemy clears an obstruction.

## State-specific behavioural changes

- **harvest**: markerless raises `MAX_SCORE` from 4 to 8 (claims a stronger priority during mid-construction). Same path/cost logic.
- **route**: same retry-loop refactor in both versions; markerless also TTL's `unpathable`.
- **attack**: markerless TTL's `cant_attack`; uses a wider danger expansion for fallback targets (`expand_chebyshev(danger)` extra hop) so we don't poke at enemy infra inside their builder vision (where they'd just heal between hits).
- **heal**: markerless's "needs heal" set adds *any* damaged friendly building within 2 cheb of an enemy bot, not just buildings with >2 damage. React to early hits, not just late ones. Also removes the attack "too close" early-return (≤5 distance) because we *want* to engage enemies that are actively damaging our buildings.
- **explore**: markerless seeds the BFS with `my_pos | _bm_friendly_bots` and interpolates intermediate points along the line to each friend (5-tile spacing), giving explore strong push-away-from-teammates behaviour. Hades currently has those code blocks commented out and seeds from `_bm_seen_observed` only.
- **secure**: same retry-loop refactor in both versions, and Hades has the new `SECURE_DONE` sentinel.
- **disrupt**: tiny — just uses voronoi_mask + register_active_target.

## Other infrastructure

- **comms_stats.py** (markerless only): instrumentation that writes per-state and per-flag stats to a directory at `PROFILE_DIR`. Toggled by `ENABLE_COMMS_STATS` in `main.py`. Not present in Hades.
- **main.py**: markerless adds `SPAWN_TURN` global, restructures profiler/comms_stats setup, and reorders `current_round` increment to *after* the timeout check.
- **core.py / spawn_plan.py**: small refactor (function rename `_spawn_toward_plan` → `_try_spawn_planned`, `pick_n_directions` takes width/height) — no strategic change.
- **turret_gunner.py / turret_sentinel.py**: markerless removes the `< conveyor_cost*4` early-return that suppressed expensive actions when titanium was tight.

## Strategic summary

Hades is the older codepath: marker comms infrastructure preserved, single-tile claims, monotonic failure state, no sabotage. Hades_markerless is the explicit response to "what if markers were free of cost but useless" — every place where markers carried information (claim ownership, target intent, explore frontier shaping) is replaced with positional inference, and the bot adds sabotage as a new low-cost pressure state to spend the surplus builder time. The TTL'd failure caches and sticky targets are quality-of-life improvements that flow naturally from the recoverability assumption "the world changes, retry old failures."
