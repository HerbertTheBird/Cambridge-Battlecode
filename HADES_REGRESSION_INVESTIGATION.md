# Hades Winrate Regression Investigation

Test setup: `_test_variants.py` — 2 maps × 2 opponents (Lethe, v872) × 2 sides = 8 matches per variant. Winrate = wins / 8.

## Per-commit timeline

Commit chain (oldest→newest, ancestor walk):

| # | Commit | Title | Winrate vs Lethe+v872 | Δ vs prior | Notes |
|---|--------|-------|----------------------|-----------|-------|
| 1 | `c082a41` | (peak baseline) | **87.5%** / 81.2% | — | Best known winrate |
| 2 | `1e59040` | bug fixes | (parallel branch off 6ea7b21) | — | Not on c082a41's line — branched independently |
| 3 | `58bc752` | Merge `1e59040` into main (parent c082a41) | **50.0%** | **−37.5pt** | The merge resolution itself is the regression — see route.py bisection |
| 4 | `8982951` | instant attack prefer | ~50% | 0pt | Gameplay neutral despite earlier suspicion |
| 5 | `b39b7ee` | small heal fix | **62.5%** | **+12.5pt** | Recovery (changes in `map_info.py`, `heal.py`) |
| 6 | `215352f` | small changes | (~25%) | **−37.5pt** | See decomposition below |
| 7 | `fe3e88a` | oops left in resign | **50.0%** | **+25pt** | Reverts `rc.resign()` from 215352f only |
| 8 | `18f7527` | misc changes | **62.5%** | **+12.5pt** | Recovery |
| 9 | `1f7a0e4` | new bot submitted | **62.5%** / 43.8% | 0pt | Current HEAD; asymmetric vs side |

Net: 87.5% → 62.5% = **−25pt** total regression, distributed unevenly across the chain.

## Decomposition of the 58bc752 merge regression (−37.5pt)

The merge brought 6 hunks into `units/states/route.py`. Per-hunk variants tested on c082a41 base:

