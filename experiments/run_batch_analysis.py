#!/usr/bin/env python3
"""Run one analyzer across multiple experiment directories and merge the CSV outputs.

Edit the configuration block below, then run:

    python3 experiments/run_batch_analysis.py

Relative paths inside `RUNS_JSON` and `MERGED_OUTPUT_CSV` are resolved from
`WORKSPACE_ROOT`. The special placeholders `${AUTOUP_ROOT}` and
`${WORKSPACE_ROOT}` are expanded automatically.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AUTOUP_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = AUTOUP_ROOT.parents[1]


# ---------------------------------------------------------------------------
# Batch configuration: edit these values.
# ---------------------------------------------------------------------------

ANALYSIS_KIND = "experiment"
# Valid values:
#   "experiment" -> runs experiments/vuln-analysis-experiment/analyze_experiment.py
#   "components" -> runs experiments/vuln-analysis-experiment/analyze_components.py

MERGED_OUTPUT_CSV = "${AUTOUP_ROOT}/experiments/analysis-data/merged-rq1-assessment-riot.csv"

TIMEOUT_SECONDS = 1800
FORCE_MAKE = False

COMPONENT_MODE = "rerun"
COMPONENT_JOBS = 10
TOOLS_SIF = "${AUTOUP_ROOT}/tools.sif"

RUNS_JSON = """
[
  {
    "repo_root": "${WORKSPACE_ROOT}/autoup-project/RIOT",
    "experiment_dir": "${WORKSPACE_ROOT}/autoup-project/RIOT/cbmc/exp-0414",
    "experiment_csv": "${AUTOUP_ROOT}/tests/test-cases/test-set-riot-main.csv",
    "config": "autoup-s1",
    "experiment_output_dir": "${WORKSPACE_ROOT}/autoup-project/AutoUP/output-2026-04-14_12-09-47"
  },
  {
    "repo_root": "${WORKSPACE_ROOT}/autoup-project/RIOT",
    "experiment_dir": "${WORKSPACE_ROOT}/autoup-project/RIOT/cbmc/exp-0416",
    "experiment_csv": "${AUTOUP_ROOT}/tests/test-cases/test-set-riot-main.csv",
    "config": "autoup-s2",
    "experiment_output_dir": "${WORKSPACE_ROOT}/autoup-project/AutoUP/output-2026-04-16_18-01-02"
  },
  {
    "repo_root": "${WORKSPACE_ROOT}/codexup-project/RIOT",
    "experiment_dir": "${WORKSPACE_ROOT}/codexup-project/RIOT/cbmc/exp-codexup-0414",
    "experiment_csv": "${AUTOUP_ROOT}/tests/test-cases/test-set-riot-main.csv",
    "config": "codexup",
    "experiment_output_dir": "${WORKSPACE_ROOT}/codexup-project/RIOT/cbmc/exp-codexup-0414/logs/codex_metrics.jsonl"
  }
]
"""


ANALYZER_PATHS = {
    "experiment": AUTOUP_ROOT / "experiments" / "vuln-analysis-experiment" / "analyze_experiment.py",
    "components": AUTOUP_ROOT / "experiments" / "vuln-analysis-experiment" / "analyze_components.py",
}


@dataclass
class RunSpec:
    repo_root: Path
    experiment_dir: Path
    experiment_csv: Path
    config: str
    experiment_output_dir: Path | None = None


def expand_placeholders(text: str) -> str:
    """Expand supported placeholders inside config strings."""
    return (
        text.replace("${AUTOUP_ROOT}", str(AUTOUP_ROOT))
        .replace("${WORKSPACE_ROOT}", str(WORKSPACE_ROOT))
    )


def resolve_config_path(raw: str | None) -> Path | None:
    """Resolve a config path against the shared workspace root."""
    if raw is None:
        return None
    expanded = Path(expand_placeholders(raw)).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (WORKSPACE_ROOT / expanded).resolve()


def load_runs() -> list[RunSpec]:
    """Parse the editable JSON list into validated run specs."""
    try:
        payload = json.loads(RUNS_JSON)
    except json.JSONDecodeError as exc:
        raise ValueError(f"RUNS_JSON is not valid JSON: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError("RUNS_JSON must decode to a list of objects")

    runs: list[RunSpec] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"RUNS_JSON entry {index} must be an object")

        missing = {"repo_root", "experiment_dir", "experiment_csv", "config"} - set(item)
        if missing:
            raise ValueError(
                f"RUNS_JSON entry {index} is missing required keys: {', '.join(sorted(missing))}"
            )

        config = str(item["config"]).strip()
        if not config:
            raise ValueError(f"RUNS_JSON entry {index} has an empty config")

        runs.append(
            RunSpec(
                repo_root=resolve_config_path(str(item["repo_root"])) or Path(),
                experiment_dir=resolve_config_path(str(item["experiment_dir"])) or Path(),
                experiment_csv=resolve_config_path(str(item["experiment_csv"])) or Path(),
                config=config,
                experiment_output_dir=resolve_config_path(
                    item.get("experiment_output_dir")
                    if item.get("experiment_output_dir") is None
                    else str(item["experiment_output_dir"])
                ),
            )
        )
    return runs


def validate_runs(runs: list[RunSpec], analysis_kind: str) -> None:
    """Fail fast on missing files or incompatible config."""
    if analysis_kind not in ANALYZER_PATHS:
        valid = ", ".join(sorted(ANALYZER_PATHS))
        raise ValueError(f"ANALYSIS_KIND must be one of: {valid}")

    analyzer_path = ANALYZER_PATHS[analysis_kind]
    if not analyzer_path.is_file():
        raise FileNotFoundError(f"Analyzer not found: {analyzer_path}")

    if not runs:
        raise ValueError("RUNS_JSON is empty")

    for index, run in enumerate(runs):
        if not run.repo_root.is_dir():
            raise FileNotFoundError(f"RUNS_JSON entry {index} repo_root not found: {run.repo_root}")
        if not run.experiment_dir.is_dir():
            raise FileNotFoundError(
                f"RUNS_JSON entry {index} experiment_dir not found: {run.experiment_dir}"
            )
        if not run.experiment_csv.is_file():
            raise FileNotFoundError(
                f"RUNS_JSON entry {index} experiment_csv not found: {run.experiment_csv}"
            )
        if analysis_kind == "components" and run.experiment_output_dir is None:
            raise ValueError(
                f"RUNS_JSON entry {index} needs experiment_output_dir for ANALYSIS_KIND=components"
            )
        if run.experiment_output_dir is not None and not run.experiment_output_dir.exists():
            raise FileNotFoundError(
                f"RUNS_JSON entry {index} experiment_output_dir not found: {run.experiment_output_dir}"
            )

    if COMPONENT_MODE not in {"rerun", "no-rerun"}:
        raise ValueError("COMPONENT_MODE must be 'rerun' or 'no-rerun'")
    if COMPONENT_JOBS < 1:
        raise ValueError("COMPONENT_JOBS must be >= 1")

    tools_sif_path = resolve_config_path(TOOLS_SIF) if TOOLS_SIF else None
    if tools_sif_path is not None and not tools_sif_path.is_file():
        raise FileNotFoundError(f"TOOLS_SIF not found: {tools_sif_path}")


def build_command(run: RunSpec, output_csv: Path) -> list[str]:
    """Build the analyzer command for one configured run."""
    analyzer_path = ANALYZER_PATHS[ANALYSIS_KIND]
    cmd = [
        sys.executable,
        str(analyzer_path),
        str(run.repo_root),
        str(run.experiment_dir),
        str(run.experiment_csv),
    ]

    if ANALYSIS_KIND == "experiment":
        cmd.extend(
            [
                "--config",
                run.config,
                "--output",
                str(output_csv),
                "--timeout",
                str(TIMEOUT_SECONDS),
            ]
        )
        if run.experiment_output_dir is not None:
            cmd.extend(["--experiment-output-dir", str(run.experiment_output_dir)])
        if FORCE_MAKE:
            cmd.append("--force-make")
        return cmd

    if run.experiment_output_dir is None:
        raise ValueError("Component analysis requires experiment_output_dir for every run")

    cmd.append(str(run.experiment_output_dir))
    cmd.extend(
        [
            "--config",
            run.config,
            "--mode",
            COMPONENT_MODE,
            "--output",
            str(output_csv),
            "--timeout",
            str(TIMEOUT_SECONDS),
            "--jobs",
            str(COMPONENT_JOBS),
        ]
    )
    tools_sif_path = resolve_config_path(TOOLS_SIF) if TOOLS_SIF else None
    if tools_sif_path is not None:
        cmd.extend(["--tools-sif", str(tools_sif_path)])
    return cmd


def merge_csv_files(csv_paths: list[Path], output_path: Path) -> None:
    """Merge multiple analyzer outputs into one CSV with a shared header."""
    fieldnames: list[str] | None = None
    merged_rows: list[dict[str, str]] = []

    for csv_path in csv_paths:
        with csv_path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError(f"Merged input CSV is missing a header: {csv_path}")
            if fieldnames is None:
                fieldnames = list(reader.fieldnames)
            elif list(reader.fieldnames) != fieldnames:
                raise ValueError(
                    f"CSV header mismatch in {csv_path}\n"
                    f"expected: {fieldnames}\n"
                    f"actual:   {reader.fieldnames}"
                )
            merged_rows.extend(dict(row) for row in reader)

    if fieldnames is None:
        raise ValueError("No CSV files were produced to merge")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged_rows)


def run_one(run: RunSpec, index: int, temp_dir: Path) -> Path:
    """Run one analyzer instance and return its per-run CSV path."""
    safe_config = run.config.replace("/", "-")
    output_csv = temp_dir / f"{index:02d}-{safe_config}.csv"
    cmd = build_command(run, output_csv)
    print(f"[run {index + 1}] config={run.config} dir={run.experiment_dir}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Analyzer failed for config={run.config} with exit code {result.returncode}")
    if not output_csv.is_file():
        raise FileNotFoundError(f"Expected analyzer output not found: {output_csv}")
    return output_csv


def main() -> int:
    try:
        runs = load_runs()
        validate_runs(runs, ANALYSIS_KIND)
        merged_output = resolve_config_path(MERGED_OUTPUT_CSV)
        if merged_output is None:
            raise ValueError("MERGED_OUTPUT_CSV must not be empty")
    except Exception as exc:
        print(f"[error] config: {exc}", file=sys.stderr)
        return 1

    temp_dir = Path(tempfile.mkdtemp(prefix="batch-analysis-"))
    per_run_csvs: list[Path] = []
    try:
        for index, run in enumerate(runs):
            per_run_csvs.append(run_one(run, index, temp_dir))
        merge_csv_files(per_run_csvs, merged_output)
    except Exception as exc:
        print(f"[error] batch run failed: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"[done] wrote merged CSV: {merged_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
