"use strict";

/**
 * This file launches the VS Code extension test host using the official
 * Electron-based extension test harness.
 */

const path = require("path");
const { runTests } = require("@vscode/test-electron");

async function main() {
  // Clear Electron's Node-only mode so the downloaded VS Code binary starts as
  // the actual desktop test host instead of a plain Node process.
  delete process.env.ELECTRON_RUN_AS_NODE;

  // Point the harness at the extension source directory and the compiled test suite.
  const extensionDevelopmentPath = path.resolve(__dirname, "..");
  const extensionTestsPath = path.resolve(__dirname, "suite", "index.js");
  await runTests({
    extensionDevelopmentPath,
    extensionTestsPath,
    launchArgs: ["--no-sandbox"],
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
