#!/usr/bin/env python3
"""Writes the OpenAPI document of every service to docs/openapi/<service>.yaml.

Each service is imported with its own virtualenv (they all have a package called
`app`), so run `python scripts/stack.py setup` first. No database is needed.

    python scripts/export_openapi.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ["booking-service", "inventory-service", "payment-service", "notification-service", "mock-bank-gateway"]

DUMP = (
    "import yaml\n"
    "from app.main import app\n"
    "class Dumper(yaml.SafeDumper):\n"
    "    def ignore_aliases(self, data):\n"
    "        return True\n"
    "def text(dumper, value):\n"
    "    style = '|' if '\\n' in value else None\n"
    "    return dumper.represent_scalar('tag:yaml.org,2002:str', value, style=style)\n"
    "Dumper.add_representer(str, text)\n"
    "print(yaml.dump(app.openapi(), Dumper=Dumper, sort_keys=False, allow_unicode=True, width=110))\n"
)


def main() -> None:
    target = ROOT / "docs" / "openapi"
    target.mkdir(parents=True, exist_ok=True)
    for service in SERVICES:
        folder = ROOT / "services" / service
        python = folder / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        result = subprocess.run([str(python), "-c", DUMP], cwd=folder, capture_output=True, text=True)
        if result.returncode != 0:
            sys.exit(f"{service}: export failed\n{result.stderr}")
        spec = result.stdout
        # Drop log lines the app may print while being imported; the YAML starts at "openapi:".
        spec = spec[spec.index("openapi:"):]
        (target / f"{service}.yaml").write_text(spec)
        paths = sum(1 for line in spec.splitlines() if line.startswith("  /"))
        print(f"{service:22} -> docs/openapi/{service}.yaml  ({paths} paths)")


if __name__ == "__main__":
    main()
