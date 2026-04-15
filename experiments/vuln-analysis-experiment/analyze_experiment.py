#!/usr/bin/env python3
"""Run CBMC experiment proofs and summarize methodology-aligned RQ1 metrics."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AUTOUP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = AUTOUP_ROOT.parent
MODEL_PRICING_PATH = AUTOUP_ROOT / "model_pricing.json"
BACKUP_BUILD_GLOB = "build_backup.*"
NA_VALUE = "N/A"

COLUMN_SPECS = [
    ("software", "Software"),
    ("config", "Config"),
    ("tag", "Tag"),
    ("source_file", "Source File"),
    ("target_function", "Target Function"),
    ("proof_relpath", "Proof Relpath"),
    ("proof_found", "Proof Found"),
    ("compile_succeeded", "Compile Succeeded"),
    ("links_target", "Links Target"),
    ("semantic_valid", "Semantic Valid"),
    ("verification_completes", "Verification Completes"),
    ("verification_time", "Verification Time"),
    ("verification_succeeds", "Verification Succeeds"),
    ("target_function_reachable_line_count", "Target Function Reachable Line Count"),
    ("target_function_covered_line_count", "Target Function Covered Line Count"),
    ("target_function_line_coverage_pct", "Target Function Line Coverage %"),
    ("program_reachable_line_count", "Program Reachable Line Count"),
    ("program_covered_line_count", "Program Covered Line Count"),
    ("program_line_coverage_pct", "Program Line Coverage %"),
    ("overall_reachable_line_count", "Overall Reachable Line Count"),
    ("overall_covered_line_count", "Overall Covered Line Count"),
    ("overall_line_coverage_pct", "Overall Line Coverage %"),
    ("property_violations", "Property Violations"),
    ("precondition_violations", "Precondition Violations"),
    ("generation_time", "Generation Time"),
    ("api_cost", "API Cost"),
    ("harness_size_loc", "Harness Size LOC"),
    ("source_files_in_scope", "Source Files In Scope"),
    ("functions_in_scope", "Functions In Scope"),
    ("loop_unwindset_count", "Loop Unwindset Count"),
    ("loop_unwind_min", "Loop Unwind Min"),
    ("loop_unwind_max", "Loop Unwind Max"),
    ("model_used_variable_count", "Model Used Variable Count"),
    ("assumption_variable_count", "Assumption Variable Count"),
    ("function_model_count", "Function Model Count"),
    ("function_model_avg_loc", "Function Model Avg LOC"),
]
CSV_COLUMNS = [label for _, label in COLUMN_SPECS]
COLUMN_ORDER = [column_id for column_id, _ in COLUMN_SPECS]
COLUMN_LABELS = dict(COLUMN_SPECS)

CONTROL_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof"}
DECL_QUALIFIERS = {
    "auto",
    "const",
    "enum",
    "extern",
    "inline",
    "long",
    "register",
    "restrict",
    "short",
    "signed",
    "static",
    "struct",
    "typedef",
    "union",
    "unsigned",
    "volatile",
    "_Bool",
    "_Complex",
    "_Imaginary",
}
DECL_BASE_TYPES = {
    "bool",
    "char",
    "double",
    "float",
    "int",
    "size_t",
    "ssize_t",
    "uint8_t",
    "uint16_t",
    "uint32_t",
    "uint64_t",
    "uintptr_t",
    "int8_t",
    "int16_t",
    "int32_t",
    "int64_t",
    "void",
    "atomic_t",
    "k_timeout_t",
}
CALLISH_PREFIXES = (
    "__CPROVER_assume",
    "__CPROVER_precondition",
    "__CPROVER_assert",
    "__CPROVER_HIDE",
    "return",
)
PATH_RE = re.compile(r"\b[A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*)*")
RUNTIME_MESSAGE_RE = re.compile(
    r"\bRuntime\s+(?P<name>[^:]+):\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(?:s|sec(?:onds?)?)?\b",
    re.IGNORECASE,
)


@dataclass
class MakeMetadata:
    entry: str
    cbmcflags: str
    proof_root: Path


@dataclass
class RunResult:
    clean_returncode: int | None
    make_returncode: int | None
    timed_out: bool
    wall_time_s: float | None
    make_ran: bool = False


@dataclass
class FunctionInfo:
    name: str
    params: list[str]
    body: str


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for a single experiment analysis run."""
    parser = argparse.ArgumentParser(
        description="Analyze CBMC experiment outputs and emit an RQ1-aligned CSV."
    )
    parser.add_argument("experiment_dir", type=Path, help="Path to experiment dir, e.g. zephyr/cbmc/exp-0413")
    parser.add_argument(
        "experiment_csv",
        type=Path,
        help="CSV containing source_file,function_name targets",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Configuration label to emit in the CSV Config column",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination CSV path. Defaults to <experiment_dir>/assessment.csv",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout in seconds for `make -j3` per proof (default: 1800)",
    )
    parser.add_argument(
        "--experiment-output-dir",
        type=Path,
        help="Optional AutoUP experiment output directory containing metrics-*.jsonl files",
    )
    parser.add_argument(
        "--force-make",
        action="store_true",
        dest="force_make",
        help="Run `make clean` and `make -j3` even when the proof build directory exists",
    )
    return parser.parse_args()


