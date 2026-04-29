#!/usr/bin/env python3
"""Generate RQ1 table and figures from merged analyze_experiment CSV outputs."""

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
    mpl_cache_dir = Path(tempfile.gettempdir()) / "process-rq1-mpl"
    mpl_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(mpl_cache_dir)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


NA_STRINGS = {"", "n/a", "na", "none", "null"}
TABLE_ORDER = [
    "Num Targets",
    "Compiles (%)",
    "Links target (%)",
    "Verification completes (%)",
    "Generation succeeds (%)",
    "Verification time (s)",
    "Avg component size (loc)",
    "Avg covered size (loc)",
    "Avg coverage (%)",
    "Avg num. properties (#)",
    "Avg num. verified properties (#)",
    "Verified properties (%)",
    "Avg num. vulnerabilities (#)",
    "Avg generation time (min)",
    "Avg API cost ($)",
    "Avg proof size (loc)",
]
MIDRULE_AFTER = {
    "Generation succeeds (%)",
    "Avg num. vulnerabilities (#)",
    "Avg API cost ($)",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate RQ1 artifacts from a merged analyze_experiment CSV."
    )
    parser.add_argument("merged_csv", type=Path, help="Merged CSV built from analyze_experiment.py outputs")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for generated artifacts. Defaults to <merged_csv parent>/rq1-artifacts",
    )
    parser.add_argument(
        "--series-labels",
        default="",
        help="Optional comma-separated raw=display mapping for Config labels",
    )
    return parser.parse_args()


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in NA_STRINGS
    return False


def parse_bool(value: Any) -> bool | None:
    if is_missing(value):
        return None
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes", "y"}:
        return True
    if lowered in {"false", "0", "no", "n"}:
        return False
    return None


