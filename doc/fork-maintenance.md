# Self-hosted codex.sale fork

`master` tracks the official upstream. `codex/codex-sale` contains the deployment customization. The pinned upstream base is `467125fafb47a8520856504fecc48d6e32055db1`. Do not merge the historical onboarding experiment into this branch again.

## What this fork changes

The configured execution preset adds a codex.sale choice to the OpenCode agent wizard. Native company secrets hold credentials. A small optional host provisioner creates one isolated SSH runtime per new agent. It does not grant host SSH or Docker access to agents. See [execution presets](execution-presets.md) for configuration and limitations.

The fork contains no company export, mission, old tasks, agent context, host addresses, or API credentials. Deployment configuration and secrets stay outside Git.

## Updating

1. Fetch `upstream` and update your clean `master` with `git merge --ff-only upstream/master`.
2. Create a new `codex/` integration branch from the intended release commit.
3. Cherry-pick the customization commits from `codex/codex-sale`. Resolve conflicts in the preset service, agent route and wizard. Do not copy an old deployment directory into a fresh database.
4. Run the targeted preset, agent wizard and provisioner tests, the token gates, typecheck and builds. If upgrading the upstream base, rebuild the native runner from the matching source too. The current application overlay only reuses the native runner at its exact pinned base.
5. Test in an isolated empty instance, pin the image digest, then deploy with owner approval.

## Fresh installation

Create a new data volume and an empty workspace. Generate a new authentication secret, owner invite, encryption master and runtime SSH key. Do not restore database backups, agent homes or Git worktrees from the retired instance. Create the owner account and company normally. Configure company-scoped API and runtime secrets and the corresponding preset and broker profile. No agents or business tasks are created by this fork.

A retained provider key is an operator secret until it is imported into the newly created company. Never commit it or expose it through the preset API.
