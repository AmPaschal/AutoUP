#!/usr/bin/env python3
"""Generate RQ3 stage-contribution artifacts from analyze_components output."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from collections import OrderedDict
from pathlib import Path
from statistics import mean
from typing import Any

if "MPLCONFIGDIR" not in os.environ:
    mpl_cache_dir = Path(tempfile.gettempdir()) / "process-rq3-mpl"
    mpl_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(mpl_cache_dir)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


NA_STRINGS = {"", "n/a", "na", "none", "null"}
PLOT_METRICS = [
    ("Harness Size LOC", "Harness LOC", False),
    ("Proof Size LOC", "Proof LOC", False),
    ("Source Files In Scope", "Files In Scope", False),
    ("Loop Unwind Max", "Max Unwind", False),
    ("Introduced Models", "Models Added", False),
    ("Overall Reachable Line Count", "Reachable LOC", False),
    ("Overall Covered Line Count", "Covered LOC", False),
    ("Property Violations", "Property Violations", False),
    ("Verification Completes (%)", "Verification Completes (%)", True),
    ("Verification Time", "Verification Time (s)", False),
    ("Development Time", "Development Time (min)", False),
    ("API Cost", "API Cost ($)", False),
]
SUMMARY_COLUMNS = [
    "Stage Order",
    "Stage",
    "Targets",
    "Verification Completes (%)",
    "Harness Size LOC",
    "Proof Size LOC",
    "Source Files In Scope",
    "Loop Unwindset Count",
    "Loop Unwind Max",
    "Assumption Variable Count",
    "Precondition Count",
    "Function Model Count",
    "Harness Symbol Count",
    "Introduced Models",
    "Overall Reachable Line Count",
    "Overall Covered Line Count",
    "Overall Line Coverage %",
    "Property Violations",
    "Precondition Violations",
    "Verification Time",
    "Development Time",
    "API Cost",
]
COST_SUMMARY_COLUMNS = [
    "Bucket",
    "Kind",
    "Count",
    "Mean",
    "Median",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate RQ3 stage-contribution artifacts from analyze_components output."
    )
    parser.add_argument("component_csv", type=Path, help="CSV emitted by analyze_components.py")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for generated artifacts. Defaults to <component_csv parent>/rq3-artifacts",
    )
    parser.add_argument(
        "--config",
        help="Optional Config filter. If omitted, all rows in the CSV are aggregated together.",
    )
    parser.add_argument(
        "--title",
        default="AutoUP stage-wise contributions",
        help="Figure title prefix",
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


def parse_bool(value: Any) -> bool | None:
    if is_missing(value):
        return None
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes", "y"}:
        return True
    if lowered in {"false", "0", "no", "n"}:
        return False
    return None


def load_rows(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {csv_path}")
        rows = [dict(row) for row in reader]
    return rows, list(reader.fieldnames)


def normalize_key(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep only the last row for each target/config/stage triple while preserving order."""
    deduped: OrderedDict[tuple[str, str, str], dict[str, str]] = OrderedDict()
    for row in rows:
        key = (
            normalize_key(row.get("Target Function")),
            normalize_key(row.get("Config")),
            normalize_key(row.get("Stage")),
        )
        deduped[key] = row
    return list(deduped.values())


def filter_rows(rows: list[dict[str, str]], config: str | None) -> list[dict[str, str]]:
    filtered = [row for row in rows if parse_bool(row.get("Snapshot Present")) is not False]
    if config is None:
        return filtered
    return [row for row in filtered if str(row.get("Config", "")).strip() == config]


def stage_order(rows: list[dict[str, str]]) -> list[tuple[int, str]]:
    ordered: OrderedDict[int, str] = OrderedDict()
    for row in rows:
        stage_num = parse_float(row.get("Stage Order"))
        stage_name = str(row.get("Stage", "")).strip()
        if stage_num is None or not stage_name:
            continue
        ordered.setdefault(int(stage_num), stage_name)
    return list(ordered.items())


def mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return mean(values)


def mean_numeric(rows: list[dict[str, str]], column: str) -> float | None:
    values = [parse_float(row.get(column)) for row in rows]
    filtered = [value for value in values if value is not None]
    return mean_or_none(filtered)


def introduced_models_mean(rows: list[dict[str, str]]) -> float | None:
    totals: list[float] = []
    for row in rows:
        assumption = parse_float(row.get("Assumption Variable Count"))
        function_models = parse_float(row.get("Function Model Count"))
        if assumption is None and function_models is None:
            continue
        totals.append((assumption or 0.0) + (function_models or 0.0))
    return mean_or_none(totals)


def completion_rate(rows: list[dict[str, str]]) -> float | None:
    values = [parse_bool(row.get("Verification Completes")) for row in rows]
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return 100.0 * sum(1 for value in filtered if value) / len(filtered)


