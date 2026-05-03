#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import re
import shlex
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path


WINNER_RE = re.compile(r"Winner:\s+([^\s]+)")
TURN_RE = re.compile(r"turn\s+(\d+)\)")


@dataclass
class MatchResult:
    map_name: str
    map_size: tuple[int, int] | None
    winner: str | None
    turn: int | None
    return_code: int
    elapsed_s: float


def load_defaults(config_path: Path) -> tuple[Path, int]:
    maps_dir = Path("maps")
    seed = 1

    if not config_path.exists():
        return maps_dir, seed

    with config_path.open("rb") as handle:
        config = tomllib.load(handle)

    maps_dir = Path(config.get("maps_dir", "maps"))
    seed = int(config.get("seed", 1))
    return maps_dir, seed


def discover_maps(maps_dir: Path) -> list[Path]:
    if not maps_dir.exists():
        raise FileNotFoundError(f"Maps directory does not exist: {maps_dir}")

    maps = sorted(path for path in maps_dir.glob("*.map26") if path.is_file())
    if not maps:
        raise FileNotFoundError(f"No .map26 files found in: {maps_dir}")
    return maps


def parse_winner(output: str) -> str | None:
    match = WINNER_RE.search(output)
    return match.group(1) if match else None


def parse_turn(output: str) -> int | None:
    match = TURN_RE.search(output)
    if not match:
        return None
    return int(match.group(1))


def read_map_size(map_path: Path) -> tuple[int, int] | None:
    data = map_path.read_bytes()

    def read_varint(start: int) -> tuple[int, int] | None:
        value = 0
        shift = 0
        index = start
        while index < len(data):
            byte = data[index]
            index += 1
            value |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                return value, index
            shift += 7
            if shift > 63:
                return None
        return None

    index = 0
    width: int | None = None
    height: int | None = None
    while index < len(data):
        tag_read = read_varint(index)
        if tag_read is None:
            return None
        tag, index = tag_read
        field = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # varint
            value_read = read_varint(index)
            if value_read is None:
                return None
            value, index = value_read
            if field == 1:
                width = value
            elif field == 2:
                height = value
        elif wire_type == 2:  # length-delimited
            length_read = read_varint(index)
            if length_read is None:
                return None
            length, index = length_read
            index += length
            if index > len(data):
                return None
        else:
            return None

        if width is not None and height is not None:
            return width, height

    return None


def run_match(command: list[str]) -> tuple[int, str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert process.stdout is not None
    output_lines: list[str] = []
    for line in process.stdout:
        print(line, end="")
        output_lines.append(line)

    return_code = process.wait()
    return return_code, "".join(output_lines)


def run_match_captured(command: list[str]) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout or ""


def execute_match(
    map_path: Path,
    command: list[str],
    stream_output: bool,
) -> tuple[MatchResult, str]:
    match_started = time.perf_counter()
    if stream_output:
        return_code, output = run_match(command)
    else:
        return_code, output = run_match_captured(command)

    elapsed_s = time.perf_counter() - match_started
    result = MatchResult(
        map_name=map_path.name,
        map_size=read_map_size(map_path),
        winner=parse_winner(output),
        turn=parse_turn(output),
        return_code=return_code,
        elapsed_s=elapsed_s,
    )
    return result, output


def format_seconds(seconds: float) -> str:
    return f"{seconds:.2f}s"


def print_summary(bot_a: str, bot_b: str, results: list[MatchResult]) -> None:
    wins_a = 0
    wins_b = 0
    ambiguous_wins = 0
    errors = 0

    for result in results:
        if result.return_code != 0:
            errors += 1
            continue
        if result.winner is None:
            continue
        if bot_a == bot_b and result.winner == bot_a:
            ambiguous_wins += 1
            continue
        if result.winner == bot_a:
            wins_a += 1
        elif result.winner == bot_b:
            wins_b += 1

    row_values: list[tuple[str, str, str, str, str, str]] = []
    for result in results:
        winner = result.winner or "-"
        size = (
            f"{result.map_size[0]}x{result.map_size[1]}"
            if result.map_size is not None
            else "-"
        )
        turn = str(result.turn) if result.turn is not None else "-"
        status = "OK" if result.return_code == 0 else f"ERR({result.return_code})"
        row_values.append(
            (
                result.map_name,
                size,
                winner,
                turn,
                status,
                format_seconds(result.elapsed_s),
            )
        )

    headers = ("Map", "Size", "Winner", "Turn", "Status", "Time")
    widths = [
        max(len(headers[idx]), *(len(row[idx]) for row in row_values))
        if row_values
        else len(headers[idx])
        for idx in range(len(headers))
    ]
    table_width = sum(widths) + (len(headers) - 1)
    divider_width = max(80, table_width)

    print("\n" + "=" * divider_width)
    print("Benchmark Summary")
    print("=" * divider_width)
    print(f"Total maps: {len(results)}")
    print(f"Slot A ({bot_a}) wins: {wins_a}")
    print(f"Slot B ({bot_b}) wins: {wins_b}")
    if ambiguous_wins > 0:
        print(f"Ambiguous wins (same bot name in both slots): {ambiguous_wins}")
    print(f"Errors: {errors}")
    print(f"Total elapsed: {format_seconds(sum(r.elapsed_s for r in results))}")
    print("-" * divider_width)
    print(" ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    print("-" * divider_width)

    for row in row_values:
        print(" ".join(row[idx].ljust(widths[idx]) for idx in range(len(headers))))


def build_arg_parser(default_maps_dir: Path, default_seed: int) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run two bots against each other on every available map and print "
            "all output to the console."
        )
    )
    parser.add_argument("bot_a", help="First bot path/name (as accepted by `cambc run`).")
    parser.add_argument("bot_b", help="Second bot path/name (as accepted by `cambc run`).")
    parser.add_argument(
        "--maps-dir",
        type=Path,
        default=default_maps_dir,
        help=f"Directory containing .map26 files (default: {default_maps_dir}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=default_seed,
        help=f"Seed passed to each `cambc run` (default: {default_seed}).",
    )
    parser.add_argument(
        "--map-filter",
        default="",
        help="Only run maps whose file name contains this substring.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop the benchmark immediately if a match command fails.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=1,
        help=(
            "Number of maps to run in parallel. "
            "Use 1 for sequential execution (default)."
        ),
    )
    return parser


