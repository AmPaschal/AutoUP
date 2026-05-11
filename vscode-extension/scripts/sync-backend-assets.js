"use strict";

/**
 * This script copies the AutoUP backend assets that the VS Code extension
 * needs into the extension package directory. The packaged extension cannot
 * assume it still lives next to the full repository checkout, so these assets
 * must be bundled before creating a VSIX.
 */

const fs = require("fs");
const path = require("path");

const BACKEND_ASSETS = [
  "src",
  "prompts",
  "container",
  "requirements.txt",
  "model_pricing.json",
];

/**
 * Remove a directory recursively when it already exists.
 *
 * Inputs:
 * - `targetPath`: Directory path to remove.
 *
 * Returns:
 * - `void`.
 */
function removeIfPresent(targetPath) {
  if (!fs.existsSync(targetPath)) {
    return;
  }
  fs.rmSync(targetPath, { recursive: true, force: true });
}

/**
 * Copy the backend assets from the repository root into the extension package.
 *
 * Inputs:
 * - None. Uses the script location to infer the repository and extension roots.
 *
 * Returns:
 * - `void`.
 */
function main() {
  const extensionRoot = path.resolve(__dirname, "..");
  const repositoryRoot = path.resolve(extensionRoot, "..");
  const bundledRoot = path.join(extensionRoot, ".autoup-bundle");

  // Start from a clean destination so removed files do not linger in later packages.
  removeIfPresent(bundledRoot);
  fs.mkdirSync(bundledRoot, { recursive: true });

  // Copy every backend asset the runtime bootstrap expects.
  for (const asset of BACKEND_ASSETS) {
    const source = path.join(repositoryRoot, asset);
    const destination = path.join(bundledRoot, asset);
    fs.cpSync(source, destination, { recursive: true, force: true });
  }
}

main();