def summarize_stage_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    by_stage: OrderedDict[int, list[dict[str, str]]] = OrderedDict()
    stage_names: dict[int, str] = {}
    for stage_num, stage_name in stage_order(rows):
        by_stage[stage_num] = []
        stage_names[stage_num] = stage_name

    for row in rows:
        stage_num = parse_float(row.get("Stage Order"))
        if stage_num is None:
            continue
        by_stage.setdefault(int(stage_num), []).append(row)
        stage_names.setdefault(int(stage_num), str(row.get("Stage", "")).strip())

    summary_rows: list[dict[str, object]] = []
    for stage_num, stage_rows in by_stage.items():
        summary_rows.append(
            {
                "Stage Order": stage_num,
                "Stage": stage_names.get(stage_num, f"Stage {stage_num}"),
                "Targets": len(stage_rows),
                "Verification Completes (%)": completion_rate(stage_rows),
                "Harness Size LOC": mean_numeric(stage_rows, "Harness Size LOC"),
                "Proof Size LOC": mean_numeric(stage_rows, "Proof Size LOC"),
                "Source Files In Scope": mean_numeric(stage_rows, "Source Files In Scope"),
                "Loop Unwindset Count": mean_numeric(stage_rows, "Loop Unwindset Count"),
                "Loop Unwind Max": mean_numeric(stage_rows, "Loop Unwind Max"),
                "Assumption Variable Count": mean_numeric(stage_rows, "Assumption Variable Count"),
                "Precondition Count": mean_numeric(stage_rows, "Precondition Count"),
                "Function Model Count": mean_numeric(stage_rows, "Function Model Count"),
                "Harness Symbol Count": mean_numeric(stage_rows, "Harness Symbol Count"),
                "Introduced Models": introduced_models_mean(stage_rows),
                "Overall Reachable Line Count": mean_numeric(stage_rows, "Overall Reachable Line Count"),
                "Overall Covered Line Count": mean_numeric(stage_rows, "Overall Covered Line Count"),
                "Overall Line Coverage %": mean_numeric(stage_rows, "Overall Line Coverage %"),
                "Property Violations": mean_numeric(stage_rows, "Property Violations"),
                "Precondition Violations": mean_numeric(stage_rows, "Precondition Violations"),
                "Verification Time": mean_numeric(stage_rows, "Verification Time"),
                "Development Time": mean_numeric(stage_rows, "Development Time"),
                "API Cost": mean_numeric(stage_rows, "API Cost"),
            }
        )
    return summary_rows


def fmt_value(value: float | None, percent: bool = False) -> str:
    if value is None:
        return "N/A"
    if percent:
        return f"{value:.1f}"
    if abs(value) >= 100:
        return f"{value:.1f}"
    if abs(value) >= 10:
        return f"{value:.2f}"
    return f"{value:.3f}"


