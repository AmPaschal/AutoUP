/**
 * This file connects the proof manager's state to the VS Code Tree View API.
 */
import * as fs from "fs/promises";
import * as path from "path";
import * as vscode from "vscode";
import { ProofTreeItem, type TreeNode } from "./proofTreeItem";
import { ProofManager } from "../../core/proofManager";
import type { ArtifactPaths, ProofJob, VerificationSummary } from "../../core/types";

/**
 * Tree data provider for the AutoUP proof explorer sidebar.
 */
export class ProofTreeProvider implements vscode.TreeDataProvider<TreeNode> {
  private readonly onDidChangeTreeDataEmitter = new vscode.EventEmitter<TreeNode | undefined>();
  readonly onDidChangeTreeData = this.onDidChangeTreeDataEmitter.event;

  constructor(private readonly proofManager: ProofManager) {
    // Refresh the whole tree whenever the proof manager reports a state change.
    this.proofManager.onDidChangeProofs(() => {
      this.onDidChangeTreeDataEmitter.fire(undefined);
    });
  }

  /**
   * Convert a tree node into a VS Code `TreeItem`.
   *
   * Inputs:
   * - `element`: Tree node to render.
   *
   * Returns:
   * - `vscode.TreeItem`: UI item for the tree.
   */
  getTreeItem(element: TreeNode): vscode.TreeItem {
    if (element.kind === "job") {
      return new ProofTreeItem(element, vscode.TreeItemCollapsibleState.Collapsed);
    }
    return new ProofTreeItem(element, vscode.TreeItemCollapsibleState.None);
  }

  /**
   * Return the children for the root or a specific proof job node.
   *
   * Inputs:
   * - `element`: Optional parent node. When omitted, the root jobs are returned.
   *
   * Returns:
   * - `TreeNode[]`: Child nodes for the requested element.
   */
  async getChildren(element?: TreeNode): Promise<TreeNode[]> {
    // At the root level, render one node per tracked proof job.
    if (!element) {
      return this.proofManager.getAllJobs().map((job) => ({ kind: "job", job }));
    }
    // Only job nodes have children in this tree.
    if (element.kind !== "job") {
      return [];
    }

    // When no summary exists yet, show a minimal status row instead of an empty expansion.
    return await buildJobChildren(element.job);
  }
}

async function buildJobChildren(job: ProofJob): Promise<TreeNode[]> {
  /**
   * Build child rows for a proof job from both summary state and on-disk
   * artifact availability.
   *
   * Inputs:
   * - `job`: Proof job being expanded in the tree.
   *
   * Returns:
   * - `Promise<TreeNode[]>`: Status/metric rows plus currently available
   *   artifact actions.
   */
  const children: TreeNode[] = [
    {
      kind: "metric",
      label: "Status",
      description: job.currentStage ? `${job.status} · ${job.currentStage}` : job.status,
    },
  ];

  if (job.verificationSummary) {
    children.push(...(await summaryMetricRows(job, job.verificationSummary)));
  }

  children.push(...(await availableArtifactActionNodes(job)));
  return children;
}

async function summaryMetricRows(job: ProofJob, summary: VerificationSummary): Promise<TreeNode[]> {
  /**
   * Convert a verification summary into read-only metric rows.
   *
   * Inputs:
   * - `job`: Proof job whose artifacts can supply live summary data.
   * - `summary`: Latest summary for the proof job.
   *
   * Returns:
   * - `TreeNode[]`: Metric nodes only.
   */
  const coveragePct = (summary.coveragePercentage * 100).toFixed(1);
  const vulnerabilitiesReported = await readVulnerabilityCount(job, summary.artifactPaths);
  return [
    {
      kind: "metric",
      label: "Verification coverage",
      description: `${summary.coverageHit}/${summary.coverageTotal} (${coveragePct}%)`,
    },
    {
      kind: "metric",
      label: "Verified properties",
      description: `${summary.propertiesVerified}/${summary.propertiesInstrumented}`,
    },
    {
      kind: "metric",
      label: "Vulnerabilities reported",
      description: String(vulnerabilitiesReported),
    },
  ];
}

