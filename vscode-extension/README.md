# AutoUP VS Code Extension

This extension integrates AutoUP into VS Code so a user can start unit-proof generation directly from a C/C++ source file instead of manually invoking `src/run.py`.

## What It Does

- Adds a `Create Unit Proof` editor context-menu action for C/C++ files.
- Detects the enclosing function at the current cursor position.
- Starts AutoUP proof generation for that function.
- Stores generated proofs under:
  `cbmc/proofs/<relative-source-path-without-ext>/<function>/`
- Supports multiple proof jobs at once with queueing when the concurrency limit is reached.
- Shows proof jobs in a sidebar with:
  - current status
  - current stage
  - verification summary
  - quick actions to open the harness, Makefile, source file, log, or proof folder
- Persists job state in:
  - `.autoup/job.json`
  - `.autoup/events.jsonl`

## Supported Execution Modes

The extension is implemented as a workspace extension. Proof execution happens wherever the VS Code workspace extension host is running.

- macOS local workspace: supported
- Linux local workspace: supported
- Remote-SSH to Linux: supported
- Windows local workspace: not supported in v1
- Browser-only VS Code / Codespaces web: not supported in v1

## Runtime Requirements

The extension still needs the same underlying proof-generation dependencies as AutoUP, but it manages the AutoUP Python environment itself.

- Python 3 available on the execution host
- OpenAI API key
- Container engine:
  - macOS: Docker only
  - Linux: Docker or Apptainer

The API key is resolved in this order:

1. VS Code `SecretStorage`
2. `OPENAI_API_KEY` environment variable
3. interactive prompt on first use

## Backend Packaging

The extension bundles the required AutoUP backend assets into the VSIX under `.autoup-bundle/`.

During source development, the build also regenerates that bundled backend directory from the parent AutoUP repository so packaged installs and source runs use the same asset layout.

## Extension Features

### Editor Command

Right-click inside a C or C++ function and choose:

- `AutoUP: Create Unit Proof`

The command is only shown for trusted workspaces and C/C++ files.

### Sidebar

The `AutoUP` activity-bar view contains a `Unit Proofs` tree showing one row per proof job.

Each job shows:

- proof status
- current stage
- verification summary when available:
  - instrumented properties
  - verified properties
  - grouped errors by line count
  - verification coverage

Each job also exposes actions to:

- open the harness file
- open the Makefile
- open the source file
- open the AutoUP log
- reveal the proof folder
- open the proof log from the job row
- cancel the proof

### Proof Storage Layout

Proofs are written under the configured proofs root, which defaults to `cbmc/proofs`.

Example:

```text
src/net/tcp/gnrc_tcp_eventloop.c
function: _receive
```

becomes:

```text
cbmc/proofs/src/net/tcp/gnrc_tcp_eventloop/_receive/
```

### Live Progress Model

The extension reads progress from backend event files instead of parsing stdout as the source of truth.

Important event types include:

- `job_started`
- `bootstrap_started`
- `bootstrap_completed`
- `stage_started`
- `stage_progress`
- `refinement_accepted`
- `summary_updated`
- `stage_completed`
- `job_failed`
- `job_cancelled`
- `job_completed`

The sidebar updates not only when a stage finishes, but also after each accepted refinement.

### Background Job Behavior

Proof jobs are launched as detached background processes.

This means:

- Remote-SSH proofs can continue running on the Linux server even if the Mac client disconnects, sleeps, or closes VS Code, as long as the remote host itself stays up.
- When VS Code reconnects, the extension rehydrates job state from `.autoup/job.json` and `.autoup/events.jsonl`.
- Local macOS proofs survive VS Code window reloads or app closure better, but they do not make progress while the Mac itself is asleep.

## Configuration

The extension contributes these settings:

- `autoup.model`
- `autoup.containerEngine`
- `autoup.scopeBound`
- `autoup.scopeTimeBudget`
- `autoup.maxConcurrentJobs`
- `autoup.proofsRoot`

Defaults:

- `autoup.model = gpt-5.3-codex`
- `autoup.containerEngine = auto`
- `autoup.maxConcurrentJobs = 2`
- `autoup.proofsRoot = cbmc/proofs`

## Using the Extension

### Normal Usage

1. Open a C/C++ project in VS Code.
2. Ensure the workspace is trusted.
3. Place the cursor inside the function you want to prove.
4. Right-click and choose `Create Unit Proof`.
5. Watch the `AutoUP` sidebar for progress and status updates.
6. Use the sidebar actions to inspect the generated harness, Makefile, log, or source file.
7. Click a proof job row to open its current log file.

### First Launch Behavior

On first use, the extension:

- creates extension-managed runtime storage
- copies bundled AutoUP backend assets into that storage
- creates a private Python virtual environment
- installs Python dependencies into that virtual environment
- validates the container engine and API key

### Remote-SSH Behavior

When VS Code is connected to a Linux server through Remote-SSH:

- bootstrap happens on the remote host
- Docker/Apptainer checks happen on the remote host
- proof jobs run on the remote host
- job files and proof artifacts live on the remote workspace filesystem

## Development Setup

From the `vscode-extension/` directory:

```bash
npm install
npm run compile
```

To run the extension in a VS Code extension development host:

1. Open the AutoUP repository in VS Code.
2. Open the `vscode-extension/` folder or workspace files as needed.
3. Start an Extension Development Host from VS Code, for example with `F5` from the extension project.

## Test Coverage

The current extension test suite is a smoke suite, not a full end-to-end proof-generation suite.

It currently verifies:

- the extension activates in a real VS Code extension host
- the main commands are registered
- proof-path computation preserves relative source structure
- function detection resolves the enclosing function in a simple C fixture

### Test Files

- [test/runTest.js](https://github.com/AmPaschal/AutoUP/blob/main/vscode-extension/test/runTest.js)
  Launches the VS Code extension-host tests using `@vscode/test-electron`.
- [test/suite/index.js](https://github.com/AmPaschal/AutoUP/blob/main/vscode-extension/test/suite/index.js)
  Mocha entrypoint executed inside the extension host.
- [test/suite/smoke.test.js](https://github.com/AmPaschal/AutoUP/blob/main/vscode-extension/test/suite/smoke.test.js)
  Smoke tests for activation, commands, path layout, and function detection.
- [test/fixture/simple-project/src/sample.c](https://github.com/AmPaschal/AutoUP/blob/main/vscode-extension/test/fixture/simple-project/src/sample.c)
  Simple C fixture used by the smoke tests.

## Running Tests

### Compile Only

```bash
npm run compile
```

### Extension Smoke Tests

On macOS or Linux with a desktop display:

```bash
npm test
```

On headless Linux:

```bash
xvfb-run -a npm test
```

The smoke test command:

- compiles TypeScript
- launches a real VS Code/Electron extension host
- loads the development extension
- runs the Mocha smoke suite inside that host

## Notes and Limitations

- The current smoke suite does not verify full proof generation against Docker, CBMC, and a live OpenAI-backed AutoUP run.
- The function detector uses document symbols first and a conservative text fallback second.
- The extension is designed for trusted workspaces only.
