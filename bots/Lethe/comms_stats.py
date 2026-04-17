from cambc import Controller

import json
from pathlib import Path

PROFILE_DIR = Path("profiles")
SUMMARY_PATH = PROFILE_DIR / "comm_stats_summary.txt"
DETAIL_PREFIX = "comm_stats_unit_"
ENABLED = False

_unit_id: int | None = None
_detail_path: Path | None = None
_stats = {
    "unit_id": 0,
    "rounds": 0,
    "markers_read": 0,
    "tiles_learned": 0,
    "tiles_known": 0,
    "tiles_conflict": 0,
    "learned_manhattan_distance_sum": 0,
    "known_manhattan_distance_sum": 0,
    "learned_chebyshev_distance_sum": 0,
    "known_chebyshev_distance_sum": 0,
    "last_round": 0,
}


def prepare_dir() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def is_enabled() -> bool:
    return ENABLED


def init(c: Controller) -> None:
    if not ENABLED:
        return

    global _unit_id, _detail_path, _stats

    prepare_dir()
    _unit_id = c.get_id()
    _detail_path = PROFILE_DIR / f"{DETAIL_PREFIX}{_unit_id}.json"
    _stats = {
        "unit_id": _unit_id,
        "rounds": 0,
        "markers_read": 0,
        "tiles_learned": 0,
        "tiles_known": 0,
        "tiles_conflict": 0,
        "learned_manhattan_distance_sum": 0,
        "known_manhattan_distance_sum": 0,
        "learned_chebyshev_distance_sum": 0,
        "known_chebyshev_distance_sum": 0,
        "last_round": 0,
    }
    _load_existing()
    _write_unit_stats()
    _write_summary()


def record_round(
    current_round: int,
    markers_read: int,
    tiles_learned: int,
    tiles_known: int,
    tiles_conflict: int,
    learned_manhattan_distance_sum: int,
    known_manhattan_distance_sum: int,
    learned_chebyshev_distance_sum: int,
    known_chebyshev_distance_sum: int,
) -> None:
    if not ENABLED or _detail_path is None:
        return

    _stats["rounds"] += 1
    _stats["markers_read"] += markers_read
    _stats["tiles_learned"] += tiles_learned
    _stats["tiles_known"] += tiles_known
    _stats["tiles_conflict"] += tiles_conflict
    _stats["learned_manhattan_distance_sum"] += learned_manhattan_distance_sum
    _stats["known_manhattan_distance_sum"] += known_manhattan_distance_sum
    _stats["learned_chebyshev_distance_sum"] += learned_chebyshev_distance_sum
    _stats["known_chebyshev_distance_sum"] += known_chebyshev_distance_sum
    _stats["last_round"] = current_round

    _write_unit_stats()
    _write_summary()


def _load_existing() -> None:
    if _detail_path is None or not _detail_path.exists():
        return

    try:
        with _detail_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    for key in _stats:
        if key in data:
            _stats[key] = data[key]


