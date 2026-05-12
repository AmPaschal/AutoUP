/**
 * This file polls the backend's manifest and event files so the extension can
 * update job state without parsing logs.
 */
import * as fs from "fs/promises";
import * as path from "path";
import * as vscode from "vscode";
import {
  isRuntimeActiveStatus,
  normalizeManifestStatus,
  parsePersistedJobManifest,
} from "./jobPersistence";
import type { ProofEvent, ProofJob, VerificationSummary } from "./types";

/**
 * Polls `.autoup/events.jsonl` and `.autoup/job.json` for a single proof job.
 */
export class StatusTracker {
  private timer: NodeJS.Timeout | null = null;
  private readonly warnedMalformedEventOffsets = new Set<number>();

  constructor(
    private readonly job: ProofJob,
    private readonly onEvent: (job: ProofJob, event: ProofEvent) => void,
    private readonly onMissing: (job: ProofJob) => void,
  ) {}

  /**
   * Start periodic polling for this job.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `void`.
   */
  start(): void {
    if (this.timer) {
      return;
    }
    // Poll once per second so the UI updates quickly without using persistent watchers.
    this.timer = setInterval(() => {
      void this.poll();
    }, 1000);
  }

  /**
   * Stop periodic polling for this job.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `void`.
   */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /**
   * Read newly appended events and the latest job manifest state.
   *
   * Inputs:
   * - None.
   *
   * Returns:
   * - `Promise<void>`.
   */
  async poll(): Promise<void> {
    // Drop jobs whose proof directory or persisted manifest disappeared.
    if (await this.shouldRemoveJob()) {
      this.onMissing(this.job);
      return;
    }

    // Read the whole event file and replay only the events the tracker has not
    // seen before.
    const lines = await this.readEventLines();
    const parseResult = parseEventLines(lines, this.job.lastEventCount);
    for (const warning of parseResult.warnings) {
      if (this.warnedMalformedEventOffsets.has(warning.lineIndex)) {
        continue;
      }
      this.warnedMalformedEventOffsets.add(warning.lineIndex);
      console.warn(
        `Skipping malformed AutoUP event line ${warning.lineIndex + 1} for ${this.job.eventFile}: ${warning.message}`,
      );
    }
    for (const event of parseResult.events) {
      this.onEvent(this.job, event);
    }
    this.job.lastEventCount = parseResult.nextIndex;

    // Refresh manifest-derived fields as a second source of truth for terminal state.
    const manifest = await this.readManifest();
    if (manifest) {
      const normalizedStatus = normalizeManifestStatus(
        manifest.status,
        manifest.pid,
        manifest.pid !== null && isPidAlive(manifest.pid),
      );
      if (normalizedStatus !== this.job.status) {
        this.job.status = normalizedStatus;
      }
    }
    if (manifest && (typeof manifest.currentStage === "string" || manifest.currentStage === null)) {
      this.job.currentStage = manifest.currentStage;
    }
  }

  private async shouldRemoveJob(): Promise<boolean> {
    /**
     * Decide whether the tracked job should be removed from the panel.
     *
     * Inputs:
     * - None.
     *
     * Returns:
     * - `Promise<boolean>`: True when the proof directory or persisted job
     *   manifest disappeared.
     */
    // Removing the proof directory always invalidates the job entry.
    if (!(await this.exists(this.job.proofDir))) {
      return true;
    }

    // If the manifest is missing for an inactive job, remove the panel item as
    // well. Active jobs may legitimately be between process start and manifest
    // creation, so preserve them until they settle.
    if (
      !(await this.exists(this.job.jobFile)) &&
      !this.job.process &&
      !isRuntimeActiveStatus(this.job.status)
    ) {
      return true;
    }

    return false;
  }

  private async readEventLines(): Promise<string[]> {
    /**
     * Read the raw event file as individual lines.
     *
     * Inputs:
     * - None.
     *
     * Returns:
     * - `Promise<string[]>`: Event log lines, or an empty array when unreadable.
     */
    try {
      const raw = await fs.readFile(this.job.eventFile, "utf-8");
      return raw.split(/\r?\n/).filter((line) => line.length > 0);
    } catch {
      return [];
    }
  }

  private async readManifest(): Promise<ReturnType<typeof parsePersistedJobManifest> | null> {
    /**
     * Read the current job manifest.
     *
     * Inputs:
     * - None.
     *
     * Returns:
     * - `Promise<Record<string, unknown> | null>`: Parsed manifest or `null`.
     */
    try {
      const raw = await fs.readFile(this.job.jobFile, "utf-8");
      return parsePersistedJobManifest(
        JSON.parse(raw) as Record<string, unknown>,
        this.job.proofDir,
        this.job.workspaceRoot,
      );
    } catch {
      return null;
    }
  }

