/**
 * This file centralizes reads of the extension configuration so the rest of the
 * codebase does not scatter raw `workspace.getConfiguration()` calls.
 */
import * as vscode from "vscode";
import type { AutoUpConfig } from "./types";
import { normalizeModelProfiles } from "../shared/modelProfiles";

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
      modelProfiles: normalizeModelProfiles(config.get<unknown>("modelProfiles", [])),
      selectedModelProfile: config.get<string | null>("selectedModelProfile", null),
      containerEngine: config.get<"auto" | "docker" | "apptainer">("containerEngine", "auto"),
      scopeBound: config.get<number | null>("scopeBound", 1),
      scopeTimeBudget: config.get<number | null>("scopeTimeBudget", null),
      makeTimeout: config.get<number>("makeTimeout", 900),
      maxConcurrentJobs: config.get<number>("maxConcurrentJobs", 5),
      proofsRoot: config.get<string>("proofsRoot", "cbmc/proofs"),
    };
  }
}
