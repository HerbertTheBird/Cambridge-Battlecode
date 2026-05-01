#!/usr/bin/env python3
"""
Find Missing Maps — discover maps used in recent ladder/tournament games that
we don't have locally, and download them.

Workflow:
  1. List recent ladder matches via `cambc match list`.
  2. For each match, query `cambc match info` to get map names + match ID.
  3. For any map name we don't have locally, download a replay and extract
     the map bytes via the same protobuf machinery as extract_map_from_replay.py.
  4. Save extracted maps to maps_new/ (or --output-dir).

Idempotent: if we already have a map by name in any of the known map dirs,
we skip it. Use --force to redownload.

Usage:
    python find_missing_maps.py
    python find_missing_maps.py --limit 200 --output-dir maps_new
    python find_missing_maps.py --type tournament  (if tournament matches are exposed)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()


# ── Minimal hand-written protobuf decode for replay -> map bytes ────────────
# We don't depend on google.protobuf because the local Python may not have it.
# The replay format has Map at field 1 of the Replay message; we just need to
# locate that length-delimited field and re-emit it as a standalone message.

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def extract_map_bytes_from_replay_bytes(replay_bytes: bytes) -> bytes:
    """Find the length-delimited field 1 (Map) of the top-level Replay message and return its payload."""
    pos = 0
    while pos < len(replay_bytes):
        tag, pos = _read_varint(replay_bytes, pos)
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:
            _, pos = _read_varint(replay_bytes, pos)
        elif wire_type == 2:
            length, pos = _read_varint(replay_bytes, pos)
            payload = replay_bytes[pos:pos + length]
            pos += length
            if field_num == 1:
                return payload
        elif wire_type == 1:
            pos += 8
        elif wire_type == 5:
            pos += 4
        else:
            raise ValueError(f"Unknown wire type {wire_type} at byte {pos}")
    raise ValueError("Map field (field 1) not found in replay")
DEFAULT_MAP_DIRS = ["maps", "maps_intl", "maps_ladder", "maps_uk", "maps_mit"]


def _find_cambc_cli() -> str:
    """Locate the cambc CLI: prefer .venv/Scripts/cambc.exe on Windows, fall back to PATH."""
    venv_exe = SCRIPT_DIR / ".venv" / "Scripts" / "cambc.exe"
    if venv_exe.is_file():
        return str(venv_exe)
    venv_bin = SCRIPT_DIR / ".venv" / "bin" / "cambc"
    if venv_bin.is_file():
        return str(venv_bin)
    return "cambc"


CAMBC = _find_cambc_cli()


# ── CLI table parsing ────────────────────────────────────────────────────────

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# cambc may emit ASCII (|) or Unicode box-drawing (│) cell separators.
ROW_START_CHARS = ("|", "│")


def _run(cmd: list[str], timeout: int = 60) -> str:
    env = os.environ.copy()
    env["COLUMNS"] = "300"
    env["LINES"] = "100000"
    env["RICH_NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         env=env, timeout=timeout)
    return res.stdout


def list_matches(match_type: str = "ladder", limit: int = 100, team: str | None = None) -> list[str]:
    """Return list of match IDs from `cambc match list`."""
    ids: list[str] = []
    cursor: str | None = None
    fetched = 0
    while fetched < limit:
        chunk = min(100, limit - fetched)
        cmd = [CAMBC, "match", "list", "--type", match_type, "--limit", str(chunk)]
        if team:
            cmd += ["--team", team]
        if cursor:
            cmd += ["--cursor", cursor]
        out = _run(cmd)
        # Match IDs are full UUIDs in the table; rows start with | or │
        for line in out.splitlines():
            if not line.startswith(ROW_START_CHARS):
                continue
            m = UUID_RE.search(line)
            if m:
                mid = m.group(0)
                if mid not in ids:
                    ids.append(mid)
                    fetched += 1
                    if fetched >= limit:
                        break
        # Pagination cursor
        cursor_m = re.search(r"--cursor\s+'([^']+)'", out)
        if not cursor_m:
            break
        cursor = cursor_m.group(1)
    return ids


def match_maps(match_id: str) -> list[tuple[int, str]]:
    """Return [(game_idx, map_name), ...] for the given match id."""
    out = _run([CAMBC, "match", "info", match_id])
    rows: list[tuple[int, str]] = []
    for line in out.splitlines():
        if not line.startswith(ROW_START_CHARS):
            continue
        # Normalize Unicode box separator to ASCII pipe for splitting
        norm = line.replace("│", "|")
        parts = [p.strip() for p in norm.strip("|").split("|")]
        if len(parts) < 2:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        map_name = parts[1].strip()
        if map_name and not set(map_name) <= set("- "):
            rows.append((idx, map_name))
    return rows


def download_replay(match_id: str, game_idx: int, dest: Path) -> bool:
    """Download a single game's replay to dest. Returns True on success."""
    out = _run([CAMBC, "match", "replay", match_id, "-g", str(game_idx),
                "-o", str(dest)], timeout=120)
    if dest.exists() and dest.stat().st_size > 0:
        return True
    print(f"    download failed for {match_id} g{game_idx}: {out[-200:]}", file=sys.stderr)
    return False