  private async exists(targetPath: string): Promise<boolean> {
    /**
     * Check whether a tracked proof path still exists on disk.
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
}

export function extractSummaryFromEvent(event: ProofEvent): VerificationSummary | null {
  /**
   * Pull a typed verification summary out of a backend event.
   *
   * Inputs:
   * - `event`: Parsed backend event.
   *
   * Returns:
   * - `VerificationSummary | null`: Summary payload or `null`.
   */
  const summary = event.data?.summary;
  if (!summary || typeof summary !== "object") {
    return null;
  }
  return summary as unknown as VerificationSummary;
}

export interface ParsedEventLinesResult {
  events: ProofEvent[];
  nextIndex: number;
  warnings: Array<{ lineIndex: number; message: string }>;
}

export function parseEventLines(lines: string[], startIndex: number): ParsedEventLinesResult {
  /**
   * Parse a JSONL event buffer with tolerance for malformed or partial lines.
   *
   * Inputs:
   * - `lines`: Event log split into individual lines.
   * - `startIndex`: Zero-based index of the first unread line.
   *
   * Returns:
   * - `ParsedEventLinesResult`: Parsed events, next safe cursor, and warnings.
   */
  const events: ProofEvent[] = [];
  const warnings: Array<{ lineIndex: number; message: string }> = [];
  let nextIndex = startIndex;

  for (let index = startIndex; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim()) {
      nextIndex = index + 1;
      continue;
    }

    try {
      events.push(JSON.parse(line) as ProofEvent);
      nextIndex = index + 1;
    } catch (error) {
      if (index === lines.length - 1) {
        break;
      }
      warnings.push({
        lineIndex: index,
        message: error instanceof Error ? error.message : String(error),
      });
      nextIndex = index + 1;
    }
  }

  return {
    events,
    nextIndex,
    warnings,
  };
}

function isPidAlive(pid: number): boolean {
  /**
   * Check whether a manifest PID still appears alive on this host.
   *
   * Inputs:
   * - `pid`: Process identifier to inspect.
   *
   * Returns:
   * - `boolean`: True when the PID appears to still be running.
   */
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

export async function openArtifact(pathOrUndefined?: string | null): Promise<void> {
  /**
   * Open a proof artifact in a VS Code text editor.
   *
   * Inputs:
   * - `pathOrUndefined`: Absolute artifact path, or `null`/`undefined`.
   *
   * Returns:
   * - `Promise<void>`.
   */
  if (!pathOrUndefined) {
    void vscode.window.showWarningMessage("The requested artifact is not available yet.");
    return;
  }
  try {
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(pathOrUndefined));
    await vscode.window.showTextDocument(document, { preview: false });
  } catch {
    void vscode.window.showWarningMessage("The requested artifact is not available yet.");
  }
}

export async function openHtmlReport(pathOrUndefined?: string | null): Promise<void> {
  /**
   * Open a generated HTML report using VS Code's browser-like viewer when
   * possible, with a fallback to the external browser.
   *
   * Inputs:
   * - `pathOrUndefined`: Absolute report path, or `null`/`undefined`.
   *
   * Returns:
   * - `Promise<void>`.
   */
  if (!pathOrUndefined) {
    void vscode.window.showWarningMessage("The HTML report is not available yet.");
    return;
  }

  try {
    const reportPath = path.resolve(pathOrUndefined);
    const reportRoot = path.dirname(reportPath);
    const panel = vscode.window.createWebviewPanel(
      "autoupReport",
      `AutoUP Report: ${path.basename(reportPath)}`,
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.file(reportRoot)],
      },
    );

    const render = async (targetPath: string, hash = ""): Promise<void> => {
      const normalizedTarget = path.resolve(targetPath);
      if (!isWithinRoot(normalizedTarget, reportRoot)) {
        void vscode.window.showWarningMessage("The requested report file is outside the report directory.");
        return;
      }
      panel.title = `AutoUP Report: ${path.basename(normalizedTarget)}`;
      panel.webview.html = await buildReportWebviewHtml(
        panel.webview,
        reportRoot,
        normalizedTarget,
        hash,
      );
    };

    panel.webview.onDidReceiveMessage(async (message) => {
      if (!message || message.type !== "openReportPath") {
        return;
      }
      if (typeof message.filePath !== "string") {
        return;
      }
      await render(message.filePath, typeof message.hash === "string" ? message.hash : "");
    });

    await render(reportPath);
  } catch {
    try {
      await vscode.env.openExternal(vscode.Uri.file(pathOrUndefined));
    } catch {
      void vscode.window.showWarningMessage("The HTML report could not be opened.");
    }
  }
}

