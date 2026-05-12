"use strict";

/**
 * This file is the Mocha entrypoint executed inside the VS Code extension host.
 */

const path = require("path");
const Mocha = require("mocha");

async function run() {
  const mocha = new Mocha({
    ui: "bdd",
    color: true,
    timeout: 20000,
  });

  // Register the smoke test file for execution inside the extension host.
  mocha.addFile(path.resolve(__dirname, "smoke.test.js"));

  return await new Promise((resolve, reject) => {
    mocha.run((failures) => {
      if (failures > 0) {
        reject(new Error(`${failures} extension test(s) failed.`));
        return;
      }
      resolve();
    });
  });
}

module.exports = { run };
