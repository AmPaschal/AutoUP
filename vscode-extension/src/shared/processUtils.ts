/**
 * This file wraps simple child-process execution used by setup and preflight
 * checks. It intentionally stays small so callers do not duplicate spawn logic.
 */
import { spawn } from "child_process";

export interface CommandResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

/**
 * Run a command and capture its complete stdout/stderr streams.
 *
 * Inputs:
 * - `command`: Executable to launch.
 * - `args`: Argument array passed to the executable.
 * - `options.cwd`: Optional working directory.
 * - `options.env`: Optional environment overrides.
 *
 * Returns:
 * - `Promise<CommandResult>`: Exit code plus captured stdout/stderr text.
 */
export async function runCommand(
  command: string,
  args: string[],
  options: { cwd?: string; env?: NodeJS.ProcessEnv } = {},
): Promise<CommandResult> {
  return await new Promise((resolve, reject) => {
    // Spawn the command with piped output so the caller gets the full result.
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    // Accumulate stdout and stderr so callers can inspect the full command result.
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    // Surface process startup failures and resolve cleanly when the process exits.
    child.on("error", reject);
    child.on("close", (exitCode) => {
      resolve({
        exitCode: exitCode ?? -1,
        stdout,
        stderr,
      });
    });
  });
}
