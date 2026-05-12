# AutoUP VS Code Extension Design

## Purpose

This document captures the implemented design of the AutoUP VS Code extension in `vscode-extension/`.

The goals of the extension are:

- let a user start AutoUP from a C/C++ editor without manually running `src/run.py`
- run on the VS Code workspace host, including Remote-SSH Linux hosts
- manage the AutoUP Python runtime on behalf of the user
- support multiple proof jobs with live progress updates
- persist enough job state that the sidebar can survive reloads
- let Remote-SSH proof jobs survive client disconnects by running in detached background processes

The design described here is the implemented design, not just the original proposal.

## Scope and Non-Goals

### In Scope

- editor command to create a unit proof for the function under the cursor
- workspace-side execution on macOS local, Linux local, and Remote-SSH Linux
- runtime bootstrap and dependency installation into extension-managed storage
- live progress in a sidebar
- artifact navigation for harness, Makefile, source, log, and report
- persisted job state under the generated proof directory

### Out of Scope for v1

- Windows support
- browser-only VS Code
- full replacement of the AutoUP CLI
- visual dashboards beyond a Tree View
- tight coupling to the VS Code Testing API

## Repository Layout

### Extension Frontend

The main extension code lives in:

```text
vscode-extension/
  package.json
  src/
    extension.ts
    core/
      configManager.ts
      proofManager.ts
      statusTracker.ts
      types.ts
    features/
      createProof/
        functionDetector.ts
        proofRunner.ts
      proofPanel/
        proofTreeItem.ts
        proofTreeProvider.ts
      setup/
        autoUpInstaller.ts
        prerequisiteChecker.ts
    shared/
      pathUtils.ts
      platform.ts
      processUtils.ts
  resources/icons/
  scripts/sync-backend-assets.js
  test/
```

### Python Backend Bridge

The extension relies on Python bridge/backend code that lives in the main AutoUP repository:

```text
src/
  autoup_vscode.py
  vscode_bridge/progress.py
  run.py
  agent.py
  coverage_debugger/
  debugger/
```

### Bundled Backend Copy

At extension build/package time, the backend assets needed at runtime are copied into:

```text
vscode-extension/.autoup-bundle/
```

This directory is generated and gitignored. It exists so an installed VSIX can run without needing the parent repository layout next to the installed extension.

## High-Level Workflow

### End-to-End Flow

```text
User right-clicks in C/C++ editor
  ->
VS Code command contribution
  ->
src/extension.ts
  ->
ProofManager.startProofFromEditor()
  ->
functionDetector.ts resolves target function
  ->
ConfigManager reads autoup.* settings
  ->
AutoUpInstaller ensures bundled backend + venv exist
  ->
PrerequisiteChecker validates Python + container engine + API key
  ->
proofRunner.ts spawns Python bridge
  ->
src/autoup_vscode.py starts AutoUP pipeline
  ->
src/run.py and AutoUP agents execute proof generation/refinement
  ->
src/vscode_bridge/progress.py writes:
     .autoup/job.json
     .autoup/events.jsonl
  ->
StatusTracker polls manifest/events
  ->
ProofManager updates in-memory job state
  ->
ProofTreeProvider renders sidebar rows
  ->
User opens artifacts or deletes/cancels jobs
```

The proof bridge is launched as a detached background process. The extension monitors persisted state, but it does not require the current VS Code session to remain connected for the proof to keep running on the execution host.

### Proof Artifact Layout

For a source file:

```text
src/net/tcp/gnrc_tcp_eventloop.c
```

and function:

```text
_receive
```

the proof directory is:

```text
cbmc/proofs/src/net/tcp/gnrc_tcp_eventloop/_receive/
```

Persisted extension state lives inside that proof directory:

```text
.autoup/job.json
.autoup/events.jsonl
```

This makes the proof directory the source of truth for both proof artifacts and sidebar state.

## Component Design

