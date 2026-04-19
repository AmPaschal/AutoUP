#!/usr/bin/env python3
"""Analyze stage-wise AutoUP proof artifacts for RQ3."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


NA_VALUE = "N/A"
STAGE_DEFS = [
    (1, "Stage 1", "harness"),
    (2, "Stage 2", "stage2"),
    (3, "Stage 3", "debugger"),
]
LOG_STAGE_MARKERS = {
    "stage1": ("coverage_debugger.py", "Initial Overall Coverage:"),
    "coverage": ("coverage_debugger.py", "Final Overall Coverage:"),
    "vuln_refiner": ("vuln_aware_refiner.py", "Final coverage:"),
    "debugger": ("debugger.py", "Final Overall Coverage:"),
}
COLUMN_SPECS = [
    ("target_function", "Target Function"),
    ("software", "Software"),
    ("config", "Config"),
    ("tag", "Tag"),
    ("stage_order", "Stage Order"),
    ("stage", "Stage"),
    ("snapshot_tag", "Snapshot Tag"),
    ("source_file", "Source File"),
    ("proof_relpath", "Proof Relpath"),
    ("snapshot_present", "Snapshot Present"),
    ("development_time", "Development Time"),
    ("api_cost", "API Cost"),
    ("verification_completes", "Verification Completes"),
    ("verification_time", "Verification Time"),
    ("verification_succeeds", "Verification Succeeds"),
    ("property_violations", "Property Violations"),
    ("precondition_violations", "Precondition Violations"),
    ("source_files_in_scope", "Source Files In Scope"),
    ("functions_in_scope", "Functions In Scope"),
    ("target_function_reachable_line_count", "Target Function Reachable Line Count"),
    ("target_function_covered_line_count", "Target Function Covered Line Count"),
    ("target_function_line_coverage_pct", "Target Function Line Coverage %"),
    ("program_reachable_line_count", "Program Reachable Line Count"),
    ("program_covered_line_count", "Program Covered Line Count"),
    ("program_line_coverage_pct", "Program Line Coverage %"),
    ("overall_reachable_line_count", "Overall Reachable Line Count"),
    ("overall_covered_line_count", "Overall Covered Line Count"),
    ("overall_line_coverage_pct", "Overall Line Coverage %"),
    ("harness_size_loc", "Harness Size LOC"),
    ("proof_size_loc", "Proof Size LOC"),
    ("loop_unwindset_count", "Loop Unwindset Count"),
    ("loop_unwind_min", "Loop Unwind Min"),
    ("loop_unwind_max", "Loop Unwind Max"),
    ("model_used_variable_count", "Model Used Variable Count"),
    ("assumption_variable_count", "Assumption Variable Count"),
    ("precondition_count", "Precondition Count"),
    ("function_model_count", "Function Model Count"),
    ("function_model_avg_loc", "Function Model Avg LOC"),
    ("harness_symbol_count", "Harness Symbol Count"),
]
COLUMN_LABELS = dict(COLUMN_SPECS)
COLUMN_ORDER = [column_id for column_id, _ in COLUMN_SPECS]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze stage-wise AutoUP proof artifacts for RQ3."
    )
    parser.add_argument("repo_root", type=Path, help="Repository root, e.g. RIOT")
    parser.add_argument("experiment_dir", type=Path, help="Experiment directory, e.g. RIOT/cbmc/exp-0414")
    parser.add_argument("experiment_csv", type=Path, help="CSV with source_file,function_name targets")
    parser.add_argument(
        "autoup_output_dir",
        type=Path,
        help="AutoUP output directory containing metrics-*.jsonl files and per-target logs",
    )
    parser.add_argument(
        "--config",
        default="",
        help="Optional configuration label to emit in the CSV Config column",
    )
    parser.add_argument(
        "--mode",
        choices=("rerun", "no-rerun"),
        default="no-rerun",
        help="Whether to rerun stage artifacts or recover verification metrics from saved logs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination CSV path. Defaults to <experiment_dir>/component-assessment.csv",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout in seconds for rerun mode make invocations (default: 1800)",
    )
    return parser.parse_args()


def load_analyze_experiment_module() -> Any:
    module_path = Path(__file__).resolve().parents[1] / "vuln-analysis-experiment" / "analyze_experiment.py"
    spec = importlib.util.spec_from_file_location("analyze_experiment_rq3", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load analyzer helpers from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_currency(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("$"):
        text = text[1:]
    return parse_float(text)


def none_to_blank(value: object | None) -> object:
    return "" if value is None else value


def format_optional_float(value: float | None, digits: int = 6) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def seconds_to_minutes(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 60.0


def run_command(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def count_code_lines(paths: list[Path]) -> int | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None

    proc = run_command(["cloc", "--json", "--quiet", *[str(path) for path in existing]])
    if proc.returncode == 0:
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            summary = payload.get("SUM", {})
            if isinstance(summary, dict):
                code = summary.get("code")
                try:
                    return int(code)
                except (TypeError, ValueError):
                    pass

    total = 0
    in_block_comment = False
    for path in existing:
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if in_block_comment:
                if "*/" in line:
                    in_block_comment = False
                    line = line.split("*/", 1)[1].strip()
                    if not line:
                        continue
                else:
                    continue
            if line.startswith("/*"):
                if "*/" not in line:
                    in_block_comment = True
                    continue
                line = line.split("*/", 1)[1].strip()
                if not line:
                    continue
            if line.startswith("//"):
                continue
            total += 1
    return total


def extract_make_var(makefile_text: str, name: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*[:?+]?=\s*(.*)$")
    lines = makefile_text.splitlines()
    parts: list[str] = []
    collecting = False

    for line in lines:
        if not collecting:
            match = pattern.match(line)
            if match is None:
                continue
            collecting = True
            value = match.group(1).rstrip()
        else:
            value = line.rstrip()

        continuation = value.endswith("\\")
        if continuation:
            value = value[:-1].rstrip()
        parts.append(value)
        if not continuation:
            break

    return " ".join(part for part in parts if part).strip()


def resolve_snapshot_path(
    token: str,
    repo_root: Path | None,
    proof_dir: Path,
    makefile_text: str,
) -> Path | None:
    text = token.strip()
    if not text:
        return None

    replacements = {
        "$(ROOT)": extract_make_var(makefile_text, "ROOT"),
        "${ROOT}": extract_make_var(makefile_text, "ROOT"),
        "$(MAKE_INCLUDE_PATH)": extract_make_var(makefile_text, "MAKE_INCLUDE_PATH"),
        "${MAKE_INCLUDE_PATH}": extract_make_var(makefile_text, "MAKE_INCLUDE_PATH"),
    }
    for placeholder, value in replacements.items():
        if placeholder in text and value:
            text = text.replace(placeholder, value)

    candidate = Path(text)
    if candidate.is_absolute():
        return candidate.resolve()
    if repo_root is not None and not text.startswith("."):
        return (repo_root / candidate).resolve()
    return (proof_dir / candidate).resolve()


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def extract_scope_files(
    harness_text: str,
    makefile_text: str,
    repo_root: Path | None,
    proof_dir: Path,
    experiment_dir: Path,
) -> int | None:
    files: set[str] = set()
    for match in re.finditer(r'#include\s+"([^"]+\.c)"', harness_text):
        include_path = match.group(1)
        resolved = resolve_snapshot_path(include_path, repo_root, proof_dir, makefile_text)
        if resolved is not None and is_within(resolved, experiment_dir):
            continue
        files.add(include_path)
    link_value = extract_make_var(makefile_text, "LINK")
    for token in shlex_split_safe(link_value):
        if token.endswith(".c"):
            resolved = resolve_snapshot_path(token, repo_root, proof_dir, makefile_text)
            if resolved is not None and is_within(resolved, experiment_dir):
                continue
            files.add(token)
    return len(files) if files else None


def shlex_split_safe(text: str) -> list[str]:
    try:
        return list(__import__("shlex").split(text))
    except ValueError:
        return text.split()


def build_stage_definitions(proof_dir: Path, target_function: str) -> list[dict[str, Any]]:
    snapshots_dir = proof_dir / "snapshots"
    harness_prefix = f"{target_function}_harness.c"
    has_vuln_refiner = (snapshots_dir / f"{harness_prefix}.vuln_refiner").is_file()
    has_coverage = (snapshots_dir / f"{harness_prefix}.coverage").is_file()
    stage2_tag = "vuln_refiner" if has_vuln_refiner else ("coverage" if has_coverage else "")

    rows: list[dict[str, Any]] = []
    for stage_order, stage_name, tag in STAGE_DEFS:
        snapshot_tag = stage2_tag if tag == "stage2" else tag
        harness_path = snapshots_dir / f"{harness_prefix}.{snapshot_tag}" if snapshot_tag else None
        makefile_path = snapshots_dir / f"Makefile.{snapshot_tag}" if snapshot_tag else None
        rows.append(
            {
                "stage_order": stage_order,
                "stage": stage_name,
                "snapshot_tag": snapshot_tag,
                "harness_path": harness_path,
                "makefile_path": makefile_path,
                "snapshot_present": bool(
                    snapshot_tag and harness_path and makefile_path and harness_path.is_file() and makefile_path.is_file()
                ),
            }
        )
    return rows


def extract_stage_development_metrics(
    helper: Any,
    metrics_records: list[dict[str, Any]],
    stage_defs: list[dict[str, Any]],
) -> dict[int, dict[str, float | None]]:
    task_attempt_costs = extract_task_attempt_costs(helper, metrics_records)
    elapsed: dict[str, float | None] = {}

    for record in metrics_records:
        if "elapsed_time" in record:
            elapsed[str(record.get("agent_name", ""))] = parse_float(record.get("elapsed_time"))

    stage2_tag = stage_defs[1]["snapshot_tag"]
    stage3_present = stage_defs[2]["snapshot_present"]

    return {
        1: {
            "development_time": elapsed.get("InitialHarnessGenerator"),
            "api_cost": sum_optional(
                task_attempt_costs.get("InitialHarnessGenerator"),
                task_attempt_costs.get("MakefileGenerator"),
            ),
        },
        2: {
            "development_time": sum_optional(
                elapsed.get("CoverageDebugger"),
                elapsed.get("VulnAwareRefiner") if stage2_tag == "vuln_refiner" else None,
            ),
            "api_cost": sum_optional(
                task_attempt_costs.get("CoverageDebugger"),
                task_attempt_costs.get("VulnAwareRefiner") if stage2_tag == "vuln_refiner" else None,
            ),
        },
        3: {
            "development_time": elapsed.get("ProofDebugger") if stage3_present else None,
            "api_cost": sum_optional(
                task_attempt_costs.get("debugger"),
                task_attempt_costs.get("PreconditionValidator"),
            )
            if stage3_present
            else None,
        },
    }


def sum_optional(*values: float | None) -> float | None:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return sum(filtered)


def extract_task_attempt_costs(
    helper: Any,
    metrics_records: list[dict[str, Any]],
) -> dict[str, float]:
    prices = helper.load_model_pricing()
    costs: dict[str, float] = {}

    for record in metrics_records:
        if record.get("type") != "task_attempt":
            continue

        llm_data = record.get("llm_data", {})
        if not isinstance(llm_data, dict):
            continue
        model_name = llm_data.get("model_name")
        if not isinstance(model_name, str) or model_name not in prices:
            continue

        token_usage = llm_data.get("token_usage", {})
        if not isinstance(token_usage, dict):
            continue

        input_tokens = int(token_usage.get("input_tokens", 0) or 0)
        cached_tokens = int(token_usage.get("cached_tokens", 0) or 0)
        output_tokens = int(token_usage.get("output_tokens", 0) or 0)
        non_cached_input_tokens = max(0, input_tokens - cached_tokens)
        pricing = prices[model_name]
        cost = (
            (non_cached_input_tokens / 1_000_000) * pricing.get("input", 0.0)
            + (cached_tokens / 1_000_000) * pricing.get("cached", 0.0)
            + (output_tokens / 1_000_000) * pricing.get("output", 0.0)
        )

        agent_name = str(record.get("agent_name", ""))
        costs[agent_name] = costs.get(agent_name, 0.0) + cost

    return costs


def stage_snapshot_proof_files(helper: Any, proof_dir: Path, target_function: str) -> list[Path]:
    harness_name = f"{target_function}_harness.c"
    return [path for path in helper.list_proof_source_files(proof_dir) if path.name != harness_name]


def extract_agent_result_data(
    metrics_records: list[dict[str, Any]],
    agent_name: str,
) -> dict[str, Any] | None:
    result: dict[str, Any] | None = None
    for record in metrics_records:
        if record.get("type") != "agent_result" or record.get("agent_name") != agent_name:
            continue
        data = record.get("data")
        if isinstance(data, dict):
            result = data
    return result


def count_preconditions_in_harness(function: Any) -> int | None:
    if function is None:
        return None
    return function.body.count("__CPROVER_assume")


def count_harness_symbols(
    helper: Any,
    experiment_dir: Path,
    proof_dir: Path,
    target_function: str,
    symbol_json: dict | None,
) -> int | None:
    if not symbol_json:
        return None

    harness_path = proof_dir / f"{target_function}_harness.c"
    if not harness_path.is_file():
        return None

    harness_text = harness_path.read_text(encoding="utf-8", errors="ignore")
    function_names = set(helper.parse_functions(harness_text))
    if not function_names:
        return 0

    symbols = symbol_json.get("viewer-symbol", {}).get("symbols", {})
    if not isinstance(symbols, dict):
        return None

    harness_candidates = helper.viewer_path_candidates(harness_path, experiment_dir)
    if not harness_candidates:
        harness_candidates = (helper.norm_viewer_path(harness_path.name),)

    count = 0
    for symbol_name, details in symbols.items():
        if not isinstance(details, dict):
            continue
        file_name = str(details.get("file", "")).strip()
        if not file_name or not any(
            helper.path_matches_suffix(file_name, candidate) for candidate in harness_candidates
        ):
            continue
        if any(symbol_name == fn_name or symbol_name.startswith(f"{fn_name}::") for fn_name in function_names):
            count += 1
    return count


def compute_snapshot_metrics(
    helper: Any,
    experiment_dir: Path,
    proof_dir: Path,
    target_function: str,
    harness_path: Path,
    makefile_path: Path,
) -> dict[str, object]:
    harness_text = harness_path.read_text(encoding="utf-8", errors="ignore")
    makefile_text = makefile_path.read_text(encoding="utf-8", errors="ignore")
    functions = helper.parse_functions(harness_text)
    harness_fn = functions.get("harness")
    model_used_count: int | None = None
    assumption_count: int | None = None
    function_model_count = 0
    function_model_avg_loc: float | None = None

    if harness_fn is not None:
        model_vars, assumption_vars = helper.extract_harness_program_variables(harness_fn, target_function)
        model_used_count = len(model_vars)
        assumption_count = len(assumption_vars)
    precondition_count = count_preconditions_in_harness(harness_fn)

    model_loc_counts: list[int] = []
    function_model_names = [name for name in functions if name != "harness"]
    if function_model_names:
        function_model_count = len(function_model_names)
        for name in function_model_names:
            body = functions[name].body
            loc = len([line for line in body.splitlines() if line.strip()])
            model_loc_counts.append(loc)
        if model_loc_counts:
            function_model_avg_loc = sum(model_loc_counts) / len(model_loc_counts)

    cbmcflags = extract_make_var(makefile_text, "H_CBMCFLAGS")
    unwindset_count, unwind_min, unwind_max = helper.parse_unwind_metrics(cbmcflags)
    harness_size_loc = None
    if harness_fn is not None:
        harness_lines = [line for line in harness_fn.body.splitlines() if line.strip()]
        harness_size_loc = len(harness_lines)
    proof_size_loc = count_code_lines([harness_path, *stage_snapshot_proof_files(helper, proof_dir, target_function)])
    source_files_in_scope = extract_scope_files(
        harness_text,
        makefile_text,
        getattr(helper, "REPO_ROOT", None),
        proof_dir,
        experiment_dir,
    )

    return {
        "source_files_in_scope": none_to_blank(source_files_in_scope),
        "functions_in_scope": "",
        "loop_unwindset_count": unwindset_count,
        "loop_unwind_min": none_to_blank(unwind_min),
        "loop_unwind_max": none_to_blank(unwind_max),
        "model_used_variable_count": none_to_blank(model_used_count),
        "assumption_variable_count": none_to_blank(assumption_count),
        "precondition_count": none_to_blank(precondition_count),
        "function_model_count": none_to_blank(function_model_count),
        "function_model_avg_loc": format_optional_float(function_model_avg_loc),
        "harness_size_loc": none_to_blank(harness_size_loc),
        "proof_size_loc": none_to_blank(proof_size_loc),
        "harness_symbol_count": "",
    }


def copy_proof_tree_for_rerun(proof_dir: Path, stage_order: int) -> Path:
    tmp_dir = proof_dir.parent / f".rq3_tmp_stage{stage_order}_{proof_dir.name}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)

    def ignore(_: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name == "build" or name == "snapshots" or name.startswith("build_backup."):
                ignored.add(name)
        return ignored

    shutil.copytree(proof_dir, tmp_dir, ignore=ignore)
    return tmp_dir


def collect_metrics_from_proof_dir(
    helper: Any,
    repo_root: Path,
    experiment_dir: Path,
    proof_dir: Path,
    target_file: str,
    target_function: str,
) -> dict[str, object]:
    helper.REPO_ROOT = repo_root

    try:
        metadata = helper.read_make_metadata(proof_dir)
        proof_root = metadata.proof_root
    except Exception:
        proof_root = proof_dir.parent.resolve()

    proof_prefixes = helper.proof_root_prefixes(proof_root, experiment_dir)
    report_json_dir = helper.resolve_report_json_dir(proof_dir)
    report_html_index = helper.resolve_report_html_index(proof_dir)
    viewer_result_json = helper.load_json(report_json_dir / "viewer-result.json" if report_json_dir else None)
    viewer_property_json = helper.load_json(report_json_dir / "viewer-property.json" if report_json_dir else None)
    coverage_json = helper.load_json(report_json_dir / "viewer-coverage.json" if report_json_dir else None)
    reachable_json = helper.load_json(report_json_dir / "viewer-reachable.json" if report_json_dir else None)
    symbol_json = helper.load_json(report_json_dir / "viewer-symbol.json" if report_json_dir else None)
    cbmc_root = helper.parse_xml(helper.resolve_cbmc_xml_path(proof_dir))

    verification_completes = helper.verification_completed(
        viewer_result_json,
        cbmc_root,
        report_json_dir,
        report_html_index,
    )
    verification_time = helper.parse_verification_time_with_fallback(viewer_result_json, cbmc_root)
    verification_succeeds = helper.verification_succeeds(viewer_result_json, viewer_property_json)
    property_violations = helper.count_reported_error_sites(viewer_result_json, viewer_property_json)
    precondition_violations = helper.count_precondition_violations(proof_dir)
    source_files_in_scope, functions_in_scope = helper.aggregate_scope_metrics(reachable_json, proof_prefixes)
    (
        program_reachable_line_count,
        program_covered_line_count,
        program_line_coverage_pct,
        _function_model_count_from_cov,
        _function_model_avg_loc_from_cov,
        _proof_size_loc_from_cov,
        _harness_size_loc_from_cov,
    ) = helper.aggregate_coverage_metrics(coverage_json, proof_prefixes)
    (
        overall_reachable_line_count,
        overall_covered_line_count,
        overall_line_coverage_pct,
    ) = helper.aggregate_overall_coverage(coverage_json)
    (
        target_function_reachable_line_count,
        target_function_covered_line_count,
        target_function_line_coverage_pct,
    ) = helper.aggregate_target_function_coverage(coverage_json, proof_prefixes, target_function)
    harness_symbol_count = count_harness_symbols(
        helper,
        experiment_dir,
        proof_dir,
        target_function,
        symbol_json,
    )

    return {
        "verification_completes": verification_completes,
        "verification_time": format_optional_float(verification_time),
        "verification_succeeds": none_to_blank(verification_succeeds),
        "property_violations": none_to_blank(property_violations),
        "precondition_violations": none_to_blank(precondition_violations),
        "source_files_in_scope": none_to_blank(source_files_in_scope),
        "functions_in_scope": none_to_blank(functions_in_scope),
        "target_function_reachable_line_count": none_to_blank(target_function_reachable_line_count),
        "target_function_covered_line_count": none_to_blank(target_function_covered_line_count),
        "target_function_line_coverage_pct": format_optional_float(target_function_line_coverage_pct),
        "program_reachable_line_count": none_to_blank(program_reachable_line_count),
        "program_covered_line_count": none_to_blank(program_covered_line_count),
        "program_line_coverage_pct": format_optional_float(program_line_coverage_pct),
        "overall_reachable_line_count": none_to_blank(overall_reachable_line_count),
        "overall_covered_line_count": none_to_blank(overall_covered_line_count),
        "overall_line_coverage_pct": format_optional_float(overall_line_coverage_pct),
        "harness_symbol_count": none_to_blank(harness_symbol_count),
    }


def rerun_stage_metrics(
    helper: Any,
    repo_root: Path,
    experiment_dir: Path,
    proof_dir: Path,
    target_file: str,
    target_function: str,
    stage_order: int,
    harness_path: Path,
    makefile_path: Path,
    timeout_s: int,
) -> dict[str, object]:
    temp_proof_dir = copy_proof_tree_for_rerun(proof_dir, stage_order)
    print(f"[rerun] {temp_proof_dir}")
    try:
        shutil.copy2(harness_path, temp_proof_dir / f"{target_function}_harness.c")
        shutil.copy2(makefile_path, temp_proof_dir / "Makefile")
        helper.REPO_ROOT = repo_root
        helper.ensure_build(temp_proof_dir, timeout_s, True)
        result = collect_metrics_from_proof_dir(
            helper,
            repo_root,
            experiment_dir,
            temp_proof_dir,
            target_file,
            target_function,
        )
        precondition_violations = helper.count_precondition_violations(proof_dir)
        if precondition_violations is not None:
            result["precondition_violations"] = precondition_violations if stage_order == 3 else 0
        return result
    finally:
        shutil.rmtree(temp_proof_dir, ignore_errors=True)


def parse_json_block(lines: list[str], start_index: int) -> dict[str, Any] | None:
    line = lines[start_index]
    if "{" not in line:
        return None
    block_lines = [line.split("{", 1)[1]]
    brace_depth = 1
    index = start_index + 1
    while index < len(lines) and brace_depth > 0:
        segment = lines[index]
        brace_depth += segment.count("{")
        brace_depth -= segment.count("}")
        block_lines.append(segment)
        index += 1
    try:
        return json.loads("{" + "\n".join(block_lines))
    except json.JSONDecodeError:
        return None


def find_log_index(lines: list[str], marker_a: str, marker_b: str) -> int | None:
    for index, line in enumerate(lines):
        if marker_a in line and marker_b in line:
            return index
    return None


def find_preceding_make_time(lines: list[str], index: int) -> float | None:
    pattern = re.compile(r"Make command finished in ([0-9.]+) seconds\.")
    for scan in range(index, -1, -1):
        match = pattern.search(lines[scan])
        if match:
            return parse_float(match.group(1))
    return None


def find_first_unresolved_errors(lines: list[str]) -> int | None:
    pattern = re.compile(r"Unresolved Errors:\s+(\d+)")
    for line in lines:
        match = pattern.search(line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def find_last_unresolved_errors_before(lines: list[str], index: int) -> int | None:
    pattern = re.compile(r"Unresolved Errors:\s+(\d+)")
    for scan in range(index, -1, -1):
        match = pattern.search(lines[scan])
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def coverage_result_from_metrics(metrics_records: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for agent_name in ("CoverageDebugger", "debugger"):
        data = extract_agent_result_data(metrics_records, agent_name)
        if not isinstance(data, dict):
            continue
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return None


def parse_log_coverage(
    lines: list[str],
    marker_a: str,
    marker_b: str,
) -> tuple[dict[str, Any] | None, float | None, int | None]:
    index = find_log_index(lines, marker_a, marker_b)
    if index is None:
        return None, None, None
    coverage = parse_json_block(lines, index)
    verification_time = find_preceding_make_time(lines, index)
    unresolved = find_last_unresolved_errors_before(lines, index)
    return coverage, verification_time, unresolved


def apply_coverage_metrics(target: dict[str, object], coverage: dict[str, Any] | None) -> None:
    if not isinstance(coverage, dict):
        return
    target["overall_covered_line_count"] = none_to_blank(coverage.get("hit"))
    target["overall_reachable_line_count"] = none_to_blank(coverage.get("total"))
    percentage = parse_float(coverage.get("percentage"))
    target["overall_line_coverage_pct"] = format_optional_float(
        None if percentage is None else percentage * 100.0
    )


def parse_log_stage_metrics(
    metrics_records: list[dict[str, Any]],
    log_path: Path,
    stage_tag: str,
) -> dict[str, object]:
    result: dict[str, object] = {}
    lines: list[str] = []
    if log_path.is_file():
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()

    if stage_tag == "harness":
        initial = coverage_result_from_metrics(metrics_records, "initial_coverage")
        apply_coverage_metrics(result, initial)
        init_status = extract_agent_result_data(metrics_records, "InitialHarnessGenerator")
        if isinstance(init_status, dict):
            verification_status = init_status.get("verification_status")
            if isinstance(verification_status, bool):
                result["verification_succeeds"] = verification_status
        if lines:
            coverage, verification_time, _ = parse_log_coverage(lines, *LOG_STAGE_MARKERS["stage1"])
            if "overall_reachable_line_count" not in result:
                apply_coverage_metrics(result, coverage)
            if verification_time is not None:
                result["verification_time"] = format_optional_float(verification_time)
        if result:
            result["verification_completes"] = True
        return result

    if stage_tag == "coverage":
        final_cov = coverage_result_from_metrics(metrics_records, "final_coverage")
        apply_coverage_metrics(result, final_cov)
        if lines:
            coverage, verification_time, unresolved = parse_log_coverage(lines, *LOG_STAGE_MARKERS["coverage"])
            if "overall_reachable_line_count" not in result:
                apply_coverage_metrics(result, coverage)
            if verification_time is not None:
                result["verification_time"] = format_optional_float(verification_time)
            if unresolved is not None:
                result["property_violations"] = unresolved
                result["verification_succeeds"] = unresolved == 0
        if result:
            result["verification_completes"] = True
        return result

    if stage_tag == "vuln_refiner":
        if lines:
            coverage, verification_time, _ = parse_log_coverage(lines, *LOG_STAGE_MARKERS["vuln_refiner"])
            apply_coverage_metrics(result, coverage)
            if verification_time is not None:
                result["verification_time"] = format_optional_float(verification_time)
        debugger_data = extract_agent_result_data(metrics_records, "debugger")
        if isinstance(debugger_data, dict):
            initial_errors = debugger_data.get("initial_errors")
            if isinstance(initial_errors, int):
                result["property_violations"] = initial_errors
                result["verification_succeeds"] = initial_errors == 0
        if result:
            result["verification_completes"] = True
        return result

    if stage_tag == "debugger":
        final_cov = coverage_result_from_metrics(metrics_records, "debugger_final_coverage")
        apply_coverage_metrics(result, final_cov)
        debugger_data = extract_agent_result_data(metrics_records, "debugger")
        if isinstance(debugger_data, dict):
            final_errors = debugger_data.get("final_errors")
            if isinstance(final_errors, int):
                result["property_violations"] = final_errors
                result["verification_succeeds"] = final_errors == 0
        if lines:
            coverage, verification_time, unresolved = parse_log_coverage(lines, *LOG_STAGE_MARKERS["debugger"])
            if "overall_reachable_line_count" not in result:
                apply_coverage_metrics(result, coverage)
            if verification_time is not None:
                result["verification_time"] = format_optional_float(verification_time)
            if "property_violations" not in result and unresolved is not None:
                result["property_violations"] = unresolved
                result["verification_succeeds"] = unresolved == 0
        if result:
            result["verification_completes"] = True
        return result

    return {}


def derive_no_rerun_metrics(
    helper: Any,
    proof_dir: Path,
    metrics_records: list[dict[str, Any]],
    stage_tag: str,
    log_path: Path,
) -> dict[str, object]:
    result = parse_log_stage_metrics(metrics_records, log_path, stage_tag)
    precondition_violations = helper.count_precondition_violations(proof_dir)
    if precondition_violations is not None:
        result["precondition_violations"] = precondition_violations if stage_tag == "debugger" else 0
    if log_path.is_file():
        print(f"[recover] {log_path}")
    else:
        print(f"[missing] {log_path}")
    return result


def initialize_row(
    software: str,
    config: str,
    tag: str,
    source_file: str,
    target_function: str,
    proof_relpath: str,
    stage_order: int,
    stage: str,
    snapshot_tag: str,
    snapshot_present: bool,
) -> dict[str, object]:
    row: dict[str, object] = {
        "software": software,
        "config": config,
        "tag": tag,
        "source_file": source_file,
        "target_function": target_function,
        "proof_relpath": proof_relpath,
        "stage_order": stage_order,
        "stage": stage,
        "snapshot_tag": snapshot_tag,
        "snapshot_present": snapshot_present,
    }
    for column_id in COLUMN_ORDER:
        row.setdefault(column_id, "")
    return row


def write_csv(rows: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[COLUMN_LABELS[column] for column in COLUMN_ORDER])
        writer.writeheader()
        for row in rows:
            writer.writerow({COLUMN_LABELS[column]: row.get(column, "") for column in COLUMN_ORDER})


def main() -> int:
    args = parse_args()
    helper = load_analyze_experiment_module()
    repo_root = args.repo_root.resolve()
    experiment_dir = args.experiment_dir.resolve()
    experiment_csv = args.experiment_csv.resolve()
    autoup_output_dir = args.autoup_output_dir.resolve()

    if not repo_root.is_dir():
        print(f"Repository root not found: {repo_root}", file=sys.stderr)
        return 1
    if not experiment_dir.is_dir():
        print(f"Experiment directory not found: {experiment_dir}", file=sys.stderr)
        return 1
    if not experiment_csv.is_file():
        print(f"Experiment CSV not found: {experiment_csv}", file=sys.stderr)
        return 1
    if not autoup_output_dir.is_dir():
        print(f"AutoUP output directory not found: {autoup_output_dir}", file=sys.stderr)
        return 1

    helper.REPO_ROOT = repo_root
    try:
        targets = helper.read_experiment_targets(experiment_csv)
    except ValueError as exc:
        print(f"[error] targets: {exc}", file=sys.stderr)
        return 1

    output_path = args.output.resolve() if args.output else experiment_dir / "component-assessment.csv"
    rows: list[dict[str, object]] = []

    for target_file, target_function in targets:
        proof_dir, proof_relpath = helper.resolve_proof_dir(experiment_dir, target_file, target_function)
        software = helper.infer_software_name(target_file)
        print(f"[proof] {proof_dir}")
        stage_defs = build_stage_definitions(proof_dir, target_function)
        metrics_records = helper.load_metrics_records(autoup_output_dir, target_file, target_function)
        stage_dev = extract_stage_development_metrics(helper, metrics_records, stage_defs)
        log_path = autoup_output_dir / f"{Path(target_file).stem}-{target_function}.log"

        final_stage_order = 0
        for stage_info in stage_defs:
            if stage_info["snapshot_present"]:
                final_stage_order = stage_info["stage_order"]

        for stage_info in stage_defs:
            print(
                f"[stage] {stage_info['stage']} "
                f"tag={stage_info['snapshot_tag'] or 'none'} "
                f"present={stage_info['snapshot_present']}"
            )
            row = initialize_row(
                software=software,
                config=args.config,
                tag=experiment_dir.name,
                source_file=target_file,
                target_function=target_function,
                proof_relpath=proof_relpath,
                stage_order=stage_info["stage_order"],
                stage=stage_info["stage"],
                snapshot_tag=stage_info["snapshot_tag"],
                snapshot_present=stage_info["snapshot_present"],
            )

            dev_metrics = stage_dev.get(stage_info["stage_order"], {})
            row["development_time"] = format_optional_float(
                seconds_to_minutes(dev_metrics.get("development_time"))
            )
            row["api_cost"] = format_optional_float(dev_metrics.get("api_cost"), digits=4)

            if not proof_dir.is_dir() or not stage_info["snapshot_present"]:
                rows.append(row)
                continue

            harness_path = stage_info["harness_path"]
            makefile_path = stage_info["makefile_path"]
            if harness_path is None or makefile_path is None:
                rows.append(row)
                continue

            row.update(
                compute_snapshot_metrics(
                    helper,
                    experiment_dir,
                    proof_dir,
                    target_function,
                    harness_path,
                    makefile_path,
                )
            )

            if args.mode == "rerun":
                row.update(
                    rerun_stage_metrics(
                        helper,
                        repo_root,
                        experiment_dir,
                        proof_dir,
                        target_file,
                        target_function,
                        stage_info["stage_order"],
                        harness_path,
                        makefile_path,
                        args.timeout,
                    )
                )
            else:
                row.update(
                    derive_no_rerun_metrics(
                        helper,
                        proof_dir,
                        metrics_records,
                        stage_info["snapshot_tag"],
                        log_path,
                    )
                )

            rows.append(row)

    write_csv(rows, output_path)
    print(f"[done] wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