async function buildReportWebviewHtml(
  webview: vscode.Webview,
  reportRoot: string,
  filePath: string,
  hash: string,
): Promise<string> {
  /**
   * Load and rewrite a report HTML file so it can run inside a VS Code webview.
   *
   * Inputs:
   * - `webview`: Target webview instance.
   * - `reportRoot`: Root directory of the HTML report bundle.
   * - `filePath`: Specific HTML file to render.
   * - `hash`: Optional fragment identifier to scroll to after load.
   *
   * Returns:
   * - `Promise<string>`: Rewritten webview HTML.
   */
  const rawHtml = await fs.readFile(filePath, "utf-8");
  const currentDir = path.dirname(filePath);
  const nonce = String(Date.now());
  const initialHash = hash.replace(/^#/, "");

  const rewritten = rawHtml.replace(
    /(href|src)=["']([^"']+)["']/gi,
    (_match, attribute: string, target: string) => {
      const resolved = resolveReportTarget(filePath, currentDir, reportRoot, target);
      if (!resolved) {
        return `${attribute}="${escapeHtmlAttribute(target)}"`;
      }

      if (resolved.kind === "external") {
        return `${attribute}="${escapeHtmlAttribute(target)}"`;
      }

      if (attribute.toLowerCase() === "href" && resolved.filePath.endsWith(".html")) {
        return `href="#" data-report-target="${escapeHtmlAttribute(resolved.filePath)}" data-report-hash="${escapeHtmlAttribute(resolved.hash)}"`;
      }

      const resourceUri = webview.asWebviewUri(vscode.Uri.file(resolved.filePath));
      return `${attribute}="${resourceUri.toString()}"`;
    },
  );

  const script = `
    <script nonce="${nonce}">
      const vscode = acquireVsCodeApi();
      window.addEventListener("click", (event) => {
        const link = event.target.closest("a[data-report-target]");
        if (!link) {
          return;
        }
        event.preventDefault();
        vscode.postMessage({
          type: "openReportPath",
          filePath: link.getAttribute("data-report-target"),
          hash: link.getAttribute("data-report-hash") || "",
        });
      });
      window.addEventListener("load", () => {
        const targetId = ${JSON.stringify(initialHash)};
        if (!targetId) {
          return;
        }
        const target = document.getElementById(targetId) || document.querySelector('[name="' + targetId + '"]');
        if (target && typeof target.scrollIntoView === "function") {
          target.scrollIntoView({ block: "start" });
        }
      });
    </script>
  `;

  if (rewritten.includes("</body>")) {
    return rewritten.replace("</body>", `${script}</body>`);
  }
  return `${rewritten}${script}`;
}

function resolveReportTarget(
  currentFilePath: string,
  currentDir: string,
  reportRoot: string,
  target: string,
): { kind: "local"; filePath: string; hash: string } | { kind: "external" } | null {
  /**
   * Resolve a report resource/link target to either a local file path or an
   * external URL marker.
   *
   * Inputs:
   * - `currentDir`: Directory of the currently rendered HTML file.
   * - `reportRoot`: Root directory of the generated report.
   * - `target`: Raw href/src value from the HTML.
   *
   * Returns:
   * - A resolved target descriptor, or `null` when the target should be left
   *   unchanged.
   */
  if (!target || target.startsWith("data:") || target.startsWith("javascript:")) {
    return null;
  }
  if (/^[a-zA-Z]+:\/\//.test(target)) {
    return { kind: "external" };
  }

  const [rawPath, rawHash = ""] = target.split("#", 2);
  const hash = rawHash ? `#${rawHash}` : "";

  if (rawPath.length === 0) {
    return { kind: "local", filePath: currentFilePath, hash };
  }

  const resolvedPath = rawPath.startsWith("/")
    ? path.resolve(reportRoot, `.${rawPath}`)
    : path.resolve(currentDir, rawPath);

  return { kind: "local", filePath: resolvedPath, hash };
}

function isWithinRoot(targetPath: string, reportRoot: string): boolean {
  /**
   * Check whether a report file path stays within the generated report root.
   *
   * Inputs:
   * - `targetPath`: Candidate file path.
   * - `reportRoot`: Root directory of the report bundle.
   *
   * Returns:
   * - `boolean`: True when the file is inside the report directory.
   */
  const relative = path.relative(reportRoot, targetPath);
  return relative.length === 0 || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function escapeHtmlAttribute(value: string): string {
  /**
   * Escape attribute text injected into generated HTML.
   *
   * Inputs:
   * - `value`: Raw attribute value.
   *
   * Returns:
   * - `string`: Escaped attribute text.
   */
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}