## 1. Extension Manifest and Activation

### Location

- `vscode-extension/package.json`
- `vscode-extension/src/extension.ts`

### Implementation References

- [package.json](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/package.json:1)
- [extension.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/extension.ts:1)

### Responsibility

- declares commands, menus, views, settings, icons, and activation events
- wires up the runtime services during activation

### Implemented Design

The extension is declared as:

- a workspace extension via `extensionKind: ["workspace"]`
- lazily activated on:
  - `autoup.createUnitProof`
  - `autoup.refreshProofs`
  - `autoup.proofExplorer`

`src/extension.ts` constructs:

- `ConfigManager`
- `AutoUpInstaller`
- `PrerequisiteChecker`
- `ProofManager`
- `ProofTreeProvider`

and registers all user-facing commands against `ProofManager`.

### Alternatives Considered

#### Alternative A: Activate on all C file opens

Pros:

- simpler to reason about
- command would definitely be available once a C file is open

Cons:

- worse startup behavior
- activates even when the user never intends to use AutoUP

#### Alternative B: Split into separate UI and workspace extensions

Pros:

- clearer separation of UI vs execution
- could support more mixed local/remote behavior later

Cons:

- more extension complexity
- unnecessary for the current feature set

### Why the Current Design Was Chosen

The workspace-extension model is the correct default because AutoUP needs filesystem access, Python, Docker/Apptainer, and proof output on the same machine where the workspace lives. That is especially important for Remote-SSH.

Lazy activation keeps the extension lightweight until the user actually invokes AutoUP or opens the AutoUP panel.

## 2. Function Target Resolution

### Location

- `vscode-extension/src/features/createProof/functionDetector.ts`

### Implementation References

- [functionDetector.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/features/createProof/functionDetector.ts:1)

### Responsibility

- determine which function the user is targeting from the active editor

### Implemented Design

The detector uses a two-stage strategy:

1. ask VS Code for document symbols with `vscode.executeDocumentSymbolProvider`
2. fall back to a conservative text scan when symbols are unavailable

The result includes:

- `functionName`
- `filePath`
- `line`
- `column`

### Alternatives Considered

#### Alternative A: Regex-only parsing

Pros:

- no dependency on language tooling
- easy to implement

Cons:

- fragile for real C/C++ code
- more false positives for calls, declarations, and control-flow statements

#### Alternative B: Require clangd or C/C++ extension symbols only

Pros:

- more accurate when available
- less custom parsing logic

Cons:

- poor behavior in minimal environments
- command would fail too often on partially configured workspaces

### Why the Current Design Was Chosen

The mixed design gives the best practical behavior:

- strong answers when language tooling exists
- graceful fallback for simple cases when it does not

It is intentionally conservative. Failing to resolve a function is safer than launching a proof on the wrong target.

## 3. Proof Job Orchestration

### Location

- `vscode-extension/src/core/proofManager.ts`

### Implementation References

- [proofManager.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/core/proofManager.ts:1)
- [pathUtils.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/shared/pathUtils.ts:1)
- [platform.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/shared/platform.ts:1)

### Responsibility

- own the lifecycle of all proof jobs in the current extension session
- queue jobs
- rehydrate persisted jobs
- open artifacts
- coordinate status tracking and tree refreshes

### Implemented Design

`ProofManager` is the central coordinator. It maintains:

- in-memory jobs keyed by job ID
- per-job `StatusTracker` instances
- queued job IDs
- per-job output channels

Its major responsibilities are:

- start a proof from the active editor
- compute deterministic proof directories
- prevent duplicate active jobs for the same proof directory
- queue jobs when `autoup.maxConcurrentJobs` is reached
- rehydrate jobs from `cbmc/proofs/**/.autoup/job.json`
- remove stale jobs when proof directories disappear
- resolve command arguments from row clicks, context actions, and rendered tree items
- avoid killing active detached jobs when the extension host shuts down

