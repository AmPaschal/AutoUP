/**
 * This file maps proof jobs, metrics, and actions into `TreeItem` instances.
 */
import * as vscode from "vscode";
import type { ProofJob, VerificationSummary } from "../../core/types";

/**
 * Union type for all nodes rendered in the proof tree.
 */
export type TreeNode =
  | { kind: "job"; job: ProofJob }
  | { kind: "metric"; label: string; description?: string }
  | { kind: "action"; label: string; command: string; jobId: string; contextValue: string };

/**
 * UI wrapper that converts a proof-tree node into a concrete VS Code tree item.
 */
export class ProofTreeItem extends vscode.TreeItem {
  constructor(
    public readonly node: TreeNode,
    collapsibleState: vscode.TreeItemCollapsibleState,
  ) {
    super(labelForNode(node), collapsibleState);

    // Configure root job rows with status, icon, and output-channel navigation.
    if (node.kind === "job") {
      this.description = describeJob(node.job);
      this.contextValue = contextValueForJob(node.job);
      this.iconPath = iconForStatus(node.job.status);
      this.command = {
        command: "autoup.showProofOutput",
        title: "Show Proof Output",
        arguments: [node.job.id],
      };
      this.tooltip = `${node.job.functionName} (${node.job.status})`;
      return;
    }

    // Configure read-only metric rows with a simple label/description pair.
    if (node.kind === "metric") {
      this.description = node.description;
      this.contextValue = "proofMetric";
      return;
    }

    // Configure artifact/action rows as clickable commands.
    this.contextValue = node.contextValue;
    this.command = {
      command: node.command,
      title: node.label,
      arguments: [node.jobId],
    };
    this.iconPath = new vscode.ThemeIcon("go-to-file");
  }
}

function labelForNode(node: TreeNode): string {
  /**
   * Convert a tree node into its primary label.
   *
   * Inputs:
   * - `node`: Tree node to label.
   *
   * Returns:
   * - `string`: Visible label text.
   */
  if (node.kind === "job") {
    return node.job.functionName;
  }
  return node.label;
}

function describeJob(job: ProofJob): string {
  /**
   * Build the secondary description text for a job row.
   *
   * Inputs:
   * - `job`: Proof job being rendered.
   *
   * Returns:
   * - `string`: Job status text, optionally including the current stage.
   */
  if (job.currentStage) {
    return `${job.status} · ${job.currentStage}`;
  }
  return job.status;
}

function iconForStatus(status: ProofJob["status"]): vscode.ThemeIcon {
  /**
   * Map a proof status to a VS Code theme icon.
   *
   * Inputs:
   * - `status`: Current proof job status.
   *
   * Returns:
   * - `vscode.ThemeIcon`: Icon used in the tree view.
   */
  switch (status) {
    case "completed":
      return new vscode.ThemeIcon("pass");
    case "failed":
    case "interrupted":
      return new vscode.ThemeIcon("error");
    case "cancelled":
      return new vscode.ThemeIcon("circle-slash");
    case "queued":
      return new vscode.ThemeIcon("clock");
    default:
      return new vscode.ThemeIcon("loading~spin");
  }
}

function contextValueForJob(job: ProofJob): string {
  /**
   * Map a proof job to the context key used by tree-view menu contributions.
   *
   * Inputs:
   * - `job`: Proof job being rendered.
   *
   * Returns:
   * - `string`: Context value consumed by `package.json` menu rules.
   */
  if (["queued", "starting", "bootstrapping", "running"].includes(job.status)) {
    return "proofJobCancellable";
  }
  if (["completed", "failed", "cancelled", "interrupted"].includes(job.status)) {
    return "proofJobDeletable";
  }
  return "proofJob";
}

export function summaryMetricNodes(job: ProofJob, summary: VerificationSummary): TreeNode[] {
  /**
   * Build the child metric/action nodes for a proof job.
   *
   * Inputs:
   * - `job`: Parent proof job.
   * - `summary`: Latest verification summary for that job.
   *
   * Returns:
   * - `TreeNode[]`: Metric rows followed by artifact actions.
   */
  const coveragePct = (summary.coveragePercentage * 100).toFixed(1);
  return [
    {
      kind: "metric",
      label: "Properties",
      description: `${summary.propertiesVerified}/${summary.propertiesInstrumented}`,
    },
    {
      kind: "metric",
      label: "Errors By Line",
      description: String(summary.errorsByLine),
    },
    {
      kind: "metric",
      label: "Coverage",
      description: `${summary.coverageHit}/${summary.coverageTotal} (${coveragePct}%)`,
    },
    {
      kind: "action",
      label: "Open Harness",
      command: "autoup.openHarness",
      jobId: job.id,
      contextValue: "proofActionHarness",
    },
    {
      kind: "action",
      label: "Open Makefile",
      command: "autoup.openMakefile",
      jobId: job.id,
      contextValue: "proofActionMakefile",
    },
    {
      kind: "action",
      label: "Open Source File",
      command: "autoup.openSourceFile",
      jobId: job.id,
      contextValue: "proofActionSource",
    },
    {
      kind: "action",
      label: "Open Log",
      command: "autoup.openLog",
      jobId: job.id,
      contextValue: "proofActionLog",
    },
    {
      kind: "action",
      label: "Open Report",
      command: "autoup.openReport",
      jobId: job.id,
      contextValue: "proofActionReport",
    },
  ];
}
