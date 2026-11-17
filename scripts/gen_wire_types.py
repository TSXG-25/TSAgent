"""Regenerate apps/desktop/src/service/wire.generated.ts from agent/service/wire_schema.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.service.wire_codegen import TS_OUTPUT, render_typescript  # noqa: E402

TS_OUTPUT.write_text(render_typescript(), encoding="utf-8")
print(f"wrote {TS_OUTPUT}")
