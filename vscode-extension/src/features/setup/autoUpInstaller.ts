/**
 * This file owns runtime bootstrap for the extension-managed AutoUP backend.
 */
import { createHash } from "crypto";
import * as fs from "fs/promises";
import * as path from "path";
import * as vscode from "vscode";
import type { AutoUpConfig, RuntimeInstallation } from "../../core/types";
import { runCommand } from "../../shared/processUtils";

const INSTALL_STATE_FILE = "runtime-state.json";
const BACKEND_ASSETS = [
  "src",
  "prompts",
  "container",
  "requirements.txt",
  "model_pricing.json",
];

/**
 * Prepares the backend assets and Python virtual environment used by the extension.
 */
export class AutoUpInstaller {
  constructor(private readonly context: vscode.ExtensionContext) {}

  /**
   * Ensure the backend assets and Python runtime exist in extension-managed storage.
   *
   * Inputs:
   * - `_config`: Current AutoUP configuration. It is unused today but kept so the
   *   installer signature can grow without changing callers.
   *
   * Returns:
   * - `Promise<RuntimeInstallation>`: Paths to the backend root and Python binary.
   */
  async ensureInstalled(_config: AutoUpConfig): Promise<RuntimeInstallation> {
    return await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "Preparing AutoUP runtime",
        cancellable: false,
      },
      async (progress) => {
        // Compute all managed storage locations up front so later steps can reuse them.
        const backendRoot = path.join(this.context.globalStorageUri.fsPath, "backend");
        const venvRoot = path.join(this.context.globalStorageUri.fsPath, "venv");
        const pythonPath = path.join(venvRoot, "bin", "python");

        // Ensure the global storage directory exists before copying or creating files in it.
        await fs.mkdir(this.context.globalStorageUri.fsPath, { recursive: true });
        const sourceRoot = await this.resolveBackendSourceRoot();
        const bundleFingerprint = await this.computeBundleFingerprint(sourceRoot);
        const installStatePath = path.join(this.context.globalStorageUri.fsPath, INSTALL_STATE_FILE);
        const installState = await this.readInstallState(installStatePath);
        const backendNeedsSync =
          !(await this.exists(backendRoot)) || installState?.bundleFingerprint !== bundleFingerprint;
        if (backendNeedsSync) {
          progress.report({ message: "Syncing bundled backend assets" });
          await fs.rm(backendRoot, { recursive: true, force: true });
          await this.syncBackendAssets(sourceRoot, backendRoot);
        }

        // Create the private venv only once.
        const pythonExists = await this.exists(pythonPath);
        if (!pythonExists) {
          progress.report({ message: "Creating Python virtual environment" });
          const createVenv = await runCommand("python3", ["-m", "venv", venvRoot]);
          if (createVenv.exitCode !== 0) {
            throw new Error(createVenv.stderr || "Failed to create AutoUP virtual environment.");
          }
        }

        // Reinstall Python dependencies when the bundled backend changes or the
        // private interpreter had to be recreated.
        const dependenciesNeedInstall = !pythonExists || installState?.bundleFingerprint !== bundleFingerprint;
        if (dependenciesNeedInstall) {
          progress.report({ message: "Installing AutoUP Python dependencies" });
          const install = await runCommand(
            pythonPath,
            ["-m", "pip", "install", "-r", path.join(backendRoot, "requirements.txt")],
            { cwd: backendRoot },
          );
          if (install.exitCode !== 0) {
            throw new Error(install.stderr || "Failed to install AutoUP Python dependencies.");
          }
          await fs.writeFile(
            installStatePath,
            `${JSON.stringify({ bundleFingerprint, installedAt: Date.now() }, null, 2)}\n`,
            "utf-8",
          );
        }

        // Return the stable runtime paths that the proof runner will launch.
        return {
          backendRoot,
          pythonPath,
        };
      },
    );
  }

  private async syncBackendAssets(sourceRoot: string, destinationRoot: string): Promise<void> {
    /**
     * Copy the backend source/assets from the repository into extension storage.
     *
     * Inputs:
     * - `destinationRoot`: Root directory for the managed backend copy.
     *
     * Returns:
     * - `Promise<void>`.
     */
    await fs.mkdir(destinationRoot, { recursive: true });
    // Copy every required backend asset so the extension can run without a
    // separate user-managed AutoUP checkout.
    for (const asset of BACKEND_ASSETS) {
      const source = path.join(sourceRoot, asset);
      const destination = path.join(destinationRoot, asset);
      await fs.cp(source, destination, { recursive: true, force: true });
    }
  }

  private async resolveBackendSourceRoot(): Promise<string> {
    /**
     * Resolve where the AutoUP backend assets should be copied from.
     *
     * Inputs:
     * - None.
     *
     * Returns:
     * - `Promise<string>`: Directory containing the backend asset set.
     */
    const packagedRoot = path.join(this.context.extensionUri.fsPath, ".autoup-bundle");
    // Prefer the bundled backend directory because a VSIX install no longer
    // lives next to the full repository checkout.
    if (await this.hasBackendAssets(packagedRoot)) {
      return packagedRoot;
    }

    const developmentRoot = path.resolve(this.context.extensionUri.fsPath, "..");
    // Fall back to the repository layout when running the extension from source.
    if (await this.hasBackendAssets(developmentRoot)) {
      return developmentRoot;
    }

    throw new Error("Bundled AutoUP backend assets were not found in the extension package.");
  }

  private async hasBackendAssets(root: string): Promise<boolean> {
    /**
     * Check whether a directory contains the full backend asset set.
     *
     * Inputs:
     * - `root`: Candidate backend root directory.
     *
     * Returns:
     * - `Promise<boolean>`: True when all required backend assets exist.
     */
    for (const asset of BACKEND_ASSETS) {
      if (!(await this.exists(path.join(root, asset)))) {
        return false;
      }
    }
    return true;
  }

  private async exists(targetPath: string): Promise<boolean> {
    /**
     * Check whether a path exists.
     *
     * Inputs:
     * - `targetPath`: Path to inspect.
     *
     * Returns:
     * - `Promise<boolean>`: True when the path exists.
     */
    try {
      await fs.stat(targetPath);
      return true;
    } catch {
      return false;
    }
  }

  private async computeBundleFingerprint(sourceRoot: string): Promise<string> {
    /**
     * Compute a content fingerprint for the bundled backend asset set.
     *
     * Inputs:
     * - `sourceRoot`: Root directory that contains the bundled backend assets.
     *
     * Returns:
     * - `Promise<string>`: Stable content hash for the current backend bundle.
     */
    const hash = createHash("sha256");
    for (const asset of BACKEND_ASSETS) {
      await this.hashPath(hash, sourceRoot, asset);
    }
    return hash.digest("hex");
  }

  private async hashPath(
    hash: ReturnType<typeof createHash>,
    root: string,
    relativePath: string,
  ): Promise<void> {
    /**
     * Feed a file or directory tree into the backend bundle fingerprint hash.
     *
     * Inputs:
     * - `hash`: Incremental crypto hash used for the fingerprint.
     * - `root`: Backend source root directory.
     * - `relativePath`: Asset path relative to that root.
     *
     * Returns:
     * - `Promise<void>`.
     */
    const absolutePath = path.join(root, relativePath);
    const stats = await fs.stat(absolutePath);
    hash.update(relativePath);
    hash.update(stats.isDirectory() ? "dir" : "file");
    if (stats.isDirectory()) {
      const entries = (await fs.readdir(absolutePath)).sort();
      for (const entry of entries) {
        await this.hashPath(hash, root, path.join(relativePath, entry));
      }
      return;
    }

    hash.update(await fs.readFile(absolutePath));
  }

  private async readInstallState(
    installStatePath: string,
  ): Promise<{ bundleFingerprint: string; installedAt: number } | null> {
    /**
     * Read the managed runtime install-state record, if one exists.
     *
     * Inputs:
     * - `installStatePath`: JSON file tracking the installed bundle fingerprint.
     *
     * Returns:
     * - `Promise<{ bundleFingerprint: string; installedAt: number } | null>`.
     */
    try {
      const raw = await fs.readFile(installStatePath, "utf-8");
      const parsed = JSON.parse(raw) as { bundleFingerprint?: string; installedAt?: number };
      if (typeof parsed.bundleFingerprint !== "string" || typeof parsed.installedAt !== "number") {
        return null;
      }
      return {
        bundleFingerprint: parsed.bundleFingerprint,
        installedAt: parsed.installedAt,
      };
    } catch {
      return null;
    }
  }
}
