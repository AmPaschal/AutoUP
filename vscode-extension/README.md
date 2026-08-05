# AutoSOUP for VS Code

Generate CBMC unit proofs for C/C++ functions from inside VS Code, and formally
verify their memory safety.

Put your cursor in a function, pick **Create Unit Proof**, and AutoSOUP generates
a proof harness and Makefile, runs CBMC against them, iteratively refines the
harness, and reports which memory-safety properties were verified — plus any
violations that may represent security vulnerabilities.

## Requirements

Read this section before installing — AutoSOUP shells out to a container engine
and a Python backend, so it has real host requirements.

| | Supported |
|---|---|
| **Linux** (local or via Remote-SSH) | Yes — Docker or Apptainer |
| **macOS** (local) | Yes — Docker only |
| **Windows** | **No.** Proof generation fails with an explicit error. |

You also need:

- **Python 3** on the execution host. AutoSOUP creates its own virtual
  environment and installs its dependencies there on first activation — you do
  not need to install anything Python-side yourself.
- **Docker or Apptainer** on the execution host. CBMC and all LLM-driven steps
  run inside a container.
- The **[clangd](https://marketplace.visualstudio.com/items?itemName=llvm-vs-code-extensions.vscode-clangd)**
  extension and a working `clangd`. AutoSOUP uses it to resolve the function
  under your cursor and extract its signature and body.
- An **API key for an LLM provider** (any provider LiteLLM supports).

Because the backend runs on the machine that hosts the workspace, AutoSOUP is a
workspace extension: over Remote-SSH it runs on the remote host, not your laptop.
It only operates in [trusted workspaces](https://code.visualstudio.com/docs/editor/workspace-trust).

## Getting started

### 1. Configure a model profile

A model profile names an LLM and the environment variables needed to reach it.
Add one or more to your settings:

```json
"autosoup.modelProfiles": [
  {
    "name": "openrouter-r1",
    "provider": "openrouter",
    "modelName": "openrouter/deepseek/deepseek-r1",
    "envVars": ["OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"]
  }
],
"autosoup.selectedModelProfile": "openrouter-r1"
```

`modelName` is passed straight to LiteLLM, so any LiteLLM-supported identifier
works.

### 2. Store your API key

Run **AutoSOUP: Manage Provider Environment Variables** from the command palette
and add each variable your profile lists. Values are kept in VS Code
`SecretStorage`, not in `settings.json`, and are keyed by variable name.

The same command also lists and clears stored variables.

### 3. Create a proof

1. Open a C or C++ file.
2. Place the cursor inside the function you want to verify.
3. Right-click and choose **Create Unit Proof**.
4. Watch the **AutoSOUP** view in the activity bar for live progress.

Several proofs can run at once; anything past `autosoup.maxConcurrentJobs` is
queued.

## The AutoSOUP view

The **Unit Proofs** tree shows one row per proof job with its status, current
stage, and — once verification runs — a summary of instrumented properties,
verified properties, errors grouped by line, and coverage.

Each row has actions to open the generated harness, the Makefile, the original
source file, the AutoSOUP log, the verification report, or the vulnerability
report; to reveal the proof folder; and to cancel a running proof.

Progress updates arrive not just when a stage finishes but after each accepted
refinement, so long-running jobs stay legible.

### Background jobs

Proof jobs run as detached background processes, which means:

- Over Remote-SSH, proofs keep running on the remote host even if your client
  disconnects, sleeps, or closes VS Code — as long as the remote host stays up.
  On reconnect, the extension rehydrates job state from disk.
- Local proofs survive window reloads and app restarts, but make no progress
  while the machine is asleep.

## Proof storage layout

Proofs are written under `autosoup.proofsRoot` (default `cbmc/proofs`), mirroring
the source tree. Verifying `_receive` in `src/net/tcp/gnrc_tcp_eventloop.c`
produces:

```text
cbmc/proofs/src/net/tcp/gnrc_tcp_eventloop/_receive/
```

Each proof folder holds the harness, Makefile, `autoup.log`, and an `.autoup/`
directory with the job manifest and event stream the sidebar reads.

## Settings

| Setting | Default | Description |
|---|---|---|
| `autosoup.modelProfiles` | `[]` | LLM profiles, each with `name`, `provider`, `modelName`, `envVars`. |
| `autosoup.selectedModelProfile` | `null` | Name of the profile to use. |
| `autosoup.containerEngine` | `auto` | `auto`, `docker`, or `apptainer`. `auto` prefers Docker and falls back to Apptainer on Linux. |
| `autosoup.proofsRoot` | `cbmc/proofs` | Workspace-relative root for generated proofs. |
| `autosoup.maxConcurrentJobs` | `5` | Proof jobs allowed to run at once. |
| `autosoup.makeTimeout` | `900` | Full-verification `make` timeout, in seconds. |
| `autosoup.scopeReducerAttempts` | `10` | Maximum scope-reducer attempts before giving up. |
| `autosoup.scopeBound` | `1` | Scope-widening depth bound. The default of `1` disables widening while keeping scope-reducer behavior. |
| `autosoup.scopeTimeBudget` | `null` | Optional scope-widening time budget, in minutes. |

## Limitations

- Windows is not supported.
- The function detector uses clangd document symbols first, with a conservative
  text-based fallback.
- Trusted workspaces only.

## Development

AutoSOUP's VS Code front end and its Python backend live in the
[AutoUP repository](https://github.com/AmPaschal/AutoUP). To build the extension
from source, from `vscode-extension/`:

```bash
npm install
npm run compile        # also syncs the Python backend into .autoup-bundle/
npm run package        # produces autosoup-vscode-<VERSION>.vsix
```

Run the smoke suite with `npm test`, or `xvfb-run -a npm test` on headless Linux.
It launches a real extension host and checks activation, command registration,
proof-path computation, and function detection; it does not exercise a full
proof-generation run against CBMC and a live LLM.

Issues and contributions: <https://github.com/AmPaschal/AutoUP/issues>
