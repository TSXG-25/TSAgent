"""Static import-boundary verification for the v2.0 architecture.

The verifier intentionally checks only direct Python imports.  Runtime
orchestration modules are allowed to compose services; pure Planner,
Reflection, Decision, and Executor packages are not allowed to reach across
their boundaries.
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class BoundaryRule:
    name: str
    package: str
    forbidden_prefixes: tuple[str, ...]


RULES = (
    BoundaryRule(
        "planner",
        "agent/planner",
        (
            "agent.runtime",
            "agent.workspace",
            "agent.services.workspace_service",
            "agent.services.memory_service",
            "agent.memory",
            "agent.executor",
            "agent.orchestrator",
        ),
    ),
    BoundaryRule(
        "reflection",
        "agent/reflection",
        (
            "agent.runtime",
            "agent.workspace",
            "agent.services",
            "agent.memory",
            "agent.executor",
            "agent.orchestrator",
            "agent.state",
        ),
    ),
    BoundaryRule(
        "decision",
        "agent/decision",
        (
            "agent.runtime",
            "agent.workspace",
            "agent.services",
            "agent.memory",
            "agent.executor",
            "agent.orchestrator",
            "agent.state",
        ),
    ),
    BoundaryRule(
        "executor",
        "agent/executor",
        (
            "agent.runtime",
            "agent.memory",
            "agent.services.memory_service",
        ),
    ),
    BoundaryRule(
        "checkpoint",
        "agent/checkpoint",
        (
            "agent.runtime",
            "agent.services",
            "agent.orchestrator",
            "agent.planner",
            "agent.executor",
            "agent.workflow",
        ),
    ),
)


def _imported_modules(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def verify(root: Path = PROJECT_ROOT) -> list[str]:
    violations: list[str] = []
    for rule in RULES:
        package_root = root / rule.package
        for path in sorted(package_root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                violations.append(f"{path.relative_to(root)}: syntax error: {exc}")
                continue

            for module in _imported_modules(tree):
                if any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in rule.forbidden_prefixes
                ):
                    relative = path.relative_to(root)
                    violations.append(
                        f"{rule.name}: {relative} imports forbidden {module}"
                    )
    return violations


def main() -> int:
    violations = verify()
    if violations:
        print("Architecture Verification: FAIL")
        print("\n".join(f"- {item}" for item in violations))
        return 1
    print("Architecture Verification: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
