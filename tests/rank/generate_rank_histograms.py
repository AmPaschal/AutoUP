"""Generate LOC and cyclomatic complexity histograms for *_rank.csv files."""

from __future__ import annotations

import argparse
import csv
import logging
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_floats(rows: list[dict[str, str]], column: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        raw = row.get(column, "")
        if raw is None:
            continue
        raw = raw.strip()
        if not raw:
            continue
        try:
            values.append(float(raw))
        except ValueError:
            logger.warning("Skipping non-numeric %s value: %r", column, raw)
    return values


def _sqrt_bins(count: int) -> int:
    if count <= 0:
        return 1
    return max(1, int(math.sqrt(count)))


def _histogram(
    values: list[float],
    title: str,
    xlabel: str,
    output_path: Path,
) -> None:
    if not values:
        logger.warning("No values for %s; skipping %s", xlabel, output_path.name)
        return

    bins = _sqrt_bins(len(values))
    logger.info("Generating %s with %d bins -> %s", xlabel, bins, output_path)

    plt.figure()
    plt.hist(values, bins=bins)
    plt.xlabel(xlabel)
    plt.ylabel("Count")
    plt.title(title)
    plt.grid(True, axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path)
    plt.close()


def generate_histograms(input_csv: Path, output_dir: Path) -> None:
    logger.info("Reading %s", input_csv)
    with input_csv.open("r", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    loc_values = _parse_floats(rows, "nloc")
    cs_values = _parse_floats(rows, "cyclomatic_complexity")

    stem = input_csv.stem
    _histogram(
        loc_values,
        "Histogram of LOC (sqrt rule bins)",
        "LOC",
        output_dir / f"{stem}_loc.png",
    )
    _histogram(
        cs_values,
        "Histogram of Cyclomatic Complexity (sqrt rule bins)",
        "Cyclomatic Complexity",
        output_dir / f"{stem}_cs.png",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate LOC and cyclomatic complexity histograms from a *_rank.csv file."
    )
    parser.add_argument(
        "input_csv",
        type=Path,
        help="Path to input *_rank.csv file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("rank/output"),
        help="Output directory for histogram images.",
    )
    args = parser.parse_args()

    if not args.input_csv.exists():
        raise SystemExit(f"Input file not found: {args.input_csv}")

    generate_histograms(args.input_csv, args.output_dir)


if __name__ == "__main__":
    main()
