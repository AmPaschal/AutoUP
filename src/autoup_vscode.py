"""VS Code bridge entrypoint for AutoUP.

This file adapts the regular AutoUP CLI into a stable entrypoint that the
VS Code extension can launch. It is responsible for:
1. Parsing extension-oriented command line arguments.
2. Resolving the execution host and target function.
3. Converting those arguments into the namespace expected by `run.py`.
4. Initializing structured job progress reporting for VS Code.
5. Running the AutoUP pipeline and translating success/failure into process
   exit codes the extension can act on.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import signal
import sys
from types import SimpleNamespace

import run as autoup_run
from vscode_bridge.progress import VSCodeJobProgress


def build_parser() -> argparse.ArgumentParser:
    """Build the bridge CLI parser.

    Inputs:
        None.

    Returns:
        argparse.ArgumentParser: A parser that understands the VS Code bridge
        commands and their arguments.

    Behavior:
        Defines the `start` subcommand used by the extension to begin a proof
        job and capture all metadata needed by the backend runner.
    """
    parser = argparse.ArgumentParser(description="VS Code bridge for AutoUP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Define the single v1 command used by the extension to start a proof job.
    start_parser = subparsers.add_parser("start", help="Start a proof job")
    start_parser.add_argument("--workspace", required=True)
    start_parser.add_argument("--source", required=True)
    start_parser.add_argument("--line", type=int, required=True)
    start_parser.add_argument("--column", type=int, required=True)
    start_parser.add_argument("--proof-dir", required=True)
    start_parser.add_argument("--job-id", required=True)
    start_parser.add_argument("--function-name")
    start_parser.add_argument("--container-engine", default="docker")
    start_parser.add_argument("--model", default="gpt-5.3-codex")
    start_parser.add_argument("--scope-bound", type=int)
    start_parser.add_argument("--scope-time-budget", type=float)
    start_parser.add_argument("--log-file")
    start_parser.add_argument("--metrics-file")
    start_parser.add_argument("--execution-host")
    return parser


def detect_execution_host() -> str:
    """Detect where the extension-hosted backend is running.

    Inputs:
        None.

    Returns:
        str: One of `remote-ssh-linux`, `local-macos`, or `local-linux`.

    Behavior:
        Uses VS Code remote environment variables and the current platform to
        describe the execution host in a way the extension can persist in the
        job manifest.
    """
    # Prefer the explicit VS Code remote marker because that determines where
    # the backend actually runs.
    remote_name = os.getenv("VSCODE_REMOTE_NAME", "")
    system_name = platform.system().lower()
    if remote_name == "ssh-remote":
        return "remote-ssh-linux"
    if system_name == "darwin":
        return "local-macos"
    return "local-linux"


def resolve_function_name(source_path: str, line_number: int) -> str:
    """Resolve a likely function name near a given source line.

    Inputs:
        source_path: Absolute or relative path to the source file.
        line_number: One-based line number selected in the editor.

    Returns:
        str: The detected function name.

    Raises:
        ValueError: If no reasonable function definition can be found nearby.

    Behavior:
        Scans backward from the requested line and uses a conservative regex to
        find the nearest plausible C function definition.
    """
    # Read the file once so the backward scan works with in-memory lines.
    with open(source_path, "r", encoding="utf-8", errors="ignore") as file:
        lines = file.readlines()

    # Clamp the search start so the scan stays inside the file bounds.
    search_limit = min(max(line_number - 1, 0), len(lines) - 1)
    pattern = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*\{?")
    for index in range(search_limit, -1, -1):
        # Skip lines that do not look like a function signature.
        match = pattern.search(lines[index])
        if not match:
            continue
        candidate = match.group(1)
        # Filter obvious language keywords so they are not misread as functions.
        if candidate in {"if", "for", "while", "switch", "return"}:
            continue
        return candidate
    raise ValueError(f"Could not resolve function name in {source_path}:{line_number}")


def build_run_args(args: argparse.Namespace) -> SimpleNamespace:
    """Translate bridge arguments into the namespace expected by `run.py`.

    Inputs:
        args: Parsed bridge arguments from `build_parser()`.

    Returns:
        SimpleNamespace: A namespace matching the fields expected by the
        existing AutoUP runtime.

    Behavior:
        Fills defaults for the proof log, metrics file, execution host, and
        function name so the bridge can call the shared runner code directly.
    """
    # Reuse the explicitly supplied function name when available, otherwise
    # derive it from the source file near the selected editor line.
    function_name = args.function_name or resolve_function_name(args.source, args.line)
    proof_dir = os.path.abspath(args.proof_dir)
    log_file = args.log_file or os.path.join(proof_dir, "autoup.log")
    metrics_file = args.metrics_file or os.path.join(proof_dir, "metrics.jsonl")
    execution_host = args.execution_host or detect_execution_host()

    # Shape the arguments so the normal AutoUP pipeline can run unchanged.
    return SimpleNamespace(
        mode="all",
        target_function_name=function_name,
        root_dir=os.path.abspath(args.workspace),
        harness_path=proof_dir,
        target_file_path=os.path.abspath(args.source),
        log_file=log_file,
        metrics_file=metrics_file,
        container_engine=args.container_engine,
        llm_model=args.model,
        scope_bound=args.scope_bound,
        scope_time_budget=args.scope_time_budget,
        job_id=args.job_id,
        execution_host=execution_host,
        line=args.line,
        column=args.column,
    )


def _install_signal_forwarding(progress: VSCodeJobProgress) -> None:
    """Install signal handlers that convert shutdown into job cancellation.

    Inputs:
        progress: The structured progress writer for the active job.

    Returns:
        None.

    Behavior:
        Ensures Ctrl+C or process termination updates the job manifest/event log
        before the bridge exits.
    """
    def _handle_signal(signum, _frame):
        # Record cancellation first so the extension sees a terminal state even
        # if the process exits immediately afterward.
        progress.job_cancelled()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def start_job(args: argparse.Namespace) -> int:
    """Start a single AutoUP proof job for the VS Code extension.

    Inputs:
        args: Parsed bridge arguments describing the proof request.

    Returns:
        int: `0` on success and `1` on failure.

    Behavior:
        Builds runtime arguments, initializes structured progress reporting,
        delegates to the shared AutoUP runner, and maps the final result into an
        extension-friendly exit code.
    """
    # Build the arguments expected by the shared AutoUP runtime.
    run_args = build_run_args(args)
    progress = VSCodeJobProgress.from_args(run_args)
    run_args.vscode_progress = progress

    # Create the manifest and initial lifecycle events before work starts.
    progress.initialize_job(pid=os.getpid())
    progress.job_started()
    progress.bootstrap_started()
    progress.bootstrap_completed()
    _install_signal_forwarding(progress)

    try:
        # Run the shared pipeline implementation from the regular AutoUP entrypoint.
        success = autoup_run.run_with_args(run_args)
    except SystemExit:
        raise
    except Exception as exc:
        # Persist unexpected failures so the extension does not get stuck in an
        # in-progress state.
        progress.job_failed(str(exc))
        raise

    # Convert the shared runner's boolean result into a standard process code.
    if success:
        progress.job_completed()
        return 0

    progress.job_failed("AutoUP pipeline failed")
    return 1


def main() -> int:
    """Parse bridge arguments and dispatch the requested command.

    Inputs:
        None. Reads `sys.argv`.

    Returns:
        int: Process exit code for the selected command.

    Behavior:
        Entrypoint used when the bridge is executed as a script.
    """
    args = build_parser().parse_args()
    if args.command == "start":
        return start_job(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
