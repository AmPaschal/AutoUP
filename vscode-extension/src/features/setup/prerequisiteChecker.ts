/**
 * This file validates that the execution host can actually run AutoUP before a
 * proof is launched.
 */
import * as vscode from "vscode";
import * as path from "path";
import type { AutoUpConfig, ExecutionHost, ResolvedModelProfile, ResolvedPrerequisites, RuntimeInstallation } from "../../core/types";
import { ActionableError } from "../../shared/actionableError";
import { getEnvironmentSecretKey, resolveSelectedModelProfile } from "../../shared/modelProfiles";
import { runCommand } from "../../shared/processUtils";

const OPEN_SETTINGS_ACTION = "Open Settings";
const MANAGE_ENVIRONMENT_ACTION = "Manage Provider Environment Variables";
const RECOVERY_ACTIONS = [OPEN_SETTINGS_ACTION, MANAGE_ENVIRONMENT_ACTION] as const;

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
    const selectedProfile = this.resolveProfile(config);
    const validatedEnvVars = await this.resolveEnvironmentVariables(selectedProfile);
    const resolvedProvider = await this.validateSelectedModel(installation, selectedProfile, validatedEnvVars);

    // Return the normalized launch prerequisites used by the proof runner.
    return {
      selectedProfile,
      resolvedProvider,
      validatedEnvVars,
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

  private resolveProfile(config: AutoUpConfig): ResolvedModelProfile {
    try {
      return resolveSelectedModelProfile(config);
    } catch (error) {
      throw this.toActionableError(this.getErrorMessage(error));
    }
  }

  private async resolveEnvironmentVariables(profile: ResolvedModelProfile): Promise<Record<string, string>> {
    const values: Record<string, string> = {};
    const missing: string[] = [];
    for (const envVar of profile.envVars) {
      const value = await this.secrets.get(getEnvironmentSecretKey(envVar));
      if (value === undefined) {
        missing.push(envVar);
        continue;
      }
      values[envVar] = value;
    }
    if (missing.length > 0) {
      throw this.toActionableError(
        `AutoUP model profile '${profile.name}' is missing stored environment variables: ${missing.join(", ")}.`,
      );
    }
    return values;
  }

  private async validateSelectedModel(
    installation: RuntimeInstallation,
    profile: ResolvedModelProfile,
    validatedEnvVars: Record<string, string>,
  ): Promise<string> {
    const bridgePath = path.join(installation.backendRoot, "src", "autoup_vscode.py");
    const validationResult = await runCommand(
      installation.pythonPath,
      [
        bridgePath,
        "validate-model",
        "--model",
        profile.modelName,
        "--provider",
        profile.provider,
      ],
      {
        cwd: installation.backendRoot,
        env: {
          ...process.env,
          ...validatedEnvVars,
        },
      },
    );

    let parsedOutput: { ok?: boolean; resolved_provider?: string; error?: string };
    try {
      parsedOutput = JSON.parse(validationResult.stdout || "{}");
    } catch {
      throw this.toActionableError(
        `AutoUP could not validate model '${profile.modelName}': invalid validator response.`,
      );
    }

    if (validationResult.exitCode !== 0 || !parsedOutput.ok || !parsedOutput.resolved_provider) {
      const details = parsedOutput.error || validationResult.stderr || "unknown model validation error";
      throw this.toActionableError(
        `AutoUP could not validate model '${profile.modelName}': ${details}`,
      );
    }
    return parsedOutput.resolved_provider;
  }

  private toActionableError(message: string): ActionableError {
    return new ActionableError(message, RECOVERY_ACTIONS);
  }

  private getErrorMessage(error: unknown): string {
    if (error instanceof Error) {
      return error.message;
    }
    return String(error);
  }
}