### Alternatives Considered

#### Alternative A: Let each UI command manage its own child process

Pros:

- fewer central abstractions

Cons:

- harder to implement queueing
- harder to persist and rehydrate
- duplicates logic across commands

#### Alternative B: Use only persisted files and no in-memory manager

Pros:

- simpler conceptual model
- easier crash recovery

Cons:

- awkward for queueing, cancellation, and live process handles
- harder to attach output channels and live child processes

### Why the Current Design Was Chosen

The extension needs both:

- persistent state on disk
- session-local process control

`ProofManager` exists because neither of those alone is enough. The chosen design uses proof directories as the durable source of truth while still keeping enough in-memory state to manage active jobs well.

The key lifecycle choice is that proof termination is explicit through cancel/delete, not implicit through extension shutdown. That is what makes reconnect/reload recovery viable.

## 4. Runtime Bootstrap and Backend Packaging

### Location

- `vscode-extension/src/features/setup/autoUpInstaller.ts`
- `vscode-extension/scripts/sync-backend-assets.js`
- `vscode-extension/.autoup-bundle/`

### Implementation References

- [autoUpInstaller.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/features/setup/autoUpInstaller.ts:1)
- [sync-backend-assets.js](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/scripts/sync-backend-assets.js:1)

### Responsibility

- make sure the installed extension can run AutoUP without a separate manual Python setup

### Implemented Design

At build/package time:

- `scripts/sync-backend-assets.js` copies required backend assets into `.autoup-bundle/`

At runtime:

- `AutoUpInstaller` copies `.autoup-bundle/` into extension global storage
- creates a private venv
- installs Python dependencies from the bundled `requirements.txt`
- returns:
  - `backendRoot`
  - `pythonPath`

The installer prefers `.autoup-bundle/` and only falls back to the repository layout when running from source.

### Alternatives Considered

#### Alternative A: Require the user to clone AutoUP and install dependencies manually

Pros:

- very little extension-side runtime logic

Cons:

- defeats the point of the extension
- increases user setup friction

#### Alternative B: Auto-clone the repo into a home-directory cache

Pros:

- works even outside the source repository

Cons:

- introduces network dependency
- creates version drift between extension and backend
- complicates trust and provenance

#### Alternative C: Ship one frozen checked-in backend copy inside the repo

Pros:

- easy packaging story

Cons:

- high maintenance burden
- easy for it to drift from the main AutoUP code

### Why the Current Design Was Chosen

The generated `.autoup-bundle/` model keeps the installed extension self-contained while still deriving its backend from the current repo at build time. It avoids user-managed cloning and reduces drift compared with a separately maintained forked backend copy.

## 5. Prerequisite Validation

### Location

- `vscode-extension/src/features/setup/prerequisiteChecker.ts`

### Implementation References

- [prerequisiteChecker.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/features/setup/prerequisiteChecker.ts:1)
- [processUtils.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/shared/processUtils.ts:1)

### Responsibility

- validate execution prerequisites before launching a proof

### Implemented Design

The checker validates:

- installed runtime Python works
- container engine availability
- API key availability

Container resolution rules:

- macOS: Docker only
- Linux/Remote-SSH Linux:
  - explicit `docker`
  - explicit `apptainer`
  - `auto`: Docker first, Apptainer fallback

API key resolution order:

1. `SecretStorage`
2. `OPENAI_API_KEY`
3. interactive prompt

### Alternatives Considered

#### Alternative A: Fail late during process launch

Pros:

- less setup code

Cons:

- worse user feedback
- harder-to-debug runtime errors

#### Alternative B: Store API key in workspace settings

Pros:

- simpler implementation

Cons:

- poor secret hygiene
- easy to commit by accident

### Why the Current Design Was Chosen

Failing fast is better here because AutoUP jobs are expensive and long-running. Secrets belong in `SecretStorage` or environment variables, not plain workspace settings.

## 6. Python Bridge Process Launch