def run_command(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its text output."""
    return subprocess.run(
        cmd,
        cwd=AUTOUP_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_quiet_command(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[None]:
    """Run a command with stdout/stderr discarded and kill the full process group on timeout."""
    proc = subprocess.Popen(
        cmd,
        cwd=AUTOUP_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        raise exc
    return subprocess.CompletedProcess(cmd, returncode)


def parse_make_assignments(make_output: str) -> dict[str, str]:
    """Extract the small subset of make variables the analysis needs."""
    values: dict[str, str] = {}
    for line in make_output.splitlines():
        if " = " not in line:
            continue
        name, value = line.split(" = ", 1)
        name = name.strip()
        if name in {"H_ENTRY", "H_CBMCFLAGS", "MAKE_INCLUDE_PATH"}:
            values[name] = value.strip()
    return values


def read_make_metadata(proof_dir: Path) -> MakeMetadata:
    """Read expanded make variables from a proof directory without executing the build."""
    proc = run_command(["make", "-pn", "-C", str(proof_dir)])
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to read make metadata for {proof_dir}:\n{proc.stderr}")
    assignments = parse_make_assignments(proc.stdout)
    entry = assignments.get("H_ENTRY", "")
    if not entry:
        raise RuntimeError(f"H_ENTRY missing in make metadata for {proof_dir}")
    return MakeMetadata(
        entry=entry,
        cbmcflags=assignments.get("H_CBMCFLAGS", ""),
        proof_root=proof_dir.parent.resolve(),
    )


def run_make(proof_dir: Path, timeout_s: int) -> RunResult:
    """Run `make clean` followed by `make -j3` for one proof directory."""
    clean_proc = run_quiet_command(["make", "-C", str(proof_dir), "clean"])
    if clean_proc.returncode != 0:
        return RunResult(
            make_ran=False,
            clean_returncode=clean_proc.returncode,
            make_returncode=None,
            timed_out=False,
            wall_time_s=None,
        )

    start = time.monotonic()
    try:
        make_proc = run_quiet_command(["make", "-C", str(proof_dir), "-j3"], timeout=timeout_s)
        wall_time_s = time.monotonic() - start
        return RunResult(
            make_ran=True,
            clean_returncode=clean_proc.returncode,
            make_returncode=make_proc.returncode,
            timed_out=False,
            wall_time_s=wall_time_s,
        )
    except subprocess.TimeoutExpired:
        wall_time_s = time.monotonic() - start
        return RunResult(
            make_ran=True,
            clean_returncode=clean_proc.returncode,
            make_returncode=None,
            timed_out=True,
            wall_time_s=wall_time_s,
        )


def ensure_build(proof_dir: Path, timeout_s: int, force_make: bool) -> RunResult:
    """Use an existing proof build unless it is absent or the caller forced a rebuild."""
    if (proof_dir / "build").exists() and not force_make:
        return RunResult(
            make_ran=False,
            clean_returncode=None,
            make_returncode=None,
            timed_out=False,
            wall_time_s=None,
        )
    return run_make(proof_dir, timeout_s)


def load_json(path: Path | None) -> dict | None:
    """Load a JSON file and return `None` if the file is missing or malformed."""
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def load_json_value(path: Path) -> Any:
    """Load a JSON file and raise when it is missing or malformed."""
    return json.loads(path.read_text())


def parse_xml(path: Path | None) -> ET.Element | None:
    """Parse an XML file and return its root element when available."""
    if path is None or not path.exists():
        return None
    try:
        return ET.parse(path).getroot()
    except ET.ParseError:
        return None


def candidate_build_dirs(proof_dir: Path) -> list[Path]:
    """Return the primary build directory followed by available backups."""
    candidates: list[Path] = []
    build_dir = proof_dir / "build"
    if build_dir.exists():
        candidates.append(build_dir)
    candidates.extend(
        sorted(
            (path for path in proof_dir.glob(BACKUP_BUILD_GLOB) if path.is_dir()),
            reverse=True,
        )
    )
    return candidates


def resolve_artifact_path(proof_dir: Path, relative_path: str) -> Path | None:
    """Resolve an artifact from build/ first, then fall back to build backups."""
    for build_dir in candidate_build_dirs(proof_dir):
        candidate = build_dir / relative_path
        if candidate.exists():
            return candidate
    return None


def resolve_report_json_dir(proof_dir: Path) -> Path | None:
    """Locate the cbmc-viewer JSON report directory for a proof."""
    path = resolve_artifact_path(proof_dir, "report/json")
    return path if path and path.is_dir() else None


def resolve_report_html_index(proof_dir: Path) -> Path | None:
    """Locate the cbmc-viewer HTML index for a proof."""
    path = resolve_artifact_path(proof_dir, "report/html/index.html")
    return path if path and path.is_file() else None


def resolve_cbmc_xml_path(proof_dir: Path) -> Path | None:
    """Locate cbmc.xml for a proof."""
    path = resolve_artifact_path(proof_dir, "reports/cbmc.xml")
    return path if path and path.is_file() else None


def resolve_metrics_path(
    experiment_output_dir: Path | None,
    target_file: str,
    target_function: str,
) -> Path | None:
    """Locate the metrics JSONL for a target proof using filename-function naming."""
    if experiment_output_dir is None:
        return None
    file_stem = Path(target_file).stem
    metrics_path = experiment_output_dir / f"metrics-{file_stem}-{target_function}.jsonl"
    return metrics_path if metrics_path.is_file() else None


def load_metrics_records(
    experiment_output_dir: Path | None,
    target_file: str,
    target_function: str,
) -> list[dict[str, Any]]:
    """Load the matching metrics JSONL file into memory."""
    metrics_path = resolve_metrics_path(experiment_output_dir, target_file, target_function)
    if metrics_path is None:
        return []

    records: list[dict[str, Any]] = []
    for line in metrics_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def parse_verification_time(cbmc_root: ET.Element | None) -> float | None:
    """Sum the individual runtime values reported in cbmc.xml."""
    if cbmc_root is None:
        return None
    return parse_runtime_message_sum(cbmc_root)


def parse_runtime_message_sum(cbmc_root: ET.Element) -> float | None:
    """Sum every `Runtime ...` value reported by CBMC."""
    runtime_sum = 0.0
    saw_any_runtime = False

    for text in cbmc_root.itertext():
        match = RUNTIME_MESSAGE_RE.search(text)
        if match is None:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue

        saw_any_runtime = True
        runtime_sum += value

    if saw_any_runtime:
        return runtime_sum
    return None


def parse_unwind_metrics(cbmcflags: str) -> tuple[int, int | None, int | None]:
    """Extract loop-specific unwind counts and overall min/max unwind limits from CBMC flags."""
    tokens = shlex.split(cbmcflags)
    unwind_values: list[int] = []
    unwindset_count = 0
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        value: str | None = None
        if token == "--unwind" and idx + 1 < len(tokens):
            value = tokens[idx + 1]
            idx += 2
        elif token.startswith("--unwind="):
            value = token.split("=", 1)[1]
            idx += 1
        elif token == "--unwindset" and idx + 1 < len(tokens):
            spec = tokens[idx + 1]
            for item in spec.split(","):
                if ":" not in item:
                    continue
                _, raw_limit = item.rsplit(":", 1)
                try:
                    limit = int(raw_limit)
                except ValueError:
                    continue
                unwind_values.append(limit)
                unwindset_count += 1
            idx += 2
            continue
        elif token.startswith("--unwindset="):
            spec = token.split("=", 1)[1]
            for item in spec.split(","):
                if ":" not in item:
                    continue
                _, raw_limit = item.rsplit(":", 1)
                try:
                    limit = int(raw_limit)
                except ValueError:
                    continue
                unwind_values.append(limit)
                unwindset_count += 1
            idx += 1
            continue
        else:
            idx += 1
            continue

        try:
            unwind_values.append(int(value))
        except (TypeError, ValueError):
            pass

    if not unwind_values:
        return unwindset_count, None, None
    return unwindset_count, min(unwind_values), max(unwind_values)


def strip_c_comments_and_strings(text: str) -> str:
    """Replace comments and string contents with whitespace while preserving structure."""
    result: list[str] = []
    idx = 0
    state = "code"
    while idx < len(text):
        ch = text[idx]
        nxt = text[idx + 1] if idx + 1 < len(text) else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                result.append("  ")
                idx += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                result.append("  ")
                idx += 2
                continue
            if ch == '"':
                state = "string"
                result.append('"')
                idx += 1
                continue
            if ch == "'":
                state = "char"
                result.append("'")
                idx += 1
                continue
            result.append(ch)
            idx += 1
            continue
        if state == "line_comment":
            if ch == "\n":
                state = "code"
                result.append("\n")
            else:
                result.append(" ")
            idx += 1
            continue
        if state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                result.append("  ")
                idx += 2
            else:
                result.append("\n" if ch == "\n" else " ")
                idx += 1
            continue
        if state == "string":
            if ch == "\\" and nxt:
                result.append("  ")
                idx += 2
                continue
            result.append('"' if ch == '"' else " ")
            if ch == '"':
                state = "code"
            idx += 1
            continue
        if state == "char":
            if ch == "\\" and nxt:
                result.append("  ")
                idx += 2
                continue
            result.append("'" if ch == "'" else " ")
            if ch == "'":
                state = "code"
            idx += 1
    return "".join(result)


def find_matching_brace(text: str, open_index: int) -> int:
    """Return the index of the closing brace paired with `open_index`."""
    depth = 0
    for idx in range(open_index, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                return idx
    raise ValueError("Unmatched brace in C source")


def split_top_level(text: str, delimiter: str) -> list[str]:
    """Split text on a delimiter while ignoring nested parentheses/brackets/braces."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == delimiter and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def parse_param_names(params: str) -> list[str]:
    """Extract parameter identifiers from a C function signature."""
    params = params.strip()
    if not params or params == "void":
        return []
    names: list[str] = []
    for raw_part in split_top_level(params, ","):
        part = raw_part.strip()
        if not part or part == "...":
            continue
        identifiers = re.findall(r"[A-Za-z_]\w*", part)
        if not identifiers:
            continue
        candidate = identifiers[-1]
        if candidate in DECL_QUALIFIERS or candidate in DECL_BASE_TYPES:
            continue
        names.append(candidate)
    return names


def parse_functions(source: str) -> dict[str, FunctionInfo]:
    """Locate top-level C function definitions and capture their parameter lists and bodies."""
    sanitized = strip_c_comments_and_strings(source)
    functions: dict[str, FunctionInfo] = {}
    pattern = re.compile(
        r"(?P<prefix>(?:^|\n)[^\n{};#]*?)\b(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^;{}()]*(?:\([^)]*\)[^;{}()]*)*)\)\s*\{",
        re.MULTILINE,
    )
    for match in pattern.finditer(sanitized):
        name = match.group("name")
        prefix = match.group("prefix")
        if name in CONTROL_KEYWORDS:
            continue
        if "=" in prefix:
            continue
        brace_index = match.end() - 1
        end_index = find_matching_brace(sanitized, brace_index)
        body = sanitized[brace_index + 1 : end_index]
        functions[name] = FunctionInfo(
            name=name,
            params=parse_param_names(match.group("params")),
            body=body,
        )
    return functions


def split_path_root(path: str) -> tuple[str, str]:
    """Split an access path like `a->b.c` into root `a` and suffix `->b.c`."""
    match = re.match(r"([A-Za-z_]\w*)(.*)", path.strip())
    if not match:
        return path.strip(), ""
    return match.group(1), match.group(2)


def is_simple_declared_variable(path: str, declared: set[str]) -> bool:
    """Check whether a path names a declared local directly rather than a field access."""
    root, suffix = split_path_root(path)
    return not suffix and root in declared


def strip_leading_casts(expr: str) -> str:
    """Remove leading C casts and pointer/address operators from an expression."""
    value = expr.strip()
    while value.startswith("("):
        depth = 0
        matched = False
        for idx, ch in enumerate(value):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    remainder = value[idx + 1 :].lstrip()
                    if remainder:
                        value = remainder
                        matched = True
                    break
        if not matched:
            break
    return value.lstrip("&* ").strip()


def extract_access_paths(expr: str) -> set[str]:
    """Extract dotted or arrow-based access paths from a statement or expression."""
    paths: set[str] = set()
    for match in PATH_RE.finditer(expr):
        path = match.group(0)
        start = match.start()
        prev = expr[start - 1] if start > 0 else ""
        if prev in {'"', "'", "#"}:
            continue
        if path.startswith("__CPROVER_"):
            continue
        if path in CONTROL_KEYWORDS or path in DECL_QUALIFIERS or path in DECL_BASE_TYPES:
            continue
        paths.add(path)
    return paths


def extract_primary_path(expr: str) -> str | None:
    """Return the single access path in an expression when it is unambiguous."""
    cleaned = strip_leading_casts(expr)
    matches = [path for path in extract_access_paths(cleaned) if path not in {"NULL"}]
    if len(matches) != 1:
        return None
    return matches[0]


def extract_lvalue_path(expr: str) -> str | None:
    """Return the path most likely to represent the assigned lvalue in a statement prefix."""
    matches = [path for path in PATH_RE.findall(expr) if path not in CONTROL_KEYWORDS]
    if not matches:
        return None
    candidate = matches[-1]
    if candidate in DECL_QUALIFIERS or candidate in DECL_BASE_TYPES:
        return None
    return candidate


def expand_path(path: str, substitutions: dict[str, set[str]], max_depth: int = 8) -> set[str]:
    """Resolve local aliases to canonical program-side access paths."""
    expanded = {path.strip()}
    for _ in range(max_depth):
        changed = False
        next_expanded: set[str] = set()
        for item in expanded:
            root, suffix = split_path_root(item)
            targets = substitutions.get(root)
            if not targets:
                next_expanded.add(item)
                continue
            changed = True
            for target in targets:
                next_expanded.add(f"{target}{suffix}")
        expanded = next_expanded
        if not changed:
            break
    return expanded


def split_statements(body: str) -> list[str]:
    """Split a function body into semicolon-terminated statements at top level."""
    statements: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in body:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        current.append(ch)
        if ch == ";" and depth == 0:
            statements.append("".join(current).strip())
            current = []
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def split_assignment(statement: str) -> tuple[str, str] | None:
    """Split a statement on its assignment operator while ignoring comparisons."""
    for idx, ch in enumerate(statement):
        if ch != "=":
            continue
        prev = statement[idx - 1] if idx > 0 else ""
        nxt = statement[idx + 1] if idx + 1 < len(statement) else ""
        if prev in "<>!=" or nxt == "=":
            continue
        return statement[:idx], statement[idx + 1 :]
    return None


def extract_local_names(function: FunctionInfo) -> set[str]:
    """Collect local variable names declared inside a function."""
    local_names = set(function.params)
    for statement in split_statements(function.body):
        stmt = statement.strip().lstrip("{} ").strip()
        if not stmt:
            continue
        if stmt.startswith(("if ", "if(", "else", "return", "__CPROVER_")):
            continue
        assignment = split_assignment(stmt)
        target = assignment[0].strip() if assignment else stmt.rstrip(";").strip()
        candidate = extract_lvalue_path(target)
        if not candidate:
            continue
        root, suffix = split_path_root(candidate)
        if suffix:
            continue
        if root in CONTROL_KEYWORDS or root in DECL_QUALIFIERS or root in DECL_BASE_TYPES:
            continue
        local_names.add(root)
    return local_names


def build_substitutions(function: FunctionInfo) -> dict[str, set[str]]:
    """Infer local-to-program alias mappings from harness assignments."""
    declared = extract_local_names(function)
    substitutions: dict[str, set[str]] = {}
    statements = split_statements(function.body)

    for _ in range(8):
        changed = False
        for statement in statements:
            stmt = statement.strip()
            if not stmt or stmt.startswith("return"):
                continue
            assignment = split_assignment(stmt)
            if assignment is None:
                continue
            lhs, rhs = assignment
            lhs = lhs.strip()
            rhs = rhs.rsplit(";", 1)[0].strip()

            lhs_path = extract_lvalue_path(lhs)
            rhs_path = extract_primary_path(rhs)
            if lhs_path:
                if is_simple_declared_variable(lhs_path, declared):
                    if rhs_path:
                        rhs_expanded = expand_path(rhs_path, substitutions)
                        before = set(substitutions.get(lhs_path, set()))
                        substitutions.setdefault(lhs_path, set()).update(rhs_expanded)
                        if substitutions[lhs_path] != before:
                            changed = True
                elif rhs_path:
                    lhs_expanded = expand_path(lhs_path, substitutions)
                    rhs_root, _ = split_path_root(rhs_path)
                    if rhs_root in declared:
                        before = set(substitutions.get(rhs_root, set()))
                        substitutions.setdefault(rhs_root, set()).update(lhs_expanded)
                        if substitutions[rhs_root] != before:
                            changed = True
                continue

        if not changed:
            break

    return substitutions


def extract_target_call_roots(function: FunctionInfo, entry: str, substitutions: dict[str, set[str]]) -> set[str]:
    """Find the program roots passed into the target function call from the harness."""
    roots: set[str] = set()
    call_pattern = re.compile(rf"\b{re.escape(entry)}\s*\((.*)\)\s*;")
    for statement in split_statements(function.body):
        match = call_pattern.search(statement)
        if not match:
            continue
        for arg in split_top_level(match.group(1), ","):
            path = extract_primary_path(arg)
            if not path:
                continue
            for expanded in expand_path(path, substitutions):
                root, _ = split_path_root(expanded)
                roots.add(root)
    return roots


def extract_global_roots(function: FunctionInfo, local_names: set[str], substitutions: dict[str, set[str]]) -> set[str]:
    """Find non-local storage locations initialized directly by the harness."""
    roots: set[str] = set()
    for statement in split_statements(function.body):
        assignment = split_assignment(statement)
        if assignment is None:
            continue
        lhs, _ = assignment
        lhs_path = extract_lvalue_path(lhs)
        if not lhs_path:
            continue
        for expanded in expand_path(lhs_path, substitutions):
            root, _ = split_path_root(expanded)
            if root not in local_names:
                roots.add(root)
    return roots


def extract_harness_program_variables(function: FunctionInfo, entry: str) -> tuple[set[str], set[str]]:
    """Count canonical program variables initialized or constrained in the harness."""
    local_names = extract_local_names(function)
    substitutions = build_substitutions(function)
    program_roots = extract_target_call_roots(function, entry, substitutions)
    program_roots.update(extract_global_roots(function, local_names, substitutions))

    model_used: set[str] = set()
    assumption_used: set[str] = set()

    for statement in split_statements(function.body):
        stmt = statement.strip()
        if not stmt:
            continue

        assignment = split_assignment(stmt)
        if assignment is not None and not stmt.startswith("return"):
            lhs, _ = assignment
            lhs_path = extract_primary_path(lhs)
            if lhs_path:
                for expanded in expand_path(lhs_path, substitutions):
                    root, _ = split_path_root(expanded)
                    if root in program_roots:
                        model_used.add(expanded)

        if "__CPROVER_assume" in stmt or "__CPROVER_precondition" in stmt:
            for path in extract_access_paths(stmt):
                for expanded in expand_path(path, substitutions):
                    root, _ = split_path_root(expanded)
                    if root in program_roots:
                        model_used.add(expanded)
                        assumption_used.add(expanded)

    return model_used, assumption_used


def norm_viewer_path(path: str) -> str:
    """Normalize a path string to the slash-based style used by cbmc-viewer JSON."""
    return path.replace("\\", "/").lstrip("./")


def path_matches_suffix(candidate: str, target_suffix: str) -> bool:
    """Check whether a viewer path matches a target path by suffix."""
    normalized_candidate = norm_viewer_path(candidate)
    normalized_suffix = norm_viewer_path(target_suffix)
    return (
        normalized_candidate == normalized_suffix
        or normalized_candidate.endswith(f"/{normalized_suffix}")
    )


def proof_root_prefix(proof_root: Path, project_root: Path) -> str:
    """Convert a proof root on disk into the corresponding viewer path prefix."""
    try:
        return norm_viewer_path(str(proof_root.relative_to(project_root)))
    except ValueError:
        return norm_viewer_path(str(proof_root))


def is_proof_side_file(viewer_path: str, proof_prefix: str) -> bool:
    """Check whether a viewer path belongs to the proof-side CBMC directory."""
    normalized = norm_viewer_path(viewer_path)
    return normalized == proof_prefix or normalized.startswith(f"{proof_prefix}/")


def is_generated_file(viewer_path: str) -> bool:
    """Check whether a viewer path refers to generated build output."""
    normalized = norm_viewer_path(viewer_path)
    return normalized.startswith("zephyr/build/") or "/include/generated/" in normalized


def is_c_source_file(viewer_path: str) -> bool:
    """Check whether a viewer path names a `.c` file."""
    return norm_viewer_path(viewer_path).endswith(".c")


def is_excluded_model_file(viewer_path: str) -> bool:
    """Exclude shared generic stub files from function-model metrics."""
    return norm_viewer_path(viewer_path).endswith("/general-stubs.c")


def parse_cbmc_xml_status(cbmc_root: ET.Element | None) -> str:
    """Return the overall prover status reported directly in cbmc.xml."""
    if cbmc_root is None:
        return ""
    status = cbmc_root.findtext("cprover-status")
    if status:
        return status.strip().upper()
    for text in cbmc_root.itertext():
        normalized_text = text.strip().upper()
        if "VERIFICATION SUCCESSFUL" in normalized_text:
            return "SUCCESS"
        if "VERIFICATION FAILED" in normalized_text:
            return "FAILED"
    return ""


def report_generated(report_json_dir: Path | None, report_html_index: Path | None) -> bool:
    """Check whether cbmc-viewer generated both HTML and JSON report artifacts."""
    if report_json_dir is None or report_html_index is None:
        return False
    return report_html_index.is_file() and any(report_json_dir.glob("*.json"))


def verification_completed(
    cbmc_root: ET.Element | None,
    report_json_dir: Path | None,
    report_html_index: Path | None,
) -> bool:
    """Check whether verification reached a conclusive status and report generation completed."""
    cbmc_status = parse_cbmc_xml_status(cbmc_root)
    return cbmc_status in {"SUCCESS", "FAILURE", "FAILED"} and report_generated(
        report_json_dir,
        report_html_index,
    )


def is_excluded_error_property(property_id: str, property_info: dict | None) -> bool:
    """Filter out failure kinds the experiment should not count as reported errors."""
    if ".no-body." in property_id:
        return True
    if ".unwind." in property_id:
        return True
    if not property_info:
        return False
    prop_class = str(property_info.get("class", "")).strip().lower()
    description = str(property_info.get("description", "")).strip().lower()
    return prop_class == "unwinding assertion" or "unwinding assertion" in description


def count_reported_error_sites(
    viewer_result_json: dict | None,
    viewer_property_json: dict | None,
) -> int | None:
    """Count distinct source sites with counted failing properties."""
    if not viewer_result_json:
        return None

    false_properties = (
        viewer_result_json.get("viewer-result", {})
        .get("results", {})
        .get("false", [])
    )
    properties = {}
    if viewer_property_json:
        properties = viewer_property_json.get("viewer-property", {}).get("properties", {})

    sites: set[tuple[str, str, str]] = set()
    fallback_count = 0
    for property_id in false_properties:
        property_info = properties.get(property_id)
        if is_excluded_error_property(property_id, property_info):
            continue
        if not property_info:
            fallback_count += 1
            continue
        location = property_info.get("location")
        if location is None:
            fallback_count += 1
            continue
        file_name = norm_viewer_path(str(location.get("file", "")))
        function = str(location.get("function", ""))
        line = str(location.get("line", ""))
        sites.add((file_name, function, line))
    return len(sites) + fallback_count


def verification_succeeds(
    viewer_result_json: dict | None,
    viewer_property_json: dict | None,
) -> bool | None:
    """Check whether all remaining reported failures are excluded categories."""
    if not viewer_result_json:
        return None
    false_properties = (
        viewer_result_json.get("viewer-result", {})
        .get("results", {})
        .get("false", [])
    )
    properties = {}
    if viewer_property_json:
        properties = viewer_property_json.get("viewer-property", {}).get("properties", {})

    for property_id in false_properties:
        if not is_excluded_error_property(property_id, properties.get(property_id)):
            return False
    return True


def aggregate_scope_metrics(
    reachable_json: dict | None,
    proof_prefix: str,
) -> tuple[int | None, int | None]:
    """Count in-scope source files and reachable functions for the project under test."""
    if not reachable_json:
        return None, None
    reachable = reachable_json.get("viewer-reachable", {}).get("reachable", {})
    source_files = 0
    functions = 0
    for file_name, file_functions in reachable.items():
        if is_proof_side_file(file_name, proof_prefix) or is_generated_file(file_name):
            continue
        if is_c_source_file(file_name):
            source_files += 1
        functions += len(file_functions)
    return source_files, functions


def aggregate_coverage_metrics(
    coverage_json: dict | None,
    proof_prefix: str,
) -> tuple[int | None, int | None, float | None, int | None, float | None, int | None]:
    """Compute filtered program coverage and proof-side model-size metrics."""
    if not coverage_json:
        return None, None, None, None, None, None

    function_coverage = coverage_json.get("viewer-coverage", {}).get("function_coverage", {})
    reachable_line_count = 0
    covered_line_count = 0
    model_line_totals: list[int] = []
    total_harness_loc = 0
    function_model_count = 0

    for file_name, functions in function_coverage.items():
        proof_side = is_proof_side_file(file_name, proof_prefix)
        generated = is_generated_file(file_name)
        for function_name, metrics in functions.items():
            total = int(metrics.get("total", 0))
            hit = int(metrics.get("hit", 0))
            if proof_side:
                total_harness_loc += total
                if function_name != "harness" and not is_excluded_model_file(file_name):
                    function_model_count += 1
                    model_line_totals.append(total)
            elif not generated:
                reachable_line_count += total
                covered_line_count += hit

    line_coverage_pct = None
    if reachable_line_count:
        line_coverage_pct = (covered_line_count / reachable_line_count) * 100.0

    function_model_avg_loc = None
    if model_line_totals:
        function_model_avg_loc = sum(model_line_totals) / len(model_line_totals)

    return (
        reachable_line_count,
        covered_line_count,
        line_coverage_pct,
        function_model_count,
        function_model_avg_loc,
        total_harness_loc,
    )


def aggregate_overall_coverage(
    coverage_json: dict | None,
) -> tuple[int | None, int | None, float | None]:
    """Compute raw overall coverage from viewer data without excluding any files."""
    if not coverage_json:
        return None, None, None

    function_coverage = coverage_json.get("viewer-coverage", {}).get("function_coverage", {})
    reachable_line_count = 0
    covered_line_count = 0

    for functions in function_coverage.values():
        for metrics in functions.values():
            reachable_line_count += int(metrics.get("total", 0))
            covered_line_count += int(metrics.get("hit", 0))

    if reachable_line_count == 0:
        return None, None, None

    return (
        reachable_line_count,
        covered_line_count,
        (covered_line_count / reachable_line_count) * 100.0,
    )


def aggregate_target_function_coverage(
    coverage_json: dict | None,
    proof_prefix: str,
    target_function: str,
) -> tuple[int | None, int | None, float | None]:
    """Compute coverage for the target function itself."""
    if not coverage_json:
        return None, None, None

    function_coverage = coverage_json.get("viewer-coverage", {}).get("function_coverage", {})
    reachable_line_count = 0
    covered_line_count = 0

    for file_name, functions in function_coverage.items():
        if is_proof_side_file(file_name, proof_prefix) or is_generated_file(file_name):
            continue
        metrics = functions.get(target_function)
        if not metrics:
            continue
        reachable_line_count += int(metrics.get("total", 0))
        covered_line_count += int(metrics.get("hit", 0))

    if reachable_line_count == 0:
        return None, None, None

    return (
        reachable_line_count,
        covered_line_count,
        (covered_line_count / reachable_line_count) * 100.0,
    )


def collect_variable_metrics(proof_dir: Path, entry: str) -> tuple[int | None, int | None]:
    """Collect harness-level modeled-variable metrics from the main harness file."""
    harness_file = proof_dir / f"{entry}_harness.c"
    if not harness_file.exists():
        return None, None

    functions = parse_functions(harness_file.read_text())
    harness = functions.get("harness")
    if harness is None:
        return None, None

    model_vars, assumption_vars = extract_harness_program_variables(harness, entry)
    return len(model_vars), len(assumption_vars)


def read_experiment_targets(csv_path: Path) -> list[tuple[str, str]]:
    """Load source_file,function_name targets from the experiment CSV."""
    with csv_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"Target CSV is empty: {csv_path}")
        missing = {"source_file", "function_name"} - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"Target CSV {csv_path} is missing required columns: {', '.join(sorted(missing))}"
            )

        targets: list[tuple[str, str]] = []
        for row in reader:
            source_file = str(row.get("source_file", "")).strip()
            function_name = str(row.get("function_name", "")).strip()
            if not source_file or not function_name:
                continue
            targets.append((source_file, function_name))
    return targets


