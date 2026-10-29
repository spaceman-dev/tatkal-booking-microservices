#!/usr/bin/env python3
"""Prints all log lines for one correlation id, from every service, in time order.

    python scripts/trace.py demo-happy-path
    python scripts/trace.py <correlation-id> --json      # raw JSON lines
"""
import argparse
import json

from common import compact_log, log_lines, log_source

SERVICES = ("booking-service", "inventory-service", "payment-service", "mock-bank-gateway", "notification-service")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("correlation_id")
    parser.add_argument("--json", action="store_true", help="print the raw JSON lines")
    args = parser.parse_args()

    entries = sorted((e for s in SERVICES for e in log_lines(s, correlation_id=args.correlation_id)),
                     key=lambda e: e["ts"])
    for entry in entries:
        print(json.dumps(entry) if args.json else compact_log(entry))
    if not entries:
        print(f"no log lines with that correlation id (log source: {log_source()}, set LOG_SOURCE=files|compose|k8s)")


if __name__ == "__main__":
    main()
