"""Structured job progress reporting for the VS Code extension.

This file centralizes the contract between the Python backend and the VS Code
frontend. It owns:
1. Writing `.autoup/job.json`.
2. Appending structured events to `.autoup/events.jsonl`.
3. Building verification summaries from AutoUP/CBMC report files.
4. Emitting lifecycle, progress, and accepted-refinement events that the
   extension can render without parsing logs.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Optional


class VSCodeJobProgress:
    """Write per-job manifests and append-only event logs for VS Code.

    Inputs:
        job_id: Stable proof job identifier from the extension.
        proof_dir: Proof output directory for the job.
        workspace_root: Root of the workspace/project under analysis.
        source_file: Target source file for the proof.
        function_name: Target function under proof generation.
        execution_host: Host label such as `local-macos`.
        log_file: Optional path to the proof log file.

    Outputs:
        None directly. Methods on this class write files to disk.

    Behavior:
        Keeps the current stage and writes structured job/event data in a form
        that can be polled by the VS Code extension.
    """

    def __init__(
        self,
        *,
        job_id: Optional[str] = None,
        proof_dir: Optional[str] = None,
        workspace_root: Optional[str] = None,
        source_file: Optional[str] = None,
        function_name: Optional[str] = None,
        execution_host: Optional[str] = None,
        log_file: Optional[str] = None,
        line: Optional[int] = None,
        column: Optional[int] = None,
    ):
        # A job is only considered enabled when the extension has supplied the
        # minimum identity and location needed for manifest/event files.
        self.enabled = bool(job_id and proof_dir)
        self.job_id = job_id
        self.proof_dir = proof_dir
        self.workspace_root = workspace_root
        self.source_file = source_file
        self.function_name = function_name
        self.execution_host = execution_host or "local-linux"
        self.log_file = log_file
        self.line = line or 1
        self.column = column or 1
        self.current_stage: Optional[str] = None
        self._lock = threading.Lock()

        if self.enabled:
            # Create the sidecar state directory up front so later event writes
            # do not race on directory creation.
            self.state_dir = os.path.join(self.proof_dir, ".autoup")
            self.job_path = os.path.join(self.state_dir, "job.json")
            self.events_path = os.path.join(self.state_dir, "events.jsonl")
            os.makedirs(self.state_dir, exist_ok=True)
        else:
            self.state_dir = None
            self.job_path = None
            self.events_path = None

    @classmethod
    def from_args(cls, args) -> "VSCodeJobProgress":
        """Build a progress writer from a runtime args namespace.

        Inputs:
            args: Namespace whose fields match the AutoUP runner arguments.

        Returns:
            VSCodeJobProgress: Configured progress writer for the job.

        Behavior:
            Extracts the VS Code specific metadata from the runner arguments.
        """
        return cls(
            job_id=getattr(args, "job_id", None),
            proof_dir=getattr(args, "harness_path", None),
            workspace_root=getattr(args, "root_dir", None),
            source_file=getattr(args, "target_file_path", None),
            function_name=getattr(args, "target_function_name", None),
            execution_host=getattr(args, "execution_host", None),
            log_file=getattr(args, "log_file", None),
            line=getattr(args, "line", None),
            column=getattr(args, "column", None),
        )

    def _write_json(self, path: str, data: dict[str, Any]) -> None:
        """Atomically write a JSON object to disk.

        Inputs:
            path: Final destination path.
            data: JSON-serializable dictionary to write.

        Returns:
            None.

        Behavior:
            Writes through a temporary file and replaces the destination so the
            extension does not read partial manifests.
        """
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        os.replace(temp_path, path)

    def _read_job(self) -> dict[str, Any]:
        """Read the current job manifest if it exists.

        Inputs:
            None.

        Returns:
            dict[str, Any]: The parsed manifest or an empty dictionary when the
            manifest is unavailable.

        Behavior:
            Hides manifest absence from callers so updates can be applied
            through a simple read-modify-write flow.
        """
        if not self.enabled or not os.path.exists(self.job_path):
            return {}
        with open(self.job_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def _update_job(self, **updates: Any) -> None:
        """Apply partial updates to the on-disk job manifest.

        Inputs:
            **updates: Manifest fields to merge into the current job state.

        Returns:
            None.

        Behavior:
            Serializes updates under a lock so concurrent stage/event calls do
            not corrupt the manifest.
        """
        if not self.enabled:
            return

        with self._lock:
            # Read the latest manifest snapshot before merging new fields.
            current = self._read_job()
            current.update(updates)
            # Always refresh the timestamp so the extension can detect state churn.
            current["updatedAt"] = time.time()
            self._write_json(self.job_path, current)

    def initialize_job(self, *, pid: int, backend_version: str = "v1") -> None:
        """Create the initial job manifest for a new proof run.

        Inputs:
            pid: Process identifier of the bridge process.
            backend_version: Version string for the bridge contract.

        Returns:
            None.

        Behavior:
            Writes the initial job identity and lifecycle fields before any
            proof work begins.
        """
        if not self.enabled:
            return

        now = time.time()
        existing = self._read_job()
        # Capture the static metadata the extension needs to rehydrate the job.
        data = {
            "jobId": self.job_id,
            "workspaceRoot": self.workspace_root,
            "sourceFile": self.source_file,
            "functionName": self.function_name,
            "proofDir": self.proof_dir,
            "pid": pid,
            "status": "starting",
            "currentStage": None,
            "createdAt": existing.get("createdAt", now),
            "updatedAt": now,
            "backendVersion": backend_version,
            "executionHost": self.execution_host,
            "line": existing.get("line", self.line),
            "column": existing.get("column", self.column),
            "lastError": None,
        }
        with self._lock:
            self._write_json(self.job_path, data)

    def emit(self, event_type: str, **payload: Any) -> None:
        """Append a structured event to the job event log.

        Inputs:
            event_type: Stable event name such as `stage_started`.
            **payload: JSON-serializable event data payload.

        Returns:
            None.

        Behavior:
            Writes a single JSON Lines entry with a timestamp and the current
            stage context.
        """
        if not self.enabled:
            return

        # Normalize the event shape so the extension can process all event
        # types using the same schema.
        event = {
            "type": event_type,
            "jobId": self.job_id,
            "timestamp": time.time(),
            "stage": payload.pop("stage", self.current_stage),
            "data": payload,
        }
        with self._lock:
            with open(self.events_path, "a", encoding="utf-8") as file:
                file.write(json.dumps(event))
                file.write("\n")

    def job_started(self) -> None:
        """Mark the job as running and emit a `job_started` event.

        Inputs:
            None.

        Returns:
            None.
        """
        self._update_job(status="running")
        self.emit("job_started")

    def bootstrap_started(self) -> None:
        """Mark runtime bootstrap as in progress.

        Inputs:
            None.

        Returns:
            None.
        """
        self._update_job(status="bootstrapping")
        self.emit("bootstrap_started")

    def bootstrap_completed(self) -> None:
        """Mark runtime bootstrap as complete.

        Inputs:
            None.

        Returns:
            None.
        """
        self._update_job(status="running")
        self.emit("bootstrap_completed")

    def stage_started(self, stage: str) -> None:
        """Mark a top-level AutoUP stage as started.

        Inputs:
            stage: Stage/class name currently running.

        Returns:
            None.
        """
        self.current_stage = stage
        self._update_job(status="running", currentStage=stage)
        self.emit("stage_started", stage=stage)

    def stage_progress(self, stage: str, message: str, **data: Any) -> None:
        """Emit a non-terminal progress event within a stage.

        Inputs:
            stage: Stage/class name that owns the progress.
            message: Human-readable progress description.
            **data: Optional structured progress fields.

        Returns:
            None.
        """
        self.emit("stage_progress", stage=stage, message=message, **data)

    def stage_completed(self, stage: str, *, success: bool) -> None:
        """Emit a stage completion event.

        Inputs:
            stage: Stage/class name that finished.
            success: Whether the stage completed successfully.

        Returns:
            None.
        """
        self._update_job(currentStage=stage)
        self.emit("stage_completed", stage=stage, success=success)

    def refinement_accepted(
        self,
        *,
        stage: str,
        message: str,
        harness_dir: str,
        root_dir: str,
        target_file_path: str,
        target_function: str,
        harness_file_name: str,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """Emit an accepted-refinement event with a fresh verification summary.

        Inputs:
            stage: Stage that accepted the refinement.
            message: Human-readable acceptance message.
            harness_dir/root_dir/target_file_path/target_function/harness_file_name:
                Paths and identifiers needed to compute the proof summary.
            extra: Optional additional event payload fields.

        Returns:
            None.

        Behavior:
            Rebuilds the proof summary immediately so the extension can show
            progress after each accepted refinement, not just after stage end.
        """
        payload = dict(extra or {})
        payload["message"] = message
        # Attach the latest proof summary directly to the event so the frontend
        # never needs to infer verification state from logs.
        payload["summary"] = build_verification_summary(
            harness_dir=harness_dir,
            root_dir=root_dir,
            target_file_path=target_file_path,
            target_function=target_function,
            harness_file_name=harness_file_name,
            log_file=self.log_file,
        )
        self.emit("refinement_accepted", stage=stage, **payload)

    def summary_updated(
        self,
        *,
        stage: str,
        harness_dir: str,
        root_dir: str,
        target_file_path: str,
        target_function: str,
        harness_file_name: str,
        reason: Optional[str] = None,
        make_result: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Emit a summary update event based on current report files.

        Inputs:
            stage: Stage that produced the summary.
            harness_dir/root_dir/target_file_path/target_function/harness_file_name:
                Paths and identifiers needed to compute the proof summary.
            reason: Optional textual reason for the update.
            make_result: Optional make execution metadata to include.

        Returns:
            dict[str, Any]: The computed summary payload.
        """
        # Recompute the summary from disk so it reflects the latest report files.
        summary = build_verification_summary(
            harness_dir=harness_dir,
            root_dir=root_dir,
            target_file_path=target_file_path,
            target_function=target_function,
            harness_file_name=harness_file_name,
            log_file=self.log_file,
        )
        payload: dict[str, Any] = {"summary": summary}
        if reason:
            payload["reason"] = reason
        if make_result:
            # Preserve a small slice of make metadata for troubleshooting.
            payload["makeResult"] = {
                "status": str(make_result.get("status")),
                "exitCode": make_result.get("exit_code"),
                "elapsedSeconds": make_result.get("elapsed_seconds"),
            }
        self.emit("summary_updated", stage=stage, **payload)
        return summary

    def job_failed(self, message: str, *, stage: Optional[str] = None) -> None:
        """Mark the job as failed and emit a failure event.

        Inputs:
            message: Human-readable failure explanation.
            stage: Optional stage associated with the failure.

        Returns:
            None.
        """
        self._update_job(status="failed", currentStage=stage or self.current_stage)
        self.emit("job_failed", stage=stage or self.current_stage, message=message)

    def job_cancelled(self) -> None:
        """Mark the job as cancelled and emit a cancellation event.

        Inputs:
            None.

        Returns:
            None.
        """
        self._update_job(status="cancelled", currentStage=self.current_stage)
        self.emit("job_cancelled")

    def job_completed(self) -> None:
        """Mark the job as completed and emit a completion event.

        Inputs:
            None.

        Returns:
            None.
        """
        self._update_job(status="completed", currentStage=self.current_stage)
        self.emit("job_completed")


