"""Executable entry point for ``python -m agent.service.local_sidecar``."""

from __future__ import annotations

import asyncio
import sys
import traceback
from contextlib import redirect_stdout
from typing import Sequence

from .lifecycle import create_service, parse_args
from .server import serve
from .dispatcher import SidecarDispatcher


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_args(argv)
    protocol_stdout = sys.stdout
    try:
        # Every ordinary print/progress bar in the Runtime is redirected to
        # stderr.  Only server.py receives the saved protocol stdout handle.
        with redirect_stdout(sys.stderr):
            # Tool registrations are loaded at the first plan boundary.  The
            # sidecar must acknowledge durable start requests without paying
            # cold imports for tools that the Run may never use.
            service = create_service(
                config,
                bootstrap_tools=False,
                defer_context_creation=True,
            )
            dispatcher = SidecarDispatcher(service, diagnostics=sys.stderr)
            return asyncio.run(
                serve(
                    dispatcher,
                    stdin=sys.stdin,
                    stdout=protocol_stdout,
                    diagnostics=sys.stderr,
                )
            )
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests
    raise SystemExit(main())
