"""Operator CLI for rehearsals: ``python -m budget_planning_mcp.admin {status,reset,set-week,export}``."""

from __future__ import annotations

import argparse
import json
import sys

from .season import Season


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="budget_planning_mcp.admin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    reset = commands.add_parser("reset")
    reset.add_argument("--week", type=int, default=0)
    set_week = commands.add_parser("set-week")
    set_week.add_argument("week", type=int)
    commands.add_parser("export")
    args = parser.parse_args(argv)
    season = Season.from_env()
    if args.command == "status":
        result = season.status()
    elif args.command == "reset":
        result = season.reset(args.week)
    elif args.command == "set-week":
        result = season.set_week(args.week)
    else:
        result = season.export()
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