| Variant | Description | Winrate | Δ |
|---------|-------------|---------|----|
| `rA` | BRIDGE/SPLITTER added to barrier mask filter | 87.5% | 0 |
| `rB` | BRIDGE/SPLITTER added to `_BARRIER_DESTROYABLE` | 87.5% | 0 |
| `rC` | Don't-destroy-occupied condition for barrier | 87.5% | 0 |
| `rD` | Don't-destroy-occupied condition for foundry | 87.5% | 0 |
| `rE` | `range(4)→range(8)` BFS + removed `dist_sq==1` guard for `near_enemy` | 50.0% | **−37.5** |
| `rE1` | rE minus the `range` change (only removed guard) | 87.5% | 0 |
| `rE2` | rE minus the guard removal (only `range(4)→range(8)`) | 75.0% | **−12.5** |
| `rF` | Permissive AND→OR condition at L285 | 87.5% | 0 |
| `rEF` | rE + rF combined | 50.0% | −37.5 (catastrophic interaction) |
| `rE1F` | rE1 + rF (current Hades's shape) | 87.5% | 0 |

**Conclusion**: the dominant regression is the rE+rF interaction (range-8 BFS with the permissive condition). The range expansion alone costs 12.5pt; combined with permissive condition, 37.5pt.

215352f later reverted `range(8)→range(4)`, recovering most of this loss.

## Decomposition of 215352f (−37.5pt before fe3e88a)

215352f changed: `log.py`, `pathing.py`, `units/core.py` (resign bug), `units/states/{attack.py, harvest.py, heal.py, route.py, secure.py}`.

Components identified so far:

| Component | Δ |
|-----------|----|
| route.py `range(8)→range(4)` revert | **+12.5pt** (good) |
| `rc.resign()` bug at round 600 (core.py) | **−25pt** (reverted in fe3e88a) |
| Other changes (attack.py, pathing.py, harvest.py, heal.py, secure.py) | **−25pt** UNEXPLAINED |

The "other changes −25pt" is what we are bisecting next via per-file reverts on top of fe3e88a.

### Per-file revert variants (on top of fe3e88a / Hades_v_215_no_resign, baseline 50%)

| Variant | File reverted to b39b7ee | Winrate | Δ vs 50% baseline |
|---------|--------------------------|---------|---|
| `Hades_v_215_no_resign` | (control, no revert) | 50.0% | 0 |
| `Hades_v_215_revert_pathing` | `pathing.py` | 0.0% | −50pt (215 bot depends on new pathing) |
| `Hades_v_215_revert_attack` | `units/states/attack.py` | 0.0% | −50pt (breaks `wanted_attack_tiles` API) |
| `Hades_v_215_revert_harvest` | `units/states/harvest.py` | 37.5% | −12.5pt |
| `Hades_v_215_revert_heal` | `units/states/heal.py` | **75.0%** | **+25pt** ← regression source |
| `Hades_v_215_revert_secure` | `units/states/secure.py` | 37.5% | −12.5pt |

**Conclusion**: `heal.py` carries the entire −25pt non-resign regression in 215352f. Reverts of `pathing.py` and `attack.py` worsen because the 215 bot's harvest/secure code depends on the new `attack.wanted_attack_tiles()` API and the bot relies on the new pathing behaviour.

### `heal.py` sub-changes (215352f vs b39b7ee)

Three substantive changes:

- **(A)** Lines 50, 69 — removed `& map_info._bm_visible` filter from `_find_chase_target` (now considers stale enemy bot positions).
- **(B)** Line 137 — `_heal_targets()` extended to include `(SENTINEL | GUNNER) & _bm_enemy_turret_threat` (heals threatened forward turrets).
- **(C)** Lines 163-164 — added `if _heal_targets(): return 1.5` (new low-priority heal score path).

### `heal.py` sub-bisection results (215_no_resign 50% baseline)

| Variant | Reverts | Winrate | Notes |
|---------|---------|---------|-------|
| `Hades_v_215_no_resign` | (control) | 50.0% | |
| `Hades_v_215_heal_visible` | A only | 62.5% | A contributes ~12.5pt |
| `Hades_v_215_heal_targetsB` | B only | 25.0% | B alone hurts (interaction with C) |
| `Hades_v_215_heal_scoreC` | C only | 50.0% | C alone neutral (B still feeds C the bigger mask) |
| `Hades_v_215_heal_AB` | A+B | 62.5% | reverting B adds nothing to A |
| `Hades_v_215_heal_AC` | A+C | **75.0%** | **full recovery** — A+C is the regression |
| `Hades_v_215_heal_BC` | B+C | 50.0% | matches baseline |
| `Hades_v_215_revert_heal` | A+B+C | 75.0% | confirms AC captures all damage |

**Conclusion**: the 215352f −25pt regression is **A + C**:
- **A**: dropped `& _bm_visible` filter on `_find_chase_target`, so chasers now use stale enemy positions outside vision and waste turns.
- **C**: new `if _heal_targets(): return 1.5` score path. With chase already broken by A, the new low-priority heal trigger competes with secure/harvest and amplifies the misallocation.
- **B** (`_heal_targets()` extension to threatened sentinels/gunners) is neutral or positive in isolation.

## Suggested fix paths

1. Restore `& map_info._bm_visible` on heal.py's L50 and L69 (`_find_chase_target` enemy_bots and friendly mask) — the highest-leverage single fix (~+12.5pt).
2. Remove the `if _heal_targets(): return 1.5` score path on heal.py L163-164 — recovers another ~12.5pt when paired with #1.
3. Optional: keep change B (heal_targets extension) as it's not harmful and may help against turret-heavy opponents.

## A+C revert applied to current HEAD

Tested `Hades_v_HEAD_heal_AC` (= current HEAD heal.py with both A and C reverted):

| Variant | Lethe | v872 | Total |
|---|---|---|---|
| `Hades` (HEAD) | 2-2 | 3-1 | **62.5%** |
| `Hades_v_HEAD_heal_AC` | 2-2 | 2-2 | **50.0%** |

Reverting A+C on HEAD does **not** recover (and may slightly hurt within noise). The 215352f-era fix doesn't translate.

### Why the fix doesn't apply to HEAD

Between fe3e88a → HEAD:
- `18f7527`: pathing.py (80 lines), harvest.py, secure.py, turrets — substantial behaviour shift around resource flow.
- `1f7a0e4`: map_info.py (137 lines) — bitmask infrastructure changed, likely altering how `_bm_visible` is populated.
- heal.py L278: `dist <= 4` distance bound on heal-move was removed in HEAD — heals now move from any distance, which interacts strongly with C's score=1.5 trigger.

So the heal A+C regression on the 215352f base is real, but on HEAD the dist-bound removal and map_info changes dominate the equilibrium. To recover winrate on HEAD, the next bisection target should be the dist-bound removal (heal.py L278) and the 18f7527 pathing/harvest/secure changes.

## Outstanding question

The 58bc752 merge regression (route.py near_enemy `range(8)` BFS) was reverted in 215352f, so route.py is no longer a contributor. The remaining HEAD regression vs c082a41 is multi-factor: the heal.py A+C interaction is one component, and whatever 18f7527/1f7a0e4 introduced (likely heal.py dist-bound removal + map_info changes) is another.

## 18f7527's specific contribution

`18f7527` changed `bots/Hades/units/states/heal.py` in *one* place — removing the `dist <= 4` bound at L278 — alongside larger changes to pathing.py (80 lines), harvest.py (20), secure.py (25), turret_gunner.py (24), turret_sentinel.py (24).

Test on 18f7527 base (62.5%):

| Variant | Description | Winrate |
|---|---|---|
| `Hades_c18f7527` | (control) | 62.5% |
| `Hades_v_18f_heal_AC` | + heal A+C revert | 50.0% |
| `Hades_v_18f_heal_AC_dist4` | + heal A+C revert + restore `dist<=4` | 50.0% |

Prediction was that `18f_heal_AC_dist4` should equal `fe3e88a + heal A+C revert = 75%`, but it lands at 50%. So 18f7527's pathing/harvest/secure/turret changes have moved the bot to a different equilibrium where the heal-A+C-revert no longer recovers, even when the heal logic is otherwise made identical to fe3e88a.

Fix priority is unclear — there is no clean "go back" that recovers winrate at the HEAD/18f7527 horizon.

### Pathing.py probe

| Variant | Description | Winrate |
|---|---|---|
| `Hades_v_18f_heal_AC_dist4` | (above) | 50.0% |
| `Hades_v_18f_full_revert` | + pathing.py reverted to fe3e88a | 62.5% |

Reverting 18f7527's pathing.py recovers +12.5pt — pathing changes are another component of the 18f7527 regression vector. Still not at 75% so harvest/secure/turret_gunner/turret_sentinel changes account for the remaining gap.

## High-replication confirmation (NUM_MAPS=4, 16 matches per variant)

The earlier 8-match tests had a 12.5pt noise floor that masked the real signal on HEAD. Re-tested key variants at 4 maps × 2 opponents × 2 sides = 16 matches:

| Variant | 16-match winrate | 8-match was |
|---|---|---|
| `Hades_cfe3e88a` | 46.7% | 50% |
| `Hades_v_215_revert_heal` (fe3e88a + heal A+B+C revert) | **62.5%** | 75% |
| `Hades` (HEAD baseline) | **53.3%** | 62.5% |
| `Hades_v_HEAD_heal_AC_dist4` (HEAD + heal A+C revert + restore dist≤4) | **64.3%** | 62.5% |

**Corrected conclusion**: the heal-A+C-revert fix *does* recover on HEAD when paired with restoring the `dist <= 4` heal-distance bound. **+11pt** improvement vs HEAD baseline (53.3% → 64.3%). The 8-match runs simply lacked the statistical power to see this.

The heal regression at 215352f is therefore a *durable* problem present in HEAD — earlier reasoning that "HEAD reached a different equilibrium" was an artifact of noise.

## Recommended fix on HEAD

Apply these 3 edits to `bots/Hades/units/states/heal.py` to recover ~11pt:

1. **L50** — restore `& map_info._bm_visible` on `enemy_bots`:
   ```python
   enemy_bots = map_info._bm_enemy_bots & map_info._bm_visible
   ```
2. **L69** — restore `& map_info._bm_visible` on the friendly mask:
   ```python
   mask = friendly_bots & ~my_bit & map_info._bm_visible & enemy_zone_4
   ```
3. **L163-164** — remove the score=1.5 path:
   ```python
   # delete:  if _heal_targets(): return 1.5
   ```
4. **L278** — restore `dist <= 4` heal-distance bound:
   ```python
   if best is not None and dist <= 4:
       nav.move_adjacent(best, avoid_turret=False)
   ```

This is the `Hades_v_HEAD_heal_AC_dist4` variant.

## Final picture

The c082a41 → HEAD regression is a multi-component, multi-stage problem rather than a single bad commit:

- 58bc752 merge: route.py rE+rF interaction → −37.5pt (mostly reverted at 215352f).
- 215352f: heal.py A+C → −25pt on its base; partially compensated by 18f7527's removal of `dist <= 4`.
- 18f7527: pathing.py + harvest/secure/turret rewrites → unclear net effect; pathing.py costs ~12.5pt in the AC-revert experiment.
- 1f7a0e4: map_info bitmask rewrite (137 lines) — reorganises the substrate enough that earlier per-file fixes no longer apply cleanly.

The total observed loss (87.5% → 62.5% = 25pt) is split across several small contributors, each near the 12.5pt noise floor of an 8-match test. Robust further bisection requires either higher replication (NUM_MAPS≥4 or multi-seed averaging) or hunk-level surgery within pathing.py / map_info.py.

## Heal.py L278 dist-bound test on HEAD

| Variant | Lethe | v872 | Total |
|---|---|---|---|
| `Hades` (HEAD baseline) | 2-2 | 3-1 | **62.5%** |
| `Hades_v_HEAD_heal_AC` | 2-2 | 2-2 | **50.0%** |
| `Hades_v_HEAD_dist4` | 2-2 | 2-2 | **50.0%** |
| `Hades_v_HEAD_heal_AC_dist4` | 2-2 | 3-1 | **62.5%** |

All HEAD-derived variants cluster at 50–62.5%, within the 1-match (12.5pt) noise floor of an 8-match test. The 215352f-era heal A+C fix and the dist-bound restoration both modify HEAD without producing a clear improvement.

**Interpretation**: 18f7527 + 1f7a0e4 (substantial pathing/map_info/turret rewrites) reorganised the bot enough that the heal.py A+C interaction is no longer the dominant lever. The HEAD bot is at a different equilibrium where the same heal toggles do not produce the same effect.

To make further progress on the c082a41→HEAD regression, the next investigation should:
- Either run more replications (16+ matches per variant) to break the noise floor.
- Or shift focus to the 18f7527/1f7a0e4 changes (map_info bitmask infrastructure, pathing.py, turret_gunner/sentinel) where the equilibrium shift originates.


## Test runner

`_test_variants.py` invokes `tournament.run_match()` with `NUM_MAPS=2`, `THREADS=8`, `SEED=1`, `OPPONENTS=["Lethe","v872"]`. 8 matches per variant.

## Naming conventions

- `Hades_c<sha>` — full snapshot of commit `<sha>` via `git archive`.
- `Hades_v_<name>` — manually constructed variant for testing a specific change.
