from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fitdecode

from fit_cleaner import writer
from fit_cleaner.merge import MergeConfig, MergeError, merge
from fit_cleaner.merge_report import format_merge_report
from fit_cleaner.reader import read_activity


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        prog="fit-merge",
        description="Joins several recordings of one ride into a single FIT file. "
        "The gap between recordings becomes a pause.",
    )
    parser.add_argument("inputs", type=Path, nargs="+", help="source .fit files, order does not matter")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="where to save (default <earliest>.merged.fit)",
    )
    parser.add_argument(
        "--bridge-gap", action="store_true",
        help="add the straight-line distance between recordings to the distance",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="merge even if the recordings are more than 2 h apart or the sport differs",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the report only, write nothing")
    args = parser.parse_args(argv)

    sources: list[Path] = args.inputs
    if len(sources) < 2:
        parser.error("at least two files are required")
    for src in sources:
        if not src.is_file():
            parser.error(f"file not found: {src}")

    activities = []
    for src in sources:
        try:
            activities.append(read_activity(src))
        except fitdecode.FitError as exc:
            print(f"Failed to read {src.name}: {exc}", file=sys.stderr)
            return 1

    cfg = MergeConfig(bridge_gap=args.bridge_gap, force=args.force)
    try:
        result = merge(activities, cfg, [src.name for src in sources])
    except MergeError as exc:
        print(f"Failed to merge: {exc}", file=sys.stderr)
        return 1

    first = sources[result.order[0]]
    dst: Path = args.output or first.with_name(first.stem + ".merged.fit")
    if any(dst.resolve() == src.resolve() for src in sources):
        parser.error("refusing to overwrite the source file")

    print(format_merge_report(result))

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    data = writer.apply_patches(result.activity, result.patches)
    if writer.verify(data) != len(result.activity.messages):
        print("Output verification failed: message count changed", file=sys.stderr)
        return 1
    writer.write(dst, data)
    print(f"\nSaved: {dst}")
    print(f'Now clean the GPS: fit-cleaner "{dst}"')
    return 0
