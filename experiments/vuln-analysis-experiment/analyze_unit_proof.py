#!/usr/bin/env python3
"""Analyze RQ4 proof directories for FreeRTOS expert and AutoUP proofs."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any


AUTOUP_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = AUTOUP_ROOT.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_REPO_ROOT = PROJECT_ROOT / "FreeRTOS-Plus-TCP"
DEFAULT_MAPPING_CSV = Path(__file__).with_name("rq4_freertos_proof_map.csv")
DEFAULT_OUTPUT_CSV = Path(__file__).with_name("rq4_unit_proof_metrics.csv")
ANALYZE_EXPERIMENT_PATH = Path(__file__).with_name("analyze_experiment.py")
TOOLS_SIF_PATH = AUTOUP_ROOT / "tools.sif"
NA_STRINGS = {"", "n/a", "na", "none", "null"}
MEMORY_SAFETY_CLASSES = {
    "array bounds",
    "pointer",
    "pointer arithmetic",
    "pointer dereference",
    "pointer primitives",
}
CONTROL_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof"}
CALL_GRAPH_EDGE_RE = re.compile(r"^([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)$")
ROW_COLUMNS = [
    "Source File",
    "Target Function",
    "Proof Origin",
    "Proof Directory",
    "Proof Relative Path",
    "Proof Entry",
    "Proof Variant",
    "Files In Scope",
    "Functions In Scope",
    "Custom Loop Bounds",
    "Custom Loop Bound Count",
    "Precondition Count",
    "Function Model Count",
    "Function Model Avg LOC",
    "Harness Size LOC",
    "Proof Size LOC",
    "Verification Completes",
    "Verification Time",
    "Overall Reachable LOC",
    "Overall Covered LOC",
    "Overall Coverage %",
    "Program Reachable LOC",
    "Program Covered LOC",
    "Program Coverage %",
    "Target Function Reachable LOC",
    "Target Function Covered LOC",
    "Target Function Coverage %",
    "Symbols In Unit Proof",
    "Harness External Proof Calls",
    "Memory Safety Properties Instrumented",
    "Memory Safety Properties Verified",
    "Memory Safety Properties Violated",
    "Loop Unwinding Assertions Instrumented",
    "Loop Unwinding Assertions Failed",
    "No Body Properties Instrumented",
    "No Body Properties Failed",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze expert and AutoUP unit-proof directories for RQ4."
    )
    parser.add_argument(
        "mapping_csv",
        nargs="?",
        type=Path,
        default=DEFAULT_MAPPING_CSV,
        help="CSV mapping targets to expert/tool proof directories",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="Repository root for FreeRTOS-Plus-TCP",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_CSV,
        help="Output CSV path",
    )
    return parser.parse_args()


def load_analyze_experiment_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "rq4_analyze_experiment",
        ANALYZE_EXPERIMENT_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {ANALYZE_EXPERIMENT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in NA_STRINGS
    return False


def none_to_blank(value: Any) -> Any:
    return "" if value is None else value


def format_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def resolve_workspace_path(raw_path: str) -> Path:
    candidate = Path(raw_path.strip())
    if candidate.is_absolute():
        return candidate.resolve()
    return (WORKSPACE_ROOT / candidate).resolve()


def read_mapping_rows(mapping_csv: Path) -> list[dict[str, str]]:
    with mapping_csv.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"Mapping CSV has no header: {mapping_csv}")
        required = {"source_file", "function_name", "freertos_proof_dirs", "autoup_proof_dir"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"Mapping CSV missing required columns: {', '.join(sorted(missing))}"
            )
        return [dict(row) for row in reader]


def split_proof_dirs(raw_value: str) -> list[str]:
    pieces = [piece.strip() for piece in raw_value.split(";")]
    return [piece for piece in pieces if piece]


def detect_origin(proof_dir: Path) -> str:
    normalized = proof_dir.as_posix()
    if "/test/cbmc/proofs/" in normalized:
        return "FreeRTOS Expert"
    return "AutoUP"


def detect_suite_root(proof_dir: Path) -> Path:
    for ancestor in [proof_dir, *proof_dir.parents]:
        if ancestor.name == "proofs":
            return ancestor
        if re.fullmatch(r"exp-\d+", ancestor.name):
            return ancestor
    return proof_dir.parent


def derive_proof_variant(proof_dir: Path, suite_root: Path) -> str:
    try:
        relative = proof_dir.relative_to(suite_root)
    except ValueError:
        return ""
    parts = relative.parts
    if parts and parts[-1].startswith("config_"):
        return parts[-1]
    if len(parts) > 1 and parts[-1] != relative.parts[0]:
        return parts[-1] if parts[-1] != proof_dir.name else ""
    return ""


def load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def resolve_report_json_dir(proof_dir: Path, ae: Any) -> Path | None:
    for candidate in (
        ae.resolve_report_json_dir(proof_dir),
        proof_dir / "report" / "json",
    ):
        if candidate is not None and candidate.is_dir():
            return candidate
    return None


def resolve_report_html_index(proof_dir: Path, ae: Any) -> Path | None:
    for candidate in (
        ae.resolve_report_html_index(proof_dir),
        proof_dir / "report" / "html" / "index.html",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def resolve_cbmc_xml_path(proof_dir: Path, ae: Any) -> Path | None:
    for candidate in (
        ae.resolve_cbmc_xml_path(proof_dir),
        proof_dir / "cbmc.xml",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def cbmc_tools_available_locally() -> bool:
    return shutil.which("goto-instrument") is not None and shutil.which("goto-cc") is not None


def run_cbmc_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = cmd
    if not cbmc_tools_available_locally():
        if shutil.which("apptainer") is None or not TOOLS_SIF_PATH.is_file():
            return subprocess.CompletedProcess(
                argv,
                127,
                "",
                "CBMC tools are unavailable locally and tools.sif/apptainer fallback is missing.",
            )
        argv = ["apptainer", "exec", str(TOOLS_SIF_PATH), *cmd]
    return subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_all_make_assignments(proof_dir: Path, ae: Any) -> dict[str, str]:
    proc = ae.run_command(["make", "-pn", "-C", str(proof_dir)])
    if proc.returncode != 0:
        return {}
    assignments: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if " = " not in line:
            continue
        name, value = line.split(" = ", 1)
        assignments[name.strip()] = value.strip()
    return assignments


def find_cbmc_root(proof_dir: Path) -> Path | None:
    for ancestor in [proof_dir, *proof_dir.parents]:
        if ancestor.name == "cbmc":
            return ancestor
    return None


def possible_viewer_paths(file_path: Path, repo_root: Path) -> list[str]:
    candidates: list[str] = []
    for base in (repo_root, WORKSPACE_ROOT):
        try:
            candidate = file_path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
        if candidate not in candidates:
            candidates.append(candidate)
    absolute = file_path.resolve().as_posix()
    if absolute not in candidates:
        candidates.append(absolute)
    return candidates


def resolve_viewer_path_for_file(
    file_path: Path,
    reachable_files: dict[str, Any],
    repo_root: Path,
) -> str | None:
    for candidate in possible_viewer_paths(file_path, repo_root):
        if candidate in reachable_files:
            return candidate
    basename = file_path.name
    matches = [viewer_path for viewer_path in reachable_files if viewer_path.endswith(f"/{basename}") or viewer_path == basename]
    if len(matches) == 1:
        return matches[0]
    return None


def is_under_viewer_root(viewer_path: str, root_path: str) -> bool:
    normalized_viewer = viewer_path.strip("/")
    normalized_root = root_path.strip("/")
    return normalized_viewer == normalized_root or normalized_viewer.startswith(f"{normalized_root}/")


def expand_make_value(value: str, assignments: dict[str, str], max_depth: int = 8) -> str:
    expanded = value
    pattern = re.compile(r"\$\(([^)]+)\)")
    for _ in range(max_depth):
        changed = False

        def replace(match: re.Match[str]) -> str:
            nonlocal changed
            name = match.group(1)
            replacement = assignments.get(name)
            if replacement is None:
                return match.group(0)
            changed = True
            return replacement

        updated = pattern.sub(replace, expanded)
        expanded = updated
        if not changed:
            break
    return expanded


def tokenize_make_value(value: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError:
        return value.split()


def discover_harness_file(proof_dir: Path, entry: str) -> Path | None:
    named = proof_dir / f"{entry}_harness.c"
    if named.exists():
        return named
    harnesses = sorted(proof_dir.glob("*_harness.c"))
    if len(harnesses) == 1:
        return harnesses[0]
    return None


def extract_c_includes(path: Path) -> list[Path]:
    includes: list[Path] = []
    include_re = re.compile(r'^\s*#\s*include\s+"([^"]+\.c)"')
    for line in path.read_text(errors="ignore").splitlines():
        match = include_re.match(line)
        if not match:
            continue
        included = (path.parent / match.group(1)).resolve()
        if included.exists():
            includes.append(included)
    return includes


def is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def collect_scope_source_files(
    proof_dir: Path,
    harness_file: Path | None,
    suite_root: Path,
    assignments: dict[str, str],
) -> list[Path]:
    files: set[Path] = set()

    for var_name in ("H_OBJS_EXCEPT_HARNESS", "H_OBJS", "LINK"):
        raw_value = assignments.get(var_name)
        if not raw_value:
            continue
        expanded = expand_make_value(raw_value, assignments)
        for token in tokenize_make_value(expanded):
            if token.endswith(".goto"):
                token = token[:-5] + ".c"
            if not token.endswith(".c"):
                continue
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = (proof_dir / candidate).resolve()
            if candidate.exists():
                files.add(candidate.resolve())

    if harness_file is not None:
        for included in extract_c_includes(harness_file):
            files.add(included.resolve())

    filtered = [
        path for path in sorted(files)
        if path.is_file() and not is_under(path, suite_root)
    ]
    return filtered


def extract_function_spans(source: str, ae: Any) -> dict[str, tuple[int, int]]:
    sanitized = ae.strip_c_comments_and_strings(source)
    spans: dict[str, tuple[int, int]] = {}
    pattern = re.compile(
        r"(?P<prefix>(?:^|\n)[^\n{};#]*?)\b(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^;{}()]*(?:\([^)]*\)[^;{}()]*)*)\)\s*\{",
        re.MULTILINE,
    )
    for match in pattern.finditer(sanitized):
        name = match.group("name")
        if name in CONTROL_KEYWORDS or "=" in match.group("prefix"):
            continue
        brace_index = match.end() - 1
        end_index = ae.find_matching_brace(sanitized, brace_index)
        start_line = source.count("\n", 0, match.start()) + 1
        end_line = source.count("\n", 0, end_index) + 1
        spans[name] = (start_line, end_line)
    return spans


def count_code_lines(segment: str, ae: Any) -> int:
    sanitized = ae.strip_c_comments_and_strings(segment)
    total = 0
    for line in sanitized.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {"{", "}"}:
            continue
        total += 1
    return total


def function_loc(source: str, span: tuple[int, int], ae: Any) -> int:
    lines = source.splitlines()
    start_line, end_line = span
    segment = "\n".join(lines[start_line - 1 : end_line])
    return count_code_lines(segment, ae)


def count_scope_functions(scope_files: list[Path], ae: Any) -> int:
    total = 0
    for path in scope_files:
        total += len(ae.parse_functions(path.read_text(errors="ignore")))
    return total


def static_proof_metrics(
    proof_dir: Path,
    harness_file: Path | None,
    ae: Any,
) -> tuple[int | None, int | None, float | None]:
    if harness_file is None:
        return None, None, None

    function_locs: list[int] = []
    for path in ae.list_proof_source_files(proof_dir):
        if path.suffix != ".c" or path.name == "general-stubs.c":
            continue
        source = path.read_text(errors="ignore")
        spans = extract_function_spans(source, ae)
        for name, span in spans.items():
            if name == "harness":
                continue
            function_locs.append(function_loc(source, span, ae))

    if not function_locs:
        return None, None, None
    avg_loc = sum(function_locs) / len(function_locs)
    return len(function_locs), None, avg_loc


def static_harness_loc(harness_file: Path | None, ae: Any) -> int | None:
    if harness_file is None or not harness_file.exists():
        return None
    source = harness_file.read_text(errors="ignore")
    spans = extract_function_spans(source, ae)
    harness_span = spans.get("harness")
    if harness_span is None:
        return None
    return function_loc(source, harness_span, ae)


def count_harness_preconditions(harness_file: Path | None, ae: Any) -> int | None:
    if harness_file is None or not harness_file.exists():
        return None
    source = harness_file.read_text(errors="ignore")
    spans = extract_function_spans(source, ae)
    harness_span = spans.get("harness")
    if harness_span is None:
        return None
    lines = source.splitlines()
    start_line, end_line = harness_span
    segment = "\n".join(lines[start_line - 1 : end_line])
    sanitized = ae.strip_c_comments_and_strings(segment)
    assume_count = len(re.findall(r"\b__CPROVER_assume\s*\(", sanitized))
    precondition_count = len(re.findall(r"\b__CPROVER_precondition\s*\(", sanitized))
    return assume_count + precondition_count


def count_harness_external_proof_calls(
    proof_dir: Path,
    harness_file: Path | None,
    reachable_json: dict[str, Any] | None,
    entry: str,
    repo_root: Path,
    ae: Any,
) -> int | None:
    if harness_file is None or not harness_file.exists():
        return None
    reachable_files = {}
    if reachable_json:
        reachable_files = reachable_json.get("viewer-reachable", {}).get("reachable", {})
        if not isinstance(reachable_files, dict):
            reachable_files = {}
    if not reachable_files:
        return None

    harness_viewer_path = resolve_viewer_path_for_file(harness_file, reachable_files, repo_root)
    if harness_viewer_path is None:
        return None

    harness_functions = reachable_files.get(harness_viewer_path, [])
    if not isinstance(harness_functions, list):
        return None
    harness_function_names = {
        function_name
        for function_name in harness_functions
        if isinstance(function_name, str) and function_name
    }
    if not harness_function_names:
        return 0

    cbmc_root = find_cbmc_root(proof_dir)
    if cbmc_root is None:
        return None
    try:
        cbmc_root_view = cbmc_root.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None

    proof_side_function_names: set[str] = set()
    for viewer_path, function_names in reachable_files.items():
        if viewer_path == harness_viewer_path or not isinstance(function_names, list):
            continue
        if not is_under_viewer_root(viewer_path, cbmc_root_view):
            continue
        for function_name in function_names:
            if isinstance(function_name, str) and function_name:
                proof_side_function_names.add(function_name)

    if not proof_side_function_names:
        return 0

    goto_path = resolve_compiled_goto_path(proof_dir, entry)
    if goto_path is None:
        return None

    call_graph_edges = extract_reachable_call_graph_edges(goto_path)
    if call_graph_edges is None:
        return None

    return sum(
        1
        for caller, callee in call_graph_edges
        if caller in harness_function_names and callee in proof_side_function_names
    )


def resolve_compiled_goto_path(proof_dir: Path, entry: str) -> Path | None:
    for candidate in (
        proof_dir / f"{entry}.goto",
        proof_dir / "build" / f"{entry}.goto",
    ):
        if candidate.is_file():
            return candidate
    return None


def extract_reachable_call_graph_edges(goto_path: Path) -> list[tuple[str, str]] | None:
    proc = run_cbmc_command(["goto-instrument", "--reachable-call-graph", str(goto_path)])
    if proc.returncode != 0:
        return None

    edges: list[tuple[str, str]] = []
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        match = CALL_GRAPH_EDGE_RE.fullmatch(line)
        if match is None:
            continue
        edges.append((match.group(1), match.group(2)))
    return edges


def parse_custom_loop_bounds(cbmcflags: str) -> list[str]:
    bounds: list[str] = []
    tokens = shlex.split(cbmcflags)
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        spec: str | None = None
        if token == "--unwindset" and idx + 1 < len(tokens):
            spec = tokens[idx + 1]
            idx += 2
        elif token.startswith("--unwindset="):
            spec = token.split("=", 1)[1]
            idx += 1
        else:
            idx += 1
            continue

        for item in spec.split(","):
            stripped = item.strip()
            if stripped and stripped not in bounds:
                bounds.append(stripped)
    return bounds


def count_symbols(viewer_symbol_json: dict[str, Any] | None) -> int | None:
    if not viewer_symbol_json:
        return None
    symbols = viewer_symbol_json.get("viewer-symbol", {}).get("symbols", {})
    return len(symbols) if isinstance(symbols, dict) else None


def property_results(
    viewer_result_json: dict[str, Any] | None,
) -> tuple[set[str] | None, set[str] | None]:
    if not viewer_result_json:
        return None, None
    results = viewer_result_json.get("viewer-result", {}).get("results", {})
    true_ids = results.get("true", [])
    false_ids = results.get("false", [])
    return (
        set(true_ids) if isinstance(true_ids, list) else None,
        set(false_ids) if isinstance(false_ids, list) else None,
    )


def is_memory_safety_property(property_id: str, info: dict[str, Any]) -> bool:
    prop_class = str(info.get("class", "")).strip().lower()
    if prop_class in MEMORY_SAFETY_CLASSES:
        return True
    description = str(info.get("description", "")).strip().lower()
    return "pointer" in description or "bounds" in description


def is_unwind_property(property_id: str, info: dict[str, Any]) -> bool:
    if ".unwind." in property_id:
        return True
    prop_class = str(info.get("class", "")).strip().lower()
    description = str(info.get("description", "")).strip().lower()
    return prop_class == "unwinding assertion" or "unwinding assertion" in description


def is_no_body_property(property_id: str, _info: dict[str, Any]) -> bool:
    return ".no-body." in property_id


def count_properties(
    viewer_property_json: dict[str, Any] | None,
    viewer_result_json: dict[str, Any] | None,
    predicate: Any,
) -> tuple[int | None, int | None, int | None]:
    properties = {}
    if viewer_property_json:
        properties = viewer_property_json.get("viewer-property", {}).get("properties", {})
        if not isinstance(properties, dict):
            properties = {}

    true_ids, false_ids = property_results(viewer_result_json)

    instrumented_ids = [
        property_id
        for property_id, info in properties.items()
        if isinstance(info, dict) and predicate(property_id, info)
    ]
    if not instrumented_ids:
        if not properties:
            return None, None, None
        return 0, 0 if true_ids is not None else None, 0 if false_ids is not None else None

    verified = None if true_ids is None else sum(1 for property_id in instrumented_ids if property_id in true_ids)
    failed = None if false_ids is None else sum(1 for property_id in instrumented_ids if property_id in false_ids)
    return len(instrumented_ids), verified, failed


def relative_path(path: Path) -> str:
    try:
        return path.relative_to(WORKSPACE_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def analyze_proof(
    source_file: str,
    target_function: str,
    proof_dir: Path,
    repo_root: Path,
    ae: Any,
) -> dict[str, Any]:
    ae.REPO_ROOT = repo_root.resolve()

    suite_root = detect_suite_root(proof_dir)
    origin = detect_origin(proof_dir)
    variant = derive_proof_variant(proof_dir, suite_root)

    assignments = parse_all_make_assignments(proof_dir, ae)
    try:
        metadata = ae.read_make_metadata(proof_dir)
        entry = metadata.entry
        cbmcflags = metadata.cbmcflags
        proof_root = metadata.proof_root
    except Exception:
        entry = target_function
        cbmcflags = assignments.get("H_CBMCFLAGS", "")
        proof_root = proof_dir.parent.resolve()

    harness_file = discover_harness_file(proof_dir, entry)
    proof_prefixes = ae.proof_root_prefixes(proof_root, suite_root)
    report_json_dir = resolve_report_json_dir(proof_dir, ae)
    report_html_index = resolve_report_html_index(proof_dir, ae)
    cbmc_xml_path = resolve_cbmc_xml_path(proof_dir, ae)
    viewer_result_json = load_json(report_json_dir / "viewer-result.json" if report_json_dir else None)
    viewer_property_json = load_json(report_json_dir / "viewer-property.json" if report_json_dir else None)
    viewer_symbol_json = load_json(report_json_dir / "viewer-symbol.json" if report_json_dir else None)
    coverage_json = load_json(report_json_dir / "viewer-coverage.json" if report_json_dir else None)
    reachable_json = load_json(report_json_dir / "viewer-reachable.json" if report_json_dir else None)
    cbmc_root = ae.parse_xml(cbmc_xml_path)

    verification_completes = ae.verification_completed(
        viewer_result_json,
        cbmc_root,
        report_json_dir,
        report_html_index,
    )
    verification_time = ae.parse_verification_time_with_fallback(viewer_result_json, cbmc_root)

    files_in_scope, functions_in_scope = ae.aggregate_scope_metrics(
        reachable_json,
        proof_prefixes,
    )

    custom_loop_bounds = parse_custom_loop_bounds(cbmcflags)
    precondition_count = count_harness_preconditions(harness_file, ae)

    (
        program_reachable_loc,
        program_covered_loc,
        program_coverage_pct,
        function_model_count,
        function_model_avg_loc,
        proof_size_loc_from_coverage,
        harness_size_loc_from_coverage,
    ) = ae.aggregate_coverage_metrics(coverage_json, proof_prefixes)
    overall_reachable_loc, overall_covered_loc, overall_coverage_pct = ae.aggregate_overall_coverage(coverage_json)
    target_function_reachable_loc, target_function_covered_loc, target_function_coverage_pct = ae.aggregate_target_function_coverage(
        coverage_json,
        proof_prefixes,
        target_function,
    )

    harness_size_loc = harness_size_loc_from_coverage
    proof_size_loc = proof_size_loc_from_coverage

    symbols_in_unit_proof = count_symbols(viewer_symbol_json)
    harness_external_proof_calls = count_harness_external_proof_calls(
        proof_dir,
        harness_file,
        reachable_json,
        entry,
        repo_root,
        ae,
    )

    (
        memory_safety_instrumented,
        _memory_safety_by_class,
        memory_safety_verified,
        _verified_memory_safety_by_class,
        _memory_safety_verified_pct,
        _memory_safety_property_lines,
        _verified_memory_safety_property_lines,
    ) = ae.memory_safety_property_metrics(
        viewer_result_json,
        viewer_property_json,
    )
    memory_safety_violated = None
    if memory_safety_instrumented is not None and viewer_result_json and viewer_property_json:
        false_properties = (
            viewer_result_json.get("viewer-result", {})
            .get("results", {})
            .get("false", [])
        )
        memory_safety_properties = ae.memory_safety_properties(viewer_property_json)
        if isinstance(false_properties, list):
            false_property_ids = {
                property_id
                for property_id in false_properties
                if isinstance(property_id, str)
            }
            memory_safety_violated = sum(
                1
                for property_id in memory_safety_properties
                if property_id in false_property_ids
            )
    unwind_instrumented, _unwind_verified, unwind_failed = count_properties(
        viewer_property_json,
        viewer_result_json,
        is_unwind_property,
    )
    no_body_instrumented, _no_body_verified, no_body_failed = count_properties(
        viewer_property_json,
        viewer_result_json,
        is_no_body_property,
    )

    return {
        "Source File": source_file,
        "Target Function": target_function,
        "Proof Origin": origin,
        "Proof Directory": relative_path(proof_dir),
        "Proof Relative Path": relative_path(proof_dir.relative_to(suite_root) if proof_dir.is_relative_to(suite_root) else proof_dir),
        "Proof Entry": entry,
        "Proof Variant": variant,
        "Files In Scope": none_to_blank(files_in_scope),
        "Functions In Scope": none_to_blank(functions_in_scope),
        "Custom Loop Bounds": ";".join(custom_loop_bounds),
        "Custom Loop Bound Count": none_to_blank(len(custom_loop_bounds)),
        "Precondition Count": none_to_blank(precondition_count),
        "Function Model Count": none_to_blank(function_model_count),
        "Function Model Avg LOC": format_float(function_model_avg_loc),
        "Harness Size LOC": none_to_blank(harness_size_loc),
        "Proof Size LOC": none_to_blank(proof_size_loc),
        "Verification Completes": none_to_blank(verification_completes),
        "Verification Time": format_float(verification_time),
        "Overall Reachable LOC": none_to_blank(overall_reachable_loc),
        "Overall Covered LOC": none_to_blank(overall_covered_loc),
        "Overall Coverage %": format_float(overall_coverage_pct),
        "Program Reachable LOC": none_to_blank(program_reachable_loc),
        "Program Covered LOC": none_to_blank(program_covered_loc),
        "Program Coverage %": format_float(program_coverage_pct),
        "Target Function Reachable LOC": none_to_blank(target_function_reachable_loc),
        "Target Function Covered LOC": none_to_blank(target_function_covered_loc),
        "Target Function Coverage %": format_float(target_function_coverage_pct),
        "Symbols In Unit Proof": none_to_blank(symbols_in_unit_proof),
        "Harness External Proof Calls": none_to_blank(harness_external_proof_calls),
        "Memory Safety Properties Instrumented": none_to_blank(memory_safety_instrumented),
        "Memory Safety Properties Verified": none_to_blank(memory_safety_verified),
        "Memory Safety Properties Violated": none_to_blank(memory_safety_violated),
        "Loop Unwinding Assertions Instrumented": none_to_blank(unwind_instrumented),
        "Loop Unwinding Assertions Failed": none_to_blank(unwind_failed),
        "No Body Properties Instrumented": none_to_blank(no_body_instrumented),
        "No Body Properties Failed": none_to_blank(no_body_failed),
    }


def main() -> int:
    args = parse_args()
    ae = load_analyze_experiment_module()

    mapping_rows = read_mapping_rows(args.mapping_csv.resolve())
    output_rows: list[dict[str, Any]] = []

    for mapping_row in mapping_rows:
        source_file = str(mapping_row.get("source_file", "")).strip()
        target_function = str(mapping_row.get("function_name", "")).strip()
        if not source_file or not target_function:
            continue

        proof_cells = [
            mapping_row.get("freertos_proof_dirs", ""),
            mapping_row.get("autoup_proof_dir", ""),
        ]
        for proof_cell in proof_cells:
            for raw_path in split_proof_dirs(str(proof_cell or "")):
                proof_dir = resolve_workspace_path(raw_path)
                if not proof_dir.is_dir():
                    continue
                output_rows.append(
                    analyze_proof(
                        source_file,
                        target_function,
                        proof_dir,
                        args.repo_root.resolve(),
                        ae,
                    )
                )

    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    with args.output.resolve().open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
