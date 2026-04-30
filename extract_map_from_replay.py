#!/usr/bin/env python3
"""
extract_map_from_replay.py — extract the map from a .replay26 file and save it
as a .map26 file.

A .replay26 is a Replay protobuf whose field 1 is a Map message; .map26 is just
that Map message serialized on its own. So extracting amounts to parsing the
replay, taking replay.map, and writing it back out as a standalone protobuf.

The replay does not store the map's name, but we can identify it by comparing
the extracted map structurally against every .map26 file in known map
directories. If we find a match we use that name; otherwise we fall back to a
caller-supplied name (or "file" if none was given).

Usage:
    python extract_map_from_replay.py <replay.replay26> [output_dir] [--name NAME]

Example:
    python extract_map_from_replay.py replay.replay26 ./out --name custom_map
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cambc_pb2


SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_MAP_DIRS = [SCRIPT_DIR / "maps", SCRIPT_DIR / "maps_mit"]


def extract_map_bytes(replay_path: Path) -> bytes:
    """Parse the replay and return the standalone serialized Map bytes."""
    replay = cambc_pb2.Replay()
    with open(replay_path, "rb") as f:
        replay.ParseFromString(f.read())
    return replay.map.SerializeToString()


def _map_signature(m: cambc_pb2.Map) -> tuple:
    """Canonical, hashable representation of a Map for equality checks."""
    rows = tuple(tuple(row.tiles) for row in m.rows)
    cores = tuple(sorted(
        (c.team, c.position.x, c.position.y) for c in m.cores
    ))
    return (m.width, m.height, rows, cores)


def find_map_name(map_bytes: bytes, search_dirs: list[Path]) -> str | None:
    """Return the stem of a .map26 file whose contents match map_bytes, or None."""
    target = cambc_pb2.Map()
    target.ParseFromString(map_bytes)
    target_sig = _map_signature(target)

    for d in search_dirs:
        if not d.is_dir():
            continue
        for map_path in sorted(d.glob("*.map26")):
            candidate = cambc_pb2.Map()
            try:
                with open(map_path, "rb") as f:
                    candidate.ParseFromString(f.read())
            except Exception:
                continue
            if _map_signature(candidate) == target_sig:
                return map_path.stem
    return None


def extract_to_file(
    replay_path: Path,
    output_dir: Path,
    fallback_name: str = "file",
    search_dirs: list[Path] | None = None,
) -> tuple[Path, str | None]:
    """
    Extract the map from replay_path into output_dir as <name>.map26.

    The name is the matching map's stem if one is found in search_dirs;
    otherwise fallback_name. Returns (output_path, identified_name_or_None).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    map_bytes = extract_map_bytes(replay_path)

    dirs = search_dirs if search_dirs is not None else DEFAULT_MAP_DIRS
    identified = find_map_name(map_bytes, dirs)

    out_path = output_dir / f"{identified or fallback_name}.map26"
    with open(out_path, "wb") as f:
        f.write(map_bytes)
    return out_path, identified


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Extract the map from a .replay26 file into a .map26 file.",
    )
    parser.add_argument("replay", type=Path, help="Path to the .replay26 file")
    parser.add_argument(
        "output_dir", type=Path, nargs="?", default=Path.cwd(),
        help="Directory to write the .map26 file (default: current directory)",
    )
    parser.add_argument(
        "--name", default="file",
        help='Fallback name (without .map26) to use if the map is not '
             'recognised (default: "file")',
    )
    args = parser.parse_args(argv[1:])

    if not args.replay.is_file():
        print(f"Error: replay file not found: {args.replay}", file=sys.stderr)
        return 1

    out_path, identified = extract_to_file(
        replay_path=args.replay,
        output_dir=args.output_dir,
        fallback_name=args.name,
    )

    size = out_path.stat().st_size
    print(f"Wrote {out_path} ({size} bytes)")
    if identified:
        print(f"Identified map: {identified}")
    else:
        print(f"Map not recognised - saved as {out_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
