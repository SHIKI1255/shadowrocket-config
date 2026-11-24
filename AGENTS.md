# Shadowrocket configuration

Use zsh on macOS (or POSIX shell) for local commands. Public rule generation and
module validation use Python 3.14 standard library only (Python 3.12+ compatible).
The user-authorized local Hosts synchronizer alone may use `PyYAML==6.0.3` inside
the repo-local `.venv` (managed via `uv`); its dedicated CI installs that pinned
dependency for synthetic-input tests. Never install or change dependencies automatically.
Source, reusable tests and documentation belong here; generated outputs and
download caches belong outside this checkout. No nodes, credentials, certificates,
subscription tokens or personal traffic logs may be committed to this public repo.

On governed macOS hosts, obtain task-specific context or run hard checks from
`/Users/gabriel/Projects/repo-naming-governance/govern.py context|check --repo-root <this repo>`.
This path is a local control entry, not a runtime dependency of the builder.
Use `artifact.py` to register local input/output directories. Do not copy unrelated
in-progress components from other repositories. The build and CI are self-contained.

Preserve source order, no-resolve and first-match behavior. Unknown syntax, unknown
conflicts, suspicious source changes or failed regression cases must block release.
Do not automatically accept a changed validation baseline. Changes to that baseline
require inspection of the candidate report. Do not claim mobile runtime validation
based on the Python matcher. Commit/push/publish require user authorization.

Run `.venv/bin/python -B -m unittest discover -s tests -v`, then a network build
and offline rebuild for changes affecting the generator or routing. Only stage named task files.

Public rule/source/conversion authority: SHIKI1255/shadowrocket-config/shared.
Consumers read versioned JSON data only; never execute downloaded source code.
Do not reintroduce local custom/source copies. Client templates and protocol contracts stay here.

Hosts synchronization: provider entry IPs and Hosts modules are never published
to this public repository. Local-only synchronization writes a `#!url`-free
`.sgmodule` atomically to the user's private local/iCloud Shadowrocket Modules
directory using external `--config` and `--state-dir` outside the checkout.
Complete-set changes require two matching provider reads and the quantity gate.
Never publish private subscriptions, provider entry IPs, or raw responses; do not
weaken guards or change unrelated files to recover a scheduled failure.