def resolve_proof_dir(
    experiment_dir: Path,
    target_file: str,
    target_function: str,
) -> tuple[Path, str]:
    """Resolve the proof directory across supported experiment layouts."""
    file_basename = Path(target_file).stem
    candidates = [
        experiment_dir / file_basename / target_function,
        experiment_dir / target_function,
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate, candidate.relative_to(experiment_dir).as_posix()
    return candidates[0], f"{file_basename}/{target_function}"


def infer_software_name(source_file: str) -> str:
    """Derive the software label from the first source path segment."""
    parts = Path(norm_viewer_path(source_file)).parts
    return parts[0] if parts else ""


def infer_project_root(experiment_dir: Path, source_file: str) -> Path:
    """Infer the project root whose child directory matches the software label."""
    software = infer_software_name(source_file)
    for candidate in (experiment_dir, *experiment_dir.parents):
        if software and (candidate / software).exists():
            return candidate
    return experiment_dir.parent


def load_model_pricing() -> dict[str, dict[str, float]]:
    """Load the model pricing table used for metrics cost calculation."""
    try:
        payload = load_json_value(MODEL_PRICING_PATH)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, float]] = {}
    for model_name, pricing in payload.items():
        if not isinstance(pricing, dict):
            continue
        result[model_name] = {
            "input": float(pricing.get("input", 0.0)),
            "cached": float(pricing.get("cached", 0.0)),
            "output": float(pricing.get("output", 0.0)),
        }
    return result