def write_summary_csv(summary_rows: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for row in summary_rows:
            out_row: dict[str, object] = {}
            for column in SUMMARY_COLUMNS:
                value = row.get(column)
                if isinstance(value, float):
                    out_row[column] = f"{value:.6f}"
                else:
                    out_row[column] = value
            writer.writerow(out_row)


def latex_escape(text: str) -> str:
    return (
        text.replace("\\", "\\textbackslash{}")
        .replace("_", "\\_")
        .replace("%", "\\%")
        .replace("$", "\\$")
        .replace("&", "\\&")
        .replace("#", "\\#")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def render_stage_table_tex(summary_rows: list[dict[str, object]], output_path: Path) -> None:
    stage_labels = [str(row["Stage"]) for row in summary_rows]
    stage_keys = list(range(len(summary_rows)))
    sections = [
        (
            "Harness",
            [
                ("Harness Size LOC", "Harness LOC"),
                ("Source Files In Scope", "Files In Scope"),
                ("Loop Unwindset Count", "Loop Unwindsets"),
                ("Loop Unwind Max", "Max Unwind"),
                ("Function Model Count", "Function Models"),
                ("Precondition Count", "Preconditions"),
                ("Harness Symbol Count", "Harness Symbols"),
            ],
        ),
        (
            "Verification",
            [
                ("Verification Time", "Verification Time (s)"),
                ("Overall Line Coverage %", "Overall Coverage (%)"),
                ("Property Violations", "Property Violations"),
                ("Precondition Violations", "Precondition Violations"),
            ],
        ),
        (
            "Development",
            [
                ("Development Time", "Development Time (min)"),
                ("API Cost", "API Cost ($)"),
            ],
        ),
    ]

    lines = [
        "\\begin{tabular}{l" + ("c" * len(stage_labels)) + "}",
        "\\toprule",
        "Metric & " + " & ".join(latex_escape(label) for label in stage_labels) + " \\\\",
        "\\midrule",
    ]

    first_section = True
    for _section_name, metrics in sections:
        if not first_section:
            lines.append("\\midrule")
        first_section = False
        for metric_key, label in metrics:
            cells = [latex_escape(label)]
            for idx in stage_keys:
                value = parse_float(summary_rows[idx].get(metric_key))
                cells.append(fmt_value(value, percent=metric_key.endswith("%")))
            lines.append(" & ".join(cells) + " \\\\")

    lines.extend(["\\bottomrule", "\\end{tabular}", ""])
    output_path.write_text("\n".join(lines))


def median_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0


def collect_stage_distributions(
    rows: list[dict[str, str]],
) -> tuple[list[str], list[list[float]], list[list[float]]]:
    ordered_stages = stage_order(rows)
    labels = [stage_name for _stage_num, stage_name in ordered_stages]

    time_groups: list[list[float]] = []
    cost_groups: list[list[float]] = []
    for stage_num, _stage_name in ordered_stages:
        stage_rows = [row for row in rows if parse_float(row.get("Stage Order")) == float(stage_num)]
        time_groups.append(
            [value for value in (parse_float(row.get("Development Time")) for row in stage_rows) if value is not None]
        )
        cost_groups.append(
            [value for value in (parse_float(row.get("API Cost")) for row in stage_rows) if value is not None]
        )

    totals_by_target: OrderedDict[tuple[str, str, str, str], dict[str, float]] = OrderedDict()
    for row in rows:
        key = (
            str(row.get("Config", "")).strip(),
            str(row.get("Tag", "")).strip(),
            str(row.get("Source File", "")).strip(),
            str(row.get("Target Function", "")).strip(),
        )
        bucket = totals_by_target.setdefault(key, {"Development Time": 0.0, "API Cost": 0.0, "time_seen": 0.0, "cost_seen": 0.0})
        dev_time = parse_float(row.get("Development Time"))
        api_cost = parse_float(row.get("API Cost"))
        if dev_time is not None:
            bucket["Development Time"] += dev_time
            bucket["time_seen"] += 1.0
        if api_cost is not None:
            bucket["API Cost"] += api_cost
            bucket["cost_seen"] += 1.0

    total_times = [item["Development Time"] for item in totals_by_target.values() if item["time_seen"] > 0.0]
    total_costs = [item["API Cost"] for item in totals_by_target.values() if item["cost_seen"] > 0.0]
    labels.append("Total")
    time_groups.append(total_times)
    cost_groups.append(total_costs)
    return labels, time_groups, cost_groups


def write_cost_summary_csv(
    labels: list[str],
    time_groups: list[list[float]],
    cost_groups: list[list[float]],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for label, group in zip(labels, time_groups):
        rows.append(
            {
                "Bucket": label,
                "Kind": "Development Time (min)",
                "Count": len(group),
                "Mean": "" if mean_or_none(group) is None else f"{mean_or_none(group):.6f}",
                "Median": "" if median_or_none(group) is None else f"{median_or_none(group):.6f}",
            }
        )
    for label, group in zip(labels, cost_groups):
        rows.append(
            {
                "Bucket": label,
                "Kind": "API Cost",
                "Count": len(group),
                "Mean": "" if mean_or_none(group) is None else f"{mean_or_none(group):.6f}",
                "Median": "" if median_or_none(group) is None else f"{median_or_none(group):.6f}",
            }
        )

    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COST_SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def plot_stage_cost_distribution(
    labels: list[str],
    time_groups: list[list[float]],
    cost_groups: list[list[float]],
    output_path: Path,
    title: str,
) -> None:
    fig, axis_time = plt.subplots(figsize=(10.5, 6.4), constrained_layout=True)
    axis_cost = axis_time.twinx()
    positions = list(range(1, len(labels) + 1))
    total_index = len(labels)
    time_positions = [pos - 0.18 for pos in positions]
    cost_positions = [pos + 0.18 for pos in positions]
    time_colors = ["#2f6c8f"] * max(0, len(labels) - 1) + ["#1d4f6b"]
    cost_colors = ["#d28b26"] * max(0, len(labels) - 1) + ["#9b4d1f"]

    def draw_boxplots(axis: Any, groups: list[list[float]], box_positions: list[float], colors: list[str]) -> None:
        nonempty = [(pos, group, color) for pos, group, color in zip(box_positions, groups, colors) if group]
        if not nonempty:
            return
        parts = axis.boxplot(
            [group for _, group, _ in nonempty],
            positions=[pos for pos, _, _ in nonempty],
            widths=0.20,
            patch_artist=True,
            showmeans=True,
        )
        for patch, (_, _, color) in zip(parts["boxes"], nonempty):
            patch.set_facecolor(color)
            patch.set_alpha(0.75)
        for median in parts["medians"]:
            median.set_color("black")
            median.set_linewidth(1.2)
        for whisker in parts["whiskers"]:
            whisker.set_linewidth(1.0)
        for cap in parts["caps"]:
            cap.set_linewidth(1.0)
        for mean_marker in parts["means"]:
            mean_marker.set_markerfacecolor("white")
            mean_marker.set_markeredgecolor("black")
            mean_marker.set_markersize(5)

    draw_boxplots(axis_time, time_groups, time_positions, time_colors)
    draw_boxplots(axis_cost, cost_groups, cost_positions, cost_colors)

    axis_time.set_xticks(positions, labels, rotation=20)
    axis_time.set_ylabel("Development Time (min)", color="#2f6c8f", fontsize=18)
    axis_cost.set_ylabel("API Cost ($)", color="#d28b26", fontsize=18)
    axis_time.tick_params(axis="x", labelsize=14)
    axis_time.tick_params(axis="y", colors="#2f6c8f", labelsize=14)
    axis_cost.tick_params(axis="y", colors="#d28b26", labelsize=14)
    axis_time.grid(axis="y", alpha=0.25, linewidth=0.8)
    if total_index > 1:
        axis_time.axvline(total_index - 0.5, color="black", linestyle="--", linewidth=1.0, alpha=0.65)

    from matplotlib.patches import Patch

    legend_handles = [
        Patch(facecolor="#2f6c8f", edgecolor="black", alpha=0.75, label="Development Time"),
        Patch(facecolor="#d28b26", edgecolor="black", alpha=0.75, label="API Cost"),
    ]
    axis_time.legend(handles=legend_handles, loc="upper left", fontsize=14, title_fontsize=15)

    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def plot_stage_contributions(summary_rows: list[dict[str, object]], output_path: Path, title: str) -> None:
    stage_labels = [str(row["Stage"]) for row in summary_rows]
    x_values = list(range(len(summary_rows)))

    fig, axes = plt.subplots(3, 4, figsize=(12.5, 11.5), constrained_layout=True)
    axes_flat = axes.flatten()

    for ax, (metric, label, is_percent) in zip(axes_flat, PLOT_METRICS):
        y_values = [parse_float(row.get(metric)) for row in summary_rows]
        plot_values = [float("nan") if value is None else value for value in y_values]
        ax.plot(x_values, plot_values, marker="o", linewidth=2.2, color="#1f4e79")
        ax.set_title(label, fontsize=18)
        ax.set_xticks(x_values, stage_labels, rotation=20)
        ax.tick_params(axis="x", labelsize=14)
        ax.tick_params(axis="y", labelsize=14)
        ax.grid(axis="y", alpha=0.25, linewidth=0.8)

        for x_pos, value in zip(x_values, y_values):
            if value is None:
                continue
            ax.annotate(
                fmt_value(value, percent=is_percent),
                (x_pos, value),
                textcoords="offset points",
                xytext=(0, 7),
                ha="center",
                fontsize=13,
            )

    for ax in axes_flat[len(PLOT_METRICS):]:
        ax.axis("off")

    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    args = parse_args()
    rows, _fieldnames = load_rows(args.component_csv.resolve())
    filtered_rows = dedupe_rows(filter_rows(rows, args.config))
    if not filtered_rows:
        config_note = f" for config {args.config!r}" if args.config else ""
        raise SystemExit(f"No component rows available{config_note}.")

    summary_rows = summarize_stage_rows(filtered_rows)
    output_dir = args.output_dir.resolve() if args.output_dir else args.component_csv.resolve().parent / "rq3-artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_csv = output_dir / "rq3_stage_summary.csv"
    cost_summary_csv = output_dir / "rq3_stage_cost_summary.csv"
    table_tex = output_dir / "rq3_stage_table.tex"
    figure_png = output_dir / "rq3_stage_contributions.png"
    cost_figure_png = output_dir / "rq3_stage_cost_distribution.png"

    title = args.title
    if args.config:
        title = f"{title} ({args.config})"

    write_summary_csv(summary_rows, summary_csv)
    render_stage_table_tex(summary_rows, table_tex)
    labels, time_groups, cost_groups = collect_stage_distributions(filtered_rows)
    write_cost_summary_csv(labels, time_groups, cost_groups, cost_summary_csv)
    plot_stage_contributions(summary_rows, figure_png, title)
    plot_stage_cost_distribution(
        labels,
        time_groups,
        cost_groups,
        cost_figure_png,
        title.replace("contributions", "cost distributions"),
    )

    print(f"[done] wrote {summary_csv}")
    print(f"[done] wrote {table_tex}")
    print(f"[done] wrote {cost_summary_csv}")
    print(f"[done] wrote {figure_png}")
    print(f"[done] wrote {cost_figure_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
