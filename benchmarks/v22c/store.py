"""JsonCheckpointStore — 跨进程持久化的 CheckpointStore（v2.2C C02）。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from agent.checkpoint import RunCheckpoint, checkpoint_digest


class JsonCheckpointStore:
    """把 (run_id, workflow_id) 的 checkpoint 链持久化到单个 JSON 文件。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._checkpoints: dict[str, RunCheckpoint] = {}
        self._run_history: dict[str, list[str]] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._checkpoints = {
            cid: RunCheckpoint.from_dict(cp)
            for cid, cp in data.get("checkpoints", {}).items()
        }
        self._run_history = {
            run_id: list(ids) for run_id, ids in data.get("history", {}).items()
        }

    def _flush(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "checkpoints": {
                        cid: cp.to_dict() for cid, cp in self._checkpoints.items()
                    },
                    "history": {rid: list(v) for rid, v in self._run_history.items()},
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def save(self, checkpoint: RunCheckpoint) -> RunCheckpoint:
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        self._run_history.setdefault(checkpoint.run_id, []).append(checkpoint.checkpoint_id)
        self._flush()
        return checkpoint

    def get(self, checkpoint_id: str) -> Optional[RunCheckpoint]:
        return self._checkpoints.get(checkpoint_id)

    def latest(self, run_id: str) -> Optional[RunCheckpoint]:
        ids = self._run_history.get(run_id, [])
        if not ids:
            return None
        return self._checkpoints.get(ids[-1])

    def latest_for_workflow(
        self,
        run_id: str,
        workflow_id: str,
        *,
        activation_attempt_id: str = "",
    ) -> Optional[RunCheckpoint]:
        for checkpoint_id in reversed(self._run_history.get(run_id, [])):
            checkpoint = self._checkpoints.get(checkpoint_id)
            if checkpoint is None or checkpoint.workflow_id != workflow_id:
                continue
            if (
                not activation_attempt_id
                or checkpoint.activation_attempt_id == activation_attempt_id
            ):
                return checkpoint
        return None

    def history(self, run_id: str) -> tuple[RunCheckpoint, ...]:
        return tuple(
            self._checkpoints[cid]
            for cid in self._run_history.get(run_id, [])
            if cid in self._checkpoints
        )