def recompute_api_cost(metrics_records: list[dict[str, Any]]) -> str:
    """Recompute API cost using the same token-pricing logic as metric_summary.py."""
    if not metrics_records:
        return ""

    prices = load_model_pricing()
    total_cost = 0.0
    saw_task_attempt = False

    for entry in metrics_records:
        if entry.get("type") != "task_attempt":
            continue
        saw_task_attempt = True
        llm_data = entry.get("llm_data", {})
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
        total_cost += (
            (non_cached_input_tokens / 1_000_000) * pricing.get("input", 0.0)
            + (cached_tokens / 1_000_000) * pricing.get("cached", 0.0)
            + (output_tokens / 1_000_000) * pricing.get("output", 0.0)
        )

    if not saw_task_attempt:
        return ""
    return f"{total_cost:.4f}"


def extract_compile_succeeded(metrics_records: list[dict[str, Any]]) -> bool | None:
    """Read the initial harness compilation result from metrics records."""
    result: bool | None = None
    for entry in metrics_records:
        if entry.get("type") != "agent_result" or entry.get("agent_name") != "InitialHarnessGenerator":
            continue
        data = entry.get("data", {})
        if not isinstance(data, dict):
            continue
        compilation_status = data.get("compilation_status")
        if isinstance(compilation_status, bool):
            result = compilation_status
    return result


