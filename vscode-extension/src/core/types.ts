/**
 * This file defines the shared type contracts used across the extension.
 * Keeping them in one place makes the runtime flow and event model easier to
 * maintain as the extension grows.
 */
import type { ChildProcess } from "child_process";
import type * as vscode from "vscode";

export type ExecutionHost =
  | "local-macos"
  | "local-linux"
  | "remote-ssh-linux"
  | "unsupported-windows";
export type ProofStatus =
  | "queued"
  | "starting"
  | "bootstrapping"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface ArtifactPaths {
  proofDir: string;
  harness?: string | null;
  makefile?: string | null;
  source?: string | null;
  log?: string | null;
  reportHtml?: string | null;
}

export interface VerificationSummary {
  propertiesInstrumented: number;
  propertiesVerified: number;
  errorsByLine: number;
  coverageHit: number;
  coverageTotal: number;
  coveragePercentage: number;
  artifactPaths: ArtifactPaths;
  targetFunction?: string;
}

export interface ProofEvent {
  type: string;
  jobId: string;
  timestamp: number;
  stage?: string | null;
  data?: Record<string, unknown>;
}

export interface ProofJob {
  id: string;
  functionName: string;
  filePath: string;
  line: number;
  column: number;
  workspaceRoot: string;
  proofDir: string;
  logFile: string;
  metricsFile: string;
  eventFile: string;
  jobFile: string;
  status: ProofStatus;
  currentStage: string | null;
  completedStages: string[];
  verificationSummary: VerificationSummary | null;
  executionHost: ExecutionHost;
  lastError: string | null;
  outputChannel: vscode.OutputChannel;
  process: ChildProcess | null;
  createdAt: number;
  trackerStarted: boolean;
  lastEventCount: number;
}

export interface RuntimeInstallation {
  backendRoot: string;
  pythonPath: string;
}

export interface ResolvedPrerequisites {
  apiKey: string;
  apiKeyEnvVar: string;
  containerEngine: "docker" | "apptainer";
}

export interface AutoUpConfig {
  model: string;
  containerEngine: "auto" | "docker" | "apptainer";
  scopeBound: number | null;
  scopeTimeBudget: number | null;
  makeTimeout: number;
  maxConcurrentJobs: number;
  proofsRoot: string;
}