### Location

- `vscode-extension/src/features/createProof/proofRunner.ts`
- `src/autoup_vscode.py`

### Implementation References

- [proofRunner.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/features/createProof/proofRunner.ts:1)
- [autoup_vscode.py](/home/pamusuo/research/cbmc-research/AutoUP/src/autoup_vscode.py:1)

### Responsibility

- start one backend process per proof job and wire it to the extension runtime

### Implemented Design

The extension launches:

```text
<managed-python> <backendRoot>/src/autoup_vscode.py start ...
```

The arguments include:

- workspace root
- source path
- proof dir
- job ID
- function name
- container engine
- model
- log file
- metrics file
- execution host
- optional scope settings

The child process:

- redirects stdout/stderr to `autoup.log`
- is launched as a detached background process group
- is monitored through persisted manifest/event files rather than a live stdout pipe

### Alternatives Considered

#### Alternative A: Call `src/run.py` directly from the extension

Pros:

- less Python surface area

Cons:

- weak contract between extension and backend
- no clear place for extension-specific event emission and lifecycle handling

#### Alternative B: Reimplement orchestration in TypeScript

Pros:

- fewer cross-language boundaries

Cons:

- large duplication of AutoUP behavior
- higher regression risk

### Why the Current Design Was Chosen

The Python bridge is the right seam. It allows the extension to remain thin while giving the backend a stable place to emit structured progress and manage proof-job metadata.

Detached launch is also a strong reason for this seam: the extension can hand off work to the backend cleanly, then reconnect later by reading the proof directory state.

## 7. Progress, Persistence, and Event Contract

### Location

- `src/vscode_bridge/progress.py`
- `src/autoup_vscode.py`
- `vscode-extension/src/core/statusTracker.ts`

### Implementation References

- [progress.py](/home/pamusuo/research/cbmc-research/AutoUP/src/vscode_bridge/progress.py:1)
- [autoup_vscode.py](/home/pamusuo/research/cbmc-research/AutoUP/src/autoup_vscode.py:165)
- [statusTracker.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/core/statusTracker.ts:1)

### Responsibility

- make progress updates durable and machine-readable

### Implemented Design

Each proof directory contains:

- `.autoup/job.json`
- `.autoup/events.jsonl`

`job.json` captures current manifest-style state such as:

- job ID
- source file
- proof dir
- PID
- status
- current stage
- execution host

`events.jsonl` captures append-only events such as:

- `job_started`
- `stage_started`
- `stage_progress`
- `refinement_accepted`
- `summary_updated`
- `stage_completed`
- `job_failed`
- `job_cancelled`
- `job_completed`

`StatusTracker` polls these files and updates the in-memory job state in `ProofManager`.

This persisted contract is what makes detached execution practical. Reconnection is mostly a matter of rehydrating job state and polling the same manifest/event files again.

### Alternatives Considered

#### Alternative A: Parse stdout logs for status

Pros:

- almost no backend changes

Cons:

- brittle
- hard to evolve safely
- poor reload/rehydration behavior

#### Alternative B: Use a socket or IPC channel only

Pros:

- more immediate push updates

Cons:

- weak persistence
- harder recovery after reload
- more complex Remote-SSH behavior

#### Alternative C: Use file watchers instead of polling

Pros:

- potentially more reactive

Cons:

- more leak-prone
- more complex cross-platform edge cases

### Why the Current Design Was Chosen

Structured event files are a better contract than stdout parsing, and polling them is simpler and more robust than maintaining many file watchers or a custom IPC protocol. The chosen design also makes reload and debugging much easier because the state is visible on disk.

## 8. Verification Summary Extraction

### Location

- backend summary logic in `src/vscode_bridge/progress.py`
- UI consumption in `vscode-extension/src/core/statusTracker.ts`

### Implementation References

