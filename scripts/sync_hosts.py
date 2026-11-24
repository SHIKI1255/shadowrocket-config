"""Local-only Hosts synchronizer writing to a private local/iCloud Shadowrocket module.

Private subscription URLs, credentials and raw YAML never leave memory or enter Git.
Run locally with --config <external_config.json> --state-dir <external_outputs_dir>
and either --check (read-only) or --sync (atomic local write + local backup).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from hosts_core import (BEIJING, LABEL, HostsError, check_quantity, differences, digest,
                        format_timestamp, normalize_hosts, ordered_hosts, parse_module,
                        render_module, require)

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 10 * 1024 * 1024
MAX_CONFIG_BYTES = 64 * 1024
MAX_BACKUPS = 10
UID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _ensure_outside_repo(path: Path, code: str) -> Path:
    require(path.is_absolute() and not path.is_symlink(), code)
    resolved = path.resolve()
    require(ROOT not in resolved.parents and resolved != ROOT, code)
    return path


def load_yaml(content: bytes):
    try:
        import yaml
        require(yaml.__version__ == "6.0.3", "yaml_version")
        text = content.decode("utf-8-sig")
        # Reject aliases, anchors and explicit tags before construction. No YAML code runs.
        for token in yaml.scan(text):
            require(not isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken,
                                           yaml.tokens.TagToken)), "yaml_alias_or_tag")

        class UniqueLoader(yaml.SafeLoader):
            pass

        def mapping(loader, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                require(type(key) in (str, int, float, bool) and key not in result,
                        "yaml_duplicate_or_complex_key")
                result[key] = loader.construct_object(value_node, deep=deep)
            return result

        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        return yaml.load(text, Loader=UniqueLoader)
    except HostsError:
        raise
    except ImportError:
        raise HostsError("yaml_unavailable") from None
    except Exception:
        raise HostsError("yaml_invalid") from None


def load_config(config_path: Path) -> dict:
    try:
        _ensure_outside_repo(config_path, "config_location")
        require(config_path.is_file() and config_path.stat().st_size <= MAX_CONFIG_BYTES,
                "config_invalid")
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except HostsError:
        raise
    except Exception:
        raise HostsError("config_invalid") from None
    require(isinstance(raw, dict), "config_invalid")
    uid = raw.get("profile_uid")
    host = raw.get("source_host")
    source_path = raw.get("source_path")
    module_name = raw.get("module_name")
    module_desc = raw.get("module_desc")
    target_raw = raw.get("target_path")
    require(type(uid) is str and bool(UID_RE.fullmatch(uid)), "config_invalid")
    require(type(host) is str and host == host.lower() and "." in host and len(host) <= 253
            and all(LABEL.fullmatch(x) for x in host.split(".")), "config_invalid")
    require(type(source_path) is str and source_path.startswith("/")
            and not any(c in source_path for c in "?#\r\n\x00 "), "config_invalid")
    require(type(module_name) is str and bool(module_name.strip()), "config_invalid")
    require(type(module_desc) is str and bool(module_desc.strip()), "config_invalid")
    require(type(target_raw) is str and target_raw.endswith(".sgmodule"), "config_invalid")
    target = Path(target_raw)
    _ensure_outside_repo(target, "target_location")
    require(not target.parent.is_symlink() and target.parent.is_dir(), "target_dir_missing")
    return {
        "profile_uid": uid,
        "source_host": host,
        "source_path": source_path,
        "module_name": module_name,
        "module_desc": module_desc,
        "target_path": target,
    }


def profiles_index_path(platform: str | None = None) -> Path:
    target = sys.platform if platform is None else platform
    if target == "darwin":
        return (Path.home() / "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev"
                / "profiles.yaml")
    return (Path.home() / "AppData/Roaming/io.github.clash-verge-rev.clash-verge-rev"
            / "profiles.yaml")


def source_url(config: dict) -> str:
    try:
        path = profiles_index_path()
        require(path.stat().st_size <= MAX_BYTES, "source_index_size")
        index = load_yaml(path.read_bytes())
        require(isinstance(index, dict) and isinstance(index.get("items"), list), "source_index")
        matches = [p for p in index["items"]
                   if isinstance(p, dict) and p.get("uid") == config["profile_uid"]]
        require(len(matches) == 1 and matches[0].get("type") == "remote", "source_identity")
        url = matches[0].get("url")
        require(type(url) is str, "source_url")
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == "https" and parsed.hostname == config["source_host"]
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and parsed.path == config["source_path"] and not parsed.fragment
                and bool(urllib.parse.parse_qs(parsed.query).get("token")), "source_origin")
        return url
    except HostsError:
        raise
    except Exception:
        raise HostsError("source_read_failed") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HostsError("redirect_rejected")


def download(url: str, *, sleeper=time.sleep, opener=None) -> bytes:
    opener = opener or urllib.request.build_opener(NoRedirect())
    headers = {"User-Agent": "Clash.Meta",
               "Accept": "text/yaml,application/yaml,text/plain",
               "Cache-Control": "no-cache"}
    for attempt in range(3):
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=25) as response:
                require(response.status == 200, "http_status")
                data = response.read(MAX_BYTES + 1)
                require(0 < len(data) <= MAX_BYTES, "response_size")
                return data
        except HostsError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise HostsError("http_rejected") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError):
            pass
        except Exception:
            raise HostsError("download_failed") from None
        if attempt < 2:
            sleeper((1, 3)[attempt])
    raise HostsError("download_exhausted")


def subscription(data: bytes) -> dict:
    parsed = load_yaml(data)
    require(isinstance(parsed, dict), "subscription_shape")
    hosts = normalize_hosts(parsed.get("hosts"))
    proxies = parsed.get("proxies")
    require(isinstance(proxies, list) and 0 < len(proxies) <= 10000, "subscription_nodes")
    references = dict.fromkeys(hosts, 0)
    for proxy in proxies:
        require(isinstance(proxy, dict) and type(proxy.get("server")) is str, "subscription_nodes")
        name = proxy["server"].lower().removesuffix(".")
        if name in references:
            references[name] += 1
    # Do not return proxy objects, arbitrary fields or raw YAML across this boundary.
    return {"hosts": hosts, "references": references, "node_count": len(proxies)}


def check_target_ready(target: Path) -> None:
    require(not target.parent.is_symlink() and target.parent.is_dir(), "target_dir_missing")
    require(not target.is_symlink(), "target_symlink")
    placeholder = target.parent / f".{target.name}.icloud"
    require(not placeholder.exists(), "icloud_placeholder_present")


def atomic_write_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    tmp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            raise
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def backup_existing(state_dir: Path, target: Path, moment: datetime) -> str | None:
    if not target.is_file():
        return None
    backups_dir = state_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{target.stem}-{stamp}-{uuid.uuid4().hex[:6]}.sgmodule"
    backup_path = backups_dir / name
    atomic_write_bytes(backup_path, target.read_bytes())
    existing = sorted(backups_dir.glob(f"{target.stem}-*.sgmodule"))
    for stale in existing[:-MAX_BACKUPS]:
        stale.unlink()
    return name


def load_baseline(state_dir: Path, target: Path, config: dict) -> tuple[dict[str, str] | None, bytes | None]:
    current_copy = state_dir / "current.sgmodule"
    state_file = state_dir / "state.json"
    mod_kw = {"module_name": config["module_name"], "module_desc": config["module_desc"]}
    target_bytes = target.read_bytes() if target.is_file() else None
    if target_bytes is not None:
        # Fail closed if the existing file still contains a remote #!url directive.
        parse_module(target_bytes, **mod_kw)
    if current_copy.is_file():
        saved_bytes = current_copy.read_bytes()
        if state_file.is_file():
            meta = json.loads(state_file.read_text(encoding="utf-8"))
            require(isinstance(meta, dict) and meta.get("module_sha256") == digest(saved_bytes),
                    "state_hash_mismatch")
        return parse_module(saved_bytes, **mod_kw), target_bytes
    if target_bytes is not None:
        return parse_module(target_bytes, **mod_kw), target_bytes
    return None, None


@contextmanager
def runtime_lock(directory: Path):
    _ensure_outside_repo(directory, "state_location")
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "run.lock").open("a+b")
    locked = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise HostsError("already_running") from None
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def run(config_path: Path, state_dir: Path, *, sync: bool = False, init: bool = False,
        sleeper=time.sleep, downloader=download, now: datetime | None = None) -> dict:
    with runtime_lock(state_dir):
        config = load_config(config_path)
        target: Path = config["target_path"]
        mod_kw = {"module_name": config["module_name"], "module_desc": config["module_desc"]}
        check_target_ready(target)
        previous, target_bytes = load_baseline(state_dir, target, config)
        if previous is None:
            require(init, "baseline_missing")
        url = source_url(config)
        first = subscription(downloader(url))
        target_matches_previous = (
            previous is not None
            and target_bytes is not None
            and parse_module(target_bytes, **mod_kw) == previous
        )
        if previous is not None and first["hosts"] == previous and target_matches_previous:
            return {
                "status": "unchanged",
                "hosts_count": len(previous),
                "target_verified": True,
                "source_checked": True,
            }
        sleeper(10)
        second = subscription(downloader(url))
        require(first["hosts"] == second["hosts"], "source_changed_between_reads")
        if previous is not None:
            check_quantity(previous, second["hosts"])
            hosts = ordered_hosts(previous, second["hosts"])
            diff = differences(previous, hosts)
        else:
            hosts = second["hosts"]
            diff = differences({}, hosts)
        moment = now if now is not None else datetime.now(BEIJING)
        checked_at = format_timestamp(moment)
        rendered = render_module(hosts, checked_at, **mod_kw)
        expected_sha = digest(rendered)
        summary = {
            "hosts_count": len(hosts),
            "node_count": second["node_count"],
            "referenced_entries": sum(1 for count in second["references"].values() if count > 0),
            "diff": diff,
            "checked_at": checked_at,
            "source_checked": True,
        }
        if not sync:
            return {"status": "candidate", "module_sha256": expected_sha, **summary}
        check_target_ready(target)
        backup_name = backup_existing(state_dir, target, moment)
        atomic_write_bytes(target, rendered)
        readback = target.read_bytes()
        require(digest(readback) == expected_sha
                and parse_module(readback, **mod_kw) == hosts, "target_readback_mismatch")
        atomic_write_bytes(state_dir / "current.sgmodule", rendered)
        state = {
            "schema_version": 1,
            "module_sha256": expected_sha,
            "hosts_count": len(hosts),
            "checked_at": checked_at,
            "target_name": target.name,
            "last_backup": backup_name,
        }
        atomic_write_bytes(
            state_dir / "state.json",
            (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        return {
            "status": "synced",
            "module_sha256": expected_sha,
            "target_name": target.name,
            "backup": backup_name,
            **summary,
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--sync", action="store_true")
    parser.add_argument("--init", action="store_true",
                        help="Explicitly allow initial baseline creation when no prior local module exists")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.config, args.state_dir, sync=args.sync, init=args.init)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except HostsError as exc:
        print(json.dumps({"status": "needs_attention", "reason": exc.code}), flush=True)
    except KeyboardInterrupt:
        print(json.dumps({"status": "needs_attention", "reason": "interrupted"}), flush=True)
    except Exception:
        print(json.dumps({"status": "failed", "reason": "internal_error"}), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
