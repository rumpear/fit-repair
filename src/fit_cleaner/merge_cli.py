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
        description="Склеивает несколько записей одной поездки в один FIT-файл. "
        "Разрыв между записями становится паузой.",
    )
    parser.add_argument("inputs", type=Path, nargs="+", help="исходные .fit, порядок не важен")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="куда сохранить (по умолчанию <первый по времени>.merged.fit)",
    )
    parser.add_argument(
        "--bridge-gap", action="store_true",
        help="добавить к дистанции расстояние по прямой между записями",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="склеить, даже если между записями больше 2 ч или отличается вид спорта",
    )
    parser.add_argument("--dry-run", action="store_true", help="только показать отчёт, файл не писать")
    args = parser.parse_args(argv)

    sources: list[Path] = args.inputs
    if len(sources) < 2:
        parser.error("нужно хотя бы два файла")
    for src in sources:
        if not src.is_file():
            parser.error(f"файл не найден: {src}")

    activities = []
    for src in sources:
        try:
            activities.append(read_activity(src))
        except fitdecode.FitError as exc:
            print(f"Не удалось прочитать {src.name}: {exc}", file=sys.stderr)
            return 1

    cfg = MergeConfig(bridge_gap=args.bridge_gap, force=args.force)
    try:
        result = merge(activities, cfg, [src.name for src in sources])
    except MergeError as exc:
        print(f"Не удалось склеить: {exc}", file=sys.stderr)
        return 1

    first = sources[result.order[0]]
    dst: Path = args.output or first.with_name(first.stem + ".merged.fit")
    if any(dst.resolve() == src.resolve() for src in sources):
        parser.error("нельзя перезаписывать исходный файл")

    print(format_merge_report(result))

    if args.dry_run:
        print("\n--dry-run: файл не записан")
        return 0

    data = writer.apply_patches(result.activity, result.patches)
    if writer.verify(data) != len(result.activity.messages):
        print("Проверка результата не прошла: число сообщений изменилось", file=sys.stderr)
        return 1
    writer.write(dst, data)
    print(f"\nСохранено: {dst}")
    print(f'Теперь почистите GPS: fit-cleaner "{dst}"')
    return 0
