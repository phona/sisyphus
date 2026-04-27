"""``python -m thanatos`` dispatcher.

- No arguments → boot the MCP stdio server (M0 behaviour, what
  ``deploy/charts/thanatos/templates/deployment.yaml`` and the Dockerfile rely on).
- One or more arguments → forward to :mod:`thanatos.cli` for shell-style
  ``run-scenario`` / ``run-all`` / ``recall`` invocations the accept-agent uses.
"""

from __future__ import annotations

import sys


def _route() -> int:
    if len(sys.argv) <= 1:
        from thanatos.server import main as _server_main

        _server_main()
        return 0

    from thanatos.cli import main as _cli_main

    return _cli_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_route())
