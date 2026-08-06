"""Legacy Store protocol for Run-level resume indexes.

The JSON implementation remains a compatibility/test adapter for v2.2C.  The
production v2.3B path reads through the scoped SQLite view and publishes index
revisions only as part of a Finalization Bundle.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Protocol, runtime_checkable

from .codec import deserialize_run_index, run_index_digest
from .contracts import (
    RunResumeIndex,
    RunWorkflowStatus,
)


class RunResumeStoreError(ValueError):
    """Raised when a Run index update would break its immutable revision chain."""

    def __init__(self, message: str, *, code: str = "STORE_ERROR") -> None:
        super().__init__(message)
        self.code = code


class RunResumeActivationError(RunResumeStoreError):
    """Raised when a pending Workflow cannot be atomically activated."""


@runtime_checkable
class RunResumeStore(Protocol):
    def save(self, index: RunResumeIndex) -> RunResumeIndex:
        """Persist and return the latest immutable Run index."""

    def get(self, run_id: str) -> RunResumeIndex | None:
        """Load the latest index for one Run."""

    def activate_workflow(
        self,
        run_id: str,
        workflow_id: str,
        *,
        expected_revision: int,
        attempt_id: str,
    ) -> RunResumeIndex:
        """Atomically commit one pending Workflow as the active Workflow."""


def _validate_revision(existing: RunResumeIndex | None, index: RunResumeIndex) -> None:
    if existing is None:
        if index.revision != 0 or index.parent_digest:
            raise RunResumeStoreError(
                "Run 的第一个 index 必须是 revision=0 且没有 parent_digest"
            )
        return
    if index.revision != existing.revision + 1:
        raise RunResumeStoreError("Run index revision 必须连续递增 1")
    if index.parent_digest != run_index_digest(existing):
        raise RunResumeStoreError("Run index parent_digest 不匹配 latest index")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _activate(
    existing: RunResumeIndex,
    workflow_id: str,
    *,
    expected_revision: int,
    attempt_id: str,
) -> RunResumeIndex:
    if not attempt_id.strip():
        raise RunResumeActivationError(
            "attempt_id 不能为空", code="ATTEMPT_ID_REQUIRED"
        )

    active = existing.workflow(existing.active_workflow_id) if existing.active_workflow_id else None
    if active is not None:
        if (
            active.workflow_id == workflow_id
            and active.activation_attempt_id == attempt_id
        ):
            # Retry of the same committed activation is idempotent, even if
            # the caller retained the pre-commit expected_revision.
            return existing
        code = (
            "ACTIVATION_ATTEMPT_CONFLICT"
            if active.workflow_id == workflow_id
            else "ACTIVE_WORKFLOW_EXISTS"
        )
        raise RunResumeActivationError(
            f"Run 已有 active Workflow: {active.workflow_id}", code=code
        )

    if existing.revision != expected_revision:
        raise RunResumeActivationError(
            f"Run revision 冲突: expected={expected_revision} actual={existing.revision}",
            code="REVISION_CONFLICT",
        )
    summary = existing.workflow(workflow_id)
    if summary is None:
        raise RunResumeActivationError(
            f"Workflow 不属于 Run: {workflow_id}", code="WORKFLOW_NOT_FOUND"
        )
    if workflow_id in existing.completed_workflow_ids or summary.status is RunWorkflowStatus.COMPLETED:
        raise RunResumeActivationError(
            f"已完成 Workflow 不得重新 active: {workflow_id}",
            code="WORKFLOW_ALREADY_COMPLETED",
        )
    if summary.status is not RunWorkflowStatus.PENDING or workflow_id not in existing.pending_workflow_ids:
        raise RunResumeActivationError(
            f"Workflow 不是 pending: {workflow_id}", code="WORKFLOW_NOT_PENDING"
        )

    workflow_map = {item.workflow_id: item for item in existing.workflows}
    for upstream in summary.depends_on:
        if workflow_map[upstream].status is not RunWorkflowStatus.COMPLETED:
            raise RunResumeActivationError(
                f"上游 Workflow 未完成: {upstream}",
                code="UPSTREAM_WORKFLOW_INCOMPLETE",
            )
    artifact_map = {item.artifact_id: item for item in existing.artifacts}
    for requirement in summary.required_artifacts:
        artifact = artifact_map.get(requirement.artifact_id)
        if artifact is None or not artifact.exists or not artifact.verified:
            raise RunResumeActivationError(
                f"上游 Artifact 缺失或未验证: {requirement.artifact_id}",
                code="UPSTREAM_ARTIFACT_MISSING",
            )
        if artifact.digest != requirement.expected_digest:
            raise RunResumeActivationError(
                f"上游 Artifact digest 不匹配: {requirement.artifact_id}",
                code="UPSTREAM_ARTIFACT_CHANGED",
            )

    activated = replace(
        summary,
        status=RunWorkflowStatus.RUNNING,
        activation_attempt_id=attempt_id,
        verifier_status="PENDING",
        checkpoint_id="",
    )
    workflows = tuple(
        activated if item.workflow_id == workflow_id else item
        for item in existing.workflows
    )
    pending = tuple(
        item for item in existing.pending_workflow_ids if item != workflow_id
    )
    return existing.evolve(
        parent_digest=run_index_digest(existing),
        workflows=workflows,
        active_workflow_id=workflow_id,
        active_checkpoint_id="",
        pending_workflow_ids=pending,
        updated_at=_timestamp(),
    )


class InMemoryRunResumeStore:
    """Latest-index store with strict immutable revision checks."""

    def __init__(self) -> None:
        self._indexes: dict[str, RunResumeIndex] = {}
        self._lock = threading.RLock()

    def save(self, index: RunResumeIndex) -> RunResumeIndex:
        with self._lock:
            existing = self._indexes.get(index.run_id)
            if existing is not None and run_index_digest(existing) == run_index_digest(index):
                return existing
            _validate_revision(existing, index)
            self._indexes[index.run_id] = index
            return index

    def get(self, run_id: str) -> RunResumeIndex | None:
        return self._indexes.get(run_id)

    def activate_workflow(
        self,
        run_id: str,
        workflow_id: str,
        *,
        expected_revision: int,
        attempt_id: str,
    ) -> RunResumeIndex:
        with self._lock:
            existing = self._indexes.get(run_id)
            if existing is None:
                raise RunResumeActivationError(
                    f"Run 不存在: {run_id}", code="RUN_NOT_FOUND"
                )
            activated = _activate(
                existing,
                workflow_id,
                expected_revision=expected_revision,
                attempt_id=attempt_id,
            )
            self._indexes[run_id] = activated
            return activated


class JsonRunResumeStore:
    """Small JSON-backed Store used to prove process-restart rehydration."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return {"runs": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RunResumeStoreError(f"Run Resume Store 无法读取: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("runs", {}), dict):
            raise RunResumeStoreError("Run Resume Store 顶层必须包含 object runs")
        return payload

    def _write(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            raise RunResumeStoreError(f"Run Resume Store 无法写入: {exc}") from exc
        finally:
            if temporary.exists():
                temporary.unlink()

    def save(self, index: RunResumeIndex) -> RunResumeIndex:
        with self._lock:
            payload = self._read()
            runs = payload["runs"]
            assert isinstance(runs, dict)
            raw_existing = runs.get(index.run_id)
            existing = (
                deserialize_run_index(raw_existing)
                if isinstance(raw_existing, dict)
                else None
            )
            if existing is not None and run_index_digest(existing) == run_index_digest(index):
                return existing
            _validate_revision(existing, index)
            runs[index.run_id] = index.to_dict()
            self._write(payload)
            return index

    def get(self, run_id: str) -> RunResumeIndex | None:
        with self._lock:
            payload = self._read()
            runs = payload["runs"]
            assert isinstance(runs, dict)
            raw = runs.get(run_id)
            if raw is None:
                return None
            if not isinstance(raw, dict):
                raise RunResumeStoreError(f"Run {run_id} 的 index 必须是 object")
            return deserialize_run_index(raw)

    def activate_workflow(
        self,
        run_id: str,
        workflow_id: str,
        *,
        expected_revision: int,
        attempt_id: str,
    ) -> RunResumeIndex:
        with self._lock:
            payload = self._read()
            runs = payload["runs"]
            assert isinstance(runs, dict)
            raw_existing = runs.get(run_id)
            if not isinstance(raw_existing, dict):
                raise RunResumeActivationError(
                    f"Run 不存在: {run_id}", code="RUN_NOT_FOUND"
                )
            existing = deserialize_run_index(raw_existing)
            activated = _activate(
                existing,
                workflow_id,
                expected_revision=expected_revision,
                attempt_id=attempt_id,
            )
            runs[run_id] = activated.to_dict()
            self._write(payload)
            return activated


__all__ = [
    "InMemoryRunResumeStore",
    "JsonRunResumeStore",
    "RunResumeActivationError",
    "RunResumeStore",
    "RunResumeStoreError",
]
