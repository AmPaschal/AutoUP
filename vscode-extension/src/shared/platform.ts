/**
 * This file isolates execution-host detection so other modules do not need to
 * know how VS Code remote mode is exposed.
 */
import * as vscode from "vscode";
import type { ExecutionHost } from "../core/types";

/**
 * Detect where the workspace extension host is running.
 *
 * Inputs:
 * - None.
 *
 * Returns:
 * - `ExecutionHost`: The normalized host label used in manifests and process
 *   launch decisions.
 */
export function detectExecutionHost(): ExecutionHost {
  return normalizeExecutionHost(vscode.env.remoteName, process.platform);
}

/**
 * Normalize VS Code remote/platform information into an execution-host label.
 *
 * Inputs:
 * - `remoteName`: VS Code remote authority name, when present.
 * - `platformName`: Node platform string for the current extension host.
 *
 * Returns:
 * - `ExecutionHost`: Normalized host label used by the extension.
 */
export function normalizeExecutionHost(
  remoteName: string | undefined,
  platformName: NodeJS.Platform,
): ExecutionHost {
  // Remote-SSH takes priority because the backend runs on the remote host even
  // when the user is sitting at a macOS client.
  if (remoteName === "ssh-remote") {
    return "remote-ssh-linux";
  }
  // Treat local macOS separately because v1 only supports Docker there.
  if (platformName === "darwin") {
    return "local-macos";
  }
  // Windows is intentionally unsupported in v1 and should fail explicitly.
  if (platformName === "win32") {
    return "unsupported-windows";
  }
  return "local-linux";
}
