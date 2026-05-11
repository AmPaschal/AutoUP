/**
 * This file is the main in-memory coordinator for proof jobs inside the
 * extension. It owns:
 * 1. Creating and rehydrating jobs.
 * 2. Launching/cancelling backend processes.
 * 3. Queueing proofs when concurrency is full.
 * 4. Applying backend events to UI state.
 * 5. Exposing artifact and output actions.
 */
import * as fs from "fs/promises";
import * as path from "path";
import * as vscode from "vscode";
import { detectFunctionAtCursor, type ResolvedFunction } from "../features/createProof/functionDetector";
import { startProofProcess } from "../features/createProof/proofRunner";
import { AutoUpInstaller } from "../features/setup/autoUpInstaller";
import { PrerequisiteChecker } from "../features/setup/prerequisiteChecker";
import { computeProofDirectory } from "../shared/pathUtils";
import { detectExecutionHost } from "../shared/platform";
import {
  isRuntimeActiveStatus,
  manifestFromJob,
  normalizeManifestStatus,
  parsePersistedJobManifest,
  readPersistedJobManifest,
  updatePersistedJobManifest,
  writePersistedJobManifest,
} from "./jobPersistence";
import { extractSummaryFromEvent, openArtifact, openHtmlReport, StatusTracker } from "./statusTracker";
import { ConfigManager } from "./configManager";
import type { AutoUpConfig, ProofEvent, ProofJob, ProofStatus, RuntimeInstallation } from "./types";

/**
 * Tracks all proof jobs known to the current VS Code session.
 */
export class ProofManager implements vscode.Disposable {
  private readonly jobs = new Map<string, ProofJob>();
  private readonly trackers = new Map<string, StatusTracker>();
  private readonly queuedIds: string[] = [];
  private runtimePromise: Promise<RuntimeInstallation> | null = null;
  private readonly changeEmitter = new vscode.EventEmitter<void>();