def main() -> int:
    config_maps_dir, config_seed = load_defaults(Path("cambc.toml"))
    parser = build_arg_parser(config_maps_dir, config_seed)
    args = parser.parse_args()

    try:
        maps = discover_maps(args.maps_dir)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.map_filter:
        maps = [m for m in maps if args.map_filter in m.name]
        if not maps:
            print(
                f"Error: no maps in '{args.maps_dir}' match filter '{args.map_filter}'.",
                file=sys.stderr,
            )
            return 2

    if args.threads < 1:
        print("Error: --threads must be >= 1.", file=sys.stderr)
        return 2

    print("Starting benchmark")
    print(f"Bots: {args.bot_a} vs {args.bot_b}")
    print(f"Maps dir: {args.maps_dir}")
    print(f"Seed: {args.seed}")
    print(f"Threads: {args.threads}")
    print(f"Maps to run: {len(maps)}")

    results: list[MatchResult] = []
    started = time.perf_counter()

    if args.threads == 1:
        for index, map_path in enumerate(maps, start=1):
            print("\n" + "=" * 80)
            print(f"[{index}/{len(maps)}] Running map: {map_path.name}")
            command = [
                "cambc",
                "run",
                args.bot_a,
                args.bot_b,
                str(map_path),
                "--seed",
                str(args.seed),
            ]
            print(f"$ {shlex.join(command)}")
            print("-" * 80)

            result, _ = execute_match(map_path, command, stream_output=True)
            results.append(result)

            if result.return_code != 0:
                print(f"Match failed on {map_path.name} (exit code {result.return_code}).")
                if args.stop_on_error:
                    break
    else:
        print(
            "Parallel mode enabled: match output is captured and printed "
            "when each map finishes."
        )
        commands_by_index: dict[int, list[str]] = {}
        paths_by_index: dict[int, Path] = {}
        results_by_index: dict[int, MatchResult] = {}
        futures: dict[Future[tuple[MatchResult, str]], int] = {}
        executor = ThreadPoolExecutor(max_workers=args.threads)
        try:
            for index, map_path in enumerate(maps, start=1):
                command = [
                    "cambc",
                    "run",
                    args.bot_a,
                    args.bot_b,
                    str(map_path),
                    "--seed",
                    str(args.seed),
                ]
                commands_by_index[index] = command
                paths_by_index[index] = map_path
                future = executor.submit(execute_match, map_path, command, False)
                futures[future] = index

            for future in as_completed(futures):
                index = futures[future]
                map_path = paths_by_index[index]
                command = commands_by_index[index]
                result, output = future.result()
                results_by_index[index] = result

                print("\n" + "=" * 80)
                print(f"[{index}/{len(maps)}] Finished map: {map_path.name}")
                print(f"$ {shlex.join(command)}")
                print("-" * 80)
                print(output, end="" if output.endswith("\n") else "\n")

                if result.return_code != 0:
                    print(
                        f"Match failed on {map_path.name} (exit code {result.return_code})."
                    )
                    if args.stop_on_error:
                        for pending in futures:
                            if pending is not future and not pending.done():
                                pending.cancel()
                        break
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        results = [results_by_index[idx] for idx in sorted(results_by_index)]

    print_summary(args.bot_a, args.bot_b, results)
    print(f"\nFinished in {format_seconds(time.perf_counter() - started)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())