/**
 * This file centralizes persisted proof-job manifest handling so queued,
 * running, and rehydrated jobs all use the same on-disk contract.
 */
import * as fs from "fs/promises";
import * as path from "path";
import type { ExecutionHost, ProofJob, ProofStatus } from "./types";

const PROOF_STATUSES: ProofStatus[] = [
  "queued",
  "starting",
  "bootstrapping",
  "running",
  "completed",
  "failed",
  "cancelled",
  "interrupted",
];

const MANIFEST_LOCK_TIMEOUT_MS = 15000;
const MANIFEST_LOCK_STALE_MS = 30000;
const MANIFEST_LOCK_POLL_MS = 50;

/**
 * Persisted `.autoup/job.json` shape shared by the extension and Python bridge.
 */
export interface PersistedJobManifest {
  jobId: string;
  workspaceRoot: string;
  sourceFile: string;
  functionName: string;
  proofDir: string;
  pid: number | null;
  status: ProofStatus;
  currentStage: string | null;
  createdAt: number;
  updatedAt: number;
  backendVersion: string;
  executionHost: ExecutionHost;
  line: number;
  column: number;
  lastError: string | null;
}

/**
 * Build the persisted manifest for an in-memory proof job.
 *
 * Inputs:
 * - `job`: In-memory job to serialize.
 *
 * Returns:
 * - `PersistedJobManifest`: Manifest representation for disk.
 */
export function manifestFromJob(job: ProofJob): PersistedJobManifest {
  const now = Date.now();
  return {
    jobId: job.id,
    workspaceRoot: job.workspaceRoot,
    sourceFile: job.filePath,
    functionName: job.functionName,
    proofDir: job.proofDir,
    pid: typeof job.process?.pid === "number" ? job.process.pid : null,
    status: job.status,
    currentStage: job.currentStage,
    createdAt: job.createdAt,
    updatedAt: now,
    backendVersion: "extension-v1",
    executionHost: job.executionHost,
    line: job.line,
    column: job.column,
    lastError: job.lastError ?? null,
  };
}

/**
 * Parse an untyped manifest payload into the persisted manifest contract.
 *
 * Inputs:
 * - `raw`: Parsed JSON object from disk.
 * - `fallbackProofDir`: Optional proof directory fallback derived from the file path.
 * - `fallbackWorkspaceRoot`: Optional workspace fallback derived from the VS Code folder.
 *
 * Returns:
 * - `PersistedJobManifest`: Normalized manifest.
 *
 * Throws:
 * - `Error`: When the manifest is missing required string identity fields.
 */
export function parsePersistedJobManifest(
  raw: Record<string, unknown>,
  fallbackProofDir?: string,
  fallbackWorkspaceRoot?: string,
): PersistedJobManifest {
  const proofDir = asNonEmptyString(raw.proofDir) ?? fallbackProofDir;
  const functionName = asNonEmptyString(raw.functionName) ?? (proofDir ? path.basename(proofDir) : undefined);
  const workspaceRoot = asNonEmptyString(raw.workspaceRoot) ?? fallbackWorkspaceRoot;
  const sourceFile = asString(raw.sourceFile) ?? "";
  const jobId = asNonEmptyString(raw.jobId) ?? (functionName ? `${functionName}-${Date.now()}` : undefined);

  if (!proofDir || !functionName || !workspaceRoot || !jobId) {
    throw new Error("Persisted AutoUP job manifest is missing required identity fields.");
  }

  return {
    jobId,
    workspaceRoot,
    sourceFile,
    functionName,
    proofDir,
    pid: asOptionalNumber(raw.pid),
    status: parseProofStatus(raw.status),
    currentStage: asNullableString(raw.currentStage),
    createdAt: asTimestamp(raw.createdAt),
    updatedAt: asTimestamp(raw.updatedAt),
    backendVersion: asNonEmptyString(raw.backendVersion) ?? "unknown",
    executionHost: parseExecutionHost(raw.executionHost),
    line: asPositiveInt(raw.line) ?? 1,
    column: asPositiveInt(raw.column) ?? 1,
    lastError: asNullableString(raw.lastError),
  };
}

