# VS Code Multi-Model Profiles Walkthrough

## Summary

This walkthrough describes the reintroduced multi-model support for the VS Code extension and the backend changes needed to make non-OpenAI models usable in practice.

The implementation has two major parts:

1. Extension-side profile, secret, and launch changes
2. Backend-side model validation, routing, and LiteLLM compatibility changes

The key design decision is that model selection is now profile-based instead of a single `autoup.model` string. A profile defines:

- `name`
- `provider`
- `modelName`
- `envVars`

Only one profile is active at a time through `autoup.selectedModelProfile`.

## What Changed

## 1. Extension configuration changed from one model string to model profiles

Files:

- `vscode-extension/package.json`
- `vscode-extension/src/core/configManager.ts`
- `vscode-extension/src/core/types.ts`
- `vscode-extension/src/shared/modelProfiles.ts`

Changes:

- Removed the extension’s dependence on a single `autoup.model` setting.
- Added `autoup.modelProfiles` as an array of model profile objects.
- Added `autoup.selectedModelProfile` as the active profile selector.
- Added shared TypeScript types for `ModelProfile`, `ResolvedModelProfile`, and the new prerequisite shape.
- Added normalization and selected-profile resolution helpers.

Reason:

- The previous design assumed one global model string and OpenAI-oriented credential handling.
- The new profile model lets users save multiple configurations and switch between them without rewriting settings each time.
- Using `envVars: string[]` avoids the ambiguity and parsing mistakes of comma-separated strings.

## 2. Provider environment variables moved to global SecretStorage management

Files:

- `vscode-extension/src/features/setup/providerEnvironmentManager.ts`
- `vscode-extension/src/shared/modelProfiles.ts`
- `vscode-extension/src/extension.ts`

Changes:

- Added `AutoUP: Manage Provider Environment Variables`.
- Added three actions behind that command:
  - add environment variable
  - list environment variables
  - clear environment variable
- Stored secrets globally under `autoup.env.<ENV_VAR_NAME>`.
- Used `SecretStorage.keys()` to list all stored env vars without maintaining a separate index.

Reason:

- The user wanted env vars to be globally managed, not scoped per profile.
- The extension should not assume that env vars only exist for already-declared profiles.
- A single management command keeps the command surface small while still exposing add/list/clear functionality.

## 3. Proof-start validation moved into the extension prerequisite flow

Files:

- `vscode-extension/src/features/setup/prerequisiteChecker.ts`
- `vscode-extension/src/shared/actionableError.ts`
- `vscode-extension/src/extension.ts`

Changes:

- Validation no longer happens at activation.
- Validation now happens when the user starts `Create Unit Proof`.
- The extension validates:
  - selected profile exists
  - selected profile is well-formed
  - each declared env var exists in `SecretStorage`
  - LiteLLM can resolve `modelName`
- Added actionable failure handling with:
  - `Open Settings`
  - `Manage Provider Environment Variables`

Reason:

- This allows users to change profiles between proof runs without stale activation-time state.
- The validation is cheap because it only resolves the model through LiteLLM and does not do a live inference probe.
- Actionable errors reduce setup friction when validation fails.

## 4. The VS Code bridge now validates models and carries resolved provider metadata

Files:

- `src/autoup_vscode.py`
- `src/commons/model_validation.py`
- `vscode-extension/src/features/createProof/proofRunner.ts`

Changes:

- Added a new bridge command: `validate-model`.
- `validate-model` resolves the model through LiteLLM and returns:
  - `ok`
  - `resolved_provider`
  - `error` on failure
- Extended `start` to accept `--provider` and `--model`.
- Changed the proof runner to:
  - pass the selected profile’s `modelName`
  - pass the resolved provider from validation
  - inject all declared env vars into the backend process

Reason:

- The backend should route based on the provider LiteLLM actually resolves, not the provider string typed by the user.
- This avoids hardcoded provider tables in the extension.
- Validation and launch now share the same model-resolution logic.

## 5. The non-extension runtime path now resolves and validates models too

Files:

- `src/run.py`
- `src/agent.py`
- `src/commons/model_validation.py`

Changes:

- Added model-resolution prevalidation to `run_with_args()`.
- Ensured `args.llm_provider` is set from LiteLLM resolution before agents are constructed.
- Preserved split routing:
  - `openai` -> `GPT`
  - everything else -> `LiteLLM`
- Removed the old unconditional startup gate that required `OPENAI_API_KEY` before any routing occurred.

Reason:

- The old startup path incorrectly blocked non-OpenAI models before provider routing even had a chance to run.
- The CLI path needed to be made consistent with the VS Code path instead of remaining OpenAI-biased.
- Routing on resolved provider is more reliable than routing on user-entered metadata.

