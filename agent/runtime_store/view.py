"""Scoped access to the v2.3B durable Runtime Store.

The SQLite connection belongs to ``ApplicationContext``.  This module exposes
only a tenant/session/run-bound view to Runtime code, so production execution
does not construct a second Store or silently fall back to JSON/InMemory
writers.  The adapters are read-only for the legacy v2.2 protocols; durable
writes must go through ``prepare_operation`` and ``finalize_bundle``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agent.run_resume.store import RunResumeActivationError
from agent.run_resume.contracts import RunResumeIndex

from .contracts import FinalizationBundle, FinalizationResult, PreparedOperation
from .errors import DurableStoreError, StoreErrorCode
from .sqlite import SqliteRuntimeStore


class DurableRuntimeStoreView:
    """A lifecycle-bound, single-Run view over an Application-owned Store."""

    def __init__(
        self,
        store: SqliteRuntimeStore,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
        request_id: str,
        writer_id: str,
        ensure_run: bool = True,
        takeover_fence: bool = False,
    ) -> None:
        self._store = store
        self.tenant_id = str(tenant_id).strip()
        self.session_id = str(session_id).strip()
        self.run_id = str(run_id).strip()
        self.request_id = str(request_id).strip()
        self.writer_id = str(writer_id).strip()
        for name in (
            "tenant_id",
            "session_id",
            "run_id",
            "request_id",
            "writer_id",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        self._closed = False
        self._fence_token = 0
        if ensure_run:
            self._store.initialize_run(
                self.tenant_id,
                self.session_id,
                self.run_id,
                self.request_id,
            )
        self._acquire_or_reuse_fence(takeover=takeover_fence)

    @property
    def store(self) -> SqliteRuntimeStore:
        """Expose the Application-owned Store for infrastructure diagnostics."""

        self._ensure_open()
        return self._store

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def store_generation(self) -> str:
        self._ensure_open()
        return self._store.store_generation

    @property
    def fence_epoch(self) -> int:
        self._ensure_open()
        return self._fence_token

    @property
    def checkpoint_store(self) -> "SqliteCheckpointStoreAdapter":
        self._ensure_open()
        return SqliteCheckpointStoreAdapter(self)

    @property
    def run_resume_store(self) -> "SqliteRunResumeStoreAdapter":
        self._ensure_open()
        return SqliteRunResumeStoreAdapter(self)

    def _ensure_open(self) -> None:
        if self._closed:
            raise DurableStoreError(
                StoreErrorCode.STORE_CLOSED,
                f"durable Run view is closed: {self.run_id}",
            )

    def _acquire_or_reuse_fence(self, *, takeover: bool = False) -> None:
        current = self._store.get_current_fence(
            self.tenant_id,
            self.run_id,
            session_id=self.session_id,
        )
        if current is not None and current.writer_id != self.writer_id:
            if not takeover:
                raise DurableStoreError(
                    StoreErrorCode.FENCE_CONFLICT,
                    f"Run is already owned by writer {current.writer_id}",
                )
            grant = self._store.takeover_fence(
                self.tenant_id,
                self.session_id,
                self.run_id,
                self.writer_id,
                expected_fence_token=current.fence_token,
                request_id=self.request_id,
            )
            self._fence_token = grant.fence_token
            return
        grant = self._store.acquire_fence(
            self.tenant_id,
            self.session_id,
            self.run_id,
            self.writer_id,
            expected_fence_token=(current.fence_token if current else None),
            request_id=self.request_id,
        )
        self._fence_token = grant.fence_token

    def head(self):
        self._ensure_open()
        head = self._store.get_run_head(
            self.tenant_id,
            self.run_id,
            session_id=self.session_id,
        )
        if head is None:
            raise DurableStoreError(
                StoreErrorCode.RUN_NOT_FOUND,
                f"Run not found: {self.run_id}",
            )
        if head.current_writer_id != self.writer_id or head.current_fence_token != self._fence_token:
            raise DurableStoreError(
                StoreErrorCode.STALE_WRITER,
                "durable Run view no longer owns the current fence",
            )
        return head

    def prepare_operation(
        self,
        *,
        expected_revision: int | None = None,
        expected_parent_digest: str | None = None,
        idempotency_key: str,
        operation_type: str,
        request_digest: str,
        expected_effect_digest: str = "",
        external_reference: str = "",
        request_id: str | None = None,
    ) -> PreparedOperation:
        head = self.head()
        return self._store.prepare_operation(
            self.tenant_id,
            self.session_id,
            self.run_id,
            request_id=request_id or self.request_id,
            writer_id=self.writer_id,
            fence_token=self._fence_token,
            expected_revision=head.current_revision if expected_revision is None else expected_revision,
            expected_parent_digest=head.current_digest if expected_parent_digest is None else expected_parent_digest,
            idempotency_key=idempotency_key,
            operation_type=operation_type,
            request_digest=request_digest,
            expected_effect_digest=expected_effect_digest,
            external_reference=external_reference,
            expected_store_generation=self.store_generation,
        )

    def finalize_bundle(self, bundle: FinalizationBundle) -> FinalizationResult:
        self._ensure_open()
        if (
            bundle.tenant_id != self.tenant_id
            or bundle.session_id != self.session_id
            or bundle.run_id != self.run_id
            or bundle.writer_id != self.writer_id
            or bundle.fence_epoch != self._fence_token
        ):
            raise DurableStoreError(
                StoreErrorCode.IDENTITY_MISMATCH,
                "Finalization Bundle does not belong to this durable Run view",
            )
        return self._store.finalize_bundle(bundle)

    def transition_run_with_event(
        self,
        *,
        run_status: str,
        event_id: str,
        event_type: str,
        timestamp: str,
        payload: dict[str, Any],
        expected_status: str | None = None,
        run_output: dict[str, Any] | None = None,
    ):
        """Atomically publish a Service-visible Run state and event."""

        self._ensure_open()
        return self._store.transition_run_with_event(
            self.tenant_id,
            self.session_id,
            self.run_id,
            run_status=run_status,
            event_id=event_id,
            event_type=event_type,
            timestamp=timestamp,
            payload=payload,
            writer_id=self.writer_id,
            fence_token=self._fence_token,
            request_id=self.request_id,
            expected_status=expected_status,
            expected_store_generation=self.store_generation,
            run_output=run_output,
        )

    def latest_run_id(self, *, exclude_run_id: str | None = None) -> str | None:
        """Find the latest Run strictly inside this tenant/session scope."""

        self._ensure_open()
        return self._store.latest_run_id(
            self.tenant_id,
            self.session_id,
            exclude_run_id=exclude_run_id,
        )

    def get_checkpoint(self, checkpoint_id: str):
        self._ensure_open()
        return self._store.get_checkpoint(
            self.tenant_id,
            self.run_id,
            checkpoint_id,
            session_id=self.session_id,
        )

    def checkpoint_history(
        self,
        *,
        workflow_id: str | None = None,
        activation_attempt_id: str | None = None,
    ) -> tuple[Any, ...]:
        self._ensure_open()
        return self._store.checkpoint_history(
            self.tenant_id,
            self.run_id,
            session_id=self.session_id,
            workflow_id=workflow_id,
            activation_attempt_id=activation_attempt_id,
        )

    def latest_checkpoint(
        self,
        *,
        workflow_id: str | None = None,
        activation_attempt_id: str | None = None,
    ):
        self._ensure_open()
        return self._store.latest_checkpoint(
            self.tenant_id,
            self.run_id,
            session_id=self.session_id,
            workflow_id=workflow_id,
            activation_attempt_id=activation_attempt_id,
        )

    def get_run_index(self) -> RunResumeIndex | None:
        self._ensure_open()
        return self._store.get_run_index(
            self.tenant_id,
            self.run_id,
            session_id=self.session_id,
        )

    def bootstrap_run_index(self, index: RunResumeIndex) -> RunResumeIndex:
        """Persist the initial Run projection before any external effect.

        Run creation is metadata initialization, not an external side effect.
        It is therefore the one allowed pre-prepare index write.  All later
        index revisions are published only by ``finalize_bundle``.
        """

        self._ensure_open()
        if index.run_id != self.run_id or index.session_id != self.session_id:
            raise DurableStoreError(
                StoreErrorCode.IDENTITY_MISMATCH,
                "initial RunResumeIndex does not belong to this durable Run view",
            )
        existing = self.get_run_index()
        if existing is not None:
            return existing
        head = self.head()
        expected_revision = head.current_revision
        expected_parent_digest = head.current_digest
        if expected_revision == 0 and not expected_parent_digest:
            pass
        elif expected_revision == 1 and expected_parent_digest:
            start_intent = self._store.get_idempotency(
                self.tenant_id,
                self.run_id,
                self.request_id,
                session_id=self.session_id,
            )
            if (
                start_intent is None
                or start_intent.operation_type != "service.start_run"
                or start_intent.prepared_revision != expected_revision
                or start_intent.run_revision != expected_revision
                or start_intent.fence_epoch != self._fence_token
            ):
                raise DurableStoreError(
                    StoreErrorCode.RUN_INDEX_CONFLICT,
                    "Run revision is not the durable Service start reservation",
                )
        else:
            raise DurableStoreError(
                StoreErrorCode.RUN_INDEX_CONFLICT,
                "cannot bootstrap an index after Runtime execution has begun",
            )
        seeded = replace(
            index,
            revision=expected_revision + 1,
            parent_digest=expected_parent_digest,
            store_generation=self.store_generation,
        )
        self._store.append_revision(
            self.tenant_id,
            self.session_id,
            self.run_id,
            request_id=self.request_id,
            payload=seeded.to_dict(),
            writer_id=self.writer_id,
            fence_token=self._fence_token,
            expected_revision=expected_revision,
            expected_parent_digest=expected_parent_digest,
            run_status="COMPLETED"
            if not seeded.active_workflow_id and not seeded.pending_workflow_ids
            else "RUNNING",
            expected_store_generation=self.store_generation,
        )
        return seeded

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._store.release_fence(
                self.tenant_id,
                self.session_id,
                self.run_id,
                self.writer_id,
                self._fence_token,
                request_id=self.request_id,
            )
        except DurableStoreError as exc:
            # A process may have been fenced by a takeover while its local
            # context was closing.  The new owner is authoritative; do not
            # hide unrelated store failures.
            if exc.code is not StoreErrorCode.STALE_WRITER:
                raise
        self._closed = True

    def activate_workflow(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        attempt_id: str,
        initial_checkpoint: Any | None = None,
    ) -> RunResumeIndex:
        """Atomically promote one pending Workflow in the durable index."""

        self._ensure_open()
        existing = self.get_run_index()
        if existing is None:
            raise RunResumeActivationError(
                f"Run index not found: {self.run_id}", code="RUN_INDEX_NOT_FOUND"
            )
        from agent.run_resume.store import _activate

        try:
            activated = _activate(
                existing,
                workflow_id,
                expected_revision=existing.revision,
                attempt_id=attempt_id,
            )
        except RunResumeActivationError:
            raise
        if (
            existing.active_workflow_id == workflow_id
            and existing.workflow(workflow_id) is not None
            and existing.workflow(workflow_id).activation_attempt_id == attempt_id
        ):
            return existing
        head = self.head()
        if head.current_revision != expected_revision:
            raise RunResumeActivationError(
                "Run revision conflict", code="REVISION_CONFLICT"
            )
        if initial_checkpoint is not None:
            active_summary = activated.workflow(workflow_id)
            if active_summary is None:
                raise RunResumeActivationError(
                    "activated Workflow summary is missing",
                    code="RUN_INDEX_INCONSISTENT",
                )
            next_index = replace(
                activated,
                revision=head.current_revision + 1,
                parent_digest=head.current_digest,
                store_generation=self.store_generation,
                active_checkpoint_id=initial_checkpoint.checkpoint_id,
                workflows=tuple(
                    replace(
                        item,
                        checkpoint_id=initial_checkpoint.checkpoint_id,
                    )
                    if item.workflow_id == workflow_id
                    else item
                    for item in activated.workflows
                ),
            )
            self._store.activate_workflow_with_checkpoint(
                self.tenant_id,
                self.session_id,
                self.run_id,
                workflow_id,
                request_id=self.request_id,
                writer_id=self.writer_id,
                fence_token=self._fence_token,
                expected_revision=expected_revision,
                expected_parent_digest=head.current_digest,
                initial_checkpoint=initial_checkpoint,
                next_run_index=next_index,
                expected_store_generation=self.store_generation,
            )
            return next_index
        activated = replace(
            activated,
            revision=head.current_revision + 1,
            parent_digest=head.current_digest,
            store_generation=self.store_generation,
        )
        self._store.append_revision(
            self.tenant_id,
            self.session_id,
            self.run_id,
            request_id=self.request_id,
            payload=activated.to_dict(),
            writer_id=self.writer_id,
            fence_token=self._fence_token,
            expected_revision=head.current_revision,
            expected_parent_digest=head.current_digest,
            run_status="RUNNING",
            expected_store_generation=self.store_generation,
        )
        return activated


class SqliteCheckpointStoreAdapter:
    """Read-only implementation of the v2.2 CheckpointStore protocol."""

    def __init__(self, view: DurableRuntimeStoreView) -> None:
        self._view = view

    def save(self, checkpoint: Any):
        raise DurableStoreError(
            StoreErrorCode.INVALID_ARGUMENT,
            "production Checkpoint writes must use FinalizationBundle",
        )

    def get(self, checkpoint_id: str):
        return self._view.get_checkpoint(checkpoint_id)

    def latest(self, run_id: str):
        if run_id != self._view.run_id:
            raise DurableStoreError(StoreErrorCode.IDENTITY_MISMATCH, "Run mismatch")
        return self._view.latest_checkpoint()

    def latest_for_workflow(
        self,
        run_id: str,
        workflow_id: str,
        *,
        activation_attempt_id: str = "",
    ):
        if run_id != self._view.run_id:
            raise DurableStoreError(StoreErrorCode.IDENTITY_MISMATCH, "Run mismatch")
        return self._view.latest_checkpoint(
            workflow_id=workflow_id,
            activation_attempt_id=activation_attempt_id or None,
        )

    def history(self, run_id: str):
        if run_id != self._view.run_id:
            raise DurableStoreError(StoreErrorCode.IDENTITY_MISMATCH, "Run mismatch")
        return self._view.checkpoint_history()


class SqliteRunResumeStoreAdapter:
    """RunResumeStore view whose mutations use SQLite CAS, never JSON."""

    def __init__(self, view: DurableRuntimeStoreView) -> None:
        self._view = view

    def save(self, index: RunResumeIndex):
        raise DurableStoreError(
            StoreErrorCode.INVALID_ARGUMENT,
            "production RunResumeIndex writes must use FinalizationBundle",
        )

    def get(self, run_id: str):
        if run_id != self._view.run_id:
            raise DurableStoreError(StoreErrorCode.IDENTITY_MISMATCH, "Run mismatch")
        return self._view.get_run_index()

    def activate_workflow(
        self,
        run_id: str,
        workflow_id: str,
        *,
        expected_revision: int,
        attempt_id: str,
    ):
        if run_id != self._view.run_id:
            raise RunResumeActivationError("Run mismatch", code="RUN_MISMATCH")
        return self._view.activate_workflow(
            workflow_id,
            expected_revision=expected_revision,
            attempt_id=attempt_id,
        )


__all__ = [
    "DurableRuntimeStoreView",
    "SqliteCheckpointStoreAdapter",
    "SqliteRunResumeStoreAdapter",
]
