/**
 * This file is the VS Code extension entrypoint. It wires together the runtime
 * manager, tree provider, and all user-facing commands.
 */
import * as vscode from "vscode";
import { ProofManager } from "./core/proofManager";
import { ConfigManager } from "./core/configManager";
import { AutoUpInstaller } from "./features/setup/autoUpInstaller";
import { ProviderEnvironmentManager } from "./features/setup/providerEnvironmentManager";
import { PrerequisiteChecker } from "./features/setup/prerequisiteChecker";
import { ProofTreeProvider } from "./features/proofPanel/proofTreeProvider";
import { isActionableError } from "./shared/actionableError";

const OPEN_SETTINGS_ACTION = "Open Settings";
const MANAGE_ENVIRONMENT_ACTION = "Manage Provider Environment Variables";

/**
 * Activate the AutoUP extension.
 *
 * Inputs:
 * - `context`: VS Code extension activation context.
 *
 * Returns:
 * - `Promise<void>`.
 */
export async function activate(context: vscode.ExtensionContext): Promise<void> {
  // Create the core services used by the extension during this session.
  const configManager = new ConfigManager();
  const installer = new AutoUpInstaller(context);
  const envManager = new ProviderEnvironmentManager(context.secrets);
  const prerequisites = new PrerequisiteChecker(context.secrets);
  const proofManager = new ProofManager(context, configManager, installer, prerequisites);
  // Rehydrate any existing proof jobs before the tree view appears.
  try {
    await proofManager.initialize();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error("AutoUP proof restore failed during activation:", error);
    void vscode.window.showWarningMessage(
      `AutoUP could not fully restore prior proof jobs during activation: ${message}`,
    );
  }

  // Create the sidebar tree view that renders all tracked proof jobs.
  const treeProvider = new ProofTreeProvider(proofManager);
  const treeView = vscode.window.createTreeView("autoup.proofExplorer", {
    treeDataProvider: treeProvider,
    showCollapseAll: false,
  });

  // Register every user-facing command and dispose them automatically with the extension.
  context.subscriptions.push(
    proofManager,
    treeView,
    vscode.commands.registerCommand("autoup.createUnitProof", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        return;
      }
      try {
        // Start a proof using the function under the current cursor.
        await proofManager.startProofFromEditor(editor);
      } catch (error) {
        await showCommandError(error);
      }
    }),
    vscode.commands.registerCommand("autoup.manageProviderEnvironmentVariables", async () => {
      await envManager.manage();
    }),
    vscode.commands.registerCommand("autoup.cancelProof", async (target?: unknown) => {
      if (!target) {
        return;
      }
      await proofManager.cancelProof(target);
    }),
    vscode.commands.registerCommand("autoup.deleteProof", async (target?: unknown) => {
      if (!target) {
        return;
      }
      await proofManager.deleteProof(target);
    }),
    vscode.commands.registerCommand("autoup.openHarness", async (target?: unknown) => {
      if (!target) {
        return;
      }
      await proofManager.openHarness(target);
    }),
    vscode.commands.registerCommand("autoup.openMakefile", async (target?: unknown) => {
      if (!target) {
        return;
      }
      await proofManager.openMakefile(target);
    }),
    vscode.commands.registerCommand("autoup.openSourceFile", async (target?: unknown) => {
      if (!target) {
        return;
      }
      await proofManager.openSourceFile(target);
    }),
    vscode.commands.registerCommand("autoup.openLog", async (target?: unknown) => {
      if (!target) {
        return;
      }
      await proofManager.openLog(target);
    }),
    vscode.commands.registerCommand("autoup.openReport", async (target?: unknown) => {
      if (!target) {
        return;
      }
      await proofManager.openReport(target);
    }),
    vscode.commands.registerCommand("autoup.openVulnerabilityReport", async (target?: unknown) => {
      if (!target) {
        return;
      }
      await proofManager.openVulnerabilityReport(target);
    }),
    vscode.commands.registerCommand("autoup.revealProofFolder", async (target?: unknown) => {
      if (!target) {
        return;
      }
      await proofManager.revealProofFolder(target);
    }),
    vscode.commands.registerCommand("autoup.refreshProofs", async () => {
      await proofManager.refresh();
    }),
    vscode.commands.registerCommand("autoup.showProofOutput", (target?: unknown) => {
      if (!target) {
        return;
      }
      proofManager.showOutput(target);
    }),
  );
}

export function deactivate(): void {
  /**
   * Deactivate the AutoUP extension.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `void`.
   */
  // VS Code disposes registered subscriptions automatically.
}

async function showCommandError(error: unknown): Promise<void> {
  const message = error instanceof Error ? error.message : String(error);
  if (!isActionableError(error)) {
    void vscode.window.showErrorMessage(message);
    return;
  }

  const selection = await vscode.window.showErrorMessage(message, ...error.actions);
  if (selection === OPEN_SETTINGS_ACTION) {
    await vscode.commands.executeCommand("workbench.action.openSettings", "autoup");
    return;
  }
  if (selection === MANAGE_ENVIRONMENT_ACTION) {
    await vscode.commands.executeCommand("autoup.manageProviderEnvironmentVariables");
  }
}
