"""Failure-oriented Dataset for the v2.3B durable Store contract.

The cases describe transaction/crash boundaries and expected facts.  They do
not open SQLite or claim that the production Store has been implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import cast


BENCHMARK_NAME = "durable-sqlite-runtime-store-v2.3b"
BENCHMARK_VERSION = "v0.2"
CONTRACT_VERSION = "adr-0020-v2"


class StoreOperation(str, Enum):
    CHECKPOINT_BUNDLE = "CHECKPOINT_BUNDLE"
    ACTIVATE_WORKFLOW = "ACTIVATE_WORKFLOW"
    COMPLETE_WORKFLOW = "COMPLETE_WORKFLOW"
    PREPARE_OPERATION = "PREPARE_OPERATION"
    ACQUIRE_FENCE = "ACQUIRE_FENCE"
    FINALIZE_BUNDLE = "FINALIZE_BUNDLE"
    SERIALIZE = "SERIALIZE"
    READ_SNAPSHOT = "READ_SNAPSHOT"


class CrashTrigger(str, Enum):
    NONE = "NONE"
    BEFORE_BEGIN = "BEFORE_BEGIN"
    AFTER_CHECKPOINT_INSERT = "AFTER_CHECKPOINT_INSERT"
    AFTER_ARTIFACT_METADATA = "AFTER_ARTIFACT_METADATA"
    AFTER_INDEX_UPDATE = "AFTER_INDEX_UPDATE"
    BEFORE_COMMIT = "BEFORE_COMMIT"
    PREPARATION_BEFORE_COMMIT = "PREPARATION_BEFORE_COMMIT"
    AFTER_PREPARATION_COMMIT = "AFTER_PREPARATION_COMMIT"
    AFTER_COMMIT_BEFORE_RESPONSE = "AFTER_COMMIT_BEFORE_RESPONSE"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    STALE_WRITER = "STALE_WRITER"
    FENCE_TAKEOVER = "FENCE_TAKEOVER"
    IDEMPOTENCY_SAME_KEY_SAME_DIGEST = "IDEMPOTENCY_SAME_KEY_SAME_DIGEST"
    IDEMPOTENCY_SAME_KEY_DIFFERENT_DIGEST = "IDEMPOTENCY_SAME_KEY_DIFFERENT_DIGEST"
    DIFFERENT_KEY = "DIFFERENT_KEY"
    SIDE_EFFECT_BEFORE_FINALIZATION = "SIDE_EFFECT_BEFORE_FINALIZATION"
    UNKNOWN_EXTERNAL_RESULT = "UNKNOWN_EXTERNAL_RESULT"
    PROCESS_RESTART_AFTER_COMMIT = "PROCESS_RESTART_AFTER_COMMIT"
    READ_DURING_COMMIT = "READ_DURING_COMMIT"


class OracleOutcome(str, Enum):
    COMMITTED = "COMMITTED"
    PREPARED = "PREPARED"
    NO_CHANGE = "NO_CHANGE"
    ROLLED_BACK = "ROLLED_BACK"
    IDEMPOTENT_RETRY = "IDEMPOTENT_RETRY"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    FENCE_ACQUIRED = "FENCE_ACQUIRED"
    REJECTED = "REJECTED"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    REQUIRE_CLARIFICATION = "REQUIRE_CLARIFICATION"
    RECOVERED = "RECOVERED"
    CONSISTENT_SNAPSHOT = "CONSISTENT_SNAPSHOT"


class VisibleState(str, Enum):
    PREVIOUS = "PREVIOUS"
    NEW = "NEW"
    BLOCKED = "BLOCKED"
    EXTERNAL_RECONCILIATION = "EXTERNAL_RECONCILIATION"
    PREVIOUS_OR_NEW = "PREVIOUS_OR_NEW"


class EffectState(str, Enum):
    PREPARED = "PREPARED"
    STARTED = "STARTED"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StoreCrashCase:
    id: str
    group: str
    operation: StoreOperation
    trigger: CrashTrigger
    expected_outcome: OracleOutcome
    expected_visible_state: VisibleState
    description: str
    invariant: str
    must_preserve: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    effect_state: str = ""
    oracle_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", StoreOperation(self.operation))
        object.__setattr__(self, "trigger", CrashTrigger(self.trigger))
        object.__setattr__(self, "expected_outcome", OracleOutcome(self.expected_outcome))
        object.__setattr__(self, "expected_visible_state", VisibleState(self.expected_visible_state))
        if self.effect_state:
            object.__setattr__(self, "effect_state", EffectState(self.effect_state).value)
        object.__setattr__(
            self,
            "must_preserve",
            tuple(str(item) for item in self.must_preserve if str(item).strip()),
        )
        object.__setattr__(
            self,
            "must_not",
            tuple(str(item) for item in self.must_not if str(item).strip()),
        )

    def to_dict(self, *, include_description: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "id": self.id,
            "group": self.group,
            "operation": self.operation.value,
            "trigger": self.trigger.value,
            "expected_outcome": self.expected_outcome.value,
            "expected_visible_state": self.expected_visible_state.value,
            "invariant": self.invariant,
            "must_preserve": list(self.must_preserve),
            "must_not": list(self.must_not),
            "effect_state": self.effect_state,
            "oracle_only": self.oracle_only,
        }
        if include_description:
            value["description"] = self.description
        return value

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "StoreCrashCase":
        return cls(
            id=str(value.get("id", "")),
            group=str(value.get("group", "")),
            operation=StoreOperation(str(value.get("operation", ""))),
            trigger=CrashTrigger(str(value.get("trigger", ""))),
            expected_outcome=OracleOutcome(str(value.get("expected_outcome", ""))),
            expected_visible_state=VisibleState(str(value.get("expected_visible_state", ""))),
            description=str(value.get("description", "")),
            invariant=str(value.get("invariant", "")),
            must_preserve=tuple(
                str(item)
                for item in cast(list[object], value.get("must_preserve", []) or [])
            ),
            must_not=tuple(
                str(item)
                for item in cast(list[object], value.get("must_not", []) or [])
            ),
            effect_state=str(value.get("effect_state", "")),
            oracle_only=bool(value.get("oracle_only", True)),
        )


def build_cases() -> tuple[StoreCrashCase, ...]:
    return (
        StoreCrashCase(
            "store-schema-001", "schema", StoreOperation.SERIALIZE, CrashTrigger.NONE,
            OracleOutcome.COMMITTED, VisibleState.NEW,
            "canonical JSON payload 与 schema version round-trip",
            "codec output is JSON-only and schema version is explicit",
            ("payload_digest", "schema_version"),
            ("live object",),
        ),
        StoreCrashCase(
            "store-before-begin-002", "atomicity", StoreOperation.CHECKPOINT_BUNDLE,
            CrashTrigger.BEFORE_BEGIN, OracleOutcome.NO_CHANGE, VisibleState.PREVIOUS,
            "事务开始前进程崩溃",
            "no transaction means no new Store fact is visible",
            ("previous index", "previous checkpoint"),
            ("partial row",),
        ),
        StoreCrashCase(
            "store-prepare-before-commit-003", "preparation", StoreOperation.PREPARE_OPERATION,
            CrashTrigger.PREPARATION_BEFORE_COMMIT, OracleOutcome.ROLLED_BACK,
            VisibleState.PREVIOUS,
            "外部副作用前的 Preparation Transaction 在提交前崩溃",
            "no external effect is allowed before durable intent commits",
            ("previous revision",),
            ("external call", "untracked side effect"),
        ),
        StoreCrashCase(
            "store-prepare-committed-004", "preparation", StoreOperation.PREPARE_OPERATION,
            CrashTrigger.AFTER_PREPARATION_COMMIT, OracleOutcome.PREPARED, VisibleState.NEW,
            "intent 已提交但进程在调用外部 Tool 前退出",
            "the next process can observe PREPARED and decide safely",
            ("idempotency key", "request digest", "expected effect digest"),
            ("duplicate preparation",),
            EffectState.PREPARED.value,
        ),
        StoreCrashCase(
            "store-after-checkpoint-005", "atomicity", StoreOperation.CHECKPOINT_BUNDLE,
            CrashTrigger.AFTER_CHECKPOINT_INSERT, OracleOutcome.ROLLED_BACK,
            VisibleState.PREVIOUS,
            "Checkpoint insert 后、Artifact metadata 写入前崩溃",
            "checkpoint/index/artifact bundle commits or rolls back as one unit",
            ("previous index", "previous artifact metadata"),
            ("orphan checkpoint", "advanced index"),
        ),
        StoreCrashCase(
            "store-after-artifact-006", "atomicity", StoreOperation.CHECKPOINT_BUNDLE,
            CrashTrigger.AFTER_ARTIFACT_METADATA, OracleOutcome.ROLLED_BACK,
            VisibleState.PREVIOUS,
            "Artifact metadata 写入后、RunResumeIndex 更新前崩溃",
            "metadata cannot survive without its checkpoint bundle",
            ("previous checkpoint", "previous index"),
            ("orphan artifact metadata",),
        ),
        StoreCrashCase(
            "store-after-index-007", "atomicity", StoreOperation.CHECKPOINT_BUNDLE,
            CrashTrigger.AFTER_INDEX_UPDATE, OracleOutcome.ROLLED_BACK,
            VisibleState.PREVIOUS,
            "RunResumeIndex 更新后、事务提交前崩溃",
            "index cannot point at a checkpoint absent from the same commit",
            ("previous index", "previous checkpoint"),
            ("torn index/checkpoint state",),
        ),
        StoreCrashCase(
            "store-before-commit-008", "atomicity", StoreOperation.COMPLETE_WORKFLOW,
            CrashTrigger.BEFORE_COMMIT, OracleOutcome.ROLLED_BACK,
            VisibleState.PREVIOUS,
            "完成 Workflow 的事务提交前崩溃",
            "completion facts become visible only after COMMIT",
            ("active workflow", "unpublished artifact"),
            ("false completed status",),
        ),
        StoreCrashCase(
            "store-after-commit-009", "idempotency", StoreOperation.FINALIZE_BUNDLE,
            CrashTrigger.AFTER_COMMIT_BEFORE_RESPONSE, OracleOutcome.IDEMPOTENT_RETRY,
            VisibleState.NEW,
            "COMMIT 成功但响应丢失，调用方使用同一 idempotency key 重试",
            "same idempotency key returns the committed fact without a second mutation",
            ("one checkpoint", "one ledger entry", "new index revision"),
            ("duplicate checkpoint", "duplicate external operation"),
        ),
        StoreCrashCase(
            "store-same-key-same-digest-010", "idempotency", StoreOperation.PREPARE_OPERATION,
            CrashTrigger.IDEMPOTENCY_SAME_KEY_SAME_DIGEST,
            OracleOutcome.IDEMPOTENT_RETRY, VisibleState.NEW,
            "同一 key、同一 operation/request digest 的重复 prepare",
            "a committed or in-progress intent is returned instead of duplicated",
            ("existing intent", "request digest"),
            ("second ledger row",),
            EffectState.PREPARED.value,
        ),
        StoreCrashCase(
            "store-same-key-different-digest-011", "idempotency", StoreOperation.PREPARE_OPERATION,
            CrashTrigger.IDEMPOTENCY_SAME_KEY_DIFFERENT_DIGEST,
            OracleOutcome.IDEMPOTENCY_CONFLICT, VisibleState.PREVIOUS,
            "同一 key 但 operation/request digest 不同",
            "one idempotency key cannot represent two different operations",
            ("original intent",),
            ("replacement intent", "second external call"),
        ),
        StoreCrashCase(
            "store-different-key-012", "idempotency", StoreOperation.PREPARE_OPERATION,
            CrashTrigger.DIFFERENT_KEY, OracleOutcome.COMMITTED, VisibleState.NEW,
            "不同 idempotency key 表示独立操作",
            "different keys are independently allowed",
            ("two independent intents",),
            ("false conflict",),
        ),
        StoreCrashCase(
            "store-revision-conflict-013", "cas", StoreOperation.ACTIVATE_WORKFLOW,
            CrashTrigger.REVISION_CONFLICT, OracleOutcome.REJECTED, VisibleState.PREVIOUS,
            "两个 writer 使用同一 expected_revision 竞争 activation",
            "only one compare-and-swap writer may advance the Run revision",
            ("unchanged latest revision",),
            ("second activation", "revision overwrite"),
        ),
        StoreCrashCase(
            "store-stale-writer-014", "fencing", StoreOperation.COMPLETE_WORKFLOW,
            CrashTrigger.STALE_WRITER, OracleOutcome.REJECTED, VisibleState.PREVIOUS,
            "旧 Worker 在 fence token 失效后尝试提交",
            "a stale fence cannot mutate a newer owner\'s Run facts",
            ("new owner revision",),
            ("stale writer mutation",),
        ),
        StoreCrashCase(
            "store-fence-takeover-015", "fencing", StoreOperation.ACQUIRE_FENCE,
            CrashTrigger.FENCE_TAKEOVER, OracleOutcome.FENCE_ACQUIRED, VisibleState.NEW,
            "Worker A 崩溃后 Worker B 在事务内接管 Run",
            "fence_epoch is monotonic and old tokens remain permanently invalid",
            ("fence_epoch + 1", "new writer ownership"),
            ("token reuse", "stale release"),
        ),
        StoreCrashCase(
            "store-side-effect-window-016", "external_effect", StoreOperation.FINALIZE_BUNDLE,
            CrashTrigger.SIDE_EFFECT_BEFORE_FINALIZATION,
            OracleOutcome.RECONCILE_REQUIRED, VisibleState.EXTERNAL_RECONCILIATION,
            "PREPARED/STARTED intent 已存在，外部文件在 Finalization 前写入",
            "database rollback does not roll back an external side effect",
            ("prepared intent", "external reference", "digest evidence"),
            ("blind retry",),
            EffectState.STARTED.value,
        ),
        StoreCrashCase(
            "store-unknown-effect-017", "external_effect", StoreOperation.FINALIZE_BUNDLE,
            CrashTrigger.UNKNOWN_EXTERNAL_RESULT,
            OracleOutcome.REQUIRE_CLARIFICATION, VisibleState.BLOCKED,
            "外部 Provider 返回未知，无法确认副作用是否已提交",
            "unknown external effect must not be auto-resumed",
            ("blocked resume decision",),
            ("automatic replay", "completed claim"),
            EffectState.UNKNOWN.value,
        ),
        StoreCrashCase(
            "store-process-restart-018", "recovery", StoreOperation.ACTIVATE_WORKFLOW,
            CrashTrigger.PROCESS_RESTART_AFTER_COMMIT,
            OracleOutcome.RECOVERED, VisibleState.NEW,
            "activation commit 后进程退出，由新进程加载同一数据库",
            "new process rebuilds the active Run from durable facts",
            ("active workflow", "activation attempt", "revision chain"),
            ("second activation",),
        ),
        StoreCrashCase(
            "store-read-snapshot-019", "read_consistency", StoreOperation.READ_SNAPSHOT,
            CrashTrigger.READ_DURING_COMMIT,
            OracleOutcome.CONSISTENT_SNAPSHOT, VisibleState.PREVIOUS_OR_NEW,
            "reader 与 writer 交错时读取恢复所需的多表事实",
            "a read sees one complete SQLite snapshot, never a mixed revision",
            ("index/checkpoint/artifact consistency",),
            ("torn snapshot",),
        ),
    )


__all__ = [
    "BENCHMARK_NAME",
    "BENCHMARK_VERSION",
    "CONTRACT_VERSION",
    "CrashTrigger",
    "EffectState",
    "OracleOutcome",
    "StoreCrashCase",
    "StoreOperation",
    "VisibleState",
    "build_cases",
]