def extract_map_bytes_from_replay(replay_path: Path) -> bytes:
    return extract_map_bytes_from_replay_bytes(replay_path.read_bytes())


# ── Local map index ─────────────────────────────────────────────────────────

def index_local_maps(dirs: list[Path]) -> dict[str, Path]:
    """name (without .map26) -> first matching path."""
    index: dict[str, Path] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.glob("*.map26"):
            if p.stem not in index:
                index[p.stem] = p
    return index


def maps_match_structurally(a: Path, b_bytes: bytes) -> bool:
    """Quick check: same byte length AND same prefix (50 bytes) is "probably the same map"."""
    a_bytes = a.read_bytes()
    if len(a_bytes) != len(b_bytes):
        return False
    return a_bytes[:64] == b_bytes[:64]


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Find tournament/ladder maps we don't have locally.")
    parser.add_argument("--limit", type=int, default=100,
                        help="Number of recent matches to scan (default 100).")
    parser.add_argument("--type", default="ladder", choices=["ladder", "unrated"],
                        help="Match type to scan (default: ladder).")
    parser.add_argument("--team", default=None,
                        help="Limit matches to those involving this team (name or ID).")
    parser.add_argument("--output-dir", type=Path, default=Path("maps_new"),
                        help="Where to save newly-found maps (default maps_new/).")
    parser.add_argument("--map-dirs", nargs="+", default=DEFAULT_MAP_DIRS,
                        help="Directories to scan for already-known maps.")
    parser.add_argument("--force", action="store_true",
                        help="Redownload even if a same-name map already exists.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only list missing map names; do not download anything.")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    map_dirs = [Path(d) for d in args.map_dirs]
    local = index_local_maps(map_dirs)
    print(f"Indexed {len(local)} local maps across {len(map_dirs)} dirs.")

    print(f"Listing last {args.limit} {args.type} matches...")
    match_ids = list_matches(match_type=args.type, limit=args.limit, team=args.team)
    print(f"Got {len(match_ids)} match IDs.")

    seen_maps: dict[str, str] = {}   # map_name -> example match_id (for download fallback)
    seen_pairs: dict[str, int] = {}  # map_name -> game_idx
    for i, mid in enumerate(match_ids, 1):
        if i % 10 == 0:
            print(f"  scanned {i}/{len(match_ids)} matches; found {len(seen_maps)} unique maps")
        for game_idx, name in match_maps(mid):
            if name not in seen_maps:
                seen_maps[name] = mid
                seen_pairs[name] = game_idx

    print(f"\nDiscovered {len(seen_maps)} unique map names across recent matches.")

    missing: list[tuple[str, str, int]] = []
    for name, mid in seen_maps.items():
        if name in local and not args.force:
            continue
        missing.append((name, mid, seen_pairs[name]))

    print(f"  Already have: {len(seen_maps) - len(missing)}")
    print(f"  Missing:      {len(missing)}")

    if not missing:
        print("  Nothing to do.")
        return 0

    if args.dry_run:
        print("\nMissing maps (dry-run, not downloading):")
        for name, mid, gi in missing:
            print(f"  {name}  (from match {mid} game {gi})")
        return 0

    print(f"\nDownloading missing maps to {args.output_dir} ...")
    with tempfile.TemporaryDirectory(prefix="map_extract_") as tmp:
        tmp_path = Path(tmp)
        for name, mid, gi in missing:
            replay_path = tmp_path / f"{mid}_g{gi}.replay26"
            print(f"  {name}  <- {mid} g{gi}")
            if not download_replay(mid, gi, replay_path):
                continue
            try:
                map_bytes = extract_map_bytes_from_replay(replay_path)
            except Exception as exc:
                print(f"    extract failed: {exc}", file=sys.stderr)
                continue
            out_path = args.output_dir / f"{name}.map26"
            # Don't overwrite if structurally equivalent already exists.
            if out_path.exists() and not args.force:
                if maps_match_structurally(out_path, map_bytes):
                    print(f"    already saved at {out_path}, skipping")
                    continue
            out_path.write_bytes(map_bytes)
            print(f"    saved -> {out_path} ({out_path.stat().st_size} bytes)")

    print(f"\nDone. New maps in {args.output_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
