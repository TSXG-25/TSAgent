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
    BoundaryRule(
        "interruption-contract",
        "agent/interruption",
        (
            "agent.runtime",
            "agent.runtime_store",
            "agent.services",
            "agent.service.service",
            "agent.orchestrator",
            "agent.planner",
            "agent.executor",
            "agent.workflow",
            "agent.checkpoint",
        ),
    ),
)


SCOPED_RUNTIME_EXCLUSIONS = {
    "agent/compat",
    "agent/event_bus.py",
    "agent/services/artifact_service.py",
    "agent/services/workspace_service.py",
    "agent/conversation/state.py",
    "agent/conversation/__init__.py",
}

FORBIDDEN_LEGACY_IMPORTS = {
    ("agent.event_bus", "event_bus"),
    ("agent.services.workspace_service", "get_workspace_service"),
    ("agent.services.artifact_service", "ArtifactService"),
    ("agent.conversation", "conversation_tracker"),
    ("agent.conversation", "conversation_retriever"),
}

CLI_FORBIDDEN_IMPORT_PREFIXES = (
    "agent.runtime",
    "agent.orchestrator",
    "agent.event_bus",
    "agent.runtime_store",
    "agent.service.runtime_launcher",
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
    for path in sorted((root / "agent").rglob("*.py")):
        relative = path.relative_to(root)
        relative_text = str(relative)
        if any(
            relative_text == excluded or relative_text.startswith(excluded + "/")
            for excluded in SCOPED_RUNTIME_EXCLUSIONS
        ):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            # The package-specific rules above already report syntax errors.
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0 or not node.module:
                continue
            for alias in node.names:
                if (node.module, alias.name) in FORBIDDEN_LEGACY_IMPORTS:
                    violations.append(
                        f"scoped-runtime: {relative} imports legacy singleton "
                        f"{node.module}.{alias.name}; use agent.compat explicitly"
                    )
    cli_path = root / "main.py"
    if cli_path.exists():
        try:
            cli_tree = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
        except SyntaxError as exc:
            violations.append(f"cli: syntax error: {exc}")
        else:
            for module in _imported_modules(cli_tree):
                if any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in CLI_FORBIDDEN_IMPORT_PREFIXES
                ):
                    violations.append(f"cli: main.py imports forbidden {module}")
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