def parse_float(value: Any) -> float | None:
    if is_missing(value):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def choose_column(fieldnames: list[str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in fieldnames:
            return candidate
    return None


def load_rows(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {csv_path}")
        return [dict(row) for row in reader], list(reader.fieldnames)


def normalize_key(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep only the last row for each config/software/target/source tuple."""
    deduped: OrderedDict[tuple[str, str, str, str], dict[str, str]] = OrderedDict()
    for row in rows:
        key = (
            normalize_key(row.get("Config")),
            normalize_key(row.get("Software")),
            normalize_key(row.get("Target Function")),
            normalize_key(row.get("Source File")),
        )
        deduped[key] = row
    return list(deduped.values())


def row_has_development_cost(row: dict[str, str]) -> bool:
    return parse_bool(row.get("Development Succeeds")) is True


def row_has_completed_verification(row: dict[str, str]) -> bool:
    return parse_bool(row.get("Verification Completes")) is True


def row_in_completed_generation_subset(row: dict[str, str]) -> bool:
    return row_has_development_cost(row) and row_has_completed_verification(row)


def parse_series_labels(raw_mapping: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not raw_mapping.strip():
        return result
    for item in raw_mapping.split(","):
        piece = item.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ValueError(f"Invalid --series-labels entry {piece!r}; expected raw=display")
        raw, display = piece.split("=", 1)
        result[raw.strip()] = display.strip()
    return result


def preserve_config_order(rows: list[dict[str, str]]) -> list[str]:
    ordered: OrderedDict[str, None] = OrderedDict()
    for row in rows:
        config = str(row.get("Config", "")).strip()
        if config:
            ordered.setdefault(config, None)
    return list(ordered.keys())


def group_rows_by_config(rows: list[dict[str, str]], configs: list[str]) -> dict[str, list[dict[str, str]]]:
    grouped = {config: [] for config in configs}
    for row in rows:
        config = str(row.get("Config", "")).strip()
        if config in grouped:
            grouped[config].append(row)
    return grouped


def mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return mean(values)


def percent_true(rows: list[dict[str, str]], column: str) -> float | None:
    values = [parse_bool(row.get(column)) for row in rows]
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return 100.0 * sum(1 for value in filtered if value) / len(filtered)


def percent_true_fullset(rows: list[dict[str, str]], column: str) -> float | None:
    if not rows:
        return None
    return 100.0 * sum(1 for row in rows if parse_bool(row.get(column)) is True) / len(rows)


def mean_numeric(
    rows: list[dict[str, str]],
    column: str,
    scale: float = 1.0,
    require_development_succeeds: bool = False,
) -> float | None:
    values = [parse_float(row.get(column)) for row in rows]
    filtered: list[float] = []
    for row, value in zip(rows, values):
        if value is None:
            continue
        if require_development_succeeds and not row_has_development_cost(row):
            continue
        filtered.append(value / scale)
    return mean_or_none(filtered)


def generated_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if row_in_completed_generation_subset(row)]


def ratio_of_sums(rows: list[dict[str, str]], numerator_col: str, denominator_col: str) -> float | None:
    numerator = 0.0
    denominator = 0.0
    saw_any = False
    for row in rows:
        num = parse_float(row.get(numerator_col))
        den = parse_float(row.get(denominator_col))
        if num is None or den is None:
            continue
        numerator += num
        denominator += den
        saw_any = True
    if not saw_any or denominator == 0.0:
        return None
    return 100.0 * numerator / denominator


def format_summary_value(metric: str, value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}"


def build_summary_table(
    grouped_rows: dict[str, list[dict[str, str]]],
    total_size_col: str,
    covered_size_col: str,
    coverage_pct_col: str,
    total_properties_col: str,
    verified_properties_col: str,
    reported_vulnerabilities_col: str,
    proof_size_col: str,
) -> list[dict[str, str]]:
    table_rows: list[dict[str, str]] = []
    for metric in TABLE_ORDER:
        row_out = {"Metric": metric}
        for config, rows in grouped_rows.items():
            generated = generated_rows(rows)
            value: float | None
            if metric == "Num Targets":
                value = float(len(rows))
            elif metric == "Compiles (%)":
                value = percent_true_fullset(rows, "Compile Succeeded")
            elif metric == "Links target (%)":
                value = percent_true_fullset(rows, "Links Target")
            elif metric == "Verification completes (%)":
                value = percent_true_fullset(rows, "Verification Completes")
            elif metric == "Generation succeeds (%)":
                value = percent_true_fullset(rows, "Development Succeeds")
            elif metric == "Verification time (s)":
                value = mean_numeric(generated, "Verification Time")
            elif metric == "Avg component size (loc)":
                value = mean_numeric(generated, total_size_col)
            elif metric == "Avg covered size (loc)":
                value = mean_numeric(generated, covered_size_col)
            elif metric == "Avg coverage (%)":
                value = mean_numeric(generated, coverage_pct_col)
            elif metric == "Avg num. properties (#)":
                value = mean_numeric(generated, total_properties_col)
            elif metric == "Avg num. verified properties (#)":
                value = mean_numeric(generated, verified_properties_col)
            elif metric == "Verified properties (%)":
                value = ratio_of_sums(generated, verified_properties_col, total_properties_col)
            elif metric == "Avg num. vulnerabilities (#)":
                value = mean_numeric(generated, reported_vulnerabilities_col)
            elif metric == "Avg generation time (min)":
                value = mean_numeric(
                    generated,
                    "Generation Time",
                    scale=60.0,
                )
            elif metric == "Avg API cost ($)":
                value = mean_numeric(generated, "API Cost")
            elif metric == "Avg proof size (loc)":
                value = mean_numeric(generated, proof_size_col)
            else:
                value = None
            row_out[config] = format_summary_value(metric, value)
        table_rows.append(row_out)
    return table_rows


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


def write_table_csv(
    path: Path,
    rows: list[dict[str, str]],
    configs: list[str],
    display_names: dict[str, str],
) -> None:
    fieldnames = ["Metric"] + [display_names.get(config, config) for config in configs]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            output = {"Metric": row["Metric"]}
            for config in configs:
                output[display_names.get(config, config)] = row[config]
            writer.writerow(output)


def write_table_latex(
    path: Path,
    rows: list[dict[str, str]],
    configs: list[str],
    display_names: dict[str, str],
) -> None:
    header_cells = " & ".join(latex_escape(display_names.get(config, config)) for config in configs)
    lines = [
        "\\begin{tabular}{l|" + ("c" * len(configs)) + "}",
        "\\toprule",
        "Metric & " + header_cells + " \\\\",
        "\\midrule",
    ]
    for row in rows:
        cells = [latex_escape(row["Metric"])] + [row[config] for config in configs]
        lines.append(" & ".join(cells) + " \\\\")
        if row["Metric"] in MIDRULE_AFTER:
            lines.append("\\midrule")
    lines.extend(["\\bottomrule", "\\end{tabular}", ""])
    path.write_text("\n".join(lines))


def save_figure(fig: plt.Figure, output_stem: Path) -> None:
    fig.savefig(output_stem.with_suffix(".png"), dpi=300, bbox_inches="tight")
    fig.savefig(output_stem.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def grouped_numeric_values(
    grouped_rows: dict[str, list[dict[str, str]]],
    configs: list[str],
    column: str,
    scale: float = 1.0,
    require_completed_generation_subset: bool = False,
) -> list[list[float]]:
    values: list[list[float]] = []
    for config in configs:
        config_values: list[float] = []
        for row in grouped_rows[config]:
            if require_completed_generation_subset and not row_in_completed_generation_subset(row):
                continue
            raw = parse_float(row.get(column))
            if raw is None:
                continue
            config_values.append(raw / scale)
        values.append(config_values)
    return values


def style_boxplot(parts: dict[str, Any], color: str) -> None:
    for patch in parts["boxes"]:
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
    for median in parts["medians"]:
        median.set_color("black")
        median.set_linewidth(1.2)


def plot_rq1_coverage(
    grouped_rows: dict[str, list[dict[str, str]]],
    configs: list[str],
    display_names: dict[str, str],
    covered_col: str,
    output_stem: Path,
    show_fliers: bool = True,
    log_y: bool = False,
) -> None:
    label_fontsize = 18
    tick_fontsize = 15
    metrics = [
        (covered_col, "Component Coverage (loc)", 1.0, "#c46b2c"),
        ("Verification Time", "Verification Time (s)", 1.0, "#4d8fcb"),
        ("Generation Time", "Generation Time (min)", 60.0, "#4aa564"),
        ("API Cost", "API Cost ($)", 1.0, "#b567c0"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    x_positions = list(range(1, len(configs) + 1))
    labels = [display_names.get(config, config) for config in configs]

    for axis, (column, ylabel, scale, color) in zip(axes.flat, metrics):
        values = grouped_numeric_values(
            grouped_rows,
            configs,
            column,
            scale=scale,
            require_completed_generation_subset=True,
        )
        nonempty_positions = [pos for pos, group in zip(x_positions, values) if group]
        nonempty_values = [group for group in values if group]
        if nonempty_values:
            parts = axis.boxplot(
                nonempty_values,
                positions=nonempty_positions,
                widths=0.6,
                patch_artist=True,
                showfliers=show_fliers,
            )
            style_boxplot(parts, color)
            if log_y:
                flattened = [value for group in nonempty_values for value in group if value > 0]
                if flattened:
                    axis.set_yscale("log")
        axis.set_xticks(x_positions)
        axis.set_xticklabels(labels, fontsize=tick_fontsize)
        axis.tick_params(axis="y", labelsize=tick_fontsize)
        axis.set_ylabel(ylabel, fontsize=label_fontsize)
        axis.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)

    fig.tight_layout()
    save_figure(fig, output_stem)


def normalize_metric_pairs(
    grouped_rows: dict[str, list[dict[str, str]]],
    configs: list[str],
    x_column: str,
    y_column: str,
    scale: float = 1.0,
    require_completed_generation_subset: bool = False,
) -> dict[str, list[tuple[float, float]]]:
    pairs_by_config: dict[str, list[tuple[float, float]]] = {}
    for config in configs:
        pairs: list[tuple[float, float]] = []
        for row in grouped_rows[config]:
            if require_completed_generation_subset and not row_in_completed_generation_subset(row):
                continue
            x_value = parse_float(row.get(x_column))
            y_value = parse_float(row.get(y_column))
            if x_value is None or y_value is None:
                continue
            scaled_y = y_value / scale
            pairs.append((x_value, scaled_y))
        pairs_by_config[config] = sorted(pairs, key=lambda item: item[0])
    return pairs_by_config


def plot_harness_analysis_figure(
    grouped_rows: dict[str, list[dict[str, str]]],
    configs: list[str],
    display_names: dict[str, str],
    x_column: str,
    x_label: str,
    proof_size_col: str,
    output_stem: Path,
) -> None:
    label_fontsize = 18
    tick_fontsize = 15
    legend_fontsize = 14
    metric_specs = [
        (proof_size_col, "Proof Size (LOC)", 1.0, "#1b9e77"),
        ("Verification Time", "Verification Time (s)", 1.0, "#d95f02"),
        ("Generation Time", "Generation Time (min)", 60.0, "#7570b3"),
        ("API Cost", "API Cost ($)", 1.0, "#e7298a"),
    ]

    linestyles = ["-", "--", "-.", ":"]
    config_styles = {config: linestyles[index % len(linestyles)] for index, config in enumerate(configs)}

    fig, base_ax = plt.subplots(figsize=(12.5, 7.0))
    axes = [base_ax, base_ax.twinx(), base_ax.twinx(), base_ax.twinx()]
    axes[2].spines["right"].set_position(("axes", 1.10))
    axes[3].spines["left"].set_position(("axes", -0.10))
    axes[3].spines["left"].set_visible(True)
    axes[3].spines["right"].set_visible(False)
    axes[3].yaxis.set_label_position("left")
    axes[3].yaxis.set_ticks_position("left")

    for axis, (metric_column, metric_label, scale, color) in zip(axes, metric_specs):
        metric_pairs = normalize_metric_pairs(
            grouped_rows,
            configs,
            x_column,
            metric_column,
            scale=scale,
            require_completed_generation_subset=True,
        )
        for config in configs:
            pairs = metric_pairs.get(config, [])
            if not pairs:
                continue
            xs = [pair[0] for pair in pairs]
            ys = [pair[1] for pair in pairs]
            axis.plot(
                xs,
                ys,
                marker="o",
                markersize=3.8,
                linewidth=1.6,
                color=color,
                linestyle=config_styles[config],
                alpha=0.9,
            )
        axis.set_ylabel(metric_label, color=color, fontsize=label_fontsize)
        axis.tick_params(axis="y", colors=color, labelsize=tick_fontsize)

    base_ax.set_xlabel(x_label, fontsize=label_fontsize)
    base_ax.tick_params(axis="x", labelsize=tick_fontsize)
    base_ax.grid(True, linestyle=":", linewidth=0.7, alpha=0.6)

    metric_handles = [
        Line2D([0], [0], color=color, linewidth=2.0, marker="o", markersize=4, label=label)
        for _, label, _, color in metric_specs
    ]
    first_legend = base_ax.legend(
        handles=metric_handles,
        loc="upper left",
        title="Metric",
        fontsize=legend_fontsize,
        title_fontsize=legend_fontsize,
    )

    if len(configs) > 1:
        config_handles = [
            Line2D(
                [0],
                [0],
                color="black",
                linewidth=2.0,
                linestyle=config_styles[config],
                label=display_names.get(config, config),
            )
            for config in configs
        ]
        base_ax.legend(
            handles=config_handles,
            loc="lower right",
            title="Config",
            fontsize=legend_fontsize,
            title_fontsize=legend_fontsize,
        )
        base_ax.add_artist(first_legend)

    fig.tight_layout(rect=(0.05, 0.02, 0.95, 1.0))
    save_figure(fig, output_stem)


def plot_cost_time_analysis_figure(
    grouped_rows: dict[str, list[dict[str, str]]],
    configs: list[str],
    display_names: dict[str, str],
    x_column: str,
    x_label: str,
    output_stem: Path,
    x_log_scale: bool = False,
    merge_configs: bool = False,
) -> None:
    label_fontsize = 18
    tick_fontsize = 15
    legend_fontsize = 14
    metric_specs = [
        ("Verification Time", "Verification Time (s)", 1.0, "#d95f02"),
        ("Generation Time", "Generation Time (min)", 60.0, "#7570b3"),
        ("API Cost", "API Cost ($)", 1.0, "#e7298a"),
    ]

    fig, base_ax = plt.subplots(figsize=(12.5, 7.0))
    axes = [base_ax, base_ax.twinx(), base_ax.twinx()]
    axes[2].spines["right"].set_position(("axes", 1.10))

    for axis, (metric_column, metric_label, scale, color) in zip(axes, metric_specs):
        metric_pairs = normalize_metric_pairs(
            grouped_rows,
            configs,
            x_column,
            metric_column,
            scale=scale,
            require_completed_generation_subset=True,
        )
        series_list: list[list[tuple[float, float]]]
        if merge_configs:
            merged_pairs: list[tuple[float, float]] = []
            for config in configs:
                merged_pairs.extend(metric_pairs.get(config, []))
            series_list = [sorted(merged_pairs, key=lambda item: item[0])]
        else:
            series_list = [metric_pairs.get(config, []) for config in configs]

        for pairs in series_list:
            if x_log_scale:
                pairs = [pair for pair in pairs if pair[0] > 0]
            if not pairs:
                continue
            xs = [pair[0] for pair in pairs]
            ys = [pair[1] for pair in pairs]
            axis.plot(
                xs,
                ys,
                marker="o",
                markersize=3.8,
                linewidth=1.8,
                color=color,
                linestyle="-",
                alpha=0.9,
            )
        axis.set_ylabel(metric_label, color=color, fontsize=label_fontsize)
        axis.tick_params(axis="y", colors=color, labelsize=tick_fontsize)

    if x_log_scale:
        base_ax.set_xscale("log")
    base_ax.set_xlabel(x_label, fontsize=label_fontsize)
    base_ax.tick_params(axis="x", labelsize=tick_fontsize)
    base_ax.grid(True, linestyle=":", linewidth=0.7, alpha=0.6)

    metric_handles = [
        Line2D([0], [0], color=color, linewidth=2.0, marker="o", markersize=4, label=label)
        for _, label, _, color in metric_specs
    ]
    first_legend = base_ax.legend(
        handles=metric_handles,
        loc="upper left",
        title="Metric",
        fontsize=legend_fontsize,
        title_fontsize=legend_fontsize,
    )

    if len(configs) > 1 and not merge_configs:
        linestyles = ["-", "--", "-.", ":"]
        config_styles = {config: linestyles[index % len(linestyles)] for index, config in enumerate(configs)}
        config_handles = [
            Line2D(
                [0],
                [0],
                color="black",
                linewidth=2.0,
                linestyle=config_styles[config],
                label=display_names.get(config, config),
            )
            for config in configs
        ]
        base_ax.legend(
            handles=config_handles,
            loc="lower right",
            title="Config",
            fontsize=legend_fontsize,
            title_fontsize=legend_fontsize,
        )
        base_ax.add_artist(first_legend)

    fig.tight_layout(rect=(0.05, 0.02, 0.95, 1.0))
    save_figure(fig, output_stem)


def write_metadata_note(
    path: Path,
    configs: list[str],
    display_names: dict[str, str],
    total_size_col: str,
    covered_size_col: str,
    proof_size_col: str,
    overall_reachable_col: str,
    overall_covered_col: str | None,
) -> None:
    lines = [
        "RQ1 artifact generation summary",
        f"Configs: {', '.join(display_names.get(config, config) for config in configs)}",
        f"Table total-size column: {total_size_col}",
        f"Table covered-size column: {covered_size_col}",
        f"Proof-size column: {proof_size_col}",
        f"Coverage figure covered-size column: {covered_size_col}",
        "Coverage figures: rq1_coverage (with outliers), rq1_coverage_no_outliers, rq1_coverage_no_outliers_logy",
        "Verification-outcome and generation-cost table sections use only rows where Development Succeeds and Verification Completes are both true.",
        "All RQ1 figures use only rows where Development Succeeds and Verification Completes are both true.",
        f"Line figure overall reachable column: {overall_reachable_col}",
        f"Line figure overall covered column: {overall_covered_col or 'N/A'}",
        f"Line figure program reachable column: {total_size_col} (verification time + generation time + API cost; autoup-s* merged; log-x)",
        f"Line figure program reachable log-x column: {total_size_col} (verification time + generation time + API cost; autoup-s* merged)",
        f"Line figure program covered column: {covered_size_col}",
        "The overall-reachable and overall-covered figures use four raw-value y-axes, one per metric.",
        "The program-reachable figure uses three raw-value y-axes for verification time, generation time, and API cost with AutoUP configurations merged.",
        "Grouping column: Config",
    ]
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    args = parse_args()
    merged_csv = args.merged_csv.resolve()
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else merged_csv.parent / "rq1-artifacts"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    rows, fieldnames = load_rows(merged_csv)
    rows = dedupe_rows(rows)
    if "Config" not in fieldnames:
        raise ValueError("Merged CSV must include a 'Config' column")

    configs = preserve_config_order(rows)
    if not configs:
        raise ValueError("Merged CSV does not contain any non-empty config values")

    display_names = parse_series_labels(args.series_labels)
    grouped_rows = group_rows_by_config(rows, configs)

    total_size_col = choose_column(
        fieldnames,
        ["Program Reachable Line Count", "Overall Reachable Line Count"],
    )
    covered_size_col = choose_column(
        fieldnames,
        ["Program Covered Line Count", "Overall Covered Line Count"],
    )
    coverage_pct_col = choose_column(
        fieldnames,
        ["Program Line Coverage %", "Overall Line Coverage %"],
    )
    total_properties_col = choose_column(
        fieldnames,
        ["Total Memory-Safety Properties"],
    )
    verified_properties_col = choose_column(
        fieldnames,
        ["Verified Memory-Safety Properties"],
    )
    reported_vulnerabilities_col = choose_column(
        fieldnames,
        ["Reported Vulnerabilities"],
    )
    proof_size_col = choose_column(
        fieldnames,
        ["Proof Size LOC", "Proof File Size", "Harness Size LOC"],
    )
    overall_reachable_col = choose_column(fieldnames, ["Overall Reachable Line Count", "Program Reachable Line Count"])
    overall_covered_col = choose_column(fieldnames, ["Overall Covered Line Count", "Program Covered Line Count"])

    missing = [
        name
        for name, column in {
            "total size": total_size_col,
            "covered size": covered_size_col,
            "coverage percent": coverage_pct_col,
            "total properties": total_properties_col,
            "verified properties": verified_properties_col,
            "reported vulnerabilities": reported_vulnerabilities_col,
            "proof size": proof_size_col,
            "overall reachable size": overall_reachable_col,
        }.items()
        if column is None
    ]
    if missing:
        raise ValueError("Merged CSV is missing required columns for: " + ", ".join(missing))

    summary_rows = build_summary_table(
        grouped_rows,
        total_size_col,
        covered_size_col,
        coverage_pct_col,
        total_properties_col,
        verified_properties_col,
        reported_vulnerabilities_col,
        proof_size_col,
    )
    write_table_csv(output_dir / "rq1_harness_utility.csv", summary_rows, configs, display_names)
    write_table_latex(output_dir / "rq1_harness_utility.tex", summary_rows, configs, display_names)

    autoup_configs = [config for config in configs if config.lower().startswith("autoup-s")]

    plot_rq1_coverage(
        grouped_rows,
        configs,
        display_names,
        covered_size_col,
        output_dir / "rq1_coverage",
        show_fliers=True,
        log_y=False,
    )
    plot_rq1_coverage(
        grouped_rows,
        configs,
        display_names,
        covered_size_col,
        output_dir / "rq1_coverage_no_outliers",
        show_fliers=False,
        log_y=False,
    )
    plot_rq1_coverage(
        grouped_rows,
        configs,
        display_names,
        covered_size_col,
        output_dir / "rq1_coverage_no_outliers_logy",
        show_fliers=False,
        log_y=True,
    )
    plot_harness_analysis_figure(
        grouped_rows,
        configs,
        display_names,
        overall_reachable_col,
        "Overall Program Reachable LOC",
        proof_size_col,
        output_dir / "rq1_harness_analysis",
    )
    if overall_covered_col is not None:
        plot_harness_analysis_figure(
            grouped_rows,
            configs,
            display_names,
            overall_covered_col,
            "Overall Program Covered LOC",
            proof_size_col,
            output_dir / "rq1_harness_analysis_overall_covered",
        )
    plot_cost_time_analysis_figure(
        grouped_rows,
        autoup_configs,
        display_names,
        total_size_col,
        "Program Reachable LOC",
        output_dir / "rq1_harness_analysis_program_reachable",
        x_log_scale=True,
        merge_configs=True,
    )
    plot_cost_time_analysis_figure(
        grouped_rows,
        autoup_configs,
        display_names,
        total_size_col,
        "Program Reachable LOC",
        output_dir / "rq1_harness_analysis_program_reachable_logx",
        x_log_scale=True,
        merge_configs=True,
    )
    plot_harness_analysis_figure(
        grouped_rows,
        configs,
        display_names,
        covered_size_col,
        "Program Covered LOC",
        proof_size_col,
        output_dir / "rq1_harness_analysis_program_covered",
    )

    write_metadata_note(
        output_dir / "rq1_artifacts_readme.txt",
        configs,
        display_names,
        total_size_col,
        covered_size_col,
        proof_size_col,
        overall_reachable_col,
        overall_covered_col,
    )
    print(f"[done] wrote artifacts to {output_dir}")


if __name__ == "__main__":
    main()
