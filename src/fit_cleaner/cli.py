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
        description="Убирает из FIT-файла последствия глушения/подмены GPS: "
        "невозможные координаты, всплески скорости и лишнюю дистанцию.",
    )
    parser.add_argument("input", type=Path, help="исходный .fit")
    parser.add_argument("-o", "--output", type=Path, help="куда сохранить (по умолчанию <имя>.clean.fit)")
    parser.add_argument(
        "--max-speed", type=float, default=100.0, metavar="KMH",
        help="максимально возможная скорость, км/ч (по умолчанию 100)",
    )
    parser.add_argument(
        "--interpolate-gap", type=float, default=30.0, metavar="SEC",
        help="интерполировать удалённые точки в дырах не длиннее SEC секунд; 0 - не интерполировать",
    )
    parser.add_argument(
        "--no-sensor-check", action="store_true",
        help="не сверять GPS с дистанцией от датчика колеса",
    )
    parser.add_argument("--dry-run", action="store_true", help="только показать отчёт, файл не писать")
    args = parser.parse_args(argv)

    src: Path = args.input
    dst: Path = args.output or src.with_name(src.stem + ".clean.fit")
    if not src.is_file():
        parser.error(f"файл не найден: {src}")
    if dst.resolve() == src.resolve():
        parser.error("нельзя перезаписывать исходный файл")

    cfg = Config(
        max_speed=args.max_speed / 3.6,
        interpolate_gap=args.interpolate_gap,
        sensor_check=not args.no_sensor_check,
    )

    try:
        activity = read_activity(src)
    except fitdecode.FitError as exc:
        print(f"Не удалось прочитать FIT: {exc}", file=sys.stderr)
        return 1

    result = clean(activity, cfg)
    print(format_report(result))

    if args.dry_run:
        print("\n--dry-run: файл не записан")
        return 0

    data = writer.apply_patches(activity, result.patches)
    if writer.verify(data) != len(activity.messages):
        print("Проверка результата не прошла: число сообщений изменилось", file=sys.stderr)
        return 1
    writer.write(dst, data)
    print(f"\nСохранено: {dst}")
    return 0
