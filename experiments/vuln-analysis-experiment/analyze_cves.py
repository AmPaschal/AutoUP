#!/usr/bin/env python3
"""Run CBMC experiment proofs and summarize methodology-aligned metrics."""

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


REPO_ROOT = "/local/scratch/a/pamusuo/research/autoup-project/zephyrproject"

CSV_COLUMNS = [
    "cve_id",
    "target_function",
    "target_file",
    "vuln_type",
    "sink",
    "Verification_completes",
    "verification_time",
    "reachable_line_count",
    "covered_line_count",
    "line_coverage_pct",
    "reported_error_count",
    "sink_included",
    "sink_covered",
    "cve_exposed_strict",
    "cve_exposed_partial",
]

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
    """Parse command-line arguments for a single experiment run."""
    parser = argparse.ArgumentParser(
        description="Run an experiment's CBMC proofs and emit a summary CSV."
    )
    parser.add_argument("experiment_dir", type=Path, help="Path to experiment dir, e.g. cbmc/exp-0325")
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
        "--vulnerability-metadata",
        type=Path,
        help="Optional path to vulnerability metadata JSON keyed by affectedFunction",
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
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_quiet_command(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[None]:
    """Run a command with stdout/stderr discarded and kill the full process group on timeout."""
    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
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


def discover_proof_dirs(experiment_dir: Path) -> list[Path]:
    """Find proof directories by locating exact `Makefile` files under the experiment tree."""
    return sorted(makefile.parent.resolve() for makefile in experiment_dir.rglob("Makefile"))


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
    except subprocess.TimeoutExpired as exc:
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


def load_json(path: Path) -> dict | None:
    """Load a JSON file and return `None` if the file is missing or malformed."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def load_json_value(path: Path) -> Any:
    """Load a JSON file and raise when it is missing or malformed."""
    return json.loads(path.read_text())


def parse_xml(path: Path) -> ET.Element | None:
    """Parse an XML file and return its root element when available."""
    if not path.exists():
        return None
    try:
        return ET.parse(path).getroot()
    except ET.ParseError:
        return None


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
    # Walk the flattened CBMC flag list and collect both global and per-loop unwind bounds.
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
    # The parser that follows relies on braces and delimiters still lining up, so we
    # erase text without changing the overall shape of the source.
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
        # Skip constructs that look function-like syntactically but are not real definitions.
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


def extract_declared_names(body: str) -> set[str]:
    """Best-effort extraction of declared local variable names from a function body."""
    names: set[str] = set()
    statements = [stmt.strip() for stmt in body.split(";")]
    for statement in statements:
        if not statement:
            continue
        stmt = statement.replace("\n", " ").strip()
        if any(stmt.startswith(prefix) for prefix in CALLISH_PREFIXES):
            continue
        if stmt.startswith("{") or stmt.startswith("}"):
            stmt = stmt.lstrip("{} ").strip()
        if not stmt:
            continue
        parts = split_top_level(stmt, ",")
        if not parts:
            continue
        identifiers = re.findall(r"[A-Za-z_]\w*", parts[0])
        if not identifiers:
            continue
        first = identifiers[0]
        if first not in DECL_QUALIFIERS and first not in DECL_BASE_TYPES and not re.match(r"(?:u?int|char|size_t|_Bool|bool)\d*_t?$", first):
            if not stmt.startswith("struct ") and not stmt.startswith("union ") and not stmt.startswith("enum "):
                continue
        for part in parts:
            cleaned = part.strip()
            if not cleaned:
                continue
            ids = re.findall(r"[A-Za-z_]\w*", cleaned)
            if not ids:
                continue
            candidate = ids[-1]
            if candidate in DECL_QUALIFIERS or candidate in DECL_BASE_TYPES:
                continue
            names.add(candidate)
    return names


def extract_identifiers(expr: str) -> set[str]:
    """Return bare identifiers referenced in an expression."""
    return set(re.findall(r"\b[A-Za-z_]\w*\b", expr))


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

    # Re-run the assignment scan a few times so aliases can chain through multiple locals.
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
                # `local = program_path` means future references to the local should resolve to
                # the program path. `program_path = local` pushes the local's alias in the other
                # direction so we count the underlying program variable once.
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

    # Record program-side lvalues written by the harness and program-side paths appearing
    # inside assumptions, after alias expansion has normalized local helper variables.
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


def normalize_space(text: str) -> str:
    """Collapse repeated whitespace to simplify exact substring matching."""
    return " ".join(text.split())


def path_matches_suffix(candidate: str, target_suffix: str) -> bool:
    """Check whether a viewer path matches a metadata path by suffix."""
    normalized_candidate = norm_viewer_path(candidate)
    normalized_suffix = norm_viewer_path(target_suffix)
    return (
        normalized_candidate == normalized_suffix
        or normalized_candidate.endswith(f"/{normalized_suffix}")
    )


def load_vulnerability_metadata_index(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load vulnerability metadata and index it by affected function.

    Multiple entries may share the same affectedFunction (e.g. when a single
    function is associated with several CVEs), so the index maps each function
    name to a *list* of metadata entries.
    """
    try:
        payload = load_json_value(path)
    except FileNotFoundError as exc:
        raise ValueError(f"Vulnerability metadata file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid vulnerability metadata JSON in {path}: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError(f"Vulnerability metadata in {path} must be a JSON array")

    index: dict[str, list[dict[str, Any]]] = {}
    for idx, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(f"Metadata entry {idx} in {path} must be a JSON object")
        affected_function = entry.get("affectedFunction")
        if not isinstance(affected_function, str) or not affected_function.strip():
            raise ValueError(f"Metadata entry {idx} in {path} is missing affectedFunction")
        index.setdefault(affected_function, []).append(entry)

    return index


def load_vulnerability_report(proof_dir: Path) -> list[dict[str, Any]] | None:
    """Load vulnerability-report.json and return its vulnerabilities list."""
    report_path = proof_dir / "vulnerability-report.json"
    report = load_json(report_path)
    if report is None or not isinstance(report, dict):
        return None
    vulnerabilities = report.get("vulnerabilities")
    if not isinstance(vulnerabilities, list):
        return None
    result: list[dict[str, Any]] = []
    for item in vulnerabilities:
        if isinstance(item, dict):
            result.append(item)
    return result


def is_strict_target_vulnerability_match(
    vulnerability: dict[str, Any], target: dict[str, Any]
) -> bool:
    """Check whether a reported vulnerability matches the metadata target."""
    code_location = vulnerability.get("code_location")
    if not isinstance(code_location, dict):
        return False

    location_file = code_location.get("file")
    if not isinstance(location_file, str):
        return False
    affected_file = target.get("affectedFile")
    if not isinstance(affected_file, str) or not path_matches_suffix(location_file, affected_file):
        return False

    manifestation_lines = target.get("manifestationLines")
    if isinstance(manifestation_lines, list) and manifestation_lines:
        line = code_location.get("line")
        if line not in manifestation_lines:
            return False

    location_function = code_location.get("function")
    if not isinstance(location_function, str):
        return False
    candidate_functions = [
        value
        for value in (target.get("sinkFunction"), target.get("affectedFunction"))
        if isinstance(value, str) and value
    ]
    return location_function in candidate_functions


def has_partial_target_vulnerability_match(
    vulnerabilities: list[dict[str, Any]], target: dict[str, Any]
) -> bool:
    """Check whether any reported vulnerability description mentions the target variable."""
    affected_variable = target.get("affectedVariable")
    if not isinstance(affected_variable, str) or not affected_variable:
        return False

    normalized_affected_variable = normalize_space(affected_variable)
    for vulnerability in vulnerabilities:
        error_type = vulnerability.get("error_type")
        if not isinstance(error_type, str):
            continue
        if affected_variable in error_type:
            return True
        if normalized_affected_variable in normalize_space(error_type):
            return True
    return False


def determine_target_vulnerability_reported(
    proof_dir: Path,
    entry: str,
    metadata_index: dict[str, list[dict[str, Any]]] | None,
) -> tuple[int | None, int, list[str]]:
    """Return the target-vulnerability detection results for one proof.

    Returns a tuple of:
      - total number of known CVEs for this function (None if no metadata)
      - number of detected CVEs (strict or partial match)
      - list of detected CVE IDs
    """
    if metadata_index is None:
        return None, 0, []

    targets = metadata_index.get(entry)
    if targets is None:
        return None, 0, []

    total_count = len(targets)

    vulnerabilities = load_vulnerability_report(proof_dir)
    if vulnerabilities is None or not vulnerabilities:
        return total_count, 0, []

    detected_cves: list[str] = []
    for target in targets:
        cve_id = target.get("cveId", "")
        if any(is_strict_target_vulnerability_match(v, target) for v in vulnerabilities):
            detected_cves.append(cve_id)
        elif has_partial_target_vulnerability_match(vulnerabilities, target):
            detected_cves.append(f"{cve_id}(partial)")

    return total_count, len(detected_cves), detected_cves


def proof_root_prefix(proof_root: Path) -> str:
    """Convert a proof root on disk into the corresponding viewer path prefix."""
    try:
        return norm_viewer_path(str(proof_root.relative_to(REPO_ROOT)))
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


def parse_cbmc_status(viewer_result_json: dict | None, cbmc_root: ET.Element | None) -> str:
    """Return the overall CBMC prover status for a proof run."""
    cbmc_xml_status = parse_cbmc_xml_status(cbmc_root)
    if cbmc_xml_status:
        return cbmc_xml_status
    if viewer_result_json:
        prover = viewer_result_json.get("viewer-result", {}).get("prover", "")
        if prover:
            return prover.strip().upper()
    return ""


def report_generated(build_dir: Path) -> bool:
    """Check whether cbmc-viewer generated both HTML and JSON report artifacts."""
    report_json_dir = build_dir / "report" / "json"
    report_html_index = build_dir / "report" / "html" / "index.html"
    return report_html_index.is_file() and any(report_json_dir.glob("*.json"))


def verification_completed(cbmc_root: ET.Element | None, build_dir: Path) -> bool:
    """Check whether verification reached a conclusive status and report generation completed."""
    cbmc_status = parse_cbmc_xml_status(cbmc_root)
    return (
        cbmc_status in {"SUCCESS", "FAILURE", "FAILED"}
        and report_generated(build_dir)
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
    if not viewer_result_json or not viewer_property_json:
        return None

    false_properties = (
        viewer_result_json.get("viewer-result", {})
        .get("results", {})
        .get("false", [])
    )
    properties = viewer_property_json.get("viewer-property", {}).get("properties", {})
    sites: set[tuple[str, str, str]] = set()
    for property_id in false_properties:
        property_info = properties.get(property_id)
        if is_excluded_error_property(property_id, property_info):
            continue
        if not property_info:
            continue
        location = property_info.get("location")
        if location is None:
            continue
        file_name = norm_viewer_path(str(location.get("file", "")))
        function = str(location.get("function", ""))
        line = str(location.get("line", ""))
        sites.add((file_name, function, line))
    return len(sites)


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

    # Separate project-under-test coverage from proof-side code while simultaneously
    # harvesting the sizes of reachable proof-side model functions.
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


def reachable_proof_side_functions(
    coverage_json: dict | None,
    proof_prefix: str,
) -> dict[str, set[str]]:
    """Return reachable proof-side functions keyed by viewer path."""
    result: dict[str, set[str]] = {}
    if not coverage_json:
        return result
    function_coverage = coverage_json.get("viewer-coverage", {}).get("function_coverage", {})
    for file_name, functions in function_coverage.items():
        if not is_proof_side_file(file_name, proof_prefix):
            continue
        result[norm_viewer_path(file_name)] = set(functions.keys())
    return result


def collect_variable_metrics(
    proof_dir: Path,
    entry: str,
    coverage_json: dict | None,
) -> tuple[int | None, int | None]:
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


def build_row(
    experiment_dir: Path,
    proof_dir: Path,
    metadata: MakeMetadata,
    run_result: RunResult,
    vulnerability_metadata_index: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, object]:
    """Build one CSV row by combining build results, viewer JSON, and harness parsing."""
    build_dir = proof_dir / "build"
    cbmc_xml = build_dir / "reports" / "cbmc.xml"
    report_json_dir = build_dir / "report" / "json"

    # Load the structured artifacts emitted by the existing proof Makefiles.
    cbmc_root = parse_xml(cbmc_xml)
    viewer_result_json = load_json(report_json_dir / "viewer-result.json")
    viewer_property_json = load_json(report_json_dir / "viewer-property.json")
    coverage_json = load_json(report_json_dir / "viewer-coverage.json")
    reachable_json = load_json(report_json_dir / "viewer-reachable.json")

    proof_prefix = proof_root_prefix(metadata.proof_root)
    cbmc_status = parse_cbmc_status(viewer_result_json, cbmc_root)
    compile_succeeded = (build_dir / f"{metadata.entry}.goto").exists()
    verification_time = parse_verification_time(cbmc_root)
    verification_is_completed = verification_completed(cbmc_root, build_dir)
    unwindset_count, unwind_min, unwind_max = parse_unwind_metrics(metadata.cbmcflags)
    source_files_in_scope, functions_in_scope = aggregate_scope_metrics(reachable_json, proof_prefix)
    (
        reachable_line_count,
        covered_line_count,
        line_coverage_pct,
        function_model_count,
        function_model_avg_loc,
        total_harness_loc,
    ) = aggregate_coverage_metrics(coverage_json, proof_prefix)
    # Keep three coverage views side by side: filtered program coverage, raw overall
    # coverage, and target-function-only coverage.
    (
        overall_reachable_line_count,
        overall_covered_line_count,
        overall_line_coverage_pct,
    ) = aggregate_overall_coverage(coverage_json)
    (
        target_function_reachable_line_count,
        target_function_covered_line_count,
        target_function_line_coverage_pct,
    ) = aggregate_target_function_coverage(coverage_json, proof_prefix, metadata.entry)
    model_used_variable_count, assumption_variable_count = collect_variable_metrics(
        proof_dir,
        metadata.entry,
        coverage_json,
    )
    reported_error_count = count_reported_error_sites(viewer_result_json, viewer_property_json)
    target_vuln_count, target_vuln_detected_count, target_vuln_detected_cves = determine_target_vulnerability_reported(
        proof_dir,
        metadata.entry,
        vulnerability_metadata_index,
    )

    return {
        "experiment": experiment_dir.name,
        "proof_relpath": proof_dir.relative_to(experiment_dir).as_posix(),
        "entry": metadata.entry,
        "compile_succeeded": compile_succeeded,
        "verification_completed": verification_is_completed,
        "verification_time": format_optional_float(verification_time),
        "timed_out": run_result.timed_out,
        "make_wall_time_s": round(run_result.wall_time_s, 6) if run_result.wall_time_s is not None else "",
        "cbmc_status": cbmc_status,
        "source_files_in_scope": none_to_blank(source_files_in_scope),
        "functions_in_scope": none_to_blank(functions_in_scope),
        "loop_unwindset_count": unwindset_count,
        "loop_unwind_min": none_to_blank(unwind_min),
        "loop_unwind_max": none_to_blank(unwind_max),
        "model_used_variable_count": none_to_blank(model_used_variable_count),
        "assumption_variable_count": none_to_blank(assumption_variable_count),
        "function_model_count": none_to_blank(function_model_count),
        "function_model_avg_loc": format_optional_float(function_model_avg_loc),
        "total_harness_loc": none_to_blank(total_harness_loc),
        "program_reachable_line_count": none_to_blank(reachable_line_count),
        "program_covered_line_count": none_to_blank(covered_line_count),
        "program_line_coverage_pct": format_optional_float(line_coverage_pct),
        "target_function_reachable_line_count": none_to_blank(target_function_reachable_line_count),
        "target_function_covered_line_count": none_to_blank(target_function_covered_line_count),
        "target_function_line_coverage_pct": format_optional_float(target_function_line_coverage_pct),
        "reachable_line_count": none_to_blank(overall_reachable_line_count),
        "covered_line_count": none_to_blank(overall_covered_line_count),
        "line_coverage_pct": format_optional_float(overall_line_coverage_pct),
        "reported_error_count": none_to_blank(reported_error_count),
        "total_cve_count": none_to_blank(target_vuln_count),
        "detected_cve_count": target_vuln_detected_count,
        "cves_detected": ";".join(target_vuln_detected_cves),
    }


def none_to_blank(value: object | None) -> object:
    """Render missing scalar values as an empty CSV cell."""
    return "" if value is None else value


def format_optional_float(value: float | None) -> str:
    """Format a float for CSV output while preserving empty cells for missing values."""
    return "" if value is None else f"{value:.6f}"


def write_csv(rows: list[dict[str, object]], output_path: Path) -> None:
    """Write the completed assessment rows to a CSV file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    """Run the full experiment analysis workflow from discovery through CSV emission."""
    args = parse_args()
    experiment_dir = args.experiment_dir.resolve()
    if not experiment_dir.is_dir():
        print(f"Experiment directory not found: {experiment_dir}", file=sys.stderr)
        return 1

    if not args.vulnerability_metadata:
        print("Vulnerability metadata json is required for CVE analysis.", file=sys.stderr)
        return 1

    try:
        cves = load_json_value(args.vulnerability_metadata.resolve())
    except Exception as exc:
        print(f"[error] vulnerability metadata: {exc}", file=sys.stderr)
        return 1

    output_path = args.output.resolve() if args.output else experiment_dir / "assessment.csv"
    rows: list[dict[str, object]] = []

    for cve in cves:
        cve_id = cve.get("cveId", "")
        target_function = cve.get("affectedFunction", "")
        target_file = cve.get("affectedFile", "")
        vuln_type = cve.get("vulnType", "")
        sink_func = cve.get("sinkFunction", "")

        manifest = cve.get("manifestationLines")
        sink_line_str = str(manifest[0]) if manifest and len(manifest) > 0 else ""
        sink_str = f"{sink_func}:{sink_line_str}"

        # find proof directory: filename without extension / function_name
        if not target_file or not target_function:
            continue
        
        file_basename = Path(target_file).stem
        proof_dir_rel = f"{file_basename}/{target_function}"
        proof_dir = experiment_dir / proof_dir_rel

        row = {
            "cve_id": cve_id,
            "target_function": target_function,
            "target_file": target_file,
            "vuln_type": vuln_type,
            "sink": sink_str,
            "Verification_completes": False,
            "verification_time": "",
            "reachable_line_count": "",
            "covered_line_count": "",
            "line_coverage_pct": "",
            "reported_error_count": "",
            "sink_included": False,
            "sink_covered": False,
            "cve_exposed_strict": False,
            "cve_exposed_partial": False,
        }

        if not proof_dir.is_dir():
            print(f"[warning] Proof directory not found for {cve_id}: {proof_dir_rel}", file=sys.stderr)
            rows.append(row)
            continue

        print(f"[proof] {proof_dir.relative_to(REPO_ROOT)}", file=sys.stderr)

        if (proof_dir / "build").exists() and not args.force_make:
            print(f"[reuse] {proof_dir.relative_to(REPO_ROOT)}/build", file=sys.stderr)
        else:
            print(f"[run] make -j3 in {proof_dir.relative_to(REPO_ROOT)}", file=sys.stderr)
            
        run_result = ensure_build(proof_dir, args.timeout, args.force_make)

        build_dir = proof_dir / "build"
        cbmc_xml = build_dir / "reports" / "cbmc.xml"
        report_json_dir = build_dir / "report" / "json"
        
        cbmc_root = parse_xml(cbmc_xml)
        verification_completes = verification_completed(cbmc_root, build_dir)
        row["Verification_completes"] = verification_completes

        verification_time = parse_verification_time(cbmc_root)
        row["verification_time"] = format_optional_float(verification_time)

        viewer_coverage = load_json(report_json_dir / "viewer-coverage.json")
        sink_included = False
        sink_covered = False
        if viewer_coverage and sink_line_str:
            line_cov = viewer_coverage.get("viewer-coverage", {}).get("line_coverage", {})
            for cov_file, cov_lines in line_cov.items():
                if path_matches_suffix(cov_file, target_file):
                    if sink_line_str in cov_lines:
                        sink_included = True
                        if cov_lines[sink_line_str] in ("hit", "partially-hit"):
                            sink_covered = True
                    break
        
        row["sink_included"] = sink_included
        row["sink_covered"] = sink_covered

        (
            overall_reachable_line_count,
            overall_covered_line_count,
            overall_line_coverage_pct,
        ) = aggregate_overall_coverage(viewer_coverage)
        row["reachable_line_count"] = none_to_blank(overall_reachable_line_count)
        row["covered_line_count"] = none_to_blank(overall_covered_line_count)
        row["line_coverage_pct"] = format_optional_float(overall_line_coverage_pct)

        viewer_result_json = load_json(report_json_dir / "viewer-result.json")
        viewer_property_json = load_json(report_json_dir / "viewer-property.json")
        reported_error_count = count_reported_error_sites(viewer_result_json, viewer_property_json)
        row["reported_error_count"] = none_to_blank(reported_error_count)

        vulnerabilities = load_vulnerability_report(proof_dir)
        if vulnerabilities:
            if any(is_strict_target_vulnerability_match(v, cve) for v in vulnerabilities):
                row["cve_exposed_strict"] = True
            if has_partial_target_vulnerability_match(vulnerabilities, cve):
                row["cve_exposed_partial"] = True

        rows.append(row)

    write_csv(rows, output_path)
    print(f"[done] wrote {output_path}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
