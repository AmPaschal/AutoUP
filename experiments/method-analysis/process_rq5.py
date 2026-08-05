#!/usr/bin/env python3
"""Generate RQ5 repeatability results from mergeable analyzer CSV files.

This script deliberately does not inspect proof directories or calculate proof
metrics.  First run ``analyze_experiment.py`` and ``analyze_components.py`` for
each software/run, then merge like-for-like analyzer outputs.  Use a common
``Config`` value for every first run (for example ``rq5-run1``) and another for
every second run (for example ``rq5-run2``).

Example after merging all operating-system outputs::

    python3 process_rq5.py merged-rq5-targets.csv merged-rq5-components.csv \
        merged-rq5-cves.csv \
        --run1-config rq5-run1 --run2-config rq5-run2

The target CSV supplies verification/result metrics and total generation
costs.  The final available stage in the component CSV supplies proof-choice
metrics.  The CVE CSV is a merged ``analyze_cves.py`` assessment.
Generation time is converted from seconds to minutes for the table.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any


NA_STRINGS = {"", "n/a", "na", "none", "null"}
SUMMARY_COLUMNS = ["Metric", "Run 1", "Run 2", "% Consistent"]
DETAIL_COLUMNS = [
    "Software",
    "Source File",
    "Target Function",
    "Metric",
    "Run 1",
    "Run 2",
    "Equivalence Rule",
    "Consistent",
]


@dataclass(frozen=True)
class MetricSpec:
    label: str
    source: str
    column: str
    scale: float = 1.0
    digits: int = 2
    relative_equivalence: bool = False
    summary_aggregation: str = "mean"


METRICS = [
    MetricSpec("# CVE", "cve", "CVE Count", digits=0, summary_aggregation="sum"),
    MetricSpec("Proof Size", "component", "Proof Size LOC", relative_equivalence=True),
    MetricSpec("# Functions in Scope", "target", "Functions In Scope"),
    MetricSpec("# Custom Loop Bounds", "component", "Loop Unwindset Count"),
    MetricSpec("# Variable Models", "component", "Assumption Variable Count"),
    MetricSpec("# Function Models", "component", "Function Model Count"),
    MetricSpec("Component Size (LOC)", "target", "Program Reachable Line Count"),
    MetricSpec("Verification Cov (LOC)", "target", "Program Covered Line Count"),
    MetricSpec("# Verified Properties", "target", "Verified Memory-Safety Properties"),
    MetricSpec("# Reported Errors", "target", "Property Violations"),
    MetricSpec(
        "# CVE Exposed",
        "cve",
        "CVE Exposed",
        digits=0,
        summary_aggregation="sum",
    ),
    MetricSpec(
        "Generation Time",
        "target",
        "Generation Time",
        scale=60.0,
        relative_equivalence=True,
    ),
    MetricSpec("API Cost", "target", "API Cost", digits=4, relative_equivalence=True),
]
MIDRULE_AFTER = {
    "Proof Size",
    "# Function Models",
    "# Verified Properties",
    "# CVE Exposed",
}
TARGET_REQUIRED_COLUMNS = {
    "Software",
    "Config",
    "Source File",
    "Target Function",
    *[metric.column for metric in METRICS if metric.source == "target"],
}
COMPONENT_REQUIRED_COLUMNS = {
    "Software",
    "Config",
    "Source File",
    "Target Function",
    "Stage Order",
    "Snapshot Present",
    *[metric.column for metric in METRICS if metric.source == "component"],
}


@dataclass
class TargetResult:
    software: str
    source_file: str
    target_function: str
    values: dict[str, float | None]

    @property
    def key(self) -> tuple[str, str, str]:
        return target_key(self.software, self.source_file, self.target_function)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate RQ5 repeatability artifacts from merged analyzer CSVs."
    )
    parser.add_argument(
        "target_csv",
        type=Path,
        help="Merged CSV emitted by analyze_experiment.py",
    )
    parser.add_argument(
        "component_csv",
        type=Path,
        help="Merged CSV emitted by analyze_components.py",
    )
    parser.add_argument(
        "--run1-config", required=True, help="Config label identifying first runs"
    )
    parser.add_argument(
        "--run2-config", required=True, help="Config label identifying second runs"
    )
    parser.add_argument(
        "cve_csv",
        type=Path,
        help="Merged CSV emitted by analyze_cves.py",
    )
    parser.add_argument(
        "--cve-exposure",
        choices=("strict", "partial"),
        default="strict",
        help="CVE exposure definition to count (default: strict)",
    )
    parser.add_argument(
        "--relative-equivalence-percent",
        type=float,
        default=10.0,
        help=(
            "Exclusive relative-difference threshold for proof size, generation time, and API "
            "cost (default: 10)"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("rq5-artifacts"),
        help="Artifact directory (default: ./rq5-artifacts)",
    )
    parser.add_argument(
        "--caption",
        default=(
            "Repeatability of AutoSOUP's proof choices, verification result and cost. "
            "Run 1 and 2 show average across targets."
        ),
        help="LaTeX table caption",
    )
    parser.add_argument(
        "--label",
        default="rq5:tab-autosoup-repeatability",
        help="LaTeX table label",
    )
    return parser.parse_args()


def normalize(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_key(value: Any) -> str:
    return normalize(value).casefold()


def target_key(
    software: Any, source_file: Any, target_function: Any
) -> tuple[str, str, str]:
    return (
        normalize_key(software),
        normalize(source_file).replace("\\", "/"),
        normalize(target_function),
    )


def parse_bool(value: Any) -> bool | None:
    text = normalize_key(value)
    if text in NA_STRINGS:
        return None
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def parse_number(value: Any) -> float | None:
    text = normalize(value)
    if text.casefold() in NA_STRINGS:
        return None
    if text.startswith("$"):
        text = text[1:]
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def load_csv(path: Path, required_columns: set[str], kind: str) -> list[dict[str, str]]:
    csv_path = path.resolve()
    if not csv_path.is_file():
        raise ValueError(f"{kind} CSV not found: {csv_path}")
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{kind} CSV has no header: {csv_path}")
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        missing = required_columns - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"{kind} CSV {csv_path} missing columns: " + ", ".join(sorted(missing))
            )
        return [
            {normalize(key): normalize(value) for key, value in row.items()}
            for row in reader
        ]


def filter_config(
    rows: list[dict[str, str]], config: str, kind: str
) -> list[dict[str, str]]:
    expected = normalize_key(config)
    selected = [row for row in rows if normalize_key(row.get("Config")) == expected]
    if not selected:
        raise ValueError(f"No {kind} rows found for Config={config!r}")
    return selected


def index_target_rows(
    rows: list[dict[str, str]],
    config: str,
) -> dict[tuple[str, str, str], dict[str, str]]:
    indexed: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in filter_config(rows, config, "target"):
        key = target_key(
            row.get("Software"), row.get("Source File"), row.get("Target Function")
        )
        if not all(key):
            raise ValueError(
                f"Target row has an empty identity field for Config={config!r}"
            )
        if key in indexed:
            raise ValueError(f"Duplicate target row for Config={config!r}: {key}")
        indexed[key] = row
    return indexed


def select_final_component_rows(
    rows: list[dict[str, str]],
    config: str,
) -> dict[tuple[str, str, str], dict[str, str]]:
    selected: dict[tuple[str, str, str], tuple[float, dict[str, str]]] = {}
    for row in filter_config(rows, config, "component"):
        if parse_bool(row.get("Snapshot Present")) is not True:
            continue
        stage_order = parse_number(row.get("Stage Order"))
        if stage_order is None:
            continue
        key = target_key(
            row.get("Software"), row.get("Source File"), row.get("Target Function")
        )
        if not all(key):
            raise ValueError(
                f"Component row has an empty identity field for Config={config!r}"
            )
        current = selected.get(key)
        if current is None or stage_order > current[0]:
            selected[key] = (stage_order, row)
        elif stage_order == current[0]:
            raise ValueError(
                f"Duplicate final component stage for Config={config!r}, target={key}, "
                f"stage={stage_order:g}"
            )
    return {key: row for key, (_, row) in selected.items()}


def load_cve_counts(
    rows: list[dict[str, str]],
    config: str,
    exposure_column: str,
) -> dict[tuple[str, str, str], dict[str, float]]:
    selected = filter_config(rows, config, "CVE")
    cves: dict[tuple[str, str, str], set[str]] = {}
    exposed: dict[tuple[str, str, str], set[str]] = {}
    for row in selected:
        key = target_key(
            row.get("Software"), row.get("Target File"), row.get("Target Function")
        )
        if not all(key):
            continue
        cves.setdefault(key, set())
        exposed.setdefault(key, set())
        cve_id = normalize(row.get("CVE ID"))
        if cve_id:
            cves[key].add(cve_id)
        if parse_bool(row.get(exposure_column)) is True:
            if cve_id:
                exposed[key].add(cve_id)
    return {
        key: {
            "CVE Count": float(len(cve_ids)),
            "CVE Exposed": float(len(exposed[key])),
        }
        for key, cve_ids in cves.items()
    }


def load_cve_rows(
    path: Path,
    exposure_column: str,
) -> list[dict[str, str]]:
    required = {
        "Software",
        "Config",
        "Target File",
        "Target Function",
        "CVE ID",
        exposure_column,
    }
    return load_csv(path, required, "CVE")


def build_run_results(
    target_rows: list[dict[str, str]],
    component_rows: list[dict[str, str]],
    config: str,
    cve_counts: dict[tuple[str, str, str], dict[str, float]],
) -> list[TargetResult]:
    targets = index_target_rows(target_rows, config)
    components = select_final_component_rows(component_rows, config)
    results: list[TargetResult] = []
    for key, target_row in targets.items():
        component_row = components.get(key, {})
        values: dict[str, float | None] = {}
        for metric in METRICS:
            if metric.source == "target":
                value = parse_number(target_row.get(metric.column))
            elif metric.source == "component":
                value = parse_number(component_row.get(metric.column))
            else:
                value = cve_counts.get(key, {}).get(metric.column)
            values[metric.label] = None if value is None else value / metric.scale
        results.append(
            TargetResult(
                software=normalize(target_row.get("Software")),
                source_file=normalize(target_row.get("Source File")),
                target_function=normalize(target_row.get("Target Function")),
                values=values,
            )
        )
    return results


def target_map(rows: list[TargetResult]) -> dict[tuple[str, str, str], TargetResult]:
    result: dict[tuple[str, str, str], TargetResult] = {}
    for row in rows:
        if row.key in result:
            raise ValueError(f"Duplicate RQ5 target: {row.key}")
        result[row.key] = row
    return result


def values_equivalent(
    left: float | None,
    right: float | None,
    metric: MetricSpec,
    relative_threshold: float,
) -> bool | None:
    if left is None and right is None:
        return None
    if left is None or right is None:
        return False
    if not metric.relative_equivalence:
        return left == right
    denominator = max(abs(left), abs(right))
    if denominator == 0:
        return left == right
    return abs(left - right) / denominator < relative_threshold


def equivalence_rule(metric: MetricSpec, relative_threshold: float) -> str:
    if metric.relative_equivalence:
        return f"relative difference < {relative_threshold * 100:g}%"
    return "exact"


def format_mean(value: float | None, digits: int) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


def format_raw(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.10g}"


def summarize_values(values: list[float], metric: MetricSpec) -> float | None:
    if not values:
        return None
    if metric.summary_aggregation == "sum":
        return sum(values)
    return mean(values)


def build_outputs(
    run1_rows: list[TargetResult],
    run2_rows: list[TargetResult],
    relative_threshold: float,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    run1 = target_map(run1_rows)
    run2 = target_map(run2_rows)
    all_keys = sorted(set(run1) | set(run2))
    common_count = len(set(run1) & set(run2))
    target_consistency = 100.0 * common_count / len(all_keys) if all_keys else None
    summary = [
        {
            "Metric": "# Targets",
            "Run 1": str(len(run1)),
            "Run 2": str(len(run2)),
            "% Consistent": "N/A"
            if target_consistency is None
            else f"{target_consistency:.1f}%",
        }
    ]
    details: list[dict[str, str]] = []

    for metric in METRICS:
        valid1 = [
            row.values[metric.label]
            for row in run1.values()
            if row.values[metric.label] is not None
        ]
        valid2 = [
            row.values[metric.label]
            for row in run2.values()
            if row.values[metric.label] is not None
        ]
        comparisons: list[bool] = []
        rule = equivalence_rule(metric, relative_threshold)
        for key in all_keys:
            left_row = run1.get(key)
            right_row = run2.get(key)
            left = left_row.values.get(metric.label) if left_row else None
            right = right_row.values.get(metric.label) if right_row else None
            consistent = (
                False
                if left_row is None or right_row is None
                else values_equivalent(left, right, metric, relative_threshold)
            )
            if consistent is not None:
                comparisons.append(consistent)
            exemplar = left_row or right_row
            assert exemplar is not None
            details.append(
                {
                    "Software": exemplar.software,
                    "Source File": exemplar.source_file,
                    "Target Function": exemplar.target_function,
                    "Metric": metric.label,
                    "Run 1": format_raw(left),
                    "Run 2": format_raw(right),
                    "Equivalence Rule": rule,
                    "Consistent": "N/A" if consistent is None else str(consistent),
                }
            )
        consistency = (
            100.0 * sum(1 for value in comparisons if value) / len(comparisons)
            if comparisons
            else None
        )
        summary.append(
            {
                "Metric": metric.label,
                "Run 1": format_mean(summarize_values(valid1, metric), metric.digits),
                "Run 2": format_mean(summarize_values(valid2, metric), metric.digits),
                "% Consistent": "N/A" if consistency is None else f"{consistency:.1f}%",
            }
        )
    return summary, details


def write_csv(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


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


def render_latex(rows: list[dict[str, str]], caption: str, label: str) -> str:
    lines = [
        "\\begin{table}",
        "    \\centering",
        f"    \\caption{{{latex_escape(caption)}}}",
        "    \\begin{tabular}{lccc}",
        "    \\toprule",
        "    \\textbf{Metric} & \\textbf{Run 1} & \\textbf{Run 2} & \\textbf{\\% Consistent} \\\\",
        "    \\midrule",
    ]
    for row in rows:
        lines.append(
            "    "
            + " & ".join(latex_escape(row[column]) for column in SUMMARY_COLUMNS)
            + " \\\\"
        )
        if row["Metric"] in MIDRULE_AFTER:
            lines.append("    \\midrule")
    lines.extend(
        [
            "    \\bottomrule",
            "    \\end{tabular}",
            f"    \\label{{{latex_escape(label)}}}",
            "\\end{table}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        if args.run1_config.casefold() == args.run2_config.casefold():
            raise ValueError("--run1-config and --run2-config must differ")
        if not 0 <= args.relative_equivalence_percent <= 100:
            raise ValueError("--relative-equivalence-percent must be between 0 and 100")
        threshold = args.relative_equivalence_percent / 100.0
        target_rows = load_csv(args.target_csv, TARGET_REQUIRED_COLUMNS, "target")
        component_rows = load_csv(
            args.component_csv, COMPONENT_REQUIRED_COLUMNS, "component"
        )
        exposure_column = (
            "CVE Exposed Strict"
            if args.cve_exposure == "strict"
            else "CVE Exposed Partial"
        )
        cve_rows = load_cve_rows(args.cve_csv, exposure_column)
        cve1 = load_cve_counts(cve_rows, args.run1_config, exposure_column)
        cve2 = load_cve_counts(cve_rows, args.run2_config, exposure_column)
        run1 = build_run_results(
            target_rows,
            component_rows,
            args.run1_config,
            cve1,
        )
        run2 = build_run_results(
            target_rows,
            component_rows,
            args.run2_config,
            cve2,
        )
        summary, details = build_outputs(run1, run2, threshold)
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        write_csv(output_dir / "rq5_repeatability.csv", summary, SUMMARY_COLUMNS)
        write_csv(output_dir / "rq5_target_details.csv", details, DETAIL_COLUMNS)
        (output_dir / "rq5_repeatability.tex").write_text(
            render_latex(summary, args.caption, args.label)
        )
    except (OSError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(f"[done] wrote RQ5 artifacts to {output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