async function availableArtifactActionNodes(job: ProofJob): Promise<TreeNode[]> {
  /**
   * Create action rows for every proof artifact that currently exists on disk.
   *
   * Inputs:
   * - `job`: Proof job whose artifact files should be checked.
   *
   * Returns:
   * - `Promise<TreeNode[]>`: Action rows for currently available artifacts.
   */
  const summaryPaths = job.verificationSummary?.artifactPaths;
  const candidates: Array<{
    label: string;
    command: string;
    contextValue: string;
    targetPath: string | null;
  }> = [
    {
      label: "Open Harness",
      command: "autoup.openHarness",
      contextValue: "proofActionHarness",
      targetPath: summaryPaths?.harness ?? path.join(job.proofDir, `${job.functionName}_harness.c`),
    },
    {
      label: "Open Makefile",
      command: "autoup.openMakefile",
      contextValue: "proofActionMakefile",
      targetPath: summaryPaths?.makefile ?? path.join(job.proofDir, "Makefile"),
    },
    {
      label: "Open Source File",
      command: "autoup.openSourceFile",
      contextValue: "proofActionSource",
      targetPath: summaryPaths?.source ?? job.filePath,
    },
    {
      label: "Open Log",
      command: "autoup.openLog",
      contextValue: "proofActionLog",
      targetPath: summaryPaths?.log ?? job.logFile,
    },
    {
      label: "Open Verification Report",
      command: "autoup.openReport",
      contextValue: "proofActionReport",
      targetPath: getReportPath(job, summaryPaths),
    },
    {
      label: "Open Vulnerability Report",
      command: "autoup.openVulnerabilityReport",
      contextValue: "proofActionVulnerabilityReport",
      targetPath: getVulnerabilityReportPath(job, summaryPaths),
    },
  ];

  const actions: TreeNode[] = [];
  for (const candidate of candidates) {
    if (!candidate.targetPath) {
      continue;
    }
    if (!(await exists(candidate.targetPath))) {
      continue;
    }
    actions.push({
      kind: "action",
      label: candidate.label,
      command: candidate.command,
      jobId: job.id,
      contextValue: candidate.contextValue,
    });
  }
  return actions;
}

function getReportPath(job: ProofJob, summaryPaths?: ArtifactPaths): string | null {
  /**
   * Resolve the HTML report path for a proof job without assuming the proof
   * directory is always present.
   *
   * Inputs:
   * - `job`: Proof job whose report path should be derived.
   * - `summaryPaths`: Optional artifact-path object from the latest summary.
   *
   * Returns:
   * - `string | null`: Report path when it can be determined.
   */
  const reportHtml = summaryPaths?.reportHtml;
  if (typeof reportHtml === "string" && reportHtml.length > 0) {
    return reportHtml;
  }
  if (typeof job.proofDir === "string" && job.proofDir.length > 0) {
    return path.join(job.proofDir, "build", "report", "html", "index.html");
  }
  return null;
}

function getVulnerabilityReportPath(job: ProofJob, summaryPaths?: ArtifactPaths): string | null {
  /**
   * Resolve the vulnerability report path for a proof job.
   *
   * Inputs:
   * - `job`: Proof job whose vulnerability report path should be derived.
   * - `summaryPaths`: Optional artifact-path object from the latest summary.
   *
   * Returns:
   * - `string | null`: Vulnerability report path when it can be determined.
   */
  const vulnerabilityReport = summaryPaths?.vulnerabilityReport;
  if (typeof vulnerabilityReport === "string" && vulnerabilityReport.length > 0) {
    return vulnerabilityReport;
  }
  if (typeof job.proofDir === "string" && job.proofDir.length > 0) {
    return path.join(job.proofDir, "vulnerability-report.json");
  }
  return null;
}

async function exists(targetPath: string): Promise<boolean> {
  /**
   * Check whether a candidate artifact path exists.
   *
   * Inputs:
   * - `targetPath`: Absolute artifact path to inspect.
   *
   * Returns:
   * - `Promise<boolean>`: True when the file exists.
   */
  try {
    await fs.stat(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function readVulnerabilityCount(job: ProofJob, summaryPaths?: ArtifactPaths): Promise<number> {
  /**
   * Read the latest vulnerability count directly from disk.
   *
   * Inputs:
   * - `job`: Proof job whose vulnerability report should be inspected.
   * - `summaryPaths`: Optional artifact-path object from the latest summary.
   *
   * Returns:
   * - `Promise<number>`: Total reported vulnerabilities, or zero when unavailable.
   */
  const reportPath = getVulnerabilityReportPath(job, summaryPaths);
  if (!reportPath) {
    return 0;
  }

  try {
    const raw = await fs.readFile(reportPath, "utf-8");
    const parsed = JSON.parse(raw) as {
      summary?: { total_vulnerabilities?: unknown };
    };
    const count = Number(parsed.summary?.total_vulnerabilities);
    return Number.isFinite(count) ? count : 0;
  } catch {
    return 0;
  }
}
