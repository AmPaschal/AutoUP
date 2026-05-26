import * as vscode from "vscode";
import { GLOBAL_ENV_SECRET_PREFIX, getEnvironmentSecretKey } from "../../shared/modelProfiles";

const ADD_LABEL = "Add Environment Variable";
const LIST_LABEL = "List Environment Variables";
const CLEAR_LABEL = "Clear Environment Variable";

export class ProviderEnvironmentManager {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  async manage(): Promise<void> {
    const selection = await vscode.window.showQuickPick(
      [ADD_LABEL, LIST_LABEL, CLEAR_LABEL],
      { placeHolder: "Manage provider environment variables" },
    );
    if (selection === ADD_LABEL) {
      await this.addEnvironmentVariable();
      return;
    }
    if (selection === LIST_LABEL) {
      await this.listEnvironmentVariables();
      return;
    }
    if (selection === CLEAR_LABEL) {
      await this.clearEnvironmentVariable();
    }
  }

  async addEnvironmentVariable(): Promise<void> {
    const key = (await vscode.window.showInputBox({
      prompt: "Enter the environment variable name to store for AutoUP",
      ignoreFocusOut: true,
    }))?.trim();
    if (!key) {
      return;
    }
    const value = await vscode.window.showInputBox({
      prompt: `Enter the value for ${key}`,
      password: true,
      ignoreFocusOut: true,
    });
    if (value === undefined) {
      return;
    }
    await this.secrets.store(getEnvironmentSecretKey(key), value);
    void vscode.window.showInformationMessage(`Stored AutoUP environment variable ${key}.`);
  }

  async listEnvironmentVariables(): Promise<void> {
    const keys = await this.getStoredEnvironmentVariableNames();
    if (keys.length === 0) {
      void vscode.window.showInformationMessage("No AutoUP provider environment variables are stored.");
      return;
    }
    await vscode.window.showQuickPick(
      keys.map((key) => ({ label: key, description: "Stored" })),
      {
        placeHolder: "Stored AutoUP provider environment variables",
        canPickMany: false,
      },
    );
  }

  async clearEnvironmentVariable(): Promise<void> {
    const keys = await this.getStoredEnvironmentVariableNames();
    if (keys.length === 0) {
      void vscode.window.showInformationMessage("No AutoUP provider environment variables are stored.");
      return;
    }
    const selection = await vscode.window.showQuickPick(
      keys.map((key) => ({ label: key })),
      { placeHolder: "Select an AutoUP environment variable to clear" },
    );
    if (!selection) {
      return;
    }
    await this.secrets.delete(getEnvironmentSecretKey(selection.label));
    void vscode.window.showInformationMessage(`Cleared AutoUP environment variable ${selection.label}.`);
  }

  async getStoredEnvironmentVariableNames(): Promise<string[]> {
    const keys = await this.secrets.keys();
    return keys
      .filter((key) => key.startsWith(GLOBAL_ENV_SECRET_PREFIX))
      .map((key) => key.slice(GLOBAL_ENV_SECRET_PREFIX.length))
      .sort((left, right) => left.localeCompare(right));
  }
}
