"""Resolution Memory（Layer 4：跨会话解析事实）。

Memory 保存"已经发生过的事实"（用户打开过 X / 讨论过 Y / 修改过 Z），
**不保存 ResolutionResult**（那是 Runtime 内部对象，Memory 不依赖 Runtime Contract）。

    ResolutionMemory:
        timestamp / utterance / resolved_target / kind / metadata

Converter 在 Resolver 层：resolve_memory() 负责 ResolutionMemory → ResolutionCandidate。
"""
import datetime
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


def record_resolution(
    user_id: str,
    utterance: str,
    resolved_target: str,
    kind: str,
    metadata: dict | None = None,
) -> None:
    """记录一条跨会话解析事实（Runtime 在每次解析后调用）。"""
    entries = _load(user_id)
    entries.append({
        "timestamp": datetime.datetime.now().isoformat(),
        "utterance": utterance,
        "resolved_target": resolved_target,
        "kind": kind,
        "metadata": metadata or {},
    })
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]
    _path(user_id).write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_resolutions(user_id: str, n: int = 20) -> list:
    """最近 N 条跨会话解析事实（旧 → 新）。"""
    return _load(user_id)[-n:]


def clear_resolutions(user_id: str) -> None:
    """删除一个 user/session namespace 的解析记忆。"""
    try:
        _path(user_id).unlink(missing_ok=True)
    except OSError:
        pass
