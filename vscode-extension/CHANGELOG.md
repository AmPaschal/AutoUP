# Changelog

## 0.1.0

First release on the VS Code Marketplace.

### Breaking

The extension was renamed from **AutoUP** to **AutoSOUP**, and every setting key,
command, and identifier was renamed with it. If you used a pre-release `.vsix`
build (0.0.x), you need to migrate:

- **Settings:** rename `autoup.*` to `autosoup.*` in `settings.json`. All nine
  keys are affected, including `autoup.modelProfiles` and
  `autoup.selectedModelProfile`.
- **Provider API keys:** stored secrets moved from the `autoup.env.*` namespace
  to `autosoup.env.*` and are not migrated automatically. Re-add them with
  **AutoSOUP: Manage Provider Environment Variables**.
- **Commands:** all commands moved from the `autoup.` prefix to `autosoup.` and
  now appear under the **AutoSOUP** category in the command palette. Update any
  keybindings or tasks that referenced them.

Existing proof folders are unaffected — the on-disk layout (`.autoup/job.json`,
`.autoup/events.jsonl`, `autoup.log`) is unchanged, so previously generated
proofs still load once your model profile is reconfigured.

### Other changes

- Added a marketplace icon, keywords, and `Testing`/`AI` categories.
- The packaged extension no longer ships internal design docs, build scripts,
  source maps, or Python bytecode.
- Removed redundant `activationEvents` entries that VS Code now generates
  automatically from the contribution manifest.
