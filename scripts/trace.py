#!/usr/bin/env python3
"""Print every log line, from every service, that carries one correlation id - in time order.

    python scripts/trace.py demo-happy-path
    python scripts/trace.py <correlation-id> --json      # raw JSON lines
"""
import argparse
import json

from common import compact_log, log_lines

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
        print("no log lines with that correlation id (are the services running through scripts/stack.py?)")


if __name__ == "__main__":
    main()
