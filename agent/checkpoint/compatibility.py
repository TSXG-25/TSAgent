"""Version compatibility rules for the v2.2A ResumeValidator."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import ResumeContext, RunCheckpoint, RuntimeEvidence
from .reason_codes import ResumeReasonCode


def major_version(version: str) -> int | None:
    """Read a conservative major version from ``1.2``, ``v2.2A`` or ``1``."""
    match = re.match(r"^\s*[vV]?(\d+)", str(version or ""))
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class WorkflowMigration:
    workflow_id: str
    from_version: str
    to_version: str
    migration_id: str

    def matches(self, workflow_id: str, from_version: str, to_version: str) -> bool:
        return (
            self.workflow_id == workflow_id
            and self.from_version == from_version
            and self.to_version == to_version
        )


@dataclass(frozen=True)
class CompatibilityRegistry:
    """Explicit compatibility declarations; no name-based guessing."""

    current_checkpoint_schema_version: str = "1.0"
    current_contract_version: str = "v2.2A"
    workflow_migrations: tuple[WorkflowMigration, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_migrations", tuple(self.workflow_migrations or ()))

    def migration_for(
        self,
        workflow_id: str,
        from_version: str,
        to_version: str,
    ) -> WorkflowMigration | None:
        return next(
            (
                migration for migration in self.workflow_migrations
                if migration.matches(workflow_id, from_version, to_version)
            ),
            None,
        )


@dataclass(frozen=True)
class CompatibilityAssessment:
    schema_compatible: bool
    contract_compatible: bool
    workflow_same: bool
    workflow_migratable: bool
    plan_same: bool
    exact_allowed: bool
    replay_allowed: bool
    replan_allowed: bool
    reason_code: ResumeReasonCode | None = None
    evidence: tuple[RuntimeEvidence, ...] = ()


def assess_compatibility(
    checkpoint: RunCheckpoint,
    context: ResumeContext,
    registry: CompatibilityRegistry,
) -> CompatibilityAssessment:
    """Compare versions and identity without accessing external systems."""
    evidence: list[RuntimeEvidence] = []
    schema_compatible = (
        major_version(checkpoint.checkpoint_schema_version) is not None
        and major_version(checkpoint.checkpoint_schema_version)
        == major_version(registry.current_checkpoint_schema_version)
    )
    evidence.append(RuntimeEvidence(
        source="compatibility",
        kind="checkpoint_schema",
        expected=registry.current_checkpoint_schema_version,
        observed=checkpoint.checkpoint_schema_version,
        status="VERIFIED" if schema_compatible else "MISMATCH",
    ))
    if not schema_compatible:
        return CompatibilityAssessment(
            schema_compatible=False,
            contract_compatible=False,
            workflow_same=False,
            workflow_migratable=False,
            plan_same=False,
            exact_allowed=False,
            replay_allowed=False,
            replan_allowed=False,
            reason_code=ResumeReasonCode.SCHEMA_INCOMPATIBLE,
            evidence=tuple(evidence),
        )

    contract_compatible = (
        major_version(checkpoint.contract_version) is not None
        and major_version(checkpoint.contract_version)
        == major_version(registry.current_contract_version)
    )
    evidence.append(RuntimeEvidence(
        source="compatibility",
        kind="contract_version",
        expected=registry.current_contract_version,
        observed=checkpoint.contract_version,
        status="VERIFIED" if contract_compatible else "MISMATCH",
    ))
    if not contract_compatible:
        return CompatibilityAssessment(
            schema_compatible=True,
            contract_compatible=False,
            workflow_same=False,
            workflow_migratable=False,
            plan_same=False,
            exact_allowed=False,
            replay_allowed=False,
            replan_allowed=False,
            reason_code=ResumeReasonCode.CONTRACT_INCOMPATIBLE,
            evidence=tuple(evidence),
        )

    workflow_same = (
        checkpoint.workflow_id == context.workflow_id
        and checkpoint.workflow_version == context.workflow_version
    )
    workflow_id_same = checkpoint.workflow_id == context.workflow_id
    migration = (
        registry.migration_for(
            checkpoint.workflow_id,
            checkpoint.workflow_version,
            context.workflow_version,
        )
        if workflow_id_same else None
    )
    workflow_migratable = migration is not None
    evidence.append(RuntimeEvidence(
        source="compatibility",
        kind="workflow_version",
        expected=f"{context.workflow_id}@{context.workflow_version}",
        observed=f"{checkpoint.workflow_id}@{checkpoint.workflow_version}",
        status="VERIFIED" if workflow_same or workflow_migratable else "MISMATCH",
        detail=migration.migration_id if migration else "",
    ))

    plan_same = checkpoint.plan_version == context.plan_version
    evidence.append(RuntimeEvidence(
        source="compatibility",
        kind="plan_version",
        expected=context.plan_version,
        observed=checkpoint.plan_version,
        status="VERIFIED" if plan_same else "MISMATCH",
    ))

    if not workflow_id_same:
        reason = ResumeReasonCode.WORKFLOW_INCOMPATIBLE
    elif not workflow_same and not workflow_migratable:
        reason = ResumeReasonCode.WORKFLOW_INCOMPATIBLE
    elif not plan_same:
        reason = ResumeReasonCode.PLAN_INCOMPATIBLE
    else:
        reason = None

    exact_allowed = workflow_same and plan_same
    replay_allowed = exact_allowed
    replan_allowed = workflow_same or workflow_migratable
    return CompatibilityAssessment(
        schema_compatible=True,
        contract_compatible=True,
        workflow_same=workflow_same,
        workflow_migratable=workflow_migratable,
        plan_same=plan_same,
        exact_allowed=exact_allowed,
        replay_allowed=replay_allowed,
        replan_allowed=replan_allowed,
        reason_code=reason,
        evidence=tuple(evidence),
    )


__all__ = [
    "CompatibilityAssessment",
    "CompatibilityRegistry",
    "WorkflowMigration",
    "assess_compatibility",
    "major_version",
]
