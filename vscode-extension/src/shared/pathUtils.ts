/**
 * This file contains shared path helpers for proof directory layout.
 */
import * as path from "path";

/**
 * Compute the deterministic proof directory for a target source/function pair.
 *
 * Inputs:
 * - `workspaceRoot`: Absolute path to the current workspace root.
 * - `sourceFile`: Absolute path to the target source file.
 * - `functionName`: Function selected for proof generation.
 * - `proofsRoot`: Workspace-relative root for all proof outputs.
 *
 * Returns:
 * - `string`: Absolute path to the proof directory.
 */
export function computeProofDirectory(
  workspaceRoot: string,
  sourceFile: string,
  functionName: string,
  proofsRoot: string,
): string {
  // Preserve the source file's relative directory structure inside the proof root.
  const relativeSource = path.relative(workspaceRoot, sourceFile);
  const parsed = path.parse(relativeSource);
  const sourceWithoutExtension = path.join(parsed.dir, parsed.name);
  // Append the function name so different functions in the same source file get
  // separate proof directories.
  return path.join(workspaceRoot, proofsRoot, sourceWithoutExtension, functionName);
}