def extract_generation_time(metrics_records: list[dict[str, Any]]) -> float | None:
    """Sum elapsed time records across all agents in the metrics file."""
    total = 0.0
    saw_any = False
    for entry in metrics_records:
        raw_value = entry.get("elapsed_time")
        if raw_value is None:
            continue
        try:
            total += float(raw_value)
        except (TypeError, ValueError):
            continue
        saw_any = True
    return total if saw_any else None


def target_links_target(
    reachable_json: dict | None,
    target_file: str,
    target_function: str,
) -> bool | None:
    """Check whether the target file/function pair is reachable in viewer output."""
    if not reachable_json:
        return None
    reachable = reachable_json.get("viewer-reachable", {}).get("reachable", {})
    for file_name, functions in reachable.items():
        if not path_matches_suffix(file_name, target_file):
            continue
        if isinstance(functions, list):
            return target_function in functions
    return False


def load_vulnerability_report(proof_dir: Path) -> list[dict[str, Any]] | None:
    """Load vulnerability-report.json and return its vulnerabilities list."""
    report = load_json(proof_dir / "vulnerability-report.json")
    if report is None or not isinstance(report, dict):
        return None
    vulnerabilities = report.get("vulnerabilities")
    if not isinstance(vulnerabilities, list):
        return None
    return [item for item in vulnerabilities if isinstance(item, dict)]


