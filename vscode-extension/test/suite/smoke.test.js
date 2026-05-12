"use strict";

/**
 * This file contains a basic extension-host smoke suite. It verifies that the
 * extension activates, registers commands, and that function detection works on
 * a simple C fixture without requiring the full AutoUP backend runtime.
 */

const assert = require("assert");
const fs = require("fs");
const fsp = require("fs/promises");
const os = require("os");
const { suite, test } = require("mocha");
const path = require("path");
const vscode = require("vscode");

suite("AutoUP Extension Smoke", () => {
  test("extension activates and registers commands", async () => {
    // Activate the extension through its declared identifier.
    const extension = vscode.extensions.getExtension("autoup.autoup-vscode");
    assert.ok(extension, "Expected AutoUP extension to be present in the test host.");
    await extension.activate();

    // Confirm a representative subset of commands is available after activation.
    const commands = await vscode.commands.getCommands(true);
    assert.ok(commands.includes("autoup.createUnitProof"));
    assert.ok(commands.includes("autoup.refreshProofs"));
    assert.ok(commands.includes("autoup.showProofOutput"));
  });

  test("computeProofDirectory preserves relative source structure", async () => {
    // Load the compiled helper module directly and validate proof path layout.
    const modulePath = path.resolve(__dirname, "..", "..", "dist", "shared", "pathUtils.js");
    const { computeProofDirectory } = require(modulePath);
    const result = computeProofDirectory(
      "/workspace/demo",
      "/workspace/demo/src/net/tcp/file.c",
      "target_fn",
      "cbmc/proofs",
    );
    assert.strictEqual(
      result,
      path.join("/workspace/demo", "cbmc/proofs", "src/net/tcp/file", "target_fn"),
    );
  });

  test("function detector resolves enclosing C function", async () => {
    // Open the fixture file and place the cursor inside the target function body.
    const fixture = path.resolve(__dirname, "..", "fixture", "simple-project", "src", "sample.c");
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(fixture));
    const editor = await vscode.window.showTextDocument(document);
    editor.selection = new vscode.Selection(new vscode.Position(5, 8), new vscode.Position(5, 8));

    // Import the compiled detector helper and resolve the function from the editor state.
    const detectorPath = path.resolve(
      __dirname,
      "..",
      "..",
      "dist",
      "features",
      "createProof",
      "functionDetector.js",
    );
    const { detectFunctionAtCursor } = require(detectorPath);
    const resolved = await detectFunctionAtCursor(editor);

    assert.ok(resolved, "Expected a function to be resolved.");
    assert.strictEqual(resolved.functionName, "sample_target");
    assert.strictEqual(resolved.filePath, fixture);
  });

  test("proof tree shows artifact actions as files become available", async () => {
    const providerPath = path.resolve(
      __dirname,
      "..",
      "..",
      "dist",
      "features",
      "proofPanel",
      "proofTreeProvider.js",
    );
    const { ProofTreeProvider } = require(providerPath);

    const tempRoot = await fsp.mkdtemp(path.join(os.tmpdir(), "autoup-tree-"));
    const proofDir = path.join(tempRoot, "proof");
    const sourceFile = path.join(tempRoot, "sample.c");
    const logFile = path.join(proofDir, "autoup.log");
    const harnessFile = path.join(proofDir, "sample_target_harness.c");
    const makefilePath = path.join(proofDir, "Makefile");
    const reportPath = path.join(proofDir, "build", "report", "html", "index.html");

    await fsp.mkdir(proofDir, { recursive: true });
    await fsp.writeFile(sourceFile, "int sample_target(int x) { return x; }\n", "utf8");
    await fsp.writeFile(logFile, "log\n", "utf8");

    const fakeManager = {
      getAllJobs() {
        return [];
      },
      onDidChangeProofs() {
        return { dispose() {} };
      },
    };

    const provider = new ProofTreeProvider(fakeManager);
    const outputChannel = createFakeOutputChannel();
    const fakeJob = {
      id: "job-1",
      functionName: "sample_target",
      filePath: sourceFile,
      workspaceRoot: tempRoot,
      proofDir,
      logFile,
      metricsFile: path.join(proofDir, "metrics.jsonl"),
      eventFile: path.join(proofDir, ".autoup", "events.jsonl"),
      jobFile: path.join(proofDir, ".autoup", "job.json"),
      status: "running",
      currentStage: "InitialHarnessGenerator",
      completedStages: [],
      verificationSummary: null,
      executionHost: "local-linux",
      outputChannel,
      process: null,
      createdAt: Date.now(),
      trackerStarted: false,
      lastEventCount: 0,
    };

    const initialChildren = await provider.getChildren({ kind: "job", job: fakeJob });
    const initialLabels = initialChildren.map((child) => child.label);
    assert.ok(initialLabels.includes("Open Source File"));
    assert.ok(initialLabels.includes("Open Log"));
    assert.ok(!initialLabels.includes("Open Harness"));
    assert.ok(!initialLabels.includes("Open Makefile"));
    assert.ok(!initialLabels.includes("Open Report"));

    await fsp.writeFile(harnessFile, "void harness(void) {}\n", "utf8");
    await fsp.writeFile(makefilePath, "all:\n\t@true\n", "utf8");
    await fsp.mkdir(path.dirname(reportPath), { recursive: true });
    await fsp.writeFile(reportPath, "<html><body>report</body></html>\n", "utf8");

    const updatedChildren = await provider.getChildren({ kind: "job", job: fakeJob });
    const updatedLabels = updatedChildren.map((child) => child.label);
    assert.ok(updatedLabels.includes("Open Harness"));
    assert.ok(updatedLabels.includes("Open Makefile"));
    assert.ok(updatedLabels.includes("Open Report"));

    await fsp.rm(tempRoot, { recursive: true, force: true });
  });

  test("job rows use cancel for active proofs and delete for completed proofs", async () => {
    const itemPath = path.resolve(
      __dirname,
      "..",
      "..",
      "dist",
      "features",
      "proofPanel",
      "proofTreeItem.js",
    );
    const { ProofTreeItem } = require(itemPath);

    const outputChannel = createFakeOutputChannel();
    const baseJob = {
      id: "job-2",
      functionName: "sample_target",
      filePath: "/tmp/sample.c",
      workspaceRoot: "/tmp",
      proofDir: "/tmp/proof",
      logFile: "/tmp/proof/autoup.log",
      metricsFile: "/tmp/proof/metrics.jsonl",
      eventFile: "/tmp/proof/.autoup/events.jsonl",
      jobFile: "/tmp/proof/.autoup/job.json",
      currentStage: null,
      completedStages: [],
      verificationSummary: null,
      executionHost: "local-linux",
      outputChannel,
      process: null,
      createdAt: Date.now(),
      trackerStarted: false,
      lastEventCount: 0,
    };

    const activeItem = new ProofTreeItem(
      { kind: "job", job: { ...baseJob, status: "running" } },
      vscode.TreeItemCollapsibleState.Collapsed,
    );
    const completedItem = new ProofTreeItem(
      { kind: "job", job: { ...baseJob, status: "completed" } },
      vscode.TreeItemCollapsibleState.Collapsed,
    );

    assert.strictEqual(activeItem.contextValue, "proofJobCancellable");
    assert.strictEqual(completedItem.contextValue, "proofJobDeletable");
  });

  test("open harness command accepts a tree-node style argument", async () => {
    const extension = vscode.extensions.getExtension("autoup.autoup-vscode");
    assert.ok(extension, "Expected AutoUP extension to be present in the test host.");
    await extension.activate();

    const tempRoot = await fsp.mkdtemp(path.join(os.tmpdir(), "autoup-open-"));
    const proofDir = path.join(tempRoot, "proof");
    const harnessFile = path.join(proofDir, "sample_target_harness.c");
    await fsp.mkdir(proofDir, { recursive: true });
    await fsp.writeFile(harnessFile, "void harness(void) {}\n", "utf8");

    const outputChannel = createFakeOutputChannel();
    const fakeJob = {
      id: "job-3",
      functionName: "sample_target",
      filePath: path.join(tempRoot, "sample.c"),
      workspaceRoot: tempRoot,
      proofDir,
      logFile: path.join(proofDir, "autoup.log"),
      metricsFile: path.join(proofDir, "metrics.jsonl"),
      eventFile: path.join(proofDir, ".autoup", "events.jsonl"),
      jobFile: path.join(proofDir, ".autoup", "job.json"),
      status: "completed",
      currentStage: null,
      completedStages: [],
      verificationSummary: null,
      executionHost: "local-linux",
      outputChannel,
      process: null,
      createdAt: Date.now(),
      trackerStarted: false,
      lastEventCount: 0,
    };

    await vscode.commands.executeCommand("autoup.openHarness", { kind: "job", job: fakeJob });

    assert.ok(vscode.window.activeTextEditor, "Expected an editor to be opened.");
    assert.strictEqual(vscode.window.activeTextEditor.document.uri.fsPath, harnessFile);

    await fsp.rm(tempRoot, { recursive: true, force: true });
  });

  test("proof manager resolves rendered tree-item action arguments", async () => {
    const itemPath = path.resolve(
      __dirname,
      "..",
      "..",
      "dist",
      "features",
      "proofPanel",
      "proofTreeItem.js",
    );
    const { ProofTreeItem } = require(itemPath);
    const managerPath = path.resolve(__dirname, "..", "..", "dist", "core", "proofManager.js");
    const { ProofManager } = require(managerPath);

    const tempRoot = await fsp.mkdtemp(path.join(os.tmpdir(), "autoup-open-item-"));
    const proofDir = path.join(tempRoot, "proof");
    await fsp.mkdir(proofDir, { recursive: true });

    const outputChannel = createFakeOutputChannel();
    const fakeJob = {
      id: "job-4",
      functionName: "sample_target",
      filePath: path.join(tempRoot, "sample.c"),
      workspaceRoot: tempRoot,
      proofDir,
      logFile: path.join(proofDir, "autoup.log"),
      metricsFile: path.join(proofDir, "metrics.jsonl"),
      eventFile: path.join(proofDir, ".autoup", "events.jsonl"),
      jobFile: path.join(proofDir, ".autoup", "job.json"),
      status: "completed",
      currentStage: null,
      completedStages: [],
      verificationSummary: null,
      executionHost: "local-linux",
      outputChannel,
      process: null,
      createdAt: Date.now(),
      trackerStarted: false,
      lastEventCount: 0,
    };

    const actionItem = new ProofTreeItem(
      {
        kind: "action",
        label: "Open Makefile",
        command: "autoup.openMakefile",
        jobId: fakeJob.id,
        contextValue: "proofActionMakefile",
      },
      vscode.TreeItemCollapsibleState.None,
    );

    const fakeManager = {
      jobs: new Map([[fakeJob.id, fakeJob]]),
      resolveJob(target) {
        return ProofManager.prototype.resolveJob.call(this, target);
      },
    };
    const resolved = fakeManager.resolveJob(actionItem);
    assert.strictEqual(resolved, fakeJob);

    await fsp.rm(tempRoot, { recursive: true, force: true });
  });

  test("status and platform helpers normalize unsupported or stale state", async () => {
    const persistencePath = path.resolve(__dirname, "..", "..", "dist", "core", "jobPersistence.js");
    const trackerPath = path.resolve(__dirname, "..", "..", "dist", "core", "statusTracker.js");
    const platformPath = path.resolve(__dirname, "..", "..", "dist", "shared", "platform.js");
    const { normalizeManifestStatus } = require(persistencePath);
    const { parseEventLines } = require(trackerPath);
    const { normalizeExecutionHost } = require(platformPath);

    assert.strictEqual(normalizeManifestStatus("running", 42, false), "interrupted");
    assert.strictEqual(normalizeManifestStatus("queued", null, false), "queued");
    assert.strictEqual(normalizeExecutionHost(undefined, "win32"), "unsupported-windows");

    const parsed = parseEventLines(
      [
        JSON.stringify({ type: "job_started", jobId: "job-1", timestamp: 1 }),
        "{ this is not valid json",
        JSON.stringify({ type: "job_completed", jobId: "job-1", timestamp: 2 }),
        '{"type":"job_started"',
      ],
      0,
    );

    assert.strictEqual(parsed.events.length, 2);
    assert.strictEqual(parsed.warnings.length, 1);
    assert.strictEqual(parsed.nextIndex, 3);
  });

  test("proof runner forwards resolved line and column to the bridge", async () => {
    const modulePath = path.resolve(
      __dirname,
      "..",
      "..",
      "dist",
      "features",
      "createProof",
      "proofRunner.js",
    );
    delete require.cache[modulePath];

    const childProcess = require("child_process");
    const originalSpawn = childProcess.spawn;
    const originalEnvKey = process.env.OPENAI_API_KEY;
    let capturedArgs = null;
    childProcess.spawn = (command, args, options) => {
      capturedArgs = { command, args, options };
      return {
        pid: 12345,
        unref() {},
      };
    };

    const tempRoot = await fsp.mkdtemp(path.join(os.tmpdir(), "autoup-proof-runner-"));
    const outputChannel = createFakeOutputChannel();
    try {
      const { startProofProcess } = require(modulePath);
      const job = {
        id: "job-5",
        functionName: "sample_target",
        filePath: path.join(tempRoot, "sample.c"),
        line: 17,
        column: 5,
        workspaceRoot: tempRoot,
        proofDir: path.join(tempRoot, "proof"),
        logFile: path.join(tempRoot, "proof", "autoup.log"),
        metricsFile: path.join(tempRoot, "proof", "metrics.jsonl"),
        eventFile: path.join(tempRoot, "proof", ".autoup", "events.jsonl"),
        jobFile: path.join(tempRoot, "proof", ".autoup", "job.json"),
        status: "queued",
        currentStage: null,
        completedStages: [],
        verificationSummary: null,
        executionHost: "local-linux",
        lastError: null,
        outputChannel,
        process: null,
        createdAt: Date.now(),
        trackerStarted: false,
        lastEventCount: 0,
      };

      startProofProcess(job, {
        runtime: {
          backendRoot: tempRoot,
          pythonPath: "/usr/bin/python3",
        },
        config: {
          model: "gpt-5.3-codex",
          containerEngine: "auto",
          scopeBound: null,
          scopeTimeBudget: null,
          makeTimeout: 600,
          maxConcurrentJobs: 2,
          proofsRoot: "cbmc/proofs",
        },
        prerequisites: {
          apiKey: "secret",
          apiKeyEnvVar: "OPENAI_API_KEY",
          containerEngine: "docker",
        },
        executionHost: "local-linux",
      });

      assert.ok(capturedArgs, "Expected the bridge process to be spawned.");
      const lineIndex = capturedArgs.args.indexOf("--line");
      const columnIndex = capturedArgs.args.indexOf("--column");
      assert.strictEqual(capturedArgs.args[lineIndex + 1], "17");
      assert.strictEqual(capturedArgs.args[columnIndex + 1], "5");
    } finally {
      childProcess.spawn = originalSpawn;
      if (originalEnvKey === undefined) {
        delete process.env.OPENAI_API_KEY;
      } else {
        process.env.OPENAI_API_KEY = originalEnvKey;
      }
      await fsp.rm(tempRoot, { recursive: true, force: true });
      delete require.cache[modulePath];
    }
  });

  test("proof manager rehydrates queued jobs, orders them by age, and skips malformed manifests", async () => {
    const managerPath = path.resolve(__dirname, "..", "..", "dist", "core", "proofManager.js");
    const { ProofManager } = require(managerPath);

    const tempWorkspace = await fsp.mkdtemp(path.join(os.tmpdir(), "autoup-rehydrate-"));
    const firstProofDir = path.join(tempWorkspace, "cbmc", "proofs", "src", "alpha", "fn_a");
    const secondProofDir = path.join(tempWorkspace, "cbmc", "proofs", "src", "beta", "fn_b");
    const malformedDir = path.join(tempWorkspace, "cbmc", "proofs", "src", "bad", "fn_bad");
    await fsp.mkdir(path.join(firstProofDir, ".autoup"), { recursive: true });
    await fsp.mkdir(path.join(secondProofDir, ".autoup"), { recursive: true });
    await fsp.mkdir(path.join(malformedDir, ".autoup"), { recursive: true });

    const writeManifest = async (proofDir, manifest) => {
      await fsp.writeFile(
        path.join(proofDir, ".autoup", "job.json"),
        `${JSON.stringify(manifest, null, 2)}\n`,
        "utf8",
      );
    };

    await writeManifest(firstProofDir, {
      jobId: "job-old",
      workspaceRoot: tempWorkspace,
      sourceFile: path.join(tempWorkspace, "src", "alpha.c"),
      functionName: "fn_a",
      proofDir: firstProofDir,
      pid: null,
      status: "queued",
      currentStage: null,
      createdAt: 10,
      updatedAt: 10,
      backendVersion: "extension-v1",
      executionHost: "local-linux",
      line: 4,
      column: 2,
      lastError: null,
    });
    await writeManifest(secondProofDir, {
      jobId: "job-new",
      workspaceRoot: tempWorkspace,
      sourceFile: path.join(tempWorkspace, "src", "beta.c"),
      functionName: "fn_b",
      proofDir: secondProofDir,
      pid: null,
      status: "queued",
      currentStage: null,
      createdAt: 20,
      updatedAt: 20,
      backendVersion: "extension-v1",
      executionHost: "local-linux",
      line: 8,
      column: 3,
      lastError: null,
    });
    await fsp.writeFile(path.join(malformedDir, ".autoup", "job.json"), "{ malformed", "utf8");

    const startingWorkspaceCount = vscode.workspace.workspaceFolders?.length ?? 0;
    await waitForWorkspaceFoldersChange(() => {
      vscode.workspace.updateWorkspaceFolders(startingWorkspaceCount, 0, {
        uri: vscode.Uri.file(tempWorkspace),
        name: "AutoUP Temp Workspace",
      });
    });

    const configManager = {
      getConfig() {
        return {
          model: "gpt-5.3-codex",
          containerEngine: "auto",
          scopeBound: null,
          scopeTimeBudget: null,
          makeTimeout: 600,
          maxConcurrentJobs: 0,
          proofsRoot: "cbmc/proofs",
        };
      },
    };
    const fakeContext = {
      globalStorageUri: vscode.Uri.file(path.join(tempWorkspace, ".global-storage")),
      extensionUri: vscode.Uri.file(path.resolve(__dirname, "..", "..")),
    };

    const originalCreateOutputChannel = vscode.window.createOutputChannel;
    vscode.window.createOutputChannel = () => createFakeOutputChannel();
    const manager = new ProofManager(fakeContext, configManager, {}, {});
    try {
      await manager.initialize();
      const jobs = manager.getAllJobs();
      assert.strictEqual(jobs.length, 2);
      assert.deepStrictEqual(manager.queuedIds, ["job-old", "job-new"]);
      assert.ok(jobs.every((job) => job.status === "queued"));
    } finally {
      vscode.window.createOutputChannel = originalCreateOutputChannel;
      manager.dispose();
      await waitForWorkspaceFoldersChange(() => {
        vscode.workspace.updateWorkspaceFolders(startingWorkspaceCount, 1);
      });
      await fsp.rm(tempWorkspace, { recursive: true, force: true });
    }
  });

  test("queued launch failures become failed and runtime install retries after failure", async () => {
    const managerPath = path.resolve(__dirname, "..", "..", "dist", "core", "proofManager.js");
    const { ProofManager } = require(managerPath);

    const tempRoot = await fsp.mkdtemp(path.join(os.tmpdir(), "autoup-queue-fail-"));
    const config = {
      model: "gpt-5.3-codex",
      containerEngine: "auto",
      scopeBound: null,
      scopeTimeBudget: null,
      makeTimeout: 600,
      maxConcurrentJobs: 1,
      proofsRoot: "cbmc/proofs",
    };

    let installAttempts = 0;
    const manager = new ProofManager(
      {
        globalStorageUri: vscode.Uri.file(path.join(tempRoot, ".global-storage")),
        extensionUri: vscode.Uri.file(path.resolve(__dirname, "..", "..")),
      },
      {
        getConfig() {
          return config;
        },
      },
      {
        async ensureInstalled() {
          installAttempts += 1;
          if (installAttempts === 1) {
            throw new Error("transient install failure");
          }
          return {
            backendRoot: tempRoot,
            pythonPath: "/usr/bin/python3",
          };
        },
      },
      {},
    );

    const firstRuntimeError = await ProofManager.prototype.getRuntime.call(manager, config).catch((error) => error);
    assert.ok(firstRuntimeError instanceof Error);
    const runtime = await ProofManager.prototype.getRuntime.call(manager, config);
    assert.strictEqual(runtime.pythonPath, "/usr/bin/python3");
    assert.strictEqual(installAttempts, 2);

    const proofDir = path.join(tempRoot, "proof");
    await fsp.mkdir(path.join(proofDir, ".autoup"), { recursive: true });
    const outputChannel = createFakeOutputChannel();
    const queuedJob = {
      id: "job-queued",
      functionName: "sample_target",
      filePath: path.join(tempRoot, "sample.c"),
      line: 3,
      column: 7,
      workspaceRoot: tempRoot,
      proofDir,
      logFile: path.join(proofDir, "autoup.log"),
      metricsFile: path.join(proofDir, "metrics.jsonl"),
      eventFile: path.join(proofDir, ".autoup", "events.jsonl"),
      jobFile: path.join(proofDir, ".autoup", "job.json"),
      status: "queued",
      currentStage: null,
      completedStages: [],
      verificationSummary: null,
      executionHost: "local-linux",
      lastError: null,
      outputChannel,
      process: null,
      createdAt: Date.now(),
      trackerStarted: false,
      lastEventCount: 0,
    };

    manager.jobs.set(queuedJob.id, queuedJob);
    manager.queuedIds.push(queuedJob.id);
    await manager.persistJobManifest(queuedJob);
    manager.launchJob = async () => {
      throw new Error("launch boom");
    };

    try {
      await manager.maybeStartNextQueued();
      assert.strictEqual(queuedJob.status, "failed");
      assert.strictEqual(queuedJob.lastError, "launch boom");
      assert.deepStrictEqual(manager.queuedIds, []);

      const persisted = JSON.parse(await fsp.readFile(queuedJob.jobFile, "utf8"));
      assert.strictEqual(persisted.status, "failed");
      assert.strictEqual(persisted.lastError, "launch boom");
    } finally {
      manager.dispose();
      await fsp.rm(tempRoot, { recursive: true, force: true });
    }
  });
});

function createFakeOutputChannel() {
  return {
    appendLine() {},
    show() {},
    clear() {},
    dispose() {},
  };
}

async function waitForWorkspaceFoldersChange(action) {
  return await new Promise((resolve) => {
    const disposable = vscode.workspace.onDidChangeWorkspaceFolders(() => {
      disposable.dispose();
      resolve();
    });
    const changed = action();
    if (changed === false) {
      disposable.dispose();
      resolve();
      return;
    }
    setTimeout(() => {
      disposable.dispose();
      resolve();
    }, 100);
  });
}
