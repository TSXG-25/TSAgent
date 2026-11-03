# ADR-0035: Publish files from verified execution receipts

Status: Implemented for managed filesystem effects and declared command outputs; offline verification only.

## Problem

Runtime derived artifact paths from Task verbs and targets instead of execution evidence. This dropped files from command generators, used incorrect logical targets as paths, and discarded successful writes when a later step made the same Task fail. Finalizer and ExecutionStage repeated target-based filesystem checks; the Service could discard an invalid artifact yet retain COMPLETED.

## Contract

The canonical Task adds `output_paths`: a list of expected file/directory output scopes for an execution task. This is a declaration to verify, never evidence or extra authorization. EffectAuthorization checks declarations against the request's write scope. Planner is instructed to supply all expected outputs for command-based generators; stdout-only execution needs no declaration.

ToolExecutor snapshots declared scopes before and after execution. Only newly created/content-changed nonempty files become command effect candidates. Managed filesystem write/copy/move receipts are collected independently of the Task's terminal status. ExecutionVerifier resolves actual paths within the Run workspace and creates canonical path + SHA-256 evidence. A no-op command and a pre-existing file cannot satisfy a declared output. Directory declarations project individual files, not an opaque directory artifact.

Both successful and failed Task results carry `verified_files`. Verified delete/move receipts carry `consumed_files` to retire prior artifacts. ExecutionStage preserves these across observations/retries; it no longer re-verifies guessed Task targets. Modify uses the write verifier at the execution boundary.

Finalizer projects the latest receipts, retires consumed paths, checks current content digests, and supplies one `file_artifact_evidence` projection to GoalVerifier and Runtime. Runtime artifact publication no longer branches on Task verb/target/status. Thus partial files from a failed Task remain visible, and the final answer uses actual paths. Changed evidence causes `ARTIFACT_CHANGED_AFTER_VERIFICATION`.

Service verifies content digest once more before durable publication. Missing/stale advertised files change terminal publication to failure and replace stale completion claims; remaining valid files can still be committed. Public artifact references remain opaque, while SQLite stores canonical workspace-relative file references.

## Limits

This does not trace arbitrary filesystem writes. Undeclared outputs from arbitrary scripts are not automatically discovered. A command that rewrites identical bytes without a managed write receipt does not prove a new file effect. The current window establishes changes to declared scopes during execution, not OS-level attribution under concurrent external writers. File verification and SQLite commit are not a cross-resource atomic transaction; a file can change after publication. These limits must not be described as universal effect capture.

Memory/session scope, summary provenance, prompt trust, global repository indexing, cancellation-time partial file convergence, and full checkpoint resume remain separate open work. No new Provider invocation, capability, hidden fallback or recovery loop is introduced.

## Offline evidence

`tests/test_ha_r3_file_evidence.py` covers actual versus logical paths, multi-file command output, stale existing files, nonzero commands with partial output, later-step failure after write, post-verification mutation, unauthorized declarations, full Service publication, publication-time mutation, copy/move destinations and retiring an earlier write after move. Files, subprocesses, SQLite and Service are real local operations; model decisions are supplied fixtures.