def count_precondition_violations(proof_dir: Path) -> int | None:
    """Count distinct reported errors with one or more violated preconditions."""
    vulnerabilities = load_vulnerability_report(proof_dir)
    if vulnerabilities is None:
        return None

    count = 0
    for vulnerability in vulnerabilities:
        violated = vulnerability.get("violated_preconditions")
        if isinstance(violated, list) and violated:
            count += 1
    return count


def initialize_row(
    software: str,
    config: str,
    tag: str,
    source_file: str,
    target_function: str,
    proof_relpath: str,
    proof_found: bool,
) -> dict[str, object]:
    """Create a row populated with the identifier columns."""
    row: dict[str, object] = {
        "software": software,
        "config": config,
        "tag": tag,
        "source_file": source_file,
        "target_function": target_function,
        "proof_relpath": proof_relpath,
        "proof_found": proof_found,
    }
    for column_id in COLUMN_ORDER:
        row.setdefault(column_id, "")
    return row


def mark_missing_proof(row: dict[str, object]) -> None:
    """Render all proof-derived columns as N/A when no proof directory exists."""
    for column_id in COLUMN_ORDER:
        if column_id in {
            "software",
            "config",
            "tag",
            "source_file",
            "target_function",
            "proof_relpath",
            "proof_found",
        }:
            continue
        row[column_id] = NA_VALUE


