"""Resolution Memory（Layer 4：跨会话解析事实）。

Memory 保存"已经发生过的事实"（用户打开过 X / 讨论过 Y / 修改过 Z），
**不保存 ResolutionResult**（那是 Runtime 内部对象，Memory 不依赖 Runtime Contract）。

    ResolutionMemory:
        timestamp / utterance / resolved_target / kind / metadata

Converter 在 Resolver 层：resolve_memory() 负责 ResolutionMemory → ResolutionCandidate。
"""
import datetime
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
RM_DIR = DATA_DIR / "resolution_memory"
RM_DIR.mkdir(parents=True, exist_ok=True)

MAX_ENTRIES = 100


@dataclass
class ResolutionMemory:
    """跨会话解析事实（Facts 层，非 Runtime 对象）。"""
    timestamp: str
    utterance: str
    resolved_target: str
    kind: str                # "file" | "symbol" | "topic" | ...
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "utterance": self.utterance,
            "resolved_target": self.resolved_target,
            "kind": self.kind,
            "metadata": self.metadata,
        }


def _path(user_id: str) -> Path:
    return RM_DIR / f"{user_id}.json"


def _load(user_id: str) -> list:
    p = _path(user_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _persist_resolution(
    user_id: str,
    utterance: str,
    resolved_target: str,
    kind: str,
    *,
    scope: str,
    canonical_key: str,
    evidence_id: str,
    source_kind: str,
    source_ref: str,
    metadata: dict | None = None,
) -> dict[str, int | str]:
    """Persist one authorized resolution and return its storage receipt."""
    if not utterance.strip() or not resolved_target.strip() or not kind.strip():
        raise ValueError("resolution persistence requires utterance, target, and kind")
    record_id = "resolution-" + hashlib.sha256(
        f"{scope}\0{user_id}\0{canonical_key}\0{evidence_id}".encode("utf-8")
    ).hexdigest()
    entries = _load(user_id)
    if any(str(entry.get("record_id", "")) == record_id for entry in entries):
        return {"record_id": record_id, "revision": len(entries)}
    for entry in entries:
        if (
            entry.get("scope") == scope
            and entry.get("canonical_key") == canonical_key
            and entry.get("resolved_target") == resolved_target
            and entry.get("kind") == kind
        ):
            existing_record_id = str(entry.get("record_id", "")).strip()
            if not existing_record_id:
                existing_evidence_id = str(entry.get("evidence_id", evidence_id))
                existing_record_id = "resolution-" + hashlib.sha256(
                    f"{scope}\0{user_id}\0{canonical_key}\0{existing_evidence_id}".encode("utf-8")
                ).hexdigest()
            return {"record_id": existing_record_id, "revision": len(entries)}
    entry_metadata = dict(metadata or {})
    entry_metadata.update({
        "scope": scope,
        "canonical_key": canonical_key,
        "evidence_id": evidence_id,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "record_id": record_id,
    })
    entries.append({
        "timestamp": datetime.datetime.now().isoformat(),
        "utterance": utterance,
        "resolved_target": resolved_target,
        "kind": kind,
        "metadata": entry_metadata,
        "record_id": record_id,
        "scope": scope,
        "canonical_key": canonical_key,
        "evidence_id": evidence_id,
        "source_kind": source_kind,
        "source_ref": source_ref,
    })
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]
    _path(user_id).write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"record_id": record_id, "revision": len(entries)}


def get_resolutions(
    user_id: str,
    n: int = 20,
    *,
    scope: str | None = None,
) -> list:
    """最近 N 条跨会话解析事实（旧 → 新）。"""
    entries = _load(user_id)
    if scope is not None:
        entries = [entry for entry in entries if entry.get("scope") == scope]
    return entries[-n:]


def clear_resolutions(user_id: str) -> None:
    """删除一个 user/session namespace 的解析记忆。"""
    try:
        _path(user_id).unlink(missing_ok=True)
    except OSError:
        pass
