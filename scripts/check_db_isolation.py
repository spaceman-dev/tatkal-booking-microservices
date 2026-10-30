#!/usr/bin/env python3
"""Database-per-service check: tries every service's credentials against every
service's database. Only the diagonal (a service's own database) may connect.

    python scripts/check_db_isolation.py
"""
import sys

import psycopg

from common import ROOT, Evidence, table

SERVICES = ["booking-service", "inventory-service", "payment-service", "notification-service"]


def env_of(service: str) -> dict:
    env_file = ROOT / "services" / service / ".env"
    if not env_file.exists():
        env_file = env_file.with_name(".env.example")
    url = next(line.split("=", 1)[1].strip() for line in env_file.read_text().splitlines()
               if line.startswith("DATABASE_URL="))
    # postgresql+psycopg://user:password@host:port/dbname
    creds, location = url.split("://", 1)[1].split("@", 1)
    user, password = creds.split(":", 1)
    hostport, dbname = location.split("/", 1)
    host, port = hostport.split(":")
    return {"user": user, "password": password, "host": host, "port": port, "dbname": dbname}


def main() -> None:
    out = Evidence("database-per-service", "isolation", "DATABASE PER SERVICE - CREDENTIAL ISOLATION CHECK")
    targets = {s: env_of(s) for s in SERVICES}
    rows, ok = [], True
    for who in SERVICES:
        cred = targets[who]
        row = [f"{cred['user']} ({who})"]
        for whose in SERVICES:
            db = targets[whose]
            try:
                with psycopg.connect(host=db["host"], port=db["port"], dbname=db["dbname"], user=cred["user"],
                                     password=cred["password"], connect_timeout=5) as conn:
                    tables = conn.execute("SELECT count(*) FROM information_schema.tables "
                                          "WHERE table_schema = 'public'").fetchone()[0]
                result = f"CONNECTED ({tables} tables)"
                allowed = who == whose
            except psycopg.OperationalError as exc:
                # for "localhost" psycopg may try ::1 and 127.0.0.1 and list every failure, so the
                # password error is not always on the last line
                reason = str(exc).strip()
                result = "refused: " + ("password authentication failed" if "password authentication failed" in reason
                                        else reason.splitlines()[-1][:40])
                allowed = who != whose
            ok &= allowed
            row.append(result)
        rows.append(row)

    out("Each service's database runs in its own PostgreSQL container (docker-compose.db.yml) with its")
    out("own user and password. Rows: credentials used. Columns: database tried.\n")
    table(out, ["credentials \\ database"] + [f"{targets[s]['dbname']} :{targets[s]['port']}" for s in SERVICES], rows)
    out("\nExpected: only the diagonal connects.")
    out(f"Result: {'PASS' if ok else 'FAIL'}")
    out.save()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