def none_to_blank(value: object | None) -> object:
    """Render missing scalar values as an empty CSV cell."""
    return "" if value is None else value


def format_optional_float(value: float | None, digits: int = 6) -> str:
    """Format a float for CSV output while preserving empty cells for missing values."""
    return "" if value is None else f"{value:.{digits}f}"


def build_row(
    experiment_dir: Path,
    proof_dir: Path,
    target_file: str,
    target_function: str,
    config: str,
    experiment_output_dir: Path | None = None,
) -> dict[str, object]:
    """Build one CSV row by combining build results, viewer JSON, metrics, and harness parsing."""
    proof_relpath = proof_dir.relative_to(experiment_dir).as_posix()
    software = infer_software_name(target_file)
    row = initialize_row(
        software=software,
        config=config,
        tag=experiment_dir.name,
        source_file=target_file,
        target_function=target_function,
        proof_relpath=proof_relpath,
        proof_found=True,
    )

    metrics_records = load_metrics_records(experiment_output_dir, target_file, target_function)
    compile_succeeded = extract_compile_succeeded(metrics_records)
    generation_time = extract_generation_time(metrics_records)
    api_cost = recompute_api_cost(metrics_records)

    try:
        metadata = read_make_metadata(proof_dir)
        cbmcflags = metadata.cbmcflags
        proof_root = metadata.proof_root
        harness_entry = metadata.entry
    except Exception:
        cbmcflags = ""
        proof_root = proof_dir.parent.resolve()
        harness_entry = target_function

    project_root = infer_project_root(experiment_dir, target_file)
    proof_prefix = proof_root_prefix(proof_root, project_root)
    cbmc_root = parse_xml(resolve_cbmc_xml_path(proof_dir))
    report_json_dir = resolve_report_json_dir(proof_dir)
    report_html_index = resolve_report_html_index(proof_dir)
    viewer_result_json = load_json(report_json_dir / "viewer-result.json" if report_json_dir else None)
    viewer_property_json = load_json(report_json_dir / "viewer-property.json" if report_json_dir else None)
    coverage_json = load_json(report_json_dir / "viewer-coverage.json" if report_json_dir else None)
    reachable_json = load_json(report_json_dir / "viewer-reachable.json" if report_json_dir else None)

    links_target = target_links_target(reachable_json, target_file, target_function)
    semantic_valid: bool | None
    if compile_succeeded is None or links_target is None:
        semantic_valid = None
    else:
        semantic_valid = compile_succeeded and links_target

    verification_completes = verification_completed(cbmc_root, report_json_dir, report_html_index)
    verification_time = parse_verification_time(cbmc_root)
    verification_success = verification_succeeds(viewer_result_json, viewer_property_json)
    property_violations = count_reported_error_sites(viewer_result_json, viewer_property_json)
    precondition_violations = count_precondition_violations(proof_dir)
    unwindset_count, unwind_min, unwind_max = parse_unwind_metrics(cbmcflags)
    source_files_in_scope, functions_in_scope = aggregate_scope_metrics(reachable_json, proof_prefix)
    (
        program_reachable_line_count,
        program_covered_line_count,
        program_line_coverage_pct,
        function_model_count,
        function_model_avg_loc,
        harness_size_loc,
    ) = aggregate_coverage_metrics(coverage_json, proof_prefix)
    (
        overall_reachable_line_count,
        overall_covered_line_count,
        overall_line_coverage_pct,
    ) = aggregate_overall_coverage(coverage_json)
    (
        target_function_reachable_line_count,
        target_function_covered_line_count,
        target_function_line_coverage_pct,
    ) = aggregate_target_function_coverage(coverage_json, proof_prefix, target_function)
    model_used_variable_count, assumption_variable_count = collect_variable_metrics(
        proof_dir,
        harness_entry,
    )

    row.update(
        {
            "compile_succeeded": none_to_blank(compile_succeeded),
            "links_target": none_to_blank(links_target),
            "semantic_valid": none_to_blank(semantic_valid),
            "verification_completes": verification_completes,
            "verification_time": format_optional_float(verification_time),
            "verification_succeeds": none_to_blank(verification_success),
            "target_function_reachable_line_count": none_to_blank(target_function_reachable_line_count),
            "target_function_covered_line_count": none_to_blank(target_function_covered_line_count),
            "target_function_line_coverage_pct": format_optional_float(target_function_line_coverage_pct),
            "program_reachable_line_count": none_to_blank(program_reachable_line_count),
            "program_covered_line_count": none_to_blank(program_covered_line_count),
            "program_line_coverage_pct": format_optional_float(program_line_coverage_pct),
            "overall_reachable_line_count": none_to_blank(overall_reachable_line_count),
            "overall_covered_line_count": none_to_blank(overall_covered_line_count),
            "overall_line_coverage_pct": format_optional_float(overall_line_coverage_pct),
            "property_violations": none_to_blank(property_violations),
            "precondition_violations": none_to_blank(precondition_violations),
            "generation_time": format_optional_float(generation_time),
            "api_cost": api_cost,
            "harness_size_loc": none_to_blank(harness_size_loc),
            "source_files_in_scope": none_to_blank(source_files_in_scope),
            "functions_in_scope": none_to_blank(functions_in_scope),
            "loop_unwindset_count": unwindset_count,
            "loop_unwind_min": none_to_blank(unwind_min),
            "loop_unwind_max": none_to_blank(unwind_max),
            "model_used_variable_count": none_to_blank(model_used_variable_count),
            "assumption_variable_count": none_to_blank(assumption_variable_count),
            "function_model_count": none_to_blank(function_model_count),
            "function_model_avg_loc": format_optional_float(function_model_avg_loc),
        }
    )
    return row