def _load_json(path: str) -> dict[str, Any]:
    """Load a JSON file from disk.

    Inputs:
        path: Path to a JSON file.

    Returns:
        dict[str, Any]: Parsed JSON object.
    """
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _count_properties(harness_dir: str) -> int:
    """Count instrumented properties from `viewer-property.json`.

    Inputs:
        harness_dir: Proof directory containing the build reports.

    Returns:
        int: Number of instrumented properties, or `0` when unavailable.
    """
    property_path = os.path.join(harness_dir, "build", "report", "json", "viewer-property.json")
    if not os.path.exists(property_path):
        return 0
    # Read the property map and count its entries directly.
    data = _load_json(property_path)
    properties = data.get("viewer-property", {}).get("properties", {})
    return len(properties)


def _count_verified_properties(harness_dir: str, property_count: int) -> int:
    """Count verified properties from `viewer-result.json`.

    Inputs:
        harness_dir: Proof directory containing the build reports.
        property_count: Total number of instrumented properties.

    Returns:
        int: Number of verified properties, or `0` when unavailable.

    Behavior:
        Uses the explicit `true` results when available and falls back to
        `property_count - false_results` when the schema only reports failures.
    """
    result_path = os.path.join(harness_dir, "build", "report", "json", "viewer-result.json")
    if not os.path.exists(result_path):
        return 0
    data = _load_json(result_path)
    results = data.get("viewer-result", {}).get("results", {})
    true_results = results.get("true", [])
    false_results = results.get("false", [])

    if isinstance(true_results, list) and true_results:
        return len(true_results)
    if isinstance(false_results, list):
        # Fall back to subtracting current failures from the instrumented total.
        return max(property_count - len(false_results), 0)
    return 0


