"""Shell-friendly CLI wrapper around :mod:`thanatos.runner`.

Exposes the same three calls that the MCP stdio server registers (``run_scenario``,
``run_all``, ``recall``) as one-shot subcommands that print JSON on stdout. This
lets the accept-agent invoke thanatos via ``kubectl exec ... -- python -m thanatos
run-scenario --skill ... --spec ... --scenario-id ... --endpoint ...`` without
implementing the MCP JSON-RPC handshake from a shell over ``mcp__aissh-tao__exec_run``.

Wire contract:

- exit code is ``0`` on a successful dispatch (even when the underlying scenario
  reports ``pass=false``); exit ``2`` on argparse / validation errors; exit ``3``
  on uncaught runner errors (parser / skill loader raises).
- stdout is exactly one JSON document (object for ``run-scenario``, array for
  ``run-all`` / ``recall``); stderr carries human-readable diagnostics.
- the MCP server (:mod:`thanatos.server`) and this CLI MUST stay in lock-step —
  any new tool added to the server MUST also gain a CLI subcommand here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from thanatos.runner import recall, run_all, run_scenario


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m thanatos",
        description="Thanatos shell CLI — same dispatch as the MCP stdio server.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="SUBCOMMAND")

    rs = sub.add_parser("run-scenario", help="Run a single scenario by id.")
    rs.add_argument("--skill", required=True, dest="skill_path")
    rs.add_argument("--spec", required=True, dest="spec_path")
    rs.add_argument("--scenario-id", required=True, dest="scenario_id")
    rs.add_argument("--endpoint", required=True)

    ra = sub.add_parser("run-all", help="Run every scenario in a spec.md.")
    ra.add_argument("--skill", required=True, dest="skill_path")
    ra.add_argument("--spec", required=True, dest="spec_path")
    ra.add_argument("--endpoint", required=True)

    rc = sub.add_parser("recall", help="Recall product knowledge by intent.")
    rc.add_argument("--skill", required=True, dest="skill_path")
    rc.add_argument("--intent", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.cmd == "run-scenario":
            payload: object = run_scenario(
                args.skill_path, args.spec_path, args.scenario_id, args.endpoint,
            ).to_dict()
        elif args.cmd == "run-all":
            payload = [
                r.to_dict()
                for r in run_all(args.skill_path, args.spec_path, args.endpoint)
            ]
        elif args.cmd == "recall":
            payload = recall(args.skill_path, args.intent)
        else:  # pragma: no cover — argparse rejects unknown subcommands
            print(f"unknown subcommand: {args.cmd!r}", file=sys.stderr)
            return 2
    except Exception as exc:
        print(f"thanatos {args.cmd}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
