"""Credential-free, standard-library-only validation for the optional Hosts module."""
from __future__ import annotations

from datetime import datetime
import hashlib
import ipaddress
import re

REPOSITORY = "SHIKI1255/shadowrocket-config"
MODULE = "modules/GeekSpeed-Hosts.sgmodule"
README = "modules/README.md"
ALLOWED_FILES = (MODULE, README)
RAW_ROOT = f"https://raw.githubusercontent.com/{REPOSITORY}"
HEADER = ("#!name=GeekSpeed Hosts\n"
          "#!desc=GeekSpeed Clash 订阅 Hosts 完整同步；由使用者在本机核对维护\n")
BEGIN = "<!-- hosts-sync:snapshot:start -->"
END = "<!-- hosts-sync:snapshot:end -->"
SHA = re.compile(r"[0-9a-f]{40}")
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


def parse_module(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise HostsError("module_encoding") from None
    require(text.replace("\r\n", "\n").startswith(HEADER), "module_metadata")
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


def render_module(hosts) -> bytes:
    checked = normalize_hosts(hosts)
    data = (HEADER + "\n[Host]\n" + "".join(f"{k} = {v}\n" for k, v in checked.items())).encode("utf-8")
    require(parse_module(data) == checked, "roundtrip_mismatch")
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


def update_readme(text: str, hosts, references, node_count: int, checked_at: str,
                  previous_sha: str) -> str:
    require(text.count(BEGIN) == text.count(END) == 1 and text.index(BEGIN) < text.index(END),
            "readme_markers")
    require(SHA.fullmatch(previous_sha), "invalid_revision")
    try:
        datetime.strptime(checked_at, "%Y-%m-%d")
    except ValueError:
        raise HostsError("invalid_date") from None
    require(type(node_count) is int and node_count >= 0
            and set(references) == set(hosts)
            and all(type(n) is int and 0 <= n <= node_count for n in references.values())
            and sum(references.values()) <= node_count, "reference_counts")
    rows = [BEGIN, "", f"发布来源核对日期：**{checked_at}**。完整同步 **{len(hosts)} 条 Hosts**；"
            f"当次订阅共 **{node_count} 个节点**。", "",
            "此日期是映射发生变化时的发布核对日期，不代表每日最近一次检查时间。",
            "未被当前节点引用的映射仍完整保留；不据此判断其必需、失效或可用。", "",
            "| 域名 | 入口 IP | 节点引用数 | 验证状态 |",
            "| --- | --- | ---: | --- |"]
    rows += [f"| `{k}` | `{v}` | {references[k]} | 服务商订阅；当前映射待手机实测 |"
             for k, v in hosts.items()]
    rows += ["", f"当前 {sum(n > 0 for n in references.values())} 个入口被节点引用，"
             f"对应 {sum(references.values())} 个节点。", "",
             f"[本次更新前的固定版本]({RAW_ROOT}/{previous_sha}/{MODULE})。"
             "旧 IP 是否仍可用需另外验证。", "", END]
    prefix, rest = text.split(BEGIN, 1)
    _, suffix = rest.split(END, 1)
    return prefix + "\n".join(rows) + suffix


def validate_readme(text: str, hosts):
    require(text.count(BEGIN) == text.count(END) == 1, "readme_markers")
    block = text.split(BEGIN)[1].split(END)[0]
    entries = {}
    for line in block.splitlines():
        if line.startswith("| `"):
            match = re.fullmatch(r"\| `([^`]+)` \| `([^`]+)` \| (\d+) \| [^|]+ \|", line)
            require(match is not None and match[1] not in entries, "readme_table")
            entries[match[1]] = match[2]
    require(entries == hosts, "readme_module_mismatch")