def _is_harness_file(root_dir: str, harness_dir: str, file_path: str) -> bool:
    """Determine whether a coverage file path belongs to the harness tree.

    Inputs:
        root_dir: Workspace/project root directory.
        harness_dir: Proof directory containing harness artifacts.
        file_path: Path from the coverage report.

    Returns:
        bool: True when the file points into the harness directory.
    """
    abs_harness_dir = os.path.normpath(os.path.abspath(harness_dir))
    if os.path.isabs(file_path):
        abs_file = os.path.normpath(file_path)
    else:
        abs_file = os.path.normpath(os.path.join(root_dir, file_path))
    return abs_file.startswith(abs_harness_dir + os.sep) or abs_file == abs_harness_dir


def _coverage_summary(harness_dir: str, root_dir: str) -> tuple[int, int, float]:
    """Summarize non-harness line coverage from `viewer-coverage.json`.

    Inputs:
        harness_dir: Proof directory containing the build reports.
        root_dir: Workspace/project root directory.

    Returns:
        tuple[int, int, float]: `(hit_lines, total_lines, percentage)`.
    """
    coverage_path = os.path.join(harness_dir, "build", "report", "json", "viewer-coverage.json")
    if not os.path.exists(coverage_path):
        return 0, 0, 0.0

    # Walk the per-function coverage map and skip files generated inside the harness.
    data = _load_json(coverage_path)
    function_coverage = data.get("viewer-coverage", {}).get("function_coverage", {})

    total_hit = 0
    total_lines = 0
    for file_path, funcs in function_coverage.items():
        if _is_harness_file(root_dir, harness_dir, file_path):
            continue
        for stats in funcs.values():
            total_hit += int(stats.get("hit", 0))
            total_lines += int(stats.get("total", 0))

    # Derive percentage only when the denominator exists.
    percentage = (total_hit / total_lines) if total_lines > 0 else 0.0
    return total_hit, total_lines, percentage


