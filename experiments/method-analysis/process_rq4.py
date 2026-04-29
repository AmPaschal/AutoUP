#!/usr/bin/env python3
"""Generate RQ4 expert-vs-AutoSOUP proof comparison boxplots."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from pathlib import Path
from statistics import mean, median
from typing import Any
import re

if "MPLCONFIGDIR" not in os.environ:
    mpl_cache_dir = Path(tempfile.gettempdir()) / "process-rq4-mpl"
    mpl_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(mpl_cache_dir)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


NA_STRINGS = {"", "n/a", "na", "none", "null"}
ORIGIN_ORDER = ["FreeRTOS Expert", "AutoUP"]
ORIGIN_DISPLAY = {
    "FreeRTOS Expert": "Experts",
    "AutoUP": "AutoSOUP",
}
LOOP_BOUNDS_VALUE_COLUMN = "__loop_bound_values__"
LOOP_BOUND_VALUE_RE = re.compile(r":\s*(-?\d+)\s*$")
FIGURE_SIZE = (15.0, 8.5)
BOX_WIDTH = 0.275
SUBPLOT_TITLE_FONTSIZE = 15
XTICK_FONTSIZE = 12
YTICK_FONTSIZE = 12
LEGEND_FONTSIZE = 12
SUPTITLE_FONTSIZE = 18
METRIC_LABELS = {
    "Proof Size LOC": "Proof Size (LOC)",
    "Functions In Scope": "# Functions In Scope",
    LOOP_BOUNDS_VALUE_COLUMN: "Loop Bounds",
    "Precondition Count": "# Var. Models",
    "Function Model Count": "# Function Models",
    "Verification Time": "Verification Time (s)",
    "Program Reachable LOC": "Component Size",
    "Program Covered LOC": "Verif. Coverage",
    "Memory Safety Properties Verified": "# Verified Properties",
    "Memory Safety Properties Violated": "# Violated Properties",
}
PLOT_GROUPS = [
    [
        ("Proof Size LOC", METRIC_LABELS["Proof Size LOC"]),
        ("Functions In Scope", METRIC_LABELS["Functions In Scope"]),
        (LOOP_BOUNDS_VALUE_COLUMN, METRIC_LABELS[LOOP_BOUNDS_VALUE_COLUMN]),
        ("Precondition Count", METRIC_LABELS["Precondition Count"]),
        ("Function Model Count", METRIC_LABELS["Function Model Count"]),
    ],
    [
        ("Verification Time", METRIC_LABELS["Verification Time"]),
        ("Program Reachable LOC", METRIC_LABELS["Program Reachable LOC"]),
        ("Program Covered LOC", METRIC_LABELS["Program Covered LOC"]),
        ("Memory Safety Properties Verified", METRIC_LABELS["Memory Safety Properties Verified"]),
        ("Memory Safety Properties Violated", METRIC_LABELS["Memory Safety Properties Violated"]),
    ],
]
SUMMARY_COLUMNS = [
    "Metric",
    "Proof Origin",
    "Count",
    "Mean",
    "Median",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate RQ4 side-by-side expert-vs-AutoUP boxplots."
    )
    parser.add_argument("analysis_csv", type=Path, help="CSV emitted by analyze_unit_proof.py")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for generated artifacts. Defaults to <analysis_csv parent>/rq4-artifacts",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Figure title",
    )
    return parser.parse_args()


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in NA_STRINGS
    return False


def parse_float(value: Any) -> float | None:
    if is_missing(value):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {csv_path}")
        return [dict(row) for row in reader]


def parse_loop_bound_values(raw_value: Any) -> list[float]:
    if is_missing(raw_value):
        return []
    values: list[float] = []
    for item in str(raw_value).split(";"):
        stripped = item.strip()
        if not stripped:
            continue
        match = LOOP_BOUND_VALUE_RE.search(stripped)
        if match is None:
            continue
        values.append(float(match.group(1)))
    return values


def grouped_values(
    rows: list[dict[str, str]],
    column: str,
) -> list[list[float]]:
    groups: list[list[float]] = []
    for origin in ORIGIN_ORDER:
        values: list[float] = []
        for row in rows:
            if str(row.get("Proof Origin", "")).strip() != origin:
                continue
            if column == LOOP_BOUNDS_VALUE_COLUMN:
                values.extend(parse_loop_bound_values(row.get("Custom Loop Bounds")))
            else:
                parsed = parse_float(row.get(column))
                if parsed is None:
                    continue
                values.append(parsed)
        groups.append(values)
    return groups


def style_boxplot(parts: dict[str, Any], colors: list[str]) -> None:
    for patch, color in zip(parts["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.78)
    for median_line in parts["medians"]:
        median_line.set_color("black")
        median_line.set_linewidth(1.2)
    for whisker in parts["whiskers"]:
        whisker.set_linewidth(1.0)
    for cap in parts["caps"]:
        cap.set_linewidth(1.0)
    for mean_marker in parts.get("means", []):
        mean_marker.set_markerfacecolor("white")
        mean_marker.set_markeredgecolor("black")
        mean_marker.set_markersize(5)


def plot_rq4(rows: list[dict[str, str]], output_path: Path, title: str) -> None:
    colors = ["#c46b2c", "#2f6c8f"]
    fig, axes = plt.subplots(2, 5, figsize=FIGURE_SIZE, constrained_layout=False)
    if title:
        fig.suptitle(title, fontsize=SUPTITLE_FONTSIZE)

    for row_axes, metrics in zip(axes, PLOT_GROUPS):
        for axis, (column, label) in zip(row_axes, metrics):
            values = grouped_values(rows, column)
            nonempty = [(idx + 1, group) for idx, group in enumerate(values) if group]
            if nonempty:
                parts = axis.boxplot(
                    [group for _, group in nonempty],
                    positions=[pos for pos, _ in nonempty],
                    widths=BOX_WIDTH,
                    patch_artist=True,
                    showmeans=True,
                )
                style_boxplot(parts, [colors[pos - 1] for pos, _ in nonempty])
            axis.set_title(label, fontsize=SUBPLOT_TITLE_FONTSIZE)
            axis.set_xticks(
                [1, 2],
                [ORIGIN_DISPLAY[origin] for origin in ORIGIN_ORDER],
                rotation=18,
            )
            axis.tick_params(axis="x", labelsize=XTICK_FONTSIZE)
            axis.tick_params(axis="y", labelsize=YTICK_FONTSIZE)
            axis.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)

    legend_handles = [
        Patch(facecolor=colors[0], edgecolor="black", alpha=0.78, label=ORIGIN_DISPLAY[ORIGIN_ORDER[0]]),
        Patch(facecolor=colors[1], edgecolor="black", alpha=0.78, label=ORIGIN_DISPLAY[ORIGIN_ORDER[1]]),
    ]
    legend_y = 0.02
    top_rect = 0.92 if title else 0.97
    bottom_rect = 0.08
    fig.legend(
        handles=legend_handles,
        loc="lower center",
        bbox_to_anchor=(0.5, legend_y),
        ncol=2,
        fontsize=LEGEND_FONTSIZE,
        frameon=False,
    )
    fig.tight_layout(rect=(0.0, bottom_rect, 1.0, top_rect))
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def write_summary(rows: list[dict[str, str]], output_path: Path) -> None:
    summary_rows: list[dict[str, str]] = []
    flat_metrics = [metric for group in PLOT_GROUPS for metric in group]
    for column, label in flat_metrics:
        for origin in ORIGIN_ORDER:
            values: list[float] = []
            for row in rows:
                if str(row.get("Proof Origin", "")).strip() != origin:
                    continue
                if column == LOOP_BOUNDS_VALUE_COLUMN:
                    values.extend(parse_loop_bound_values(row.get("Custom Loop Bounds")))
                else:
                    value = parse_float(row.get(column))
                    if value is not None:
                        values.append(value)
            summary_rows.append(
                {
                    "Metric": label,
                    "Proof Origin": ORIGIN_DISPLAY[origin],
                    "Count": str(len(values)),
                    "Mean": "" if not values else f"{mean(values):.6f}",
                    "Median": "" if not values else f"{median(values):.6f}",
                }
            )

    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(summary_rows)


def main() -> int:
    args = parse_args()
    rows = load_rows(args.analysis_csv.resolve())
    if not rows:
        raise SystemExit(f"No rows found in {args.analysis_csv}")

    output_dir = args.output_dir.resolve() if args.output_dir else args.analysis_csv.resolve().parent / "rq4-artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)

    plot_path = output_dir / "rq4_expert_vs_autoup_boxplots.png"
    summary_path = output_dir / "rq4_metric_summary.csv"

    plot_rq4(rows, plot_path, args.title)
    write_summary(rows, summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
