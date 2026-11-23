#!/usr/bin/env python3
"""Portable project artifact lifecycle CLI (Python 3 stdlib)."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def _load_artifacts_module(module_rel: str):
    module_path = (ROOT / module_rel).resolve()
    try:
        module_path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Artifact module escapes repository") from exc
    spec = importlib.util.spec_from_file_location("governance_artifacts", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load artifact module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project external artifact lifecycle CLI")
    parser.add_argument(
        "action",
        nargs="?",
        default="cleanup",
        help="Action: new | complete | activate | pin | unpin | cleanup | restore (case-insensitive)",
    )
    parser.add_argument("--kind", default="tmp", choices=["tmp", "cache", "logs", "reports", "inputs", "outputs"])
    parser.add_argument("--id", dest="artifact_id", default="")
    parser.add_argument("--workbench-root", default="")
    parser.add_argument("--runtime-root", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--opportunistic", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = json.loads((ROOT / "artifact_config.json").read_text(encoding="utf-8-sig"))
        mod = _load_artifacts_module(config.get("module", "lib/artifacts.py"))
        roots = mod.get_artifact_roots(ROOT, config["repo_slug"], args.workbench_root, args.runtime_root)
        result = mod.invoke_artifact_action(
            roots=roots,
            action=args.action,
            kind=args.kind,
            artifact_id=args.artifact_id,
            apply=args.apply,
            opportunistic=args.opportunistic,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