def write_csv(rows: list[dict[str, object]], output_path: Path) -> None:
    """Write the completed assessment rows to a CSV file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {COLUMN_LABELS[column_id]: row.get(column_id, "") for column_id in COLUMN_ORDER}
            )


def main() -> int:
    """Run the full experiment analysis workflow from target CSV through CSV emission."""
    args = parse_args()
    experiment_dir = args.experiment_dir.resolve()
    if not experiment_dir.is_dir():
        print(f"Experiment directory not found: {experiment_dir}", file=sys.stderr)
        return 1

    experiment_csv = args.experiment_csv.resolve()
    if not experiment_csv.is_file():
        print(f"Experiment CSV not found: {experiment_csv}", file=sys.stderr)
        return 1

    experiment_output_dir = args.experiment_output_dir.resolve() if args.experiment_output_dir else None
    if experiment_output_dir is not None and not experiment_output_dir.is_dir():
        print(f"Experiment output directory not found: {experiment_output_dir}", file=sys.stderr)
        return 1

    try:
        targets = read_experiment_targets(experiment_csv)
    except ValueError as exc:
        print(f"[error] targets: {exc}", file=sys.stderr)
        return 1

    output_path = args.output.resolve() if args.output else experiment_dir / "assessment.csv"
    rows: list[dict[str, object]] = []

    for source_file, target_function in targets:
        proof_dir, proof_relpath = resolve_proof_dir(experiment_dir, source_file, target_function)
        if not proof_dir.is_dir():
            row = initialize_row(
                software=infer_software_name(source_file),
                config=args.config,
                tag=experiment_dir.name,
                source_file=source_file,
                target_function=target_function,
                proof_relpath=proof_relpath,
                proof_found=False,
            )
            mark_missing_proof(row)
            rows.append(row)
            continue

        print(f"[proof] {proof_dir}", file=sys.stderr)
        if (proof_dir / "build").exists() and not args.force_make:
            print(f"[reuse] {proof_dir / 'build'}", file=sys.stderr)
        else:
            print(f"[run] make -j3 in {proof_dir}", file=sys.stderr)
        ensure_build(proof_dir, args.timeout, args.force_make)
        rows.append(
            build_row(
                experiment_dir=experiment_dir,
                proof_dir=proof_dir,
                target_file=source_file,
                target_function=target_function,
                config=args.config,
                experiment_output_dir=experiment_output_dir,
            )
        )

    write_csv(rows, output_path)
    print(f"[done] wrote {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