/**
 * Normalize a manifest status against the current PID liveness.
 *
 * Inputs:
 * - `status`: Manifest status from disk.
 * - `pid`: Recorded bridge PID, if any.
 * - `pidAlive`: Whether that PID is still alive on the execution host.
 *
 * Returns:
 * - `ProofStatus`: Effective status the extension should render.
 */
export function normalizeManifestStatus(
  status: ProofStatus,
  pid: number | null,
  pidAlive: boolean,
): ProofStatus {
  if (status === "queued") {
    return "queued";
  }

  if (["completed", "failed", "cancelled", "interrupted"].includes(status)) {
    return status;
  }

  if (pid !== null && pidAlive) {
    return status;
  }

  return "interrupted";
}

/**
 * Write a persisted proof manifest atomically.
 *
 * Inputs:
 * - `manifestPath`: Destination `.autoup/job.json` path.
 * - `manifest`: Manifest content to write.
 *
 * Returns:
 * - `Promise<void>`.
 */
export async function writePersistedJobManifest(
  manifestPath: string,
  manifest: PersistedJobManifest,
): Promise<void> {
  await withManifestLock(manifestPath, async () => {
    await writePersistedJobManifestUnlocked(manifestPath, manifest);
  });
}

/**
 * Update a persisted job manifest with partial field changes.
 *
 * Inputs:
 * - `manifestPath`: Existing `.autoup/job.json` path.
 * - `updates`: Partial manifest fields to merge.
 *
 * Returns:
 * - `Promise<PersistedJobManifest>`: The updated manifest content.
 */
export async function updatePersistedJobManifest(
  manifestPath: string,
  updates: Partial<PersistedJobManifest>,
): Promise<PersistedJobManifest> {
  return await withManifestLock(manifestPath, async () => {
    const current = await readPersistedJobManifestUnlocked(manifestPath);
    const updated: PersistedJobManifest = {
      ...current,
      ...updates,
      updatedAt: Date.now(),
    };
    await writePersistedJobManifestUnlocked(manifestPath, updated);
    return updated;
  });
}

/**
 * Merge manifest updates under a shared cross-process lock.
 *
 * Inputs:
 * - `manifestPath`: `.autoup/job.json` path to update.
 * - `baseManifest`: Fallback manifest when the file does not exist yet.
 * - `updates`: Partial manifest fields to overlay on the latest on-disk state.
 *
 * Returns:
 * - `Promise<PersistedJobManifest>`: The merged manifest written to disk.
 */
export async function upsertPersistedJobManifest(
  manifestPath: string,
  baseManifest: PersistedJobManifest,
  updates: Partial<PersistedJobManifest> = {},
): Promise<PersistedJobManifest> {
  return await withManifestLock(manifestPath, async () => {
    const current = await tryReadPersistedJobManifestUnlocked(manifestPath);
    const updated: PersistedJobManifest = current
      ? {
          ...current,
          jobId: baseManifest.jobId,
          workspaceRoot: baseManifest.workspaceRoot,
          sourceFile: baseManifest.sourceFile,
          functionName: baseManifest.functionName,
          proofDir: baseManifest.proofDir,
          executionHost: baseManifest.executionHost,
          line: baseManifest.line,
          column: baseManifest.column,
          ...updates,
          updatedAt: Date.now(),
        }
      : {
          ...baseManifest,
          ...updates,
          updatedAt: Date.now(),
        };
    await writePersistedJobManifestUnlocked(manifestPath, updated);
    return updated;
  });
}

/**
 * Read and parse a persisted proof manifest.
 *
 * Inputs:
 * - `manifestPath`: `.autoup/job.json` path to read.
 *
 * Returns:
 * - `Promise<PersistedJobManifest>`: Parsed manifest content.
 */
export async function readPersistedJobManifest(manifestPath: string): Promise<PersistedJobManifest> {
  return await readPersistedJobManifestUnlocked(manifestPath);
}

async function readPersistedJobManifestUnlocked(manifestPath: string): Promise<PersistedJobManifest> {
  const raw = await fs.readFile(manifestPath, "utf-8");
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const fallbackProofDir = path.dirname(path.dirname(manifestPath));
  return parsePersistedJobManifest(parsed, fallbackProofDir);
}

