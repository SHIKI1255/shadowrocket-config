# Shadowrocket configuration

Use PowerShell 7 for local shell commands. Python 3.12 standard library only.
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
