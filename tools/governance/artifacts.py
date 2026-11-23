"""Portable external workbench artifact lifecycle, version 2.0.0 (Python stdlib)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

if os.name == "nt":
    import msvcrt
else:
    import fcntl

VERSION = "2.0.0"
RETENTION_DAYS = {
    "tmp": 7,
    "cache": 7,
    "logs": 30,
    "reports": 90,
    "inputs": 0,
    "outputs": 0,
}
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ARTIFACT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")
_DARWIN_SYSTEM_ALIASES = ("/var", "/tmp", "/etc")


def _normalize_platform_path(path: str | os.PathLike[str]) -> Path:
    """Resolve macOS fixed root aliases (/var, /tmp, /etc -> /private/*) without following user links."""
    abs_path = os.path.abspath(os.fspath(path))
    if sys.platform == "darwin":
        for prefix in _DARWIN_SYSTEM_ALIASES:
            if abs_path == prefix or abs_path.startswith(prefix + "/"):
                abs_path = "/private" + abs_path
                break
    return Path(abs_path)


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        st = path.lstat()
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)


def assert_no_link(path: str | os.PathLike[str]) -> Path:
    """Ensure neither the target nor any ancestor is a symlink or reparse point."""
    full = _normalize_platform_path(path)
    for parent in (full, *full.parents):
        if _is_link_or_reparse(parent):
            raise ValueError(f"LINK_FORBIDDEN: {parent}")
    return full


def is_within(child: Path, parent: Path) -> bool:
    """Return True if child equals parent or lies strictly inside parent."""
    c_str = os.path.normcase(os.path.abspath(os.fspath(child)))
    p_str = os.path.normcase(os.path.abspath(os.fspath(parent)))
    if c_str == p_str:
        return True
    try:
        return os.path.commonpath([c_str, p_str]) == p_str
    except ValueError:
        return False


def _default_external_roots() -> tuple[Path, Path]:
    projects_root = Path.home() / "Projects"
    return projects_root / "_workbench", projects_root / "_runtime"


def get_artifact_roots(
    repo_root: str | os.PathLike[str],
    slug: str,
    workbench_root: str = "",
    runtime_root: str = "",
) -> dict[str, str]:
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("INVALID_PROJECT_ID")
    prefix = slug.replace("-", "_").upper()
    default_work, default_runtime = _default_external_roots()
    work_raw = workbench_root or os.environ.get(f"{prefix}_WORKBENCH_ROOT", "") or str(default_work)
    runtime_raw = runtime_root or os.environ.get(f"{prefix}_RUNTIME_ROOT", "") or str(default_runtime)

    work = assert_no_link(work_raw)
    runtime = assert_no_link(runtime_raw)
    source = assert_no_link(repo_root)

    home = assert_no_link(Path.home())
    for root in (work, runtime):
        if root == Path(root.anchor) or root == home:
            raise ValueError("BROAD_ROOT_FORBIDDEN")

    if is_within(work, runtime) or is_within(runtime, work):
        raise ValueError("ROOTS_OVERLAP")

    # A packaged helper may live inside a runtime environment. It is not a
    # checkout; its files remain protected, but its containing runtime is valid.
    packaged = source.name == "governance" and (source.parent / "artifact_lifecycle.py").is_file()
    protected = [home / ".gemini", home / ".codex", home / ".agents"]

    for root in (work, runtime):
        if is_within(root, source) or (is_within(source, root) and not (packaged and root == runtime)):
            raise ValueError("PROTECTED_ROOT_OVERLAP")
        for protected_root in protected:
            if is_within(root, protected_root) or is_within(protected_root, root):
                raise ValueError("PROTECTED_ROOT_OVERLAP")
        for parent in (root, *root.parents):
            if (parent / ".git").exists():
                raise ValueError("SOURCE_ROOT_FORBIDDEN")

    return {
        "workbench": str(work),
        "runtime": str(runtime),
        "project": str(work / "projects" / slug),
        "slug": slug,
        "source": str(source),
    }


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def write_artifact_json(path: str | os.PathLike[str], value: object) -> None:
    target = assert_no_link(path)
    tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


def get_artifact_inventory(root: str | os.PathLike[str]) -> dict[str, object]:
    base = assert_no_link(root)
    stack = [base]
    files: list[dict[str, object]] = []
    dirs: list[str] = []

    while stack:
        current_dir = stack.pop()
        if _is_link_or_reparse(current_dir):
            raise ValueError(f"LINK_FORBIDDEN: {current_dir}")
        dirs.append(str(current_dir))
        with os.scandir(current_dir) as it:
            entries = list(it)
        for entry in entries:
            entry_path = Path(entry.path)
            if _is_link_or_reparse(entry_path):
                raise ValueError(f"LINK_FORBIDDEN: {entry_path}")
            if entry.name == ".git":
                raise ValueError("NESTED_REPOSITORY_FORBIDDEN")
            if entry.is_dir(follow_symlinks=False):
                stack.append(entry_path)
                continue
            st_before = entry_path.lstat()
            digest = file_sha256(entry_path)
            st_after = entry_path.lstat()
            if st_after.st_size != st_before.st_size or st_after.st_mtime_ns != st_before.st_mtime_ns:
                raise ValueError("ARTIFACT_CHANGED")
            files.append(
                {
                    "path": entry_path.relative_to(base).as_posix(),
                    "bytes": st_before.st_size,
                    "mtime_ns": st_before.st_mtime_ns,
                    "sha256": digest,
                }
            )

    files.sort(key=lambda item: str(item["path"]))
    dirs.sort(key=len, reverse=True)
    payload = json.dumps(files, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    tree_hash = hashlib.sha256(payload).hexdigest().upper()
    return {"files": files, "directories": dirs, "sha256": tree_hash}


def remove_artifact_tree(root: str | os.PathLike[str], expected: dict[str, object]) -> None:
    base = assert_no_link(root)
    current = get_artifact_inventory(base)
    if current["sha256"] != expected["sha256"]:
        raise ValueError("ARTIFACT_CHANGED")
    for entry in current["files"]:
        assert isinstance(entry, dict)
        path = assert_no_link(base / str(entry["path"]))
        if file_sha256(path) != entry["sha256"]:
            raise ValueError("ARTIFACT_CHANGED")
        path.unlink()
    for directory in current["directories"]:
        dir_path = assert_no_link(directory)
        if any(dir_path.iterdir()):
            raise ValueError("DIRECTORY_CHANGED")
        dir_path.rmdir()


@contextmanager
def _project_lock(lock_path: Path):
    target = assert_no_link(lock_path)
    fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlockingIOError(f"PROJECT_LOCK_BUSY: {target}") from exc
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BlockingIOError(f"PROJECT_LOCK_BUSY: {target}") from exc
        yield
    finally:
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def invoke_artifact_action(
    roots: dict[str, str],
    action: str,
    kind: str = "tmp",
    artifact_id: str = "",
    apply: bool = False,
    opportunistic: bool = False,
    now: datetime | None = None,
) -> dict[str, object]:
    action_map = {
        "new": "New",
        "complete": "Complete",
        "activate": "Activate",
        "pin": "Pin",
        "unpin": "Unpin",
        "cleanup": "Cleanup",
        "restore": "Restore",
    }
    norm_action = action_map.get(action.lower())
    if not norm_action:
        raise ValueError(f"INVALID_ACTION: {action}")
    if kind not in RETENTION_DAYS:
        raise ValueError(f"INVALID_KIND: {kind}")
    if artifact_id and not ARTIFACT_ID_RE.fullmatch(artifact_id):
        raise ValueError("INVALID_ARTIFACT_ID")
    if norm_action not in ("New", "Cleanup") and not artifact_id:
        raise ValueError("ARTIFACT_ID_REQUIRED")

    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_dt = now_dt.astimezone(timezone.utc)

    project = assert_no_link(roots["project"])
    workbench = assert_no_link(roots["workbench"])
    if not is_within(project, workbench) or project == workbench:
        raise ValueError("INVALID_PROJECT_BOUNDARY")

    meta = project / ".registry"
    mutating = (norm_action not in ("Cleanup", "Restore")) or bool(apply)
    if not mutating and not meta.exists():
        return {"status": "preview", "actions": [], "version": VERSION}
    if mutating:
        meta.mkdir(parents=True, exist_ok=True)
    assert_no_link(meta)

    lock_cm = _project_lock(meta / "project.lock") if mutating else contextmanager(lambda: (yield))()
    try:
        with lock_cm:
            events: list[dict[str, object]] = []
            clock_path = assert_no_link(meta / "cleanup_clock.json")
            if norm_action == "Cleanup" and opportunistic and clock_path.is_file():
                clock = json.loads(clock_path.read_text(encoding="utf-8-sig"))
                if (now_dt - _parse_iso(clock["last_attempt"])).total_seconds() < 24 * 3600:
                    return {"status": "throttled", "actions": []}
            if norm_action == "Cleanup" and opportunistic and apply:
                write_artifact_json(clock_path, {"last_attempt": _format_iso(now_dt)})

            if norm_action == "New":
                new_id = artifact_id or uuid.uuid4().hex
                record_path = meta / f"{new_id}.json"
                target = project / kind / new_id
                if record_path.exists() or target.exists():
                    raise ValueError("ARTIFACT_EXISTS")
                assert_no_link(target)
                target.mkdir(parents=True, exist_ok=False)
                record = {
                    "schema": 1,
                    "id": new_id,
                    "repo": roots["slug"],
                    "kind": kind,
                    "state": "active",
                    "pinned": False,
                    "created_at": _format_iso(now_dt),
                    "last_used_at": _format_iso(now_dt),
                    "completed_at": None,
                    "quarantined_at": None,
                    "inventory": None,
                    "version": VERSION,
                }
                write_artifact_json(record_path, record)
                return {"status": "active", "id": new_id, "path": str(target), "version": VERSION}

            if norm_action == "Cleanup":
                record_files = sorted(
                    p for p in meta.iterdir()
                    if p.is_file() and p.suffix == ".json" and p.name != "cleanup_clock.json"
                )
            else:
                single = meta / f"{artifact_id}.json"
                if not single.is_file():
                    raise FileNotFoundError(f"ARTIFACT_RECORD_MISSING: {artifact_id}")
                record_files = [single]

            for rf in record_files:
                assert_no_link(rf)
                try:
                    rec = json.loads(rf.read_text(encoding="utf-8-sig"))
                    if (
                        not isinstance(rec, dict)
                        or rec.get("schema") != 1
                        or rec.get("version") != VERSION
                        or rec.get("repo") != roots["slug"]
                        or not isinstance(rec.get("id"), str)
                        or not ARTIFACT_ID_RE.fullmatch(rec["id"])
                        or rf.stem != rec["id"]
                        or rec.get("kind") not in RETENTION_DAYS
                    ):
                        raise ValueError("INVALID_REGISTRATION")

                    original = assert_no_link(project / rec["kind"] / rec["id"])
                    quarantine = assert_no_link(project / ".quarantine" / rec["id"])

                    if norm_action in ("Complete", "Activate", "Pin", "Unpin"):
                        if rec.get("state") not in ("active", "completed") or not original.exists():
                            raise ValueError("ARTIFACT_NOT_AVAILABLE")
                        if norm_action == "Complete":
                            rec["state"] = "completed"
                            rec["completed_at"] = _format_iso(now_dt)
                        elif norm_action == "Activate":
                            rec["state"] = "active"
                            rec["completed_at"] = None
                        elif norm_action == "Pin":
                            rec["pinned"] = True
                        elif norm_action == "Unpin":
                            rec["pinned"] = False
                        rec["last_used_at"] = _format_iso(now_dt)
                        write_artifact_json(rf, rec)
                        events.append({"id": rec["id"], "action": norm_action, "path": str(original)})
                        continue

                    if norm_action == "Restore":
                        if rec.get("state") != "quarantined" or not quarantine.exists():
                            raise ValueError("NOT_QUARANTINED")
                        if original.exists():
                            raise ValueError("RESTORE_COLLISION")
                        if get_artifact_inventory(quarantine)["sha256"] != rec["inventory"]["sha256"]:
                            raise ValueError("QUARANTINE_CHANGED")
                        if apply:
                            original.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(quarantine), str(original))
                            rec["state"] = "completed"
                            rec["pinned"] = True
                            rec["last_used_at"] = _format_iso(now_dt)
                            rec["quarantined_at"] = None
                            write_artifact_json(rf, rec)
                        events.append({"id": rec["id"], "action": "restore", "applied": bool(apply)})
                        continue

                    if rec.get("pinned") or rec["kind"] in ("inputs", "outputs") or rec.get("state") in ("active", "deleted"):
                        continue

                    if rec.get("state") == "completed":
                        completed_at = _parse_iso(rec["completed_at"])
                        last_used_at = _parse_iso(rec["last_used_at"])
                        if completed_at > now_dt or last_used_at > now_dt:
                            raise ValueError("FUTURE_TIMESTAMP")
                        reference = max(completed_at, last_used_at)
                        elapsed_days = (now_dt - reference).total_seconds() / 86400.0
                        if elapsed_days < RETENTION_DAYS[rec["kind"]]:
                            continue
                        inventory = get_artifact_inventory(original)
                        if apply:
                            if quarantine.exists():
                                raise ValueError("QUARANTINE_COLLISION")
                            quarantine.parent.mkdir(parents=True, exist_ok=True)
                            # Record intent before move. An interrupted operation is review-only, never swept.
                            rec["inventory"] = inventory
                            rec["state"] = "quarantining"
                            write_artifact_json(rf, rec)
                            if get_artifact_inventory(original)["sha256"] != inventory["sha256"]:
                                raise ValueError("ARTIFACT_CHANGED")
                            shutil.move(str(original), str(quarantine))
                            if get_artifact_inventory(quarantine)["sha256"] != inventory["sha256"]:
                                raise ValueError("QUARANTINE_CHANGED")
                            rec["state"] = "quarantined"
                            rec["quarantined_at"] = _format_iso(now_dt)
                            write_artifact_json(rf, rec)
                        events.append(
                            {
                                "id": rec["id"],
                                "action": "quarantine",
                                "sha256": inventory["sha256"],
                                "applied": bool(apply),
                            }
                        )
                    elif rec.get("state") == "quarantined":
                        elapsed_days = (now_dt - _parse_iso(rec["quarantined_at"])).total_seconds() / 86400.0
                        if elapsed_days < 7.0:
                            continue
                        if get_artifact_inventory(quarantine)["sha256"] != rec["inventory"]["sha256"]:
                            raise ValueError("QUARANTINE_CHANGED")
                        if apply:
                            rec["state"] = "deleting"
                            write_artifact_json(rf, rec)
                            remove_artifact_tree(quarantine, rec["inventory"])
                            rec["state"] = "deleted"
                            rec["deleted_at"] = _format_iso(now_dt)
                            rec["inventory"] = {"sha256": rec["inventory"]["sha256"]}
                            write_artifact_json(rf, rec)
                        events.append({"id": rec["id"], "action": "delete", "applied": bool(apply)})
                    else:
                        raise ValueError("INCOMPLETE_TRANSACTION_REQUIRES_REVIEW")
                except Exception as exc:
                    if norm_action != "Cleanup":
                        raise
                    events.append({"id": rf.stem, "action": "preserve", "error": str(exc)})

            if mutating and events:
                log_path = assert_no_link(meta / "events.jsonl")
                with log_path.open("a", encoding="utf-8", newline="\n") as stream:
                    for ev in events:
                        line = json.dumps({"at": _format_iso(now_dt), "event": ev}, ensure_ascii=False, separators=(",", ":"))
                        stream.write(line + "\n")

            return {"status": "applied" if mutating else "preview", "actions": events, "version": VERSION}
    finally:
        if norm_action in ("New", "Complete"):
            try:
                sweep = invoke_artifact_action(
                    roots=roots,
                    action="Cleanup",
                    apply=True,
                    opportunistic=True,
                    now=now_dt,
                )
                for entry in sweep.get("actions", []):
                    if entry.get("action") == "preserve":
                        sys.stderr.write(f"Cleanup preserved {entry['id']}: {entry['error']}\n")
            except Exception as exc:
                sys.stderr.write(f"Cleanup skipped: {exc}\n")