async function tryReadPersistedJobManifestUnlocked(
  manifestPath: string,
): Promise<PersistedJobManifest | null> {
  try {
    return await readPersistedJobManifestUnlocked(manifestPath);
  } catch (error) {
    if (isMissingFileError(error)) {
      return null;
    }
    throw error;
  }
}

async function writePersistedJobManifestUnlocked(
  manifestPath: string,
  manifest: PersistedJobManifest,
): Promise<void> {
  await fs.mkdir(path.dirname(manifestPath), { recursive: true });
  const tempPath = `${manifestPath}.tmp.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}`;
  try {
    await fs.writeFile(tempPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf-8");
    await fs.rename(tempPath, manifestPath);
  } finally {
    try {
      await fs.unlink(tempPath);
    } catch {
      // Ignore missing temp cleanup.
    }
  }
}

async function withManifestLock<T>(
  manifestPath: string,
  callback: () => Promise<T>,
): Promise<T> {
  const lockPath = `${manifestPath}.lock`;
  const token = `${process.pid}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
  const deadline = Date.now() + MANIFEST_LOCK_TIMEOUT_MS;

  while (true) {
    try {
      const handle = await fs.open(lockPath, "wx");
      try {
        await handle.writeFile(token, "utf-8");
      } finally {
        await handle.close();
      }
      break;
    } catch (error) {
      if (!isAlreadyExistsError(error)) {
        throw error;
      }

      if (await isManifestLockStale(lockPath)) {
        await removeManifestLockIfStale(lockPath);
        continue;
      }

      if (Date.now() >= deadline) {
        throw new Error(`Timed out waiting for AutoUP manifest lock: ${lockPath}`);
      }
      await sleep(MANIFEST_LOCK_POLL_MS);
    }
  }

  try {
    return await callback();
  } finally {
    await releaseManifestLock(lockPath, token);
  }
}

async function isManifestLockStale(lockPath: string): Promise<boolean> {
  try {
    const stats = await fs.stat(lockPath);
    return Date.now() - stats.mtimeMs > MANIFEST_LOCK_STALE_MS;
  } catch (error) {
    if (isMissingFileError(error)) {
      return false;
    }
    throw error;
  }
}

async function removeManifestLockIfStale(lockPath: string): Promise<void> {
  try {
    const stats = await fs.stat(lockPath);
    if (Date.now() - stats.mtimeMs <= MANIFEST_LOCK_STALE_MS) {
      return;
    }
    await fs.unlink(lockPath);
  } catch (error) {
    if (isMissingFileError(error)) {
      return;
    }
    throw error;
  }
}

async function releaseManifestLock(lockPath: string, token: string): Promise<void> {
  try {
    const currentToken = await fs.readFile(lockPath, "utf-8");
    if (currentToken !== token) {
      return;
    }
    await fs.unlink(lockPath);
  } catch (error) {
    if (isMissingFileError(error)) {
      return;
    }
    throw error;
  }
}

function isAlreadyExistsError(error: unknown): boolean {
  return Boolean(error && typeof error === "object" && "code" in error && (error as NodeJS.ErrnoException).code === "EEXIST");
}

function isMissingFileError(error: unknown): boolean {
  return Boolean(error && typeof error === "object" && "code" in error && (error as NodeJS.ErrnoException).code === "ENOENT");
}

async function sleep(durationMs: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, durationMs));
}

/**
 * Check whether a proof status consumes a runtime slot.
 *
 * Inputs:
 * - `status`: Proof status to classify.
 *
 * Returns:
 * - `boolean`: True when the job should count as active.
 */
export function isRuntimeActiveStatus(status: ProofStatus): boolean {
  return ["starting", "bootstrapping", "running"].includes(status);
}

function parseExecutionHost(value: unknown): ExecutionHost {
  const host = asNonEmptyString(value);
  switch (host) {
    case "local-macos":
    case "local-linux":
    case "remote-ssh-linux":
    case "unsupported-windows":
      return host;
    default:
      return "local-linux";
  }
}

function parseProofStatus(value: unknown): ProofStatus {
  return typeof value === "string" && PROOF_STATUSES.includes(value as ProofStatus)
    ? (value as ProofStatus)
    : "interrupted";
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asNullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asOptionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asPositiveInt(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
}

function asTimestamp(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : Date.now();
}
