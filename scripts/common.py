"""Shared helpers for the challenge scripts and the e2e test.

The scripts call the services over HTTP like any client. For the evidence they also read
each service's database (read-only, with that service's own credentials from its .env).
Only these scripts do that, the services never touch each other's databases.
"""
import json
import os
import random
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parent.parent

BOOKING = os.environ.get("BOOKING_URL", "http://localhost:8001")
INVENTORY = os.environ.get("INVENTORY_URL", "http://localhost:8002")
PAYMENT = os.environ.get("PAYMENT_URL", "http://localhost:8003")
NOTIFICATION = os.environ.get("NOTIFICATION_URL", "http://localhost:8004")
GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8099")


def http(timeout: float = 60) -> httpx.Client:
    return httpx.Client(timeout=timeout)


def check_services_up() -> None:
    missing = []
    with http(3) as client:
        for name, url in (("booking", BOOKING), ("inventory", INVENTORY), ("payment", PAYMENT),
                          ("notification", NOTIFICATION), ("mock-bank-gateway", GATEWAY)):
            try:
                client.get(f"{url}/health").raise_for_status()
            except httpx.HTTPError:
                missing.append(name)
    if missing:
        sys.exit(f"Not running: {', '.join(missing)}. Start everything with `docker compose up -d` "
                 "(or python scripts/stack.py start)")


# reading a service's own database (evidence only)
def _dsn(service: str) -> str:
    env_file = ROOT / "services" / service / ".env"
    if not env_file.exists():
        env_file = env_file.with_name(".env.example")
    for line in env_file.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().replace("postgresql+psycopg://", "postgresql://")
    raise RuntimeError(f"no DATABASE_URL for {service}")


def query(service: str, sql: str, params: dict | None = None) -> tuple[list[str], list[tuple]]:
    with psycopg.connect(_dsn(service), connect_timeout=5) as conn:
        cur = conn.execute(sql, params or {})
        return [d.name for d in cur.description], cur.fetchall()


def show_query(out, title: str, service: str, sql: str, params: dict | None = None) -> list[tuple]:
    headers, rows = query(service, sql, params)
    out(f"\n{title}   [{service} DB]")
    out("  " + " ".join(sql.split()))
    table(out, headers, rows)
    return rows


