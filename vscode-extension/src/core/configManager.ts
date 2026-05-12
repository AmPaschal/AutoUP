/**
 * This file centralizes reads of the extension configuration so the rest of the
 * codebase does not scatter raw `workspace.getConfiguration()` calls.
 */
import * as vscode from "vscode";
import type { AutoUpConfig } from "./types";

/**
 * Provides typed access to AutoUP extension settings.
 */
export class ConfigManager {
  /**
   * Read and normalize the current AutoUP settings from the workspace.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `AutoUpConfig`: The current extension configuration with defaults applied.
   */
  getConfig(): AutoUpConfig {
    // Read the shared `autoup.*` configuration section once for this call.
    const config = vscode.workspace.getConfiguration("autoup");
    // Return the exact typed shape used throughout the extension.
    return {
      model: config.get<string>("model", "gpt-5.3-codex"),
      containerEngine: config.get<"auto" | "docker" | "apptainer">("containerEngine", "auto"),
      scopeBound: config.get<number | null>("scopeBound", null),
      scopeTimeBudget: config.get<number | null>("scopeTimeBudget", null),
      maxConcurrentJobs: config.get<number>("maxConcurrentJobs", 2),
      proofsRoot: config.get<string>("proofsRoot", "cbmc/proofs"),
    };
  }
}
