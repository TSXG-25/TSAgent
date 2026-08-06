"""Deterministic Dataset for the v2.3C AgentService boundary.

The Dataset validates the public contract and its event oracle.  It does not
claim that a concrete AgentService, Runtime executor, or event persistence
implementation exists; those belong to v2.3C-2 and v2.3C-3.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, cast


BENCHMARK_NAME = "agent-service-event-stream-v2.3c"
BENCHMARK_VERSION = "v0.1"
CONTRACT_VERSION = "adr-0021-v1"


class Probe(str, Enum):
    MISSING_TENANT = "MISSING_TENANT"
    MISSING_USER = "MISSING_USER"
    MISSING_SESSION = "MISSING_SESSION"
    MISSING_RUN = "MISSING_RUN"
    MISSING_REQUEST = "MISSING_REQUEST"
    EMPTY_START_TEXT = "EMPTY_START_TEXT"
    IDEMPOTENCY_SAME_DIGEST = "IDEMPOTENCY_SAME_DIGEST"
    IDEMPOTENCY_DIFFERENT_DIGEST = "IDEMPOTENCY_DIFFERENT_DIGEST"
    DTO_ROUNDTRIP = "DTO_ROUNDTRIP"
    SNAPSHOT_PROJECTION = "SNAPSHOT_PROJECTION"
    EVENT_MONOTONIC = "EVENT_MONOTONIC"
    EVENT_GAP = "EVENT_GAP"
    EVENT_IDENTITY_MISMATCH = "EVENT_IDENTITY_MISMATCH"
    EVENT_REPLAY = "EVENT_REPLAY"
    TERMINAL_EVENT = "TERMINAL_EVENT"
    EVENT_AFTER_TERMINAL = "EVENT_AFTER_TERMINAL"


class ExpectedOutcome(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"
    IDEMPOTENT = "IDEMPOTENT"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class ServiceContractCase:
    id: str
    group: str
    probe: Probe
    expected_outcome: ExpectedOutcome
    description: str
    invariant: str
    must_not: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "probe", Probe(self.probe))
        object.__setattr__(self, "expected_outcome", ExpectedOutcome(self.expected_outcome))
        object.__setattr__(
            self,
            "must_not",
            tuple(str(item) for item in self.must_not if str(item).strip()),
        )
        for field_name in ("id", "group", "description", "invariant"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must be non-empty")

    def to_dict(self, *, include_description: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "group": self.group,
            "probe": self.probe.value,
            "expected_outcome": self.expected_outcome.value,
            "invariant": self.invariant,
            "must_not": list(self.must_not),
        }
        if include_description:
            value["description"] = self.description
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ServiceContractCase":
        return cls(
            id=str(value.get("id", "")),
            group=str(value.get("group", "")),
            probe=Probe(str(value.get("probe", ""))),
            expected_outcome=ExpectedOutcome(str(value.get("expected_outcome", ""))),
            description=str(value.get("description", "")),
            invariant=str(value.get("invariant", "")),
            must_not=tuple(
                str(item)
                for item in cast(list[Any], value.get("must_not", []) or [])
            ),
        )


def build_cases() -> tuple[ServiceContractCase, ...]:
    return (
        ServiceContractCase(
            "service-identity-001",
            "identity",
            Probe.MISSING_TENANT,
            ExpectedOutcome.REJECT,
            "缺少 tenant_id 的请求必须在 Runtime 查找前拒绝",
            "tenant scope is explicit and never defaults",
            ("default tenant", "global lookup"),
        ),
        ServiceContractCase(
            "service-identity-002",
            "identity",
            Probe.MISSING_USER,
            ExpectedOutcome.REJECT,
            "缺少 user_id 的请求必须拒绝",
            "user identity is explicit for every service request",
            ("default user",),
        ),
        ServiceContractCase(
            "service-identity-003",
            "identity",
            Probe.MISSING_SESSION,
            ExpectedOutcome.REJECT,
            "缺少 session_id 的请求必须拒绝",
            "conversation scope cannot fall back to a global session",
            ("global session",),
        ),
        ServiceContractCase(
            "service-identity-004",
            "identity",
            Probe.MISSING_RUN,
            ExpectedOutcome.REJECT,
            "缺少 run_id 的读写请求必须拒绝",
            "durable lookup is bounded by tenant plus run identity",
            ("current run", "last workspace"),
        ),
        ServiceContractCase(
            "service-identity-005",
            "identity",
            Probe.MISSING_REQUEST,
            ExpectedOutcome.REJECT,
            "缺少 request_id 的调用不能进入服务",
            "request_id is required for API idempotency and diagnostics",
            ("anonymous request",),
        ),
        ServiceContractCase(
            "service-validation-006",
            "identity",
            Probe.EMPTY_START_TEXT,
            ExpectedOutcome.REJECT,
            "start_run 不接受空请求文本",
            "invalid DTOs fail before Runtime or Provider invocation",
            ("provider call", "runtime mutation"),
        ),
        ServiceContractCase(
            "service-idempotency-007",
            "idempotency",
            Probe.IDEMPOTENCY_SAME_DIGEST,
            ExpectedOutcome.IDEMPOTENT,
            "相同 request_id 与 request digest 必须返回同一逻辑结果",
            "same request identity and digest do not create another Run",
            ("second Run", "second external effect"),
        ),
        ServiceContractCase(
            "service-idempotency-008",
            "idempotency",
            Probe.IDEMPOTENCY_DIFFERENT_DIGEST,
            ExpectedOutcome.CONFLICT,
            "相同 request_id 但请求内容不同必须稳定冲突",
            "one request_id cannot represent two different operations",
            ("overwrite existing request", "second Run"),
        ),
        ServiceContractCase(
            "service-dto-009",
            "dto",
            Probe.DTO_ROUNDTRIP,
            ExpectedOutcome.PASS,
            "公开 Request/Handle/Event DTO 可 canonical round-trip",
            "public DTO serialization is deterministic and JSON-only",
            ("SQLite row", "ExecutionPlan"),
        ),
        ServiceContractCase(
            "service-snapshot-010",
            "dto",
            Probe.SNAPSHOT_PROJECTION,
            ExpectedOutcome.PASS,
            "RunSnapshot 只暴露稳定投影，不泄漏内部模型",
            "public projection is smaller than RunCheckpoint and stable",
            ("RunCheckpoint", "RunResumeIndex", "planner state"),
        ),
        ServiceContractCase(
            "service-event-011",
            "event_ordering",
            Probe.EVENT_MONOTONIC,
            ExpectedOutcome.PASS,
            "同一 Run 的事件 sequence_number 连续递增",
            "persisted event order is deterministic per Run",
            ("out-of-order event",),
        ),
        ServiceContractCase(
            "service-event-012",
            "event_ordering",
            Probe.EVENT_GAP,
            ExpectedOutcome.REJECT,
            "事件序列出现间隙必须拒绝",
            "a replayable history cannot silently skip a sequence",
            ("silent gap", "workflow re-execution"),
        ),
        ServiceContractCase(
            "service-event-013",
            "event_ordering",
            Probe.EVENT_IDENTITY_MISMATCH,
            ExpectedOutcome.REJECT,
            "跨 tenant/session/run 的事件不能进入当前流",
            "event stream identity is checked before replay",
            ("cross-scope event",),
        ),
        ServiceContractCase(
            "service-event-014",
            "event_replay",
            Probe.EVENT_REPLAY,
            ExpectedOutcome.PASS,
            "客户端可从 after_sequence 继续读取且不重复执行 Run",
            "reconnect is a read/replay operation, not a Runtime action",
            ("duplicate workflow execution",),
        ),
        ServiceContractCase(
            "service-terminal-015",
            "terminal_state",
            Probe.TERMINAL_EVENT,
            ExpectedOutcome.PASS,
            "完整事件流必须以明确终态事件结束",
            "completed, failed, and blocked Runs have explicit terminal events",
            ("implicit terminal",),
        ),
        ServiceContractCase(
            "service-terminal-016",
            "terminal_state",
            Probe.EVENT_AFTER_TERMINAL,
            ExpectedOutcome.REJECT,
            "终态事件后不得追加新的 Run 事件",
            "terminal state is monotonic and cannot reopen through the stream",
            ("post-terminal mutation",),
        ),
    )


__all__ = [
    "BENCHMARK_NAME",
    "BENCHMARK_VERSION",
    "CONTRACT_VERSION",
    "ExpectedOutcome",
    "Probe",
    "ServiceContractCase",
    "build_cases",
]
