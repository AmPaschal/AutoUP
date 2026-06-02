# AutoUP VS Code Extension

This extension integrates automated unit proof generation for memory-safety verification into VS Code.
Unit proofs enable users to formally verify memory safety of specific functions and identify any violations that may represent security vulnerabilities. 
The underlying AutoUP framework is described in [AutoUP_README](https://github.com/uwned/AutoUP/blob/master/README.md).

## What It Does

- Adds a `Create Unit Proof` editor context-menu action for C/C++ files.
- When selected, it starts AutoUP proof generation for the function where the cursor is located.
- Stores generated proofs under:
  `cbmc/proofs/<relative-source-path-without-ext>/<function>/`
- Supports multiple proof jobs at once with queueing when the concurrency limit is reached.
- Shows proof jobs in a sidebar with:
  - current status
  - verification summary
  - quick actions to open the harness, Makefile, source file, log, or proof folder

## Supported Environments

The extension has been tested on the following environments:

- macOS local workspace
- Linux workspace via Remote-SSH

## Runtime Requirements

- Python 3 available on the execution host.
- Clangd VS Code extension and the associated `clangd` backend. This enables parsing the C code and extracting the function signature and body.
- Container engine (docker or apptainer): This ensures verification environment is properly setup and LLM-based operations is properly isolated.


## Development Setup

After cloning the repository, follow the following steps to compile and package the VS Code extension for installation.

From the `vscode-extension/` directory:

```bash
npm install
npm run compile
npx @vscode/vsce package
```

If successful, this creates an `autoup-vscode-<VERSION>.vsix` file in the `vscode-extension/` directory, ready for installation in VS Code.

To install the extension (`*.vsix` file) in a VS Code instance:

1. Open the Extensions view in VS Code (`Ctrl+Shift+X`).
2. Click the `...` button (three dots in the top-right corner of the Extensions view) and select `Install from VSIX`.
3. Navigate to and select the `autoup-vscode-<VERSION>.vsix` file.
4. VS Code will install the extension and prompt to reload the window if necessary.

## Using the Extension

### First Launch Setup

On first launch, setup model profiles and API key to enable LLM-based operations.

1. Configure one or more `autoup.modelProfiles` in VS Code Settings. See the `Configuration` section for more details.
2. Set `autoup.selectedModelProfile` to the profile name you want to use.
3. Run `AutoUP: Manage Provider Environment Variables` and add the environment variables required by that profile.

### Normal Usage

1. Open a C/C++ project in VS Code.
2. Place the cursor inside the function you want to verify.
3. Right-click and choose `Create Unit Proof`.
4. Watch the `AutoUP` sidebar for progress and status updates.
5. Use the sidebar actions to inspect the generated harness, Makefile, log, or source file.


## Configuration

The extension contributes these settings:

- `autoup.modelProfiles`
- `autoup.selectedModelProfile`
- `autoup.containerEngine`
- `autoup.scopeBound`
- `autoup.scopeTimeBudget`
- `autoup.maxConcurrentJobs`
- `autoup.proofsRoot`

Defaults:

- `autoup.modelProfiles = []`
- `autoup.selectedModelProfile = null`
- `autoup.containerEngine = auto`
- `autoup.maxConcurrentJobs = 5`
- `autoup.proofsRoot = cbmc/proofs`

Example profile:

```json
{
  "name": "openrouter-r1",
  "provider": "openrouter",
  "modelName": "openrouter/deepseek/deepseek-r1",
  "envVars": ["OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"]
}
```

Provider environment variables are stored in VS Code `SecretStorage` and managed through the `AutoUP: Manage Provider Environment Variables` command.

## Extension Features

### Editor Command

Right-click inside a C or C++ function and choose:

- `AutoUP: Create Unit Proof`

The command is only shown for trusted workspaces and C/C++ files.

### Provider Environment Variables

Use `AutoUP: Manage Provider Environment Variables` from the command palette to:

- add a stored environment variable
- list all stored environment variables
- clear a stored environment variable

Stored values are global to the extension and keyed by environment variable name.

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
