/**
 * This file validates that the execution host can actually run AutoUP before a
 * proof is launched.
 */
import * as vscode from "vscode";
import type { AutoUpConfig, ExecutionHost, ResolvedPrerequisites, RuntimeInstallation } from "../../core/types";
import { runCommand } from "../../shared/processUtils";

/**
 * Checks Python, container-engine, and API-key prerequisites.
 */
export class PrerequisiteChecker {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  /**
   * Validate the runtime and gather all secrets needed to launch a proof.
   *
   * Inputs:
   * - `config`: Current AutoUP extension settings.
   * - `executionHost`: Host label describing where the backend will run.
   * - `installation`: Prepared runtime installation from the installer.
   *
   * Returns:
   * - `Promise<ResolvedPrerequisites>`: Fully resolved execution prerequisites.
   */
  async ensureReady(
    config: AutoUpConfig,
    executionHost: ExecutionHost,
    installation: RuntimeInstallation,
  ): Promise<ResolvedPrerequisites> {
    if (executionHost === "unsupported-windows") {
      throw new Error("AutoUP proof generation is not supported on Windows in this extension.");
    }

    // Verify the installed runtime is callable before checking higher-level requirements.
    const pythonCheck = await runCommand(installation.pythonPath, ["--version"]);
    if (pythonCheck.exitCode !== 0) {
      throw new Error("AutoUP Python runtime is unavailable.");
    }

    // Resolve the container engine next because launch configuration depends on it.
    const containerEngine = await this.resolveContainerEngine(config, executionHost);
    const apiKey = await this.ensureApiKey();

    // Return the normalized launch prerequisites used by the proof runner.
    return {
      apiKey,
      apiKeyEnvVar: "OPENAI_API_KEY",
      containerEngine,
    };
  }

  private async resolveContainerEngine(
    config: AutoUpConfig,
    executionHost: ExecutionHost,
  ): Promise<"docker" | "apptainer"> {
    /**
     * Resolve the container engine that this proof job should use.
     *
     * Inputs:
     * - `config`: Current extension configuration.
     * - `executionHost`: Normalized execution-host label.
     *
     * Returns:
     * - `Promise<"docker" | "apptainer">`: Resolved container engine.
     */
    // macOS is intentionally Docker-only in v1.
    if (executionHost === "local-macos") {
      const docker = await runCommand("docker", ["info"]);
      if (docker.exitCode !== 0) {
        throw new Error("Docker is required on macOS for AutoUP proof execution.");
      }
      return "docker";
    }

    // Respect an explicit Docker selection and fail fast when it is unavailable.
    if (config.containerEngine === "docker") {
      const docker = await runCommand("docker", ["info"]);
      if (docker.exitCode !== 0) {
        throw new Error("Configured container engine 'docker' is not available.");
      }
      return "docker";
    }

    // Respect an explicit Apptainer selection and fail fast when it is unavailable.
    if (config.containerEngine === "apptainer") {
      const apptainer = await runCommand("apptainer", ["--version"]);
      if (apptainer.exitCode !== 0) {
        throw new Error("Configured container engine 'apptainer' is not available.");
      }
      return "apptainer";
    }

    // In `auto` mode, prefer Docker and fall back to Apptainer on Linux hosts.
    const docker = await runCommand("docker", ["info"]);
    if (docker.exitCode === 0) {
      return "docker";
    }

    const apptainer = await runCommand("apptainer", ["--version"]);
    if (apptainer.exitCode === 0) {
      return "apptainer";
    }

    throw new Error("Neither Docker nor Apptainer is available on the execution host.");
  }

  private async ensureApiKey(): Promise<string> {
    /**
     * Resolve the API key from secrets, environment, or a prompt.
     *
     * Inputs:
     * - None.
     *
     * Returns:
     * - `Promise<string>`: API key used for proof execution.
     */
    // Prefer secret storage so the key is not kept in plain workspace settings.
    const stored = await this.secrets.get("autoup.openaiApiKey");
    if (stored) {
      return stored;
    }

    // Accept an environment-provided key so remote and CI-like environments can
    // inject credentials without prompting.
    const envKey = process.env.OPENAI_API_KEY;
    if (envKey) {
      return envKey;
    }

    // Prompt only as a last resort when no stored or injected key is available.
    const entered = await vscode.window.showInputBox({
      password: true,
      prompt: "Enter the OpenAI API key to use for AutoUP proof generation",
      ignoreFocusOut: true,
    });
    if (!entered) {
      throw new Error("An OpenAI API key is required to start AutoUP proofs.");
    }

    // Persist the entered key so the next launch can reuse it without prompting.
    await this.secrets.store("autoup.openaiApiKey", entered);
    return entered;
  }
}