  readonly onDidChangeProofs = this.changeEmitter.event;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly configManager: ConfigManager,
    private readonly installer: AutoUpInstaller,
    private readonly prerequisites: PrerequisiteChecker,
  ) {}

  /**
   * Rehydrate persisted jobs from proof manifests on extension activation.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async initialize(): Promise<void> {
    await this.rehydrateJobs();
    await this.maybeStartNextQueued();
  }

  /**
   * Return all jobs sorted newest-first for the sidebar.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `ProofJob[]`: Sorted job list.
   */
  getAllJobs(): ProofJob[] {
    return [...this.jobs.values()].sort((left, right) => right.createdAt - left.createdAt);
  }

  /**
   * Start a proof using the function under the current editor cursor.
   *
   * Inputs:
   * - `editor`: Active text editor for the command invocation.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async startProofFromEditor(editor: vscode.TextEditor): Promise<void> {
    // Resolve the target function from the editor before allocating any proof job state.
    const resolvedFunction = await detectFunctionAtCursor(editor);
    if (!resolvedFunction) {
      void vscode.window.showWarningMessage("Could not resolve a C/C++ function at the current cursor position.");
      return;
    }
    await this.startProof(resolvedFunction);
  }

  /**
   * Create or queue a proof job for a resolved source/function target.
   *
   * Inputs:
   * - `resolvedFunction`: Function metadata returned by the detector.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async startProof(resolvedFunction: ResolvedFunction): Promise<void> {
    // Require the source file to belong to a workspace so proof storage is deterministic.
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(resolvedFunction.filePath));
    if (!workspaceFolder) {
      throw new Error("The selected file is not inside an open workspace folder.");
    }

    // Read the latest configuration and compute the proof output directory.
    const config = this.configManager.getConfig();
    const proofDir = computeProofDirectory(
      workspaceFolder.uri.fsPath,
      resolvedFunction.filePath,
      resolvedFunction.functionName,
      config.proofsRoot,
    );

    // Do not allow duplicate active jobs for the same proof directory.
    const activeExisting = this.findActiveJobByProofDir(proofDir);
    if (activeExisting) {
      void this.openLog(activeExisting);
      void vscode.window.showInformationMessage(
        `A proof job for ${resolvedFunction.functionName} is already active.`,
      );
      return;
    }

    // When the user reruns a proof after completion, replace the previous
    // sidebar entry for that proof directory instead of duplicating it.
    this.removeJobsForProofDir(proofDir);

    // Create the proof state directory and initialize the in-memory job record.
    await fs.mkdir(path.join(proofDir, ".autoup"), { recursive: true });
    const jobId = `${path.basename(resolvedFunction.filePath)}-${resolvedFunction.functionName}-${Date.now()}`;
    const outputChannel = vscode.window.createOutputChannel(`AutoUP: ${resolvedFunction.functionName}`);
    const executionHost = detectExecutionHost();
    if (executionHost === "unsupported-windows") {
      throw new Error("AutoUP proof generation is not supported on Windows in this extension.");
    }
    const job: ProofJob = {
      id: jobId,
      functionName: resolvedFunction.functionName,
      filePath: resolvedFunction.filePath,
      line: resolvedFunction.line,
      column: resolvedFunction.column,
      workspaceRoot: workspaceFolder.uri.fsPath,
      proofDir,
      logFile: path.join(proofDir, "autoup.log"),
      metricsFile: path.join(proofDir, "metrics.jsonl"),
      eventFile: path.join(proofDir, ".autoup", "events.jsonl"),
      jobFile: path.join(proofDir, ".autoup", "job.json"),
      status: "queued",
      currentStage: null,
      completedStages: [],
      verificationSummary: null,
      executionHost,
      lastError: null,
      outputChannel,
      process: null,
      createdAt: Date.now(),
      trackerStarted: false,
      lastEventCount: 0,
    };
    this.jobs.set(job.id, job);
    await this.persistJobManifest(job);
    this.ensureTracker(job);

    // Queue the job when the running count is already at the configured limit.
    if (this.getRunningJobCount() >= config.maxConcurrentJobs) {
      this.queuedIds.push(job.id);
      this.fireChange();
      return;
    }

    // Launch immediately when capacity is available and surface startup failures.
    try {
      await this.launchJob(job, config);
    } catch (error) {
      await this.markJobFailed(job, this.getErrorMessage(error));
      throw error;
    }
  }

  /**
   * Cancel an active or queued proof job.
   *
   * Inputs:
   * - `target`: Job object or job ID.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async cancelProof(target: unknown): Promise<void> {
    const job = this.resolveJob(target);
    if (!job) {
      return;
    }

    // Ignore cancel requests for jobs that already reached a terminal state.
    if (!["queued", "starting", "bootstrapping", "running"].includes(job.status)) {
      return;
    }

    // Kill the detached bridge process group so cancellation survives reloads
    // and also terminates any direct subprocess tree rooted at the bridge.
    await this.killJobProcess(job);
    // Remove queued work so a cancelled job is not relaunched later.
    const queueIndex = this.queuedIds.indexOf(job.id);
    if (queueIndex >= 0) {
      this.queuedIds.splice(queueIndex, 1);
    }
    job.status = "cancelled";
    job.lastError = null;
    await this.persistJobManifest(job, { status: "cancelled", lastError: null });
    this.fireChange();
  }

  /**
   * Delete a completed or otherwise inactive proof job and its proof directory.
   *
   * Inputs:
   * - `target`: Job object or job ID.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async deleteProof(target: unknown): Promise<void> {
    const job = this.resolveJob(target);
    if (!job) {
      return;
    }

    // Active proof jobs must be cancelled before their artifacts are removed.
    if (["queued", "starting", "bootstrapping", "running"].includes(job.status)) {
      throw new Error("Cannot delete a running proof. Cancel it first.");
    }

    // Confirm the destructive action before removing the proof artifacts.
    const choice = await vscode.window.showWarningMessage(
      `Delete the unit proof for ${job.functionName}?`,
      { modal: true },
      "Delete",
    );
    if (choice !== "Delete") {
      return;
    }

    await fs.rm(job.proofDir, { recursive: true, force: true });
    this.removeJob(job.id);
    this.fireChange();
  }

  /**
   * Open the harness file for a proof job.
   *
   * Inputs:
   * - `target`: Job object or job ID.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async openHarness(target: unknown): Promise<void> {
    const job = this.resolveJob(target);
    if (!job) {
      return;
    }
    // Prefer the latest summary artifact path and fall back to the conventional harness filename.
    await openArtifact(
      job.verificationSummary?.artifactPaths.harness ??
        path.join(job.proofDir, `${job.functionName}_harness.c`),
    );
  }

  /**
   * Open the makefile for a proof job.
   *
   * Inputs:
   * - `target`: Job object or job ID.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async openMakefile(target: unknown): Promise<void> {
    const job = this.resolveJob(target);
    if (!job) {
      return;
    }
    await openArtifact(job.verificationSummary?.artifactPaths.makefile ?? path.join(job.proofDir, "Makefile"));
  }

  /**
   * Open the target source file for a proof job.
   *
   * Inputs:
   * - `target`: Job object or job ID.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async openSourceFile(target: unknown): Promise<void> {
    const job = this.resolveJob(target);
    if (!job) {
      return;
    }
    await openArtifact(job.verificationSummary?.artifactPaths.source ?? job.filePath);
  }

  /**
   * Open the AutoUP log file for a proof job.
   *
   * Inputs:
   * - `target`: Job object or job ID.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async openLog(target: unknown): Promise<void> {
    const job = this.resolveJob(target);
    if (!job) {
      return;
    }
    await openArtifact(job.verificationSummary?.artifactPaths.log ?? job.logFile);
  }

  /**
   * Open the generated HTML verification report for a proof job.
   *
   * Inputs:
   * - `target`: Job object or job ID.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async openReport(target: unknown): Promise<void> {
    const job = this.resolveJob(target);
    if (!job) {
      return;
    }
    await openHtmlReport(this.getReportPath(job));
  }

  /**
   * Show the per-job output channel.
   *
   * Inputs:
   * - `target`: Job object or job ID.
   *
   * Returns:
   * - `void`.
   */
  showOutput(target: unknown): void {
    const job = this.resolveJob(target);
    if (!job) {
      return;
    }
    void this.openLog(job);
  }

  /**
   * Reveal the proof directory in the VS Code explorer.
   *
   * Inputs:
   * - `target`: Job object or job ID.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async revealProofFolder(target: unknown): Promise<void> {
    const job = this.resolveJob(target);
    if (!job) {
      return;
    }
    await vscode.commands.executeCommand("revealInExplorer", vscode.Uri.file(job.proofDir));
  }

  /**
   * Force all trackers to poll immediately and refresh the tree state.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async refresh(): Promise<void> {
    this.removeDuplicateJobsByProofDir();
    await this.pruneMissingJobs();
    // Poll every tracker so the UI sees manifest/event changes immediately.
    for (const tracker of this.trackers.values()) {
      await tracker.poll();
    }
    this.removeDuplicateJobsByProofDir();
    await this.pruneMissingJobs();
    this.fireChange();
  }

  /**
   * Dispose background trackers, running processes, and output channels.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `void`.
   */
  dispose(): void {
    // Stop polling before tearing down UI resources.
    for (const tracker of this.trackers.values()) {
      tracker.stop();
    }
    for (const job of this.jobs.values()) {
      job.process = null;
      job.outputChannel.dispose();
    }
    this.changeEmitter.dispose();
  }

  /**
   * Launch the backend process for a proof job.
   *
   * Inputs:
   * - `job`: Job to launch.
   * - `config`: Current extension configuration.
   *
   * Returns:
   * - `Promise<void>`.
   */
  private async launchJob(job: ProofJob, config: AutoUpConfig): Promise<void> {
    // Ensure the runtime exists and all launch prerequisites are satisfied.
    const runtime = await this.getRuntime(config);
    const prerequisites = await this.prerequisites.ensureReady(config, job.executionHost, runtime);

    // Start the bridge process in detached mode and keep only a best-effort
    // handle for startup error reporting and same-session cancellation.
    job.status = "starting";
    job.lastError = null;
    await this.persistJobManifest(job, {
      pid: null,
      status: "starting",
      currentStage: null,
      lastError: null,
    });
    const child = startProofProcess(job, {
      runtime,
      config,
      prerequisites,
      executionHost: job.executionHost,
    });
    job.process = child;
    await this.persistJobManifest(job, {
      pid: typeof child.pid === "number" ? child.pid : null,
      status: "starting",
      currentStage: null,
      lastError: null,
    });
    job.outputChannel.appendLine("Proof launched as a detached background job.");
    job.outputChannel.appendLine(`Log file: ${job.logFile}`);
    if (typeof child.pid === "number") {
      job.outputChannel.appendLine(`Bridge PID: ${child.pid}`);
    }
    this.fireChange();

    // Clear the best-effort process handle when the detached bridge exits and
    // reconcile state from the persisted manifest/event files.
    child.on("error", async (error) => {
      job.process = null;
      job.outputChannel.appendLine(`Failed to launch detached proof job: ${String(error)}`);
      await this.markJobFailed(job, `Failed to launch detached proof job: ${String(error)}`);
      void this.maybeStartNextQueued();
    });
    child.on("close", async (exitCode) => {
      job.process = null;
      await this.refresh();
      if (["running", "starting", "bootstrapping"].includes(job.status)) {
        job.status = exitCode === 0 ? "interrupted" : "failed";
        job.lastError = exitCode === 0 ? null : `Detached proof bridge exited with code ${exitCode}.`;
        await this.persistJobManifest(job, {
          pid: null,
          status: job.status,
          lastError: job.lastError,
        });
      }
      await this.maybeStartNextQueued();
      this.fireChange();
    });
  }

  /**
   * Return the shared runtime installation promise, creating it on first use.
   *
   * Inputs:
   * - `config`: Current extension configuration.
   *
   * Returns:
   * - `Promise<RuntimeInstallation>`.
   */
  private async getRuntime(config: AutoUpConfig): Promise<RuntimeInstallation> {
    if (!this.runtimePromise) {
      this.runtimePromise = this.installer.ensureInstalled(config).catch((error) => {
        this.runtimePromise = null;
        throw error;
      });
    }
    return await this.runtimePromise;
  }

  /**
   * Ensure a status tracker exists for the job.
   *
   * Inputs:
   * - `job`: Proof job to track.
   *
   * Returns:
   * - `void`.
   */
  private ensureTracker(job: ProofJob): void {
    if (this.trackers.has(job.id)) {
      return;
    }
    // Create a tracker that feeds backend events back into the proof manager.
    const tracker = new StatusTracker(
      job,
      (trackedJob, event) => {
        this.applyEvent(trackedJob, event);
      },
      (missingJob) => {
        this.removeJob(missingJob.id);
        this.fireChange();
      },
    );
    tracker.start();
    this.trackers.set(job.id, tracker);
    job.trackerStarted = true;
  }

  /**
   * Apply a backend event to the in-memory job state.
   *
   * Inputs:
   * - `job`: Proof job to mutate.
   * - `event`: Backend event just received from the tracker.
   *
   * Returns:
   * - `void`.
   */
  private applyEvent(job: ProofJob, event: ProofEvent): void {
    // Update only the fields that each event type owns.
    switch (event.type) {
      case "bootstrap_started":
        job.status = "bootstrapping";
        job.lastError = null;
        break;
      case "bootstrap_completed":
      case "job_started":
        job.status = "running";
        job.lastError = null;
        break;
      case "stage_started":
        job.currentStage = event.stage ?? null;
        job.status = "running";
        job.lastError = null;
        break;
      case "stage_completed":
        if (event.stage && !job.completedStages.includes(event.stage)) {
          job.completedStages.push(event.stage);
        }
        break;
      case "summary_updated":
      case "refinement_accepted": {
        // Summary-bearing events update both the current stage and verification metrics.
        const summary = extractSummaryFromEvent(event);
        if (summary) {
          job.verificationSummary = summary;
        }
        if (event.stage) {
          job.currentStage = event.stage;
        }
        break;
      }
      case "job_failed":
        job.status = "failed";
        job.lastError = this.getEventMessage(event);
        job.process = null;
        void this.maybeStartNextQueued();
        break;
      case "job_cancelled":
        job.status = "cancelled";
        job.lastError = null;
        job.process = null;
        void this.maybeStartNextQueued();
        break;
      case "job_completed":
        job.status = "completed";
        job.lastError = null;
        job.process = null;
        void this.maybeStartNextQueued();
        break;
      default:
        break;
    }
    // Notify the UI after every state transition.
    this.fireChange();
  }

  /**
   * Fire the proof-change event consumed by the tree provider.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `void`.
   */
  private fireChange(): void {
    this.changeEmitter.fire();
  }

  /**
   * Count jobs currently consuming a launch slot.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `number`: Running/starting job count.
   */
  private getRunningJobCount(): number {
    return [...this.jobs.values()].filter((job) => isRuntimeActiveStatus(job.status)).length;
  }

  /**
   * Launch the next queued job when concurrency allows.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `Promise<void>`.
   */
  private async maybeStartNextQueued(): Promise<void> {
    const config = this.configManager.getConfig();
    while (this.getRunningJobCount() < config.maxConcurrentJobs) {
      const nextId = this.queuedIds[0];
      if (!nextId) {
        return;
      }

      const nextJob = this.jobs.get(nextId);
      if (!nextJob) {
        this.queuedIds.shift();
        continue;
      }

      try {
        await this.launchJob(nextJob, config);
        this.queuedIds.shift();
      } catch (error) {
        this.queuedIds.shift();
        await this.markJobFailed(nextJob, this.getErrorMessage(error));
      }
    }
  }

  /**
   * Find an active job already targeting the same proof directory.
   *
   * Inputs:
   * - `proofDir`: Absolute proof directory path.
   *
   * Returns:
   * - `ProofJob | undefined`: Existing active job when present.
   */
  private findActiveJobByProofDir(proofDir: string): ProofJob | undefined {
    return [...this.jobs.values()].find((job) =>
      job.proofDir === proofDir && ["queued", "starting", "bootstrapping", "running"].includes(job.status),
    );
  }

  /**
   * Resolve a job object from either a job ID or a job reference.
   *
   * Inputs:
   * - `target`: Job object or ID.
   *
   * Returns:
   * - `ProofJob | undefined`.
   */
  private resolveJob(target: unknown): ProofJob | undefined {
    /**
     * Resolve a proof job from a direct job ID, a proof job, or a tree node
     * object delivered by VS Code menu/context commands.
     *
     * Inputs:
     * - `target`: Raw command argument from VS Code.
     *
     * Returns:
     * - `ProofJob | undefined`.
     */
    if (typeof target === "string") {
      return this.jobs.get(target);
    }

    if (!target || typeof target !== "object") {
      return undefined;
    }

    const candidate = target as Record<string, unknown>;
    if (candidate.node) {
      return this.resolveJob(candidate.node);
    }

    if (typeof candidate.jobId === "string") {
      return this.jobs.get(candidate.jobId);
    }

    if (candidate.job && typeof candidate.job === "object") {
      const nestedJob = candidate.job as Record<string, unknown>;
      if (
        typeof nestedJob.id === "string" &&
        typeof nestedJob.proofDir === "string" &&
        typeof nestedJob.functionName === "string"
      ) {
        return nestedJob as unknown as ProofJob;
      }
    }

    if (
      typeof candidate.id === "string" &&
      typeof candidate.proofDir === "string" &&
      typeof candidate.functionName === "string"
    ) {
      return candidate as unknown as ProofJob;
    }

    return undefined;
  }

  /**
   * Remove a job from in-memory state, tracker polling, and UI resources.
   *
   * Inputs:
   * - `jobId`: Identifier of the job to remove.
   *
   * Returns:
   * - `void`.
   */
  private removeJob(jobId: string): void {
    const job = this.jobs.get(jobId);
    if (!job) {
      return;
    }

    // Stop polling and terminate any stray child process before forgetting the job.
    const tracker = this.trackers.get(jobId);
    tracker?.stop();
    this.trackers.delete(jobId);
    job.process = null;
    job.outputChannel.dispose();

    const queueIndex = this.queuedIds.indexOf(jobId);
    if (queueIndex >= 0) {
      this.queuedIds.splice(queueIndex, 1);
    }

    this.jobs.delete(jobId);
  }

  /**
   * Remove every in-memory job currently pointing at a proof directory.
   *
   * Inputs:
   * - `proofDir`: Absolute proof directory path.
   *
   * Returns:
   * - `void`.
   */
  private removeJobsForProofDir(proofDir: string): void {
    for (const job of [...this.jobs.values()]) {
      if (job.proofDir === proofDir) {
        this.removeJob(job.id);
      }
    }
  }

  /**
   * Kill the bridge process using the PID recorded in the job manifest.
   *
   * Inputs:
   * - `job`: Proof job whose manifest should be consulted.
   *
   * Returns:
   * - `Promise<void>`.
   */
  private async killPidFromManifest(job: ProofJob): Promise<void> {
    try {
      // Read the manifest directly because rehydrated jobs may not have a live
      // `ChildProcess` handle in this extension session.
      const manifest = await readPersistedJobManifest(job.jobFile);
      if (manifest.pid) {
        this.killPidOrGroup(manifest.pid);
      }
    } catch {
      // Best effort only.
    }
  }

  /**
   * Terminate the active background process for a job.
   *
   * Inputs:
   * - `job`: Proof job whose detached process should be signaled.
   *
   * Returns:
   * - `Promise<void>`.
   */
  private async killJobProcess(job: ProofJob): Promise<void> {
    // Prefer the live same-session process handle when it has a PID.
    if (typeof job.process?.pid === "number") {
      this.killPidOrGroup(job.process.pid);
      return;
    }

    // Rehydrated jobs rely on the manifest PID because there is no direct
    // child-process handle in the current VS Code session.
    await this.killPidFromManifest(job);
  }

  /**
   * Send SIGTERM to a detached process group, falling back to the single PID.
   *
   * Inputs:
   * - `pid`: Detached bridge process identifier.
   *
   * Returns:
   * - `void`.
   */
  private killPidOrGroup(pid: number): void {
    try {
      process.kill(-pid, "SIGTERM");
      return;
    } catch {
      // Fall through to single-process signaling when the process group is not available.
    }

    try {
      process.kill(pid, "SIGTERM");
    } catch {
      // Best effort only.
    }
  }

  /**
   * Remove panel entries whose proof directory or persisted manifest vanished.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `Promise<void>`.
   */
  private async pruneMissingJobs(): Promise<void> {
    for (const job of [...this.jobs.values()]) {
      // Dropped proof directories should disappear from the panel immediately.
      if (!(await this.exists(job.proofDir))) {
        this.removeJob(job.id);
        continue;
      }

      // If the job manifest is gone for an inactive job, remove the stale row.
      if (
        !(await this.exists(job.jobFile)) &&
        !job.process &&
        !isRuntimeActiveStatus(job.status)
      ) {
        this.removeJob(job.id);
      }
    }
  }

  /**
   * Collapse accidental duplicate rows that point at the same proof directory.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `void`.
   */
  private removeDuplicateJobsByProofDir(): void {
    const jobsByProofDir = new Map<string, ProofJob[]>();
    for (const job of this.jobs.values()) {
      const entries = jobsByProofDir.get(job.proofDir) ?? [];
      entries.push(job);
      jobsByProofDir.set(job.proofDir, entries);
    }

    for (const jobs of jobsByProofDir.values()) {
      if (jobs.length <= 1) {
        continue;
      }

      // Prefer an active job when present; otherwise keep the newest row.
      jobs.sort((left, right) => {
        const leftActive = ["queued", "starting", "bootstrapping", "running"].includes(left.status) ? 1 : 0;
        const rightActive = ["queued", "starting", "bootstrapping", "running"].includes(right.status) ? 1 : 0;
        if (leftActive !== rightActive) {
          return rightActive - leftActive;
        }
        return right.createdAt - left.createdAt;
      });

      for (const duplicate of jobs.slice(1)) {
        this.removeJob(duplicate.id);
      }
    }
  }

  /**
   * Rebuild in-memory jobs by scanning proof manifests under each workspace.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `Promise<void>`.
   */
  private async rehydrateJobs(): Promise<void> {
    const workspaceFolders = vscode.workspace.workspaceFolders ?? [];
    const config = this.configManager.getConfig();
    const skippedManifests: string[] = [];
    const rehydratedQueuedJobs: ProofJob[] = [];
    // Scan every workspace folder for persisted proof manifests.
    for (const folder of workspaceFolders) {
      const pattern = new vscode.RelativePattern(
        folder,
        path.posix.join(config.proofsRoot.replace(/\\/g, "/"), "**/.autoup/job.json"),
      );
      const matches = await vscode.workspace.findFiles(pattern);
      for (const uri of matches) {
        try {
          // Rebuild the in-memory job from the manifest contents and conventional paths.
          const raw = await fs.readFile(uri.fsPath, "utf-8");
          const manifest = parsePersistedJobManifest(
            JSON.parse(raw) as Record<string, unknown>,
            path.dirname(path.dirname(uri.fsPath)),
            folder.uri.fsPath,
          );
          const job: ProofJob = {
            id: manifest.jobId,
            functionName: manifest.functionName,
            filePath: manifest.sourceFile,
            line: manifest.line,
            column: manifest.column,
            workspaceRoot: manifest.workspaceRoot,
            proofDir: manifest.proofDir,
            logFile: path.join(manifest.proofDir, "autoup.log"),
            metricsFile: path.join(manifest.proofDir, "metrics.jsonl"),
            eventFile: path.join(manifest.proofDir, ".autoup", "events.jsonl"),
            jobFile: uri.fsPath,
            status: normalizeManifestStatus(
              manifest.status,
              manifest.pid,
              manifest.pid !== null && this.isPidAlive(manifest.pid),
            ),
            currentStage: manifest.currentStage,
            completedStages: [],
            verificationSummary: null,
            executionHost: manifest.executionHost,
            lastError: manifest.lastError,
            outputChannel: vscode.window.createOutputChannel(`AutoUP: ${manifest.functionName}`),
            process: null,
            createdAt: manifest.createdAt,
            trackerStarted: false,
            lastEventCount: 0,
          };
          this.jobs.set(job.id, job);
          this.ensureTracker(job);
          if (job.status === "queued") {
            rehydratedQueuedJobs.push(job);
          }
        } catch {
          skippedManifests.push(uri.fsPath);
        }
      }
    }
    rehydratedQueuedJobs
      .sort((left, right) => left.createdAt - right.createdAt)
      .forEach((job) => {
        if (!this.queuedIds.includes(job.id)) {
          this.queuedIds.push(job.id);
        }
      });
    if (skippedManifests.length > 0) {
      void vscode.window.showWarningMessage(
        `Skipped ${skippedManifests.length} malformed AutoUP job manifest(s) during reload.`,
      );
    }
    // Immediately poll all rehydrated jobs so the tree reflects their latest state.
    await this.refresh();
  }

  /**
   * Check whether a PID is still alive on the current execution host.
   *
   * Inputs:
   * - `pid`: Process identifier from a job manifest.
   *
   * Returns:
   * - `boolean`: True when the process appears to still be alive.
   */
  private isPidAlive(pid: number): boolean {
    if (!Number.isFinite(pid) || pid <= 0) {
      return false;
    }
    try {
      process.kill(pid, 0);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Check whether a proof artifact path still exists on disk.
   *
   * Inputs:
   * - `targetPath`: Path to inspect.
   *
   * Returns:
   * - `Promise<boolean>`: True when the path exists.
   */
  private async exists(targetPath: string): Promise<boolean> {
    try {
      await fs.stat(targetPath);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Resolve the HTML report entrypoint for a proof job.
   *
   * Inputs:
   * - `job`: Proof job whose report path should be derived.
   *
   * Returns:
   * - `string | null`: Absolute report path when it can be determined.
   */
  private getReportPath(job: ProofJob): string | null {
    const summaryPath = job.verificationSummary?.artifactPaths.reportHtml;
    if (typeof summaryPath === "string" && summaryPath.length > 0) {
      return summaryPath;
    }
    if (typeof job.proofDir === "string" && job.proofDir.length > 0) {
      return path.join(job.proofDir, "build", "report", "html", "index.html");
    }
    return null;
  }

  /**
   * Persist the current proof job manifest with optional field overrides.
   *
   * Inputs:
   * - `job`: In-memory job to persist.
   * - `overrides`: Optional persisted-field overrides for this write.
   *
   * Returns:
   * - `Promise<void>`.
   */
  private async persistJobManifest(
    job: ProofJob,
    overrides: Partial<ReturnType<typeof manifestFromJob>> = {},
  ): Promise<void> {
    const manifest = {
      ...manifestFromJob(job),
      ...overrides,
      updatedAt: Date.now(),
    };
    await writePersistedJobManifest(job.jobFile, manifest);
  }

  /**
   * Mark a proof job as failed and persist the failure reason.
   *
   * Inputs:
   * - `job`: Job to transition to failed.
   * - `message`: Human-readable failure explanation.
   *
   * Returns:
   * - `Promise<void>`.
   */
  private async markJobFailed(job: ProofJob, message: string): Promise<void> {
    job.status = "failed";
    job.lastError = message;
    job.process = null;
    await this.persistJobManifest(job, {
      pid: null,
      status: "failed",
      lastError: message,
    });
    this.fireChange();
  }

  /**
   * Extract a human-readable error string from an unknown thrown value.
   *
   * Inputs:
   * - `error`: Unknown thrown value.
   *
   * Returns:
   * - `string`: Safe user-facing error text.
   */
  private getErrorMessage(error: unknown): string {
    if (error instanceof Error) {
      return error.message;
    }
    return String(error);
  }

  /**
   * Extract an optional textual message from a backend event payload.
   *
   * Inputs:
   * - `event`: Backend event emitted by the Python bridge.
   *
   * Returns:
   * - `string | null`: Event message when present.
   */
  private getEventMessage(event: ProofEvent): string | null {
    const message = event.data?.message;
    return typeof message === "string" ? message : null;
  }
}
