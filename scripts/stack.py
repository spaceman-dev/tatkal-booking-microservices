#!/usr/bin/env python3
"""Run the whole system locally - works on macOS, Linux and Windows, needs only Python 3.11+.

    python scripts/stack.py setup               # venv per service, install deps, create .env files
    python scripts/stack.py start               # start all services in the background (logs/ folder)
    python scripts/stack.py status              # health of every service
    python scripts/stack.py stop                # stop everything
    python scripts/stack.py restart inventory-service
    python scripts/stack.py test                # unit + integration tests of every service

Environment variables override the .env files, e.g.
    HOLD_STRATEGY=naive python scripts/stack.py restart inventory-service

The PostgreSQL databases are not started here:  docker compose -f docker-compose.db.yml up -d
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES_DIR = ROOT / "services"
LOG_DIR = ROOT / "logs"
RUN_DIR = ROOT / ".run"
TOOLS_VENV = ROOT / ".venv"

# Start order: the external mock first, the orchestrator last.
SERVICES = ["mock-bank-gateway", "inventory-service", "payment-service", "notification-service", "booking-service"]
WINDOWS = os.name == "nt"


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if WINDOWS else "bin/python")


def read_env_file(service: str) -> dict:
    env_file = SERVICES_DIR / service / ".env"
    if not env_file.exists():
        env_file = SERVICES_DIR / service / ".env.example"
    values = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def port_of(service: str) -> int:
    return int(read_env_file(service)["PORT"])


def health(service: str) -> tuple[bool, str]:
    url = f"http://localhost:{port_of(service)}/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            body = json.loads(resp.read())
            return True, body.get("status", "?")
    except Exception as exc:  # noqa: BLE001 - any failure means "not healthy"
        return False, type(exc).__name__


def pid_file(service: str) -> Path:
    return RUN_DIR / f"{service}.pid"


def process_cwd(pid: int) -> Path | None:
    proc = Path(f"/proc/{pid}/cwd")
    if proc.exists():  # Linux
        return Path(os.readlink(proc)).resolve()
    out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],  # macOS
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("n"):
            return Path(line[1:]).resolve()
    return None


def is_service_process(pid: int, service: str) -> bool:
    """True only if `pid` is this service's `python -m app`. Once a service has died,
    the OS can hand its number to any other program, so a pid file alone is not proof."""
    folder = SERVICES_DIR / service
    try:
        if WINDOWS:
            # The venv's python.exe is a small launcher, and its command line holds the venv path.
            query = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"
            command = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                                     capture_output=True, text=True, timeout=30).stdout
            return str(folder / ".venv").lower() in command.lower() and "-m app" in command
        # On macOS the interpreter re-executes itself, so the venv path is not in the
        # command line; the working directory tells the services apart instead.
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
        return "-m app" in command and process_cwd(pid) == folder.resolve()
    except (OSError, subprocess.SubprocessError):
        return False


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def running_pid(service: str) -> int | None:
    path = pid_file(service)
    if not path.exists():
        return None
    text = path.read_text().strip()
    if text.isdigit() and is_service_process(int(text), service):
        return int(text)
    path.unlink(missing_ok=True)  # stale: never signal whatever process has that number now
    return None


# ---- commands --------------------------------------------------------------------

def cmd_setup(_args) -> None:
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or newer is required")
    for service in SERVICES:
        folder = SERVICES_DIR / service
        venv = folder / ".venv"
        print(f"==> {service}")
        if not venv.exists():
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        subprocess.run([str(venv_python(venv)), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
        subprocess.run([str(venv_python(venv)), "-m", "pip", "install", "-q", "-r", str(folder / "requirements.txt")],
                       check=True)
        env_file = folder / ".env"
        if not env_file.exists():
            shutil.copy(folder / ".env.example", env_file)
            print("    created .env from .env.example")
    print("==> tooling venv for scripts/ and tests/e2e")
    if not TOOLS_VENV.exists():
        subprocess.run([sys.executable, "-m", "venv", str(TOOLS_VENV)], check=True)
    subprocess.run([str(venv_python(TOOLS_VENV)), "-m", "pip", "install", "-q", "-r",
                    str(ROOT / "scripts" / "requirements.txt")], check=True)
    print("\nDone. Next: docker compose -f docker-compose.db.yml up -d  &&  python scripts/stack.py start")


def start_one(service: str) -> None:
    pid = running_pid(service)
    if pid:
        print(f"{service:22} already running (pid {pid})")
        return
    folder = SERVICES_DIR / service
    python = venv_python(folder / ".venv")
    if not python.exists():
        sys.exit(f"{service}: no virtualenv - run 'python scripts/stack.py setup' first")
    LOG_DIR.mkdir(exist_ok=True)
    RUN_DIR.mkdir(exist_ok=True)
    log = open(LOG_DIR / f"{service}.log", "ab")
    if WINDOWS:  # no console of its own, so closing this terminal does not stop the service
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    else:
        kwargs = {"start_new_session": True}
    proc = subprocess.Popen([str(python), "-m", "app"], cwd=folder, stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, **kwargs)
    pid_file(service).write_text(str(proc.pid))
    print(f"{service:22} starting on port {port_of(service)} (pid {proc.pid}, log logs/{service}.log)")


def wait_healthy(services: list[str], timeout: float = 45) -> bool:
    deadline = time.time() + timeout
    pending = set(services)
    while pending and time.time() < deadline:
        for service in list(pending):
            if health(service)[0]:
                pending.discard(service)
        time.sleep(0.5)
    for service in services:
        ok, detail = health(service)
        print(f"  {service:22} {'UP' if ok else 'NOT READY (' + detail + ')'}")
    return not pending


def cmd_start(args) -> None:
    services = args.services or SERVICES
    for service in services:
        start_one(service)
    print("waiting for /health ...")
    if not wait_healthy(services):
        sys.exit("some services did not become healthy - check the logs/ folder (is the database running?)")


def stop_one(service: str) -> None:
    pid = running_pid(service)
    if not pid:
        print(f"{service:22} not running")
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):  # graceful shutdown stops the background jobs first
            if not alive(pid):
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
    pid_file(service).unlink(missing_ok=True)
    print(f"{service:22} stopped")


def cmd_stop(args) -> None:
    for service in reversed(args.services or SERVICES):
        stop_one(service)


def cmd_restart(args) -> None:
    cmd_stop(args)
    cmd_start(args)


def cmd_status(_args) -> None:
    for service in SERVICES:
        ok, detail = health(service)
        pid = running_pid(service)
        print(f"{service:22} port {port_of(service):5}  {'UP  ' if ok else 'DOWN'}  "
              f"{'pid ' + str(pid) if pid else ''}  {detail if not ok else ''}")


def cmd_test(args) -> None:
    failed = []
    for service in args.services or SERVICES:
        folder = SERVICES_DIR / service
        print(f"\n==================== {service} ====================", flush=True)
        result = subprocess.run([str(venv_python(folder / ".venv")), "-m", "pytest", "-v"], cwd=folder)
        if result.returncode != 0:
            failed.append(service)
    print("\nFAILED: " + ", ".join(failed) if failed else "\nAll service test suites passed.")
    sys.exit(1 if failed else 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup").set_defaults(func=cmd_setup)
    for name, func in (("start", cmd_start), ("stop", cmd_stop), ("restart", cmd_restart), ("test", cmd_test)):
        p = sub.add_parser(name)
        p.add_argument("services", nargs="*", metavar="service", help="default: all of " + ", ".join(SERVICES))
        p.set_defaults(func=func)
    sub.add_parser("status").set_defaults(func=cmd_status)
    args = parser.parse_args()
    unknown = [s for s in getattr(args, "services", None) or [] if s not in SERVICES]
    if unknown:
        parser.error(f"unknown service(s): {', '.join(unknown)}. Choose from: {', '.join(SERVICES)}")
    args.func(args)


if __name__ == "__main__":
    main()
