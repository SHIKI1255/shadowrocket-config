# Shadowrocket configuration

Use PowerShell 7 for local shell commands. Public rule generation and module
validation use Python 3.12 standard library only. The user-authorized local Hosts
synchronizer alone may use the existing PyYAML 6.0.3 installation; its dedicated
CI installs that pinned dependency for synthetic-input tests. Never install or
change a local dependency automatically.
Source, reusable tests and documentation belong here; generated outputs and
download caches belong outside this checkout. No nodes, credentials, certificates,
subscription tokens or personal traffic logs may be committed to this public repo.

On governed Windows hosts, obtain task-specific context from
`D:/CodexProjects/repo-naming-governance/govern.ps1 -Action Context -RepoRoot <this repo> -Task Read|Change|File|Git`.
This path is a local control entry, not a runtime dependency of the builder.
Use `artifact.ps1` to register local input/output directories. Do not copy unrelated
in-progress components from other repositories. The build and CI are self-contained.

Preserve source order, no-resolve and first-match behavior. Unknown syntax, unknown
conflicts, suspicious source changes or failed regression cases must block release.
Do not automatically accept a changed validation baseline. Changes to that baseline
require inspection of the candidate report. Do not claim mobile runtime validation
based on the Python matcher. Commit/push/publish require user authorization.

Run `python -B -m unittest discover -s tests -v`, then a network build and offline
rebuild for changes affecting the generator or routing. Only stage named task files.

Public rule/source/conversion authority: SHIKI1255/shadowrocket-config/shared.
Consumers read versioned JSON data only; never execute downloaded source code.
Do not reintroduce local custom/source copies. Client templates and protocol contracts stay here.

Hosts synchronization: only modules/GeekSpeed-Hosts.sgmodule and modules/README.md
may be published by the recurring task. Complete-set changes require two matching
provider reads and the quantity gate. Never publish private subscriptions or raw
responses. Keep state outside the checkout; do not weaken guards, merge stale
pending PRs, force-push or change unrelated files to recover a scheduled failure.
