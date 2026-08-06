"""Failure-oriented Dataset for v2.3A state ownership boundaries."""
from __future__ import annotations

from dataclasses import dataclass


BENCHMARK_NAME = "runtime-context-isolation-v2.3a"
BENCHMARK_VERSION = "v0.1"
CONTRACT_VERSION = "adr-0019-v1"


@dataclass(frozen=True)
class IsolationCase:
    id: str
    group: str
    scope: str
    description: str
    invariant: str
    expected: str
    failure_signal: str

    def to_dict(self, *, include_description: bool = True) -> dict[str, str]:
        value = {
            "id": self.id,
            "group": self.group,
            "scope": self.scope,
            "invariant": self.invariant,
            "expected": self.expected,
            "failure_signal": self.failure_signal,
        }
        if include_description:
            value["description"] = self.description
        return value


def build_cases() -> tuple[IsolationCase, ...]:
    return (
        IsolationCase(
            "artifact-run-key-001", "artifact_isolation", "run",
            "两个 Run 使用相同 artifact key",
            "artifact keys are scoped by run_id",
            "each run reads its own artifact",
            "Run B reads or overwrites Run A artifact",
        ),
        IsolationCase(
            "artifact-concurrent-002", "artifact_isolation", "run",
            "两个 Run 并发写入相同文件名",
            "concurrent runs do not clear or overwrite each other's artifacts",
            "both artifacts remain addressable with distinct ownership",
            "one write disappears or digest changes in the other Run",
        ),
        IsolationCase(
            "artifact-cross-reference-003", "ownership", "run",
            "Run A 引用 Run B Artifact",
            "artifact reference owner must equal current run_id",
            "reference is rejected before execution",
            "cross-run artifact content is silently accepted",
        ),
        IsolationCase(
            "session-reset-004", "session_isolation", "session",
            "Session A reset 不影响 Session B",
            "reset mutates only the owning session namespace",
            "Session B conversation and facts remain unchanged",
            "Session B state is cleared",
        ),
        IsolationCase(
            "session-memory-005", "session_isolation", "session",
            "两个 Session 使用相同 user-facing key",
            "session memory view has explicit session ownership",
            "facts and conversation are not cross-recalled",
            "one Session answers with the other Session's fact",
        ),
        IsolationCase(
            "event-run-scope-006", "event_isolation", "run",
            "Run A 事件不送达 Run B",
            "event delivery is restricted to the declared run scope",
            "only Run A subscribers receive the event",
            "Run B subscriber receives Run A event",
        ),
        IsolationCase(
            "event-old-agent-007", "event_lifecycle", "session",
            "旧 Agent 不处理新 Session 事件",
            "agent close unsubscribes every owned subscription",
            "old Agent callback count remains zero",
            "old callback receives a new Session event",
        ),
        IsolationCase(
            "event-reset-leak-008", "event_lifecycle", "session",
            "连续 reset 不累积 listener",
            "reset and recreate are listener-count neutral",
            "subscriber count returns to baseline",
            "subscriber count grows after repeated reset",
        ),
        IsolationCase(
            "run-close-events-009", "event_lifecycle", "run",
            "Run close 后禁止发布事件",
            "closed Run rejects event publication",
            "publish raises a closed-scope error",
            "event is accepted after Run close",
        ),
        IsolationCase(
            "run-close-resources-010", "resource_lifecycle", "run",
            "Run close 释放订阅和临时 workspace handle",
            "close releases every Run-owned resource",
            "subscription count and handles return to zero",
            "handle or subscriber remains reachable after close",
        ),
        IsolationCase(
            "context-identity-011", "ownership", "run",
            "Session/Run/Workspace identity 错配",
            "all scoped dependencies must agree on owner identity",
            "mismatch is rejected before mutation",
            "runtime silently falls back to default/global state",
        ),
        IsolationCase(
            "lifecycle-idempotence-012", "resource_lifecycle", "run",
            "重复 create/reset/destroy 1000 次",
            "lifecycle operations are idempotent and leak-free",
            "no duplicate event handling and no resource growth",
            "duplicate callback or resource count increases",
        ),
    )


__all__ = [
    "BENCHMARK_NAME",
    "BENCHMARK_VERSION",
    "CONTRACT_VERSION",
    "IsolationCase",
    "build_cases",
]
