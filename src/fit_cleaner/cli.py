from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fitdecode

from fit_cleaner import writer
from fit_cleaner.model import Config
from fit_cleaner.pipeline import clean
from fit_cleaner.reader import read_activity
from fit_cleaner.report import format_report


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        prog="fit-cleaner",
        description="Removes the effects of GPS jamming/spoofing from a FIT file: "
        "impossible coordinates, speed spikes and the extra distance.",
    )
    parser.add_argument("input", type=Path, help="source .fit file")
    parser.add_argument("-o", "--output", type=Path, help="where to save (default <name>.clean.fit)")
    parser.add_argument(
        "--max-speed", type=float, default=100.0, metavar="KMH",
        help="maximum possible speed, km/h (default 100)",
    )
    parser.add_argument(
        "--interpolate-gap", type=float, default=30.0, metavar="SEC",
        help="interpolate removed points in gaps of at most SEC seconds; 0 disables",
    )
    parser.add_argument(
        "--no-sensor-check", action="store_true",
        help="do not cross-check GPS against the wheel sensor distance",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the report only, write nothing")
    args = parser.parse_args(argv)

    src: Path = args.input
    dst: Path = args.output or src.with_name(src.stem + ".clean.fit")
    if not src.is_file():
        parser.error(f"file not found: {src}")
    if dst.resolve() == src.resolve():
        parser.error("refusing to overwrite the source file")

    cfg = Config(
        max_speed=args.max_speed / 3.6,
        interpolate_gap=args.interpolate_gap,
        sensor_check=not args.no_sensor_check,
    )

    try:
        activity = read_activity(src)
    except fitdecode.FitError as exc:
        print(f"Failed to read FIT: {exc}", file=sys.stderr)
        return 1

    result = clean(activity, cfg)
    print(format_report(result))

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    data = writer.apply_patches(activity, result.patches)
    if writer.verify(data) != len(activity.messages):
        print("Output verification failed: message count changed", file=sys.stderr)
        return 1
    writer.write(dst, data)
    print(f"\nSaved: {dst}")
    return 0
