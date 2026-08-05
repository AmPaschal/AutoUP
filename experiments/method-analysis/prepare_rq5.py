#!/usr/bin/env python3
"""Run all RQ5 analyzers for two runs of one operating system and merge them.

The three merged outputs are designed to be concatenated with the corresponding
files from the other operating systems before invoking ``process_rq5.py``.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


EXPERIMENTS_DIR = Path(__file__).resolve().parents[1]
ANALYZER_DIR = EXPERIMENTS_DIR / "vuln-analysis-experiment"
ANALYZE_EXPERIMENT = ANALYZER_DIR / "analyze_experiment.py"
ANALYZE_COMPONENTS = ANALYZER_DIR / "analyze_components.py"
ANALYZE_CVES = ANALYZER_DIR / "analyze_cves.py"


@dataclass(frozen=True)
class RunSpec:
    label: str
    experiment_dir: Path
    autoup_output_dir: Path
    output_dir: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run analyze_experiment.py, analyze_components.py, and analyze_cves.py "
            "for two runs of one OS, then merge each output kind."
        )
    )
    parser.add_argument("repo_root", type=Path, help="Operating-system repository root")
    parser.add_argument("run1_dir", type=Path, help="First proof experiment directory")
    parser.add_argument("run2_dir", type=Path, help="Second proof experiment directory")
    parser.add_argument(
        "experiment_csv",
        type=Path,
        help="CSV containing the selected source_file,function_name targets",
    )
    parser.add_argument(
        "--run1-output-dir",
        type=Path,
        required=True,
        help="First run's AutoUP metrics/log output directory",
    )
    parser.add_argument(
        "--run2-output-dir",
        type=Path,
        required=True,
        help="Second run's AutoUP metrics/log output directory",
    )
    parser.add_argument(
        "--vulnerability-metadata",
        type=Path,
        required=True,
        help="Vulnerability metadata JSON consumed by analyze_cves.py",
    )
    parser.add_argument(
        "--run1-config", default="rq5-run1", help="First-run Config label"
    )
    parser.add_argument(
        "--run2-config", default="rq5-run2", help="Second-run Config label"
    )
    parser.add_argument(
        "--component-mode",
        choices=("rerun", "no-rerun"),
        default="no-rerun",
        help="analyze_components.py mode (default: no-rerun)",
    )
    parser.add_argument(
        "--component-jobs",
        type=int,
        default=1,
        help="Parallel component-analysis targets (default: 1)",
    )
    parser.add_argument(
        "--tools-sif",
        type=Path,
        help="Optional tools image passed to analyze_components.py",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Analyzer timeout per proof in seconds (default: 1800)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: rq5-intermediate-<repo name>)",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.run1_config.casefold() == args.run2_config.casefold():
        raise ValueError("--run1-config and --run2-config must differ")
    if args.component_jobs < 1:
        raise ValueError("--component-jobs must be at least 1")
    if args.timeout < 1:
        raise ValueError("--timeout must be at least 1")
    required_dirs = {
        "repository": args.repo_root,
        "run 1": args.run1_dir,
        "run 2": args.run2_dir,
        "run 1 AutoUP output": args.run1_output_dir,
        "run 2 AutoUP output": args.run2_output_dir,
    }
    for label, path in required_dirs.items():
        if not path.resolve().is_dir():
            raise ValueError(f"{label} directory not found: {path.resolve()}")
    required_files = {
        "target": args.experiment_csv,
        "vulnerability metadata": args.vulnerability_metadata,
    }
    for label, path in required_files.items():
        if not path.resolve().is_file():
            raise ValueError(f"{label} file not found: {path.resolve()}")
    if args.tools_sif is not None and not args.tools_sif.resolve().is_file():
        raise ValueError(f"tools image not found: {args.tools_sif.resolve()}")
    for analyzer in (ANALYZE_EXPERIMENT, ANALYZE_COMPONENTS, ANALYZE_CVES):
        if not analyzer.is_file():
            raise ValueError(f"analyzer not found: {analyzer}")


def output_paths(run: RunSpec) -> dict[str, Path]:
    return {
        "target": run.output_dir / "target_metrics.csv",
        "component": run.output_dir / "component_metrics.csv",
        "cve": run.output_dir / "cve_metrics.csv",
    }


def analyzer_commands(
    args: argparse.Namespace,
    run: RunSpec,
) -> list[tuple[str, list[str], Path]]:
    paths = output_paths(run)
    common = [str(args.repo_root.resolve()), str(run.experiment_dir.resolve())]
    target_command = [
        sys.executable,
        str(ANALYZE_EXPERIMENT),
        *common,
        str(args.experiment_csv.resolve()),
        "--config",
        run.label,
        "--experiment-output-dir",
        str(run.autoup_output_dir.resolve()),
        "--timeout",
        str(args.timeout),
        "--output",
        str(paths["target"]),
    ]
    component_command = [
        sys.executable,
        str(ANALYZE_COMPONENTS),
        *common,
        str(args.experiment_csv.resolve()),
        str(run.autoup_output_dir.resolve()),
        "--config",
        run.label,
        "--mode",
        args.component_mode,
        "--jobs",
        str(args.component_jobs),
        "--timeout",
        str(args.timeout),
        "--output",
        str(paths["component"]),
    ]
    if args.tools_sif is not None:
        component_command.extend(["--tools-sif", str(args.tools_sif.resolve())])
    cve_command = [
        sys.executable,
        str(ANALYZE_CVES),
        *common,
        "--config",
        run.label,
        "--vulnerability-metadata",
        str(args.vulnerability_metadata.resolve()),
        "--experiment-output-dir",
        str(run.autoup_output_dir.resolve()),
        "--timeout",
        str(args.timeout),
        "--output",
        str(paths["cve"]),
    ]
    return [
        ("target", target_command, paths["target"]),
        ("component", component_command, paths["component"]),
        ("CVE", cve_command, paths["cve"]),
    ]


def run_analyzers(args: argparse.Namespace, run: RunSpec) -> dict[str, Path]:
    run.output_dir.mkdir(parents=True, exist_ok=True)
    produced: dict[str, Path] = {}
    for kind, command, output_path in analyzer_commands(args, run):
        print(f"[{run.label}] running {kind} analyzer", file=sys.stderr)
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"{kind} analyzer failed for {run.label} with exit code {result.returncode}"
            )
        if not output_path.is_file():
            raise RuntimeError(f"{kind} analyzer did not produce {output_path}")
        produced[kind] = output_path
    return produced


def merge_csv_files(inputs: list[Path], output: Path) -> None:
    fieldnames: list[str] | None = None
    rows: list[dict[str, str]] = []
    for path in inputs:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError(f"CSV has no header: {path}")
            current = list(reader.fieldnames)
            if fieldnames is None:
                fieldnames = current
            elif current != fieldnames:
                raise ValueError(f"CSV header mismatch while merging {path}")
            rows.extend(dict(row) for row in reader)
    if fieldnames is None:
        raise ValueError("No CSV files supplied for merging")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        repo_root = args.repo_root.resolve()
        root_output = (
            args.output_dir.resolve()
            if args.output_dir is not None
            else (Path.cwd() / f"rq5-intermediate-{repo_root.name}").resolve()
        )
        runs = [
            RunSpec(
                args.run1_config,
                args.run1_dir.resolve(),
                args.run1_output_dir.resolve(),
                root_output / "run1",
            ),
            RunSpec(
                args.run2_config,
                args.run2_dir.resolve(),
                args.run2_output_dir.resolve(),
                root_output / "run2",
            ),
        ]
        per_run = [run_analyzers(args, run) for run in runs]
        merged = {
            "target": root_output / "rq5_target_metrics.csv",
            "component": root_output / "rq5_component_metrics.csv",
            "CVE": root_output / "rq5_cve_metrics.csv",
        }
        for kind, output in merged.items():
            merge_csv_files([result[kind] for result in per_run], output)
            print(f"[merged] {kind}: {output}", file=sys.stderr)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(f"[done] wrote per-OS RQ5 intermediates to {root_output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