- [progress.py](/home/pamusuo/research/cbmc-research/AutoUP/src/vscode_bridge/progress.py:162)
- [statusTracker.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/core/statusTracker.ts:183)

### Responsibility

- surface proof quality/progress metrics in the sidebar

### Implemented Design

The backend computes summary payloads from generated reports and emits them in `summary_updated` events. Summary data includes:

- `propertiesInstrumented`
- `propertiesVerified`
- `errorsByLine`
- `coverageHit`
- `coverageTotal`
- `coveragePercentage`
- artifact paths

This is intentionally computed in Python, close to the existing AutoUP/CBMC report logic.

### Alternatives Considered

#### Alternative A: Let the extension parse all report JSON directly

Pros:

- fewer backend changes

Cons:

- duplicates report parsing logic
- weaker alignment with backend semantics
- harder to keep error grouping consistent

### Why the Current Design Was Chosen

The backend already understands the meaning of the proof reports. Keeping summary extraction in Python avoids duplicating that logic in TypeScript and makes the event stream more self-contained.

## 9. Sidebar UI

### Location

- `vscode-extension/src/features/proofPanel/proofTreeProvider.ts`
- `vscode-extension/src/features/proofPanel/proofTreeItem.ts`

### Implementation References

- [proofTreeProvider.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/features/proofPanel/proofTreeProvider.ts:1)
- [proofTreeItem.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/features/proofPanel/proofTreeItem.ts:1)
- [package.json](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/package.json:1)

### Responsibility

- render proof jobs and artifact actions in the AutoUP panel

### Implemented Design

The sidebar uses a VS Code Tree View:

- root rows represent proof jobs
- child rows represent:
  - current status
  - verification metrics
  - artifact actions

Artifact actions appear when the target files exist on disk, not only after the proof fully completes. The artifact row itself is the clickable action. Redundant inline open buttons were removed.

Job rows use inline actions only for:

- cancel
- delete

### Alternatives Considered

#### Alternative A: Webview dashboard

Pros:

- richer UI possibilities
- easier charts and custom layout

Cons:

- more implementation overhead
- less native VS Code feel
- higher maintenance cost

#### Alternative B: Flat output-channel-only UX

Pros:

- very simple

Cons:

- poor discoverability
- weak artifact navigation
- poor multi-job visibility

### Why the Current Design Was Chosen

The Tree View is the best v1 choice. It is native, lightweight, and fits the proof-job list model well. A webview would have been harder to maintain without materially improving the core workflow.

## 10. HTML Report Opening

### Location

- `vscode-extension/src/core/statusTracker.ts`

### Implementation References

- [statusTracker.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/core/statusTracker.ts:222)

### Responsibility

- open generated CBMC HTML reports from the sidebar

### Implemented Design

`Open Report` opens the generated `build/report/html/index.html` through an extension-owned webview. The loader:

- reads the HTML file
- rewrites local links/resources into webview-safe URIs
- intercepts intra-report HTML navigation
- keeps access restricted to the report root

### Alternatives Considered

#### Alternative A: Open the file in VS Code preview/simple browser

Pros:

- minimal implementation effort

Cons:

- inconsistent behavior in practice
- weak control over navigation and asset loading

#### Alternative B: Always open externally in the system browser

Pros:

- simple
- uses a normal browser engine

Cons:

- loses workspace-integrated UX
- worse for Remote-SSH scenarios

### Why the Current Design Was Chosen

The webview-based loader keeps the report inside VS Code and gives the extension full control over how report assets and links are resolved.

## 11. Shared Utilities

### Location

- `vscode-extension/src/shared/pathUtils.ts`
- `vscode-extension/src/shared/platform.ts`
- `vscode-extension/src/shared/processUtils.ts`

### Implementation References

- [pathUtils.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/shared/pathUtils.ts:1)
- [platform.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/shared/platform.ts:1)
- [processUtils.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/shared/processUtils.ts:1)
- [configManager.ts](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/src/core/configManager.ts:1)