## 6. LiteLLM no longer assumes OpenAI-native structured output

Files:

- `src/commons/models.py`

Changes:

- Reworked the `LiteLLM` wrapper so it no longer passes `response_format=output_format`.
- Changed non-OpenAI structured output to always use JSON-text fallback.
- Added JSON-only prompt augmentation using the Pydantic schema.
- Added parsing and validation through:
  - `json.loads(...)`
  - `output_format.model_validate(...)`
- Added defensive handling for:
  - missing final content
  - malformed JSON
  - malformed tool-call payloads

Reason:

- A major source of breakage for open models was assuming they behaved like OpenAI on native structured output.
- Always using JSON fallback removes the need to guess whether a given provider/model combination supports native structured output through LiteLLM.
- This is less strict than native schemas, but much more portable.

## 7. LiteLLM tool payloads are now translated from the existing OpenAI-style schema

Files:

- `src/commons/models.py`

Changes:

- Added a helper that translates the current flat function-tool schema into LiteLLM’s nested `function` payload shape.
- Preserved:
  - `name`
  - `description`
  - `parameters`
  - `strict`
- Made LiteLLM request construction conditional:
  - no tools -> omit `tools` and `tool_choice`
  - with tools -> translate first, then send

Reason:

- The codebase already produced OpenAI-style tool definitions.
- LiteLLM expects a different nested tool structure.
- Translation allowed the rest of the agent/tooling code to remain stable while fixing the actual integration boundary.

## 8. Tool-call response handling in LiteLLM is now defensive

Files:

- `src/commons/models.py`

Changes:

- Added normalization helpers for:
  - serialized assistant messages
  - extracted tool calls
  - extracted tool-call content
- Validated that tool calls have:
  - an id
  - a function name
  - string JSON arguments
- Failed explicitly instead of continuing with malformed payloads.

Reason:

- Open-model and LiteLLM integrations return more varied payload shapes than the original code assumed.
- Defensive parsing prevents silent corruption of the prompt history or false-positive “successful” responses.

## 9. Harness and Makefile prompts were tightened for open models

Files:

- `prompts/harness_generator_system.prompt`
- `prompts/makefile_generator_system.prompt`

Changes:

- Added stricter output constraints.
- Explicitly disallowed extra prose and markdown fences.
- Reinforced that the model should use tools instead of guessing.
- Reinforced staying within the allowed edit/output surface.

Reason:

- Some open-model failures were not transport-level failures; they were prompt-discipline failures.
- Stronger output constraints reduce malformed JSON and reduce drift in tool-using workflows.

## 10. Tests were extended to cover the new model/profile path

Files:

- `vscode-extension/test/suite/smoke.test.js`
- `tests/test_vscode_model_validation.py`
- `tests/test_agent_model_routing.py`
- `tests/test_models_litellm.py`

Changes:

- Added extension smoke coverage for:
  - new command registration
  - resolved provider/model/env-var launch forwarding
  - global env-var add/list/clear behavior
- Added backend tests for:
  - bridge model validation
  - provider routing
  - direct CLI provider resolution
  - LiteLLM JSON fallback and tool translation

Reason:

- This feature crosses the extension, bridge, runtime, and model layers.
- Without regression coverage in each layer, failures would be easy to reintroduce silently.

## Why This Architecture Was Chosen

The central architectural choice was to treat model/provider setup as user-owned configuration instead of hardcoded provider knowledge.

That drove the rest of the implementation:

- profiles in settings instead of a single model string
- env vars in global `SecretStorage`
- cheap local model-resolution validation instead of live reachability checks
- routing based on resolved provider, not typed provider
- JSON fallback for LiteLLM instead of provider-capability guessing

This gives a more flexible extension while keeping the validation cost low enough to run before each proof launch.

## Known Tradeoffs

- Model validation is local LiteLLM resolution only. It does not prove credentials or network reachability.
- Global env vars mean profiles that use the same variable name share the same stored value.
- The user-configured `provider` field is treated as metadata, not canonical runtime truth.
- JSON fallback is more portable than native structured output, but less strict.
- Some standalone scripts outside the main `run.py`/VS Code path still contain direct `OPENAI_API_KEY` assumptions and were not part of this reintroduction.

## Result

The extension can now:

- store multiple model profiles
- select one active profile
- manage required env vars globally
- validate profile/model setup at proof start
- pass declared env vars into the backend
- route OpenAI and non-OpenAI models correctly
- use LiteLLM in a way that is materially more tolerant of open-model behavior
