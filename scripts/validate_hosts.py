"""CI validation: never opens a private subscription or requires PyYAML."""
from pathlib import Path
import json
import sys

from hosts_core import HostsError, MODULE, README, digest, module_timestamp, parse_module, validate_readme


def main():
    try:
        root = Path(__file__).resolve().parents[1]
        content = (root / MODULE).read_bytes()
        hosts = parse_module(content)
        validate_readme((root / README).read_text(encoding="utf-8"), hosts, module_timestamp(content))
        print(json.dumps({"status": "valid", "hosts": len(hosts), "sha256": digest(content)}))
        return 0
    except HostsError as exc:
        print(json.dumps({"status": "failed", "reason": exc.code}))
    except Exception:
        print(json.dumps({"status": "failed", "reason": "validation_error"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