### Responsibility

- provide small reusable helpers for path computation, execution-host detection, and subprocess execution

### Implemented Design

These helpers isolate low-level behavior so higher-level code can stay focused on proof orchestration.

Examples:

- proof directory layout
- Remote-SSH vs local host detection
- shell command execution for setup checks

### Alternatives Considered

#### Alternative A: Inline utilities inside each component

Pros:

- fewer files

Cons:

- duplicated logic
- harder testing
- weaker maintainability

### Why the Current Design Was Chosen

These concerns are small but cross-cutting. Putting them in `shared/` keeps the main feature modules cleaner.

## 12. Testing Strategy

### Location

- `vscode-extension/test/runTest.js`
- `vscode-extension/test/suite/index.js`
- `vscode-extension/test/suite/smoke.test.js`

### Implementation References

- [runTest.js](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/test/runTest.js:1)
- [index.js](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/test/suite/index.js:1)
- [smoke.test.js](/home/pamusuo/research/cbmc-research/AutoUP/vscode-extension/test/suite/smoke.test.js:1)

### Responsibility

- provide extension-host validation without requiring a full live proof run

### Implemented Design

The current suite is a smoke suite that runs in a real VS Code extension host via `@vscode/test-electron`.

It validates:

- activation
- command registration
- proof-path computation
- function detection
- tree rendering for available artifacts
- cancel/delete job row behavior
- command argument handling for tree-row and rendered tree-item shapes

### Alternatives Considered

#### Alternative A: No extension-host tests, only TypeScript compilation

Pros:

- faster

Cons:

- misses command wiring and extension runtime bugs

#### Alternative B: Full end-to-end AutoUP proof generation in CI

Pros:

- strongest coverage

Cons:

- slow
- expensive
- requires Docker, CBMC, and API-key-backed runtime setup

### Why the Current Design Was Chosen

The smoke suite is a pragmatic middle ground. It catches the extension-specific regressions that matter most without making the test loop dependent on the full proof-generation stack.

## Cross-Cutting Design Decisions

## Workspace-Relative Proof Storage

The extension stores proofs inside the workspace rather than hidden extension storage.

Why:

- proof outputs belong to the project
- generated artifacts should be inspectable and versionable by the user
- persisted job state should disappear if the proof directory is deleted

## Session State Plus Durable State

The design intentionally uses both:

- in-memory manager state
- on-disk manifests/events

Why:

- in-memory state is better for active process control
- on-disk state is better for reloads and transparency

## Extension-Owned Runtime

The extension owns its runtime instead of requiring the user to manage a separate checkout/venv.

Why:

- lower user friction
- better packaging story
- tighter coupling between extension version and backend behavior

## Known Tradeoffs and Limitations

- the extension still depends on Python and a supported container engine on the execution host
- the smoke suite does not prove a full end-to-end AutoUP run against Docker, CBMC, and a live model
- HTML report rendering inside VS Code is more complex than delegating to an external browser
- polling is simpler than watchers, but it is not instantaneous
- the bridge/event contract introduces additional Python surface area that must remain in sync with the extension

## Future Directions

Potential follow-up improvements include:

- stronger automated tests for actual report rendering and navigation
- richer per-stage progress detail in the panel
- better restart semantics for completed proofs
- explicit diagnostics view for setup failures
- optional integration with the VS Code Testing API
- more robust caching/upgrade strategy for the managed Python environment

## Summary

The extension is designed around one core principle:

AutoUP should feel like a native workspace tool in VS Code, while keeping the real proof-generation logic in Python where AutoUP already lives.

That principle drives the major decisions in this design:

- workspace extension execution
- extension-managed runtime bootstrap
- Python bridge entrypoint
- proof-directory-based persisted state
- structured event files instead of log parsing
- Tree View UI instead of a custom webview dashboard

The result is a design that is pragmatic, debuggable, and aligned with how AutoUP already works.
