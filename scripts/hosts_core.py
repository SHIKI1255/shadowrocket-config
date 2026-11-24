"""Credential-free, standard-library-only validation for local Shadowrocket Hosts modules."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import re

DEFAULT_MODULE_NAME = "Local Hosts"
DEFAULT_MODULE_DESC = "仅用于节点入口域名解析，不改规则/DNS/MITM"
TIME_MARKER = "；Hosts 内容更新时间："
TIME_FORMAT = "%Y-%m-%d %H:%M:%S +08:00"
BEIJING = timezone(timedelta(hours=8))
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class HostsError(Exception):
    """Only a fixed reason code may cross the private-data boundary."""
    def __init__(self, code: str):
        self.code = code if re.fullmatch(r"[a-z_]+", code) else "internal_error"
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise HostsError(code)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _header_lines(module_name: str, module_desc: str) -> tuple[str, str]:
    require(type(module_name) is str and bool(module_name.strip()) and module_name == module_name.strip()
            and not any(c in module_name for c in "\r\n\x00") and not module_name.startswith("#!"),
            "module_metadata")
    require(type(module_desc) is str and bool(module_desc.strip()) and module_desc == module_desc.strip()
            and not any(c in module_desc for c in "\r\n\x00") and TIME_MARKER not in module_desc
            and not module_desc.startswith("#!"),
            "module_metadata")
    return f"#!name={module_name}", f"#!desc={module_desc}"


def normalize_hosts(value) -> dict[str, str]:
    require(isinstance(value, dict) and 0 < len(value) <= 10000, "hosts_shape")
    result = {}
    for domain, address in value.items():
        require(type(domain) is str and type(address) is str, "hosts_value_type")
        name = domain.lower().removesuffix(".")
        require(domain == domain.strip() and 0 < len(name) <= 253
                and "." in name and all(LABEL.fullmatch(x) for x in name.split(".")),
                "invalid_domain")
        require(name not in result, "duplicate_domain")
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise HostsError("invalid_ip") from None
        require("%" not in address and ip.is_global and not ip.is_multicast,
                "unsafe_ip")
        result[name] = str(ip)
    return result


def validate_timestamp(value: str) -> str:
    require(type(value) is str and re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} \+08:00", value),
        "invalid_timestamp")
    try:
        datetime.strptime(value, TIME_FORMAT)
    except ValueError:
        raise HostsError("invalid_timestamp") from None
    return value


def format_timestamp(moment: datetime) -> str:
    require(isinstance(moment, datetime) and moment.utcoffset() is not None,
            "timestamp_timezone")
    return validate_timestamp(moment.astimezone(BEIJING).strftime(TIME_FORMAT))


def module_timestamp(data: bytes, *, module_name: str = DEFAULT_MODULE_NAME,
                     module_desc: str = DEFAULT_MODULE_DESC) -> str | None:
    """Validate local-only module headers (no #!url) and optional Beijing timestamp."""
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise HostsError("module_encoding") from None
    lines = text.splitlines()
    name_line, desc_line = _header_lines(module_name, module_desc)
    require(not any(line.strip().lower().startswith("#!url") for line in lines),
            "remote_url_module_present")
    require(len(lines) >= 2 and lines[0] == name_line, "module_metadata")
    if lines[1] == desc_line:
        require(TIME_MARKER not in text, "module_metadata")
        return None
    prefix = desc_line + TIME_MARKER
    require(lines[1].startswith(prefix) and text.count(TIME_MARKER) == 1, "module_metadata")
    return validate_timestamp(lines[1][len(prefix):])


def parse_module(data: bytes, *, module_name: str = DEFAULT_MODULE_NAME,
                 module_desc: str = DEFAULT_MODULE_DESC) -> dict[str, str]:
    module_timestamp(data, module_name=module_name, module_desc=module_desc)
    text = data.decode("utf-8")
    entries = {}
    section = False
    for line in text.splitlines()[2:]:
        line = line.strip()
        if not line or line.startswith("#"):
            require(not line.startswith("#!"), "module_metadata")
            continue
        if line.startswith("["):
            require(line == "[Host]" and not section, "module_section")
            section = True
            continue
        require(section and line.count("=") == 1, "module_syntax")
        name, address = [x.strip() for x in line.split("=")]
        require(name not in entries, "duplicate_domain")
        entries[name] = address
    require(section, "module_section")
    return normalize_hosts(entries)


def ordered_hosts(previous, current):
    return {name: current[name] for name in list(previous) + list(current)
            if name in current}


def render_module(hosts, checked_at: str | None = None, *,
                  module_name: str = DEFAULT_MODULE_NAME,
                  module_desc: str = DEFAULT_MODULE_DESC) -> bytes:
    checked = normalize_hosts(hosts)
    name_line, desc_line = _header_lines(module_name, module_desc)
    if checked_at is not None:
        desc_line = desc_line + TIME_MARKER + validate_timestamp(checked_at)
    data = (f"{name_line}\n{desc_line}\n\n[Host]\n"
            + "".join(f"{k} = {v}\n" for k, v in checked.items())).encode("utf-8")
    require(parse_module(data, module_name=module_name, module_desc=module_desc) == checked,
            "roundtrip_mismatch")
    return data


def check_quantity(previous, current):
    require(len(current) * 2 > len(previous) and len(current) < len(previous) * 2,
            "quantity_gate")


def differences(previous, current):
    return {
        "added": [{"domain": k, "ip": v} for k, v in current.items() if k not in previous],
        "removed": [{"domain": k, "ip": v} for k, v in previous.items() if k not in current],
        "changed": [{"domain": k, "old": previous[k], "new": v}
                    for k, v in current.items() if k in previous and v != previous[k]],
    }
