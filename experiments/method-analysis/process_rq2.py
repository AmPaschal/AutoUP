#!/usr/bin/env python3
"""Generate an RQ2 LaTeX table from the CVE analysis CSV."""

from __future__ import annotations

import argparse
import csv
from collections import OrderedDict
from pathlib import Path
from typing import Any


NA_STRINGS = {"", "n/a", "na", "none", "null"}
SUMMARY_ROWS = [
    "Number of CVEs",
    "Number exposed",
]
ROOT_CAUSE_DISPLAY_NAMES = {
    "incomplete cve recreation": "Incomplete CVE recreation",
    "precondition validation error": "Precondition validation error",
    "bmc tool limitation": "BMC tool limitation",
    "limited scope": "Limited scope",
    "limited loop unwinding": "Limited loop unwinding",
    "over-constrained environment": "Over-constrained environment",
    "resource exhaustion": "Resource exhaustion",
    "semantic invalidity": "Semantic invalidity",
    "limited property modelling": "Limited property modeling",
    "limited property model": "Limited property modeling",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an RQ2 LaTeX table from the CVE analysis CSV."
    )
    parser.add_argument("cve_csv", type=Path, help="Path to the CVE analysis CSV")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output LaTeX file path. Defaults to <csv parent>/rq2_vuln_exposure.tex",
    )
    parser.add_argument(
        "--series-labels",
        default="",
        help="Optional comma-separated raw=display mapping for Config labels",
    )
    parser.add_argument(
        "--config-order",
        default="",
        help="Optional comma-separated config order to use for output columns",
    )
    parser.add_argument(
        "--caption",
        default="Exposure rate of known vulnerabilities",
        help="LaTeX table caption",
    )
    parser.add_argument(
        "--label",
        default="tab:rq2-vuln-exposure",
        help="LaTeX table label",
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


def normalize_key(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def canonicalize_root_cause(value: Any) -> str:
    normalized = normalize_key(value)
    if not normalized:
        return ""
    return ROOT_CAUSE_DISPLAY_NAMES.get(normalized, str(value).strip())


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


def parse_mapping(raw_mapping: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not raw_mapping.strip():
        return result
    for item in raw_mapping.split(","):
        piece = item.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ValueError(f"Invalid mapping entry {piece!r}; expected raw=value")
        raw, value = piece.split("=", 1)
        raw_key = normalize_key(raw)
        if not raw_key:
            raise ValueError(f"Invalid mapping entry {piece!r}; raw key cannot be empty")
        result[raw_key] = value.strip()
    return result


def parse_config_order(raw_order: str) -> list[str]:
    if not raw_order.strip():
        return []
    order: list[str] = []
    seen: set[str] = set()
    for item in raw_order.split(","):
        key = normalize_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        order.append(key)
    return order


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {csv_path}")
        required = {"CVE ID", "Config", "Exposed", "Non-exposure Root Cause"}
        missing = [name for name in required if name not in reader.fieldnames]
        if missing:
            raise ValueError("CSV is missing required columns: " + ", ".join(sorted(missing)))
        return [dict(row) for row in reader]


def should_skip_row(row: dict[str, str]) -> bool:
    return normalize_key(row.get("Exposed")) == "n/a"


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: OrderedDict[tuple[str, str], dict[str, str]] = OrderedDict()
    for row in rows:
        if should_skip_row(row):
            continue
        config_key = normalize_key(row.get("Config"))
        if not config_key:
            continue
        key = (normalize_key(row.get("CVE ID")), config_key)
        deduped[key] = row
    return list(deduped.values())


def canonical_config_names(rows: list[dict[str, str]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in rows:
        config = str(row.get("Config", "")).strip()
        config_key = normalize_key(config)
        if config_key and config_key not in names:
            names[config_key] = config
    return names


def order_configs(rows: list[dict[str, str]], requested_order: list[str]) -> list[str]:
    present = OrderedDict((normalize_key(row.get("Config")), None) for row in rows)
    present_configs = [config for config in present if config]
    if not requested_order:
        return present_configs

    missing = [config for config in requested_order if config not in present]
    if missing:
        raise ValueError(
            "Requested config(s) not present after filtering: " + ", ".join(missing)
        )
    return requested_order


def group_rows_by_config(rows: list[dict[str, str]], configs: list[str]) -> dict[str, list[dict[str, str]]]:
    grouped = {config: [] for config in configs}
    for row in rows:
        config_key = normalize_key(row.get("Config"))
        if config_key in grouped:
            grouped[config_key].append(row)
    return grouped


def collect_root_cause_rows(rows: list[dict[str, str]]) -> list[str]:
    root_causes: OrderedDict[str, None] = OrderedDict()
    for row in rows:
        if parse_bool(row.get("Exposed")) is not False:
            continue
        root_cause = canonicalize_root_cause(row.get("Non-exposure Root Cause"))
        if root_cause:
            root_causes.setdefault(root_cause, None)
    return list(root_causes)


def build_counts(
    grouped_rows: dict[str, list[dict[str, str]]],
    root_cause_rows: list[str],
) -> list[dict[str, str]]:
    root_cause_keys = {root_cause: normalize_key(root_cause) for root_cause in root_cause_rows}
    per_config_counts: dict[str, dict[str, int]] = {}

    for config, rows in grouped_rows.items():
        counts = {metric: 0 for metric in SUMMARY_ROWS + root_cause_rows}
        counts["Number of CVEs"] = len(rows)
        counts["Number exposed"] = sum(1 for row in rows if parse_bool(row.get("Exposed")) is True)

        for row in rows:
            if parse_bool(row.get("Exposed")) is not False:
                continue
            root_cause_raw = canonicalize_root_cause(row.get("Non-exposure Root Cause"))
            if not root_cause_raw:
                continue
            root_cause_key = normalize_key(root_cause_raw)
            for root_cause, expected_key in root_cause_keys.items():
                if root_cause_key == expected_key:
                    counts[root_cause] += 1
                    break

        per_config_counts[config] = counts

    table_rows: list[dict[str, str]] = []
    for metric in SUMMARY_ROWS + root_cause_rows:
        metric_row = {"Metric": metric}
        for config in grouped_rows:
            metric_row[config] = str(per_config_counts[config][metric])
        table_rows.append(metric_row)
    return table_rows


def build_display_names(
    configs: list[str],
    canonical_names: dict[str, str],
    label_mapping: dict[str, str],
) -> dict[str, str]:
    display_names: dict[str, str] = {}
    for config in configs:
        display_names[config] = label_mapping.get(config, canonical_names.get(config, config))
    return display_names


def render_latex_table(
    table_rows: list[dict[str, str]],
    configs: list[str],
    display_names: dict[str, str],
    caption: str,
    label: str,
) -> str:
    column_spec = "l" + ("c" * len(configs))
    header_cells = " & ".join(latex_escape(display_names[config]) for config in configs)
    lines = [
        "\\begin{table}",
        "    \\centering",
        f"    \\caption{{{latex_escape(caption)}}}",
        f"    \\begin{{tabular}}{{{column_spec}}}",
        "    \\toprule",
        f"        Metric & {header_cells} \\\\",
        "        \\midrule",
    ]

    for index, row in enumerate(table_rows):
        values = " & ".join(row[config] for config in configs)
        lines.append(f"        {latex_escape(row['Metric'])} & {values} \\\\")
        if index in {1}:
            lines.append("        \\midrule")

    lines.extend(
        [
            "        \\bottomrule",
            "    \\end{tabular}",
            f"    \\label{{{latex_escape(label)}}}",
            "\\end{table}",
            "",
        ]
    )
    return "\n".join(lines)

def main() -> None:
    args = parse_args()
    cve_csv = args.cve_csv.resolve()
    output_path = (
        args.output.resolve()
        if args.output is not None
        else cve_csv.parent / "rq2_vuln_exposure.tex"
    )

    rows = load_rows(cve_csv)
    rows = dedupe_rows(rows)
    if not rows:
        raise ValueError("No rows remain after applying the RQ2 filtering rules")

    config_order = parse_config_order(args.config_order)
    configs = order_configs(rows, config_order)
    label_mapping = parse_mapping(args.series_labels)
    canonical_names = canonical_config_names(rows)
    display_names = build_display_names(configs, canonical_names, label_mapping)
    grouped_rows = group_rows_by_config(rows, configs)
    root_cause_rows = collect_root_cause_rows(rows)
    table_rows = build_counts(grouped_rows, root_cause_rows)

    latex = render_latex_table(
        table_rows,
        configs,
        display_names,
        args.caption,
        args.label,
    )
    output_path.write_text(latex)
    print(f"[done] wrote {output_path}")


if __name__ == "__main__":
    main()