# output
def table(out, headers: list[str], rows: list) -> None:
    cells = [[_fmt(v) for v in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    out("  " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    out("  " + "-+-".join("-" * w for w in widths))
    for row in cells:
        out("  " + " | ".join(c.ljust(w) for c, w in zip(row, widths)))
    if not cells:
        out("  (no rows)")


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%H:%M:%S.%f")[:-3]
    return str(value)


def now_local() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")


class Evidence:
    """Prints to the terminal and keeps a copy for evidence/<folder>/<name>_<timestamp>.txt"""

    def __init__(self, folder: str, name: str, title: str):
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.folder = ROOT / "evidence" / folder
        self.path = self.folder / f"{name}_{self.stamp}.txt"
        self.started_utc = datetime.now(timezone.utc)
        self.lines: list[str] = []
        self("=" * 100)
        self(title)
        script = Path(sys.argv[0]).resolve()
        shown = script.relative_to(ROOT) if script.is_relative_to(ROOT) else script
        self(f"command : python {shown.as_posix()} {' '.join(sys.argv[1:])}".rstrip())
        self(f"run at  : {now_local()}   |   UTC {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
        self(f"git     : {git_commit()}")
        self("=" * 100)

    def __call__(self, *parts) -> None:
        text = " ".join(str(p) for p in parts)
        print(text, flush=True)
        self.lines.append(text)

    def section(self, title: str) -> None:
        self("")
        self(f"--- {title} " + "-" * max(0, 95 - len(title)))

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self.lines) + "\n")
        print(f"\n[saved to {self.path.relative_to(ROOT)}]")
        return self.path

    def save_raw_logs(self, name: str, entries: list[dict]) -> None:
        """Raw JSON log lines (as written by the services) next to the run's output."""
        if not entries:
            return
        path = self.folder / f"{name}_{self.stamp}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
        self(f"[{len(entries)} raw log lines saved to {path.relative_to(ROOT)}]")


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip() or "n/a"
    except Exception:  # noqa: BLE001
        return "n/a"


# logs
# The services log JSON lines to stdout. Where those end up depends on how they run:
#   files    scripts/stack.py writes logs/<service>.log
#   compose  docker compose logs <service>
#   k8s      kubectl logs (all pods of the service)
# LOG_SOURCE picks one; by default it is files if stack.py is running, else compose if the
# compose stack is up. For Kubernetes set LOG_SOURCE=k8s.
K8S_NAMESPACE = os.environ.get("K8S_NAMESPACE", "tatkal")


def _compose_is_up() -> bool:
    try:
        out = subprocess.run(["docker", "compose", "ps", "-q", "--status", "running", "booking-service"],
                             cwd=ROOT, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out.strip())


def log_source() -> str:
    """files, compose, k8s, or none when there is nowhere to read logs from."""
    chosen = os.environ.get("LOG_SOURCE")
    if chosen:
        return chosen
    if (ROOT / ".run" / "booking-service.pid").exists():
        return "files"
    if _compose_is_up():
        return "compose"
    return "none"


def _raw_log_lines(service: str) -> list[str]:
    source = log_source()
    if source == "files":
        path = ROOT / "logs" / f"{service}.log"
        return path.read_text(errors="replace").splitlines() if path.exists() else []
    if source == "compose":
        cmd = ["docker", "compose", "logs", "--no-color", "--no-log-prefix", service]
    elif source == "k8s":
        cmd = ["kubectl", "logs", "-n", K8S_NAMESPACE, "-l", f"app={service}", "--tail=-1",
               "--max-log-requests=20"]
    else:
        return []
    try:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def log_lines(service: str, *, contains: str | None = None, correlation_id: str | None = None,
              since: datetime | None = None) -> list[dict]:
    found = []
    for raw in _raw_log_lines(service):
        if not raw.startswith("{"):
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if correlation_id and entry.get("correlation_id") != correlation_id:
            continue
        if contains and contains not in raw:
            continue
        if since and datetime.fromisoformat(entry["ts"]) < since:
            continue
        found.append(entry)
    return found


def compact_log(entry: dict) -> str:
    skip = {"ts", "level", "service", "correlation_id", "logger", "msg"}
    extra = " ".join(f"{k}={v}" for k, v in entry.items() if k not in skip)
    return (f"{entry['ts'][11:23]} {entry['level']:<7} {entry['service']:<20} cid={entry['correlation_id'][:16]:<16} "
            f"{entry['msg']}  {extra}").rstrip()


# test data
def fresh_run_date() -> date:
    """A journey date far in the future that no other run uses, so every run has its own quota."""
    return date.today() + timedelta(days=random.randint(400, 30_000))


def open_quota(client: httpx.Client, train_no: str, journey_date: date, travel_class: str, seats: int,
               base_fare_paise: int) -> dict:
    response = client.post(f"{INVENTORY}/api/v1/inventory", json={
        "train_no": train_no, "journey_date": journey_date.isoformat(), "travel_class": travel_class,
        "total_seats": seats, "base_fare_paise": base_fare_paise})
    response.raise_for_status()
    return response.json()


def booking_body(train_no: str, journey_date: date, travel_class: str, passengers: list[tuple[str, int, str]],
                 payer_ref: str = "asha@okmock", mobile: str = "9876543210", email: str | None = None) -> dict:
    body = {
        "train_no": train_no,
        "journey_date": journey_date.isoformat(),
        "travel_class": travel_class,
        "passengers": [{"name": n, "age": a, "gender": g} for n, a, g in passengers],
        "contact": {"mobile": mobile},
        "payment": {"method": "UPI", "payer_ref": payer_ref},
    }
    if email:
        body["contact"]["email"] = email
    return body


def rupees(paise: int | None) -> str:
    return "-" if paise is None else f"Rs {paise / 100:,.2f}"


_FIRST = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Ananya", "Vihaan", "Ishita", "Arjun", "Sneha",
          "Karthik", "Pooja", "Nikhil", "Lakshmi", "Farhan", "Riya", "Siddharth", "Neha", "Aditya", "Kavya"]
_LAST = ["Sharma", "Iyer", "Reddy", "Patil", "Nair", "Gupta", "Kulkarni", "Menon", "Joshi", "Das"]


def passenger_name(number: int) -> str:
    """Deterministic, valid passenger names (the API rejects digits in names)."""
    return f"{_FIRST[number % len(_FIRST)]} {_LAST[(number // len(_FIRST)) % len(_LAST)]}"