def _errors_by_line(harness_dir: str, harness_file_name: str) -> int:
    """Count unresolved error groups by `function:line`.

    Inputs:
        harness_dir: Proof directory containing the build reports.
        harness_file_name: Harness filename used by the debugger parser.

    Returns:
        int: Number of unresolved grouped errors by line.

    Behavior:
        Prefers the existing debugger parser/report logic and falls back to the
        raw false-result count when that parser cannot run.
    """
    try:
        # Reuse the existing debugger parser so the grouping semantics match the
        # rest of AutoUP.
        from debugger.error_report import ErrorReport
        from debugger.parser import extract_errors_and_payload

        harness_file_path = os.path.join(harness_dir, harness_file_name)
        error_clusters = extract_errors_and_payload(harness_file_name, harness_file_path)
        error_report = ErrorReport(error_clusters)
        return len(error_report.errors_by_line)
    except Exception:
        # Fall back to the raw failing-result count so the extension still gets
        # a usable summary when the debugger parser cannot run.
        result_path = os.path.join(harness_dir, "build", "report", "json", "viewer-result.json")
        if not os.path.exists(result_path):
            return 0
        data = _load_json(result_path)
        false_results = data.get("viewer-result", {}).get("results", {}).get("false", [])
        return len(false_results) if isinstance(false_results, list) else 0


def build_verification_summary(
    *,
    harness_dir: str,
    root_dir: str,
    target_file_path: str,
    target_function: str,
    harness_file_name: str,
    log_file: Optional[str] = None,
) -> dict[str, Any]:
    """Build a VS Code summary payload from current proof artifacts.

    Inputs:
        harness_dir: Proof directory containing the build reports.
        root_dir: Workspace/project root directory.
        target_file_path: Source file under proof.
        target_function: Function under proof.
        harness_file_name: Harness filename inside the proof directory.
        log_file: Optional log file path for artifact navigation.

    Returns:
        dict[str, Any]: Structured proof summary used by the extension UI.
    """
    # Gather individual summary dimensions from the current report files.
    property_count = _count_properties(harness_dir)
    verified_count = _count_verified_properties(harness_dir, property_count)
    coverage_hit, coverage_total, coverage_percentage = _coverage_summary(harness_dir, root_dir)
    harness_file_path = os.path.join(harness_dir, harness_file_name)
    makefile_path = os.path.join(harness_dir, "Makefile")
    report_html_path = os.path.join(harness_dir, "build", "report", "html", "index.html")

    # Return the flattened summary shape expected by the extension.
    return {
        "propertiesInstrumented": property_count,
        "propertiesVerified": verified_count,
        "errorsByLine": _errors_by_line(harness_dir, harness_file_name),
        "coverageHit": coverage_hit,
        "coverageTotal": coverage_total,
        "coveragePercentage": coverage_percentage,
        "artifactPaths": {
            "proofDir": harness_dir,
            "harness": harness_file_path if os.path.exists(harness_file_path) else None,
            "makefile": makefile_path if os.path.exists(makefile_path) else None,
            "source": target_file_path if os.path.exists(target_file_path) else None,
            "log": log_file,
            "reportHtml": report_html_path if os.path.exists(report_html_path) else None,
        },
        "targetFunction": target_function,
    }