def _write_unit_stats() -> None:
    if _detail_path is None:
        return

    tmp_path = _detail_path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(_stats, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp_path.replace(_detail_path)


def _iter_unit_stats():
    if not PROFILE_DIR.exists():
        return

    for path in sorted(PROFILE_DIR.glob(f"{DETAIL_PREFIX}*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                yield json.load(f)
        except Exception:
            continue


def _safe_div(num: float, den: float) -> float:
    if not den:
        return 0.0
    return num / den


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _fmt_float(value: float) -> str:
    return f"{value:.3f}"


def _write_summary() -> None:
    units = list(_iter_unit_stats() or [])
    prepare_dir()

    total_builders = len(units)
    total_rounds = sum(unit.get("rounds", 0) for unit in units)
    total_markers = sum(unit.get("markers_read", 0) for unit in units)
    total_learned = sum(unit.get("tiles_learned", 0) for unit in units)
    total_known = sum(unit.get("tiles_known", 0) for unit in units)
    total_conflict = sum(unit.get("tiles_conflict", 0) for unit in units)
    total_learned_manhattan_distance = sum(unit.get("learned_manhattan_distance_sum", 0) for unit in units)
    total_known_manhattan_distance = sum(unit.get("known_manhattan_distance_sum", 0) for unit in units)
    total_learned_chebyshev_distance = sum(unit.get("learned_chebyshev_distance_sum", 0) for unit in units)
    total_known_chebyshev_distance = sum(unit.get("known_chebyshev_distance_sum", 0) for unit in units)
    total_sampled = total_learned + total_known + total_conflict
    total_non_conflict = total_learned + total_known

    avg_rounds = _safe_div(total_rounds, total_builders)
    avg_markers = _safe_div(total_markers, total_builders)
    avg_learned = _safe_div(total_learned, total_builders)
    avg_known = _safe_div(total_known, total_builders)
    avg_conflict = _safe_div(total_conflict, total_builders)

    lines = [
        "Builder comm stats",
        f"Builders reporting: {total_builders}",
        f"Total rounds: {total_rounds}",
        f"Average rounds per builder: {_fmt_float(avg_rounds)}",
        "",
        f"Total markers read: {total_markers}",
        f"Average markers read per builder: {_fmt_float(avg_markers)}",
        f"Total sampled tiles: {total_sampled}",
        f"Total tiles learned: {total_learned}",
        f"Total tiles already known: {total_known}",
        f"Total conflicting tiles: {total_conflict}",
        f"Average learned Manhattan distance from marker: {_fmt_float(_safe_div(total_learned_manhattan_distance, total_learned))}",
        f"Average known Manhattan distance from marker: {_fmt_float(_safe_div(total_known_manhattan_distance, total_known))}",
        f"Average learned Chebyshev distance from marker: {_fmt_float(_safe_div(total_learned_chebyshev_distance, total_learned))}",
        f"Average known Chebyshev distance from marker: {_fmt_float(_safe_div(total_known_chebyshev_distance, total_known))}",
        f"Average learned tiles per builder: {_fmt_float(avg_learned)}",
        f"Average known tiles per builder: {_fmt_float(avg_known)}",
        f"Average conflicting tiles per builder: {_fmt_float(avg_conflict)}",
        "",
        f"Learned per marker: {_fmt_float(_safe_div(total_learned, total_markers))}",
        f"Known per marker: {_fmt_float(_safe_div(total_known, total_markers))}",
        f"Conflicts per marker: {_fmt_float(_safe_div(total_conflict, total_markers))}",
        f"Learned share of non-conflict samples: {_fmt_pct(_safe_div(total_learned, total_non_conflict))}",
        f"Known share of non-conflict samples: {_fmt_pct(_safe_div(total_known, total_non_conflict))}",
        f"Learned share of all sampled tiles: {_fmt_pct(_safe_div(total_learned, total_sampled))}",
        f"Learned per round: {_fmt_float(_safe_div(total_learned, total_rounds))}",
        f"Markers per round: {_fmt_float(_safe_div(total_markers, total_rounds))}",
        "",
        "Per builder:",
    ]

    for unit in units:
        rounds = unit.get("rounds", 0)
        markers = unit.get("markers_read", 0)
        learned = unit.get("tiles_learned", 0)
        known = unit.get("tiles_known", 0)
        conflict = unit.get("tiles_conflict", 0)
        learned_manhattan_distance = unit.get("learned_manhattan_distance_sum", 0)
        known_manhattan_distance = unit.get("known_manhattan_distance_sum", 0)
        learned_chebyshev_distance = unit.get("learned_chebyshev_distance_sum", 0)
        known_chebyshev_distance = unit.get("known_chebyshev_distance_sum", 0)
        non_conflict = learned + known
        lines.append(
            " ".join(
                (
                    f"unit_{unit.get('unit_id', '?')}:",
                    f"rounds={rounds}",
                    f"markers={markers}",
                    f"learned={learned}",
                    f"known={known}",
                    f"conflict={conflict}",
                    f"learned/marker={_fmt_float(_safe_div(learned, markers))}",
                    f"learned-man-dist={_fmt_float(_safe_div(learned_manhattan_distance, learned))}",
                    f"known-man-dist={_fmt_float(_safe_div(known_manhattan_distance, known))}",
                    f"learned-cheb-dist={_fmt_float(_safe_div(learned_chebyshev_distance, learned))}",
                    f"known-cheb-dist={_fmt_float(_safe_div(known_chebyshev_distance, known))}",
                    f"learned-share={_fmt_pct(_safe_div(learned, non_conflict))}",
                    f"learned/round={_fmt_float(_safe_div(learned, rounds))}",
                    f"last_round={unit.get('last_round', 0)}",
                )
            )
        )

    tmp_path = SUMMARY_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")
    tmp_path.replace(SUMMARY_PATH)
