"""Credential-free, standard-library-only validation for the optional Hosts module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
TIME_MARKER = "；Hosts 内容更新时间："
TIME_FORMAT = "%Y-%m-%d %H:%M:%S +08:00"
BEIJING = timezone(timedelta(hours=8))
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


def module_timestamp(data: bytes) -> str | None:
    """Accept the exact legacy header or its single, validated timestamp suffix."""
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise HostsError("module_encoding") from None
    lines = text.splitlines()
    name, description = HEADER.splitlines()
    require(len(lines) >= 2 and lines[0] == name, "module_metadata")
    if lines[1] == description:
        require(TIME_MARKER not in text, "module_metadata")
        return None
    prefix = description + TIME_MARKER
    require(lines[1].startswith(prefix) and text.count(TIME_MARKER) == 1, "module_metadata")
    return validate_timestamp(lines[1][len(prefix):])


def parse_module(data: bytes) -> dict[str, str]:
    module_timestamp(data)
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


def render_module(hosts, checked_at: str | None = None) -> bytes:
    checked = normalize_hosts(hosts)
    header = HEADER
    if checked_at is not None:
        header = HEADER.rstrip("\n") + TIME_MARKER + validate_timestamp(checked_at) + "\n"
    data = (header + "\n[Host]\n" + "".join(f"{k} = {v}\n" for k, v in checked.items())).encode("utf-8")
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
    legacy = bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", checked_at))
    if legacy:
        try:
            datetime.strptime(checked_at, "%Y-%m-%d")
        except ValueError:
            raise HostsError("invalid_date") from None
    else:
        validate_timestamp(checked_at)
    require(type(node_count) is int and node_count >= 0
            and set(references) == set(hosts)
            and all(type(n) is int and 0 <= n <= node_count for n in references.values())
            and sum(references.values()) <= node_count, "reference_counts")
    label = "发布来源核对日期" if legacy else "Hosts 内容更新时间"
    rows = [BEGIN, "", f"{label}：**{checked_at}**。完整同步 **{len(hosts)} 条 Hosts**；"
            f"当次订阅共 **{node_count} 个节点**。", "",
            "此时间是该版映射完成来源确认、生成候选的北京时间，不是服务商修改、合并或每日检查时间。",
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


def validate_readme(text: str, hosts, checked_at: str | None = None):
    require(text.count(BEGIN) == text.count(END) == 1, "readme_markers")
    block = text.split(BEGIN)[1].split(END)[0]
    headings = re.findall(r"^(?:发布来源核对日期|Hosts 内容更新时间)：.*$", block, re.MULTILINE)
    stamps = re.findall(r"^(发布来源核对日期|Hosts 内容更新时间)：\*\*(.+)\*\*。"
                        r"完整同步 \*\*([0-9]+) 条 Hosts\*\*；当次订阅共 \*\*[0-9]+ 个节点\*\*。$",
                        block, re.MULTILINE)
    require(len(headings) == len(stamps) == 1 and int(stamps[0][2]) == len(hosts), "readme_timestamp")
    label, stamp, _ = stamps[0]
    if checked_at is None:
        require(label == "发布来源核对日期" and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", stamp),
                "readme_timestamp")
        try:
            datetime.strptime(stamp, "%Y-%m-%d")
        except ValueError:
            raise HostsError("invalid_date") from None
    else:
        validate_timestamp(checked_at)
        require(label == "Hosts 内容更新时间" and stamp == checked_at, "readme_timestamp_mismatch")
    entries = {}
    for line in block.splitlines():
        if line.startswith("| `"):
            match = re.fullmatch(r"\| `([^`]+)` \| `([^`]+)` \| (\d+) \| [^|]+ \|", line)
            require(match is not None and match[1] not in entries, "readme_table")
            entries[match[1]] = match[2]
    require(entries == hosts, "readme_module_mismatch")
