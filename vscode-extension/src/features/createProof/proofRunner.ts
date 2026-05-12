/**
 * This file launches the Python bridge process for a single proof job.
 */
import * as fs from "fs";
import { spawn, type ChildProcess } from "child_process";
import * as path from "path";
import type { AutoUpConfig, ExecutionHost, ProofJob, ResolvedPrerequisites, RuntimeInstallation } from "../../core/types";

/**
 * All runtime inputs required to start the Python bridge for one proof job.
 */
export interface ProofRunnerOptions {
  runtime: RuntimeInstallation;
  config: AutoUpConfig;
  prerequisites: ResolvedPrerequisites;
  executionHost: ExecutionHost;
}

/**
 * Start the Python bridge process for a proof job.
 *
 * Inputs:
 * - `job`: In-memory proof job definition.
 * - `options`: Runtime/config/prerequisite information needed to launch it.
 *
 * Returns:
 * - `ChildProcess`: The launched detached bridge process.
 */
export function startProofProcess(
  job: ProofJob,
  options: ProofRunnerOptions,
): ChildProcess {
  // Build the bridge path inside the managed backend copy.
  const bridgePath = path.join(options.runtime.backendRoot, "src", "autoup_vscode.py");
  // Forward all proof metadata and configuration as bridge arguments.
  const args = [
    bridgePath,
    "start",
    "--workspace",
    job.workspaceRoot,
    "--source",
    job.filePath,
    "--line",
    String(job.line),
    "--column",
    String(job.column),
    "--proof-dir",
    job.proofDir,
    "--job-id",
    job.id,
    "--function-name",
    job.functionName,
    "--container-engine",
    options.prerequisites.containerEngine,
    "--model",
    options.config.model,
    "--make-timeout",
    String(options.config.makeTimeout),
    "--log-file",
    job.logFile,
    "--metrics-file",
    job.metricsFile,
    "--execution-host",
    options.executionHost,
  ];

  // Forward optional scope settings only when the user configured them.
  if (options.config.scopeBound !== null) {
    args.push("--scope-bound", String(options.config.scopeBound));
  }
  if (options.config.scopeTimeBudget !== null) {
    args.push("--scope-time-budget", String(options.config.scopeTimeBudget));
  }

  // Ensure the log file exists before the detached process redirects stdout/stderr into it.
  fs.mkdirSync(path.dirname(job.logFile), { recursive: true });
  const logFd = fs.openSync(job.logFile, "a");

  // Launch the Python bridge as a detached background process so it can
  // survive VS Code window reloads and Remote-SSH client disconnects.
  let child: ChildProcess;
  try {
    child = spawn(options.runtime.pythonPath, args, {
      cwd: options.runtime.backendRoot,
      env: {
        ...process.env,
        [options.prerequisites.apiKeyEnvVar]: options.prerequisites.apiKey,
      },
      detached: true,
      stdio: ["ignore", logFd, logFd],
    });
  } finally {
    // Close the parent's descriptor copy and let the background process keep
    // its own open handles.
    fs.closeSync(logFd);
  }

  child.unref();

  return child;
}
