"""In-run transcript compaction: graduated pruning + spill offload."""

from __future__ import annotations

from pathlib import Path

from agent.llm import ToolCall
from agent.loop.runner import (
    COMPACT_KEEP_TAIL,
    TOOL_RESULT_CHAR_CAP,
    TRANSCRIPT_CHAR_BUDGET,
    _compact_transcript,
    _tool_message,
)


class _Workspace:
    """Minimal scoped-workspace stub backed by a tmp dir."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve_path(self, rel: str) -> Path:
        return self._root / rel


class _Events:
    def __init__(self) -> None:
        self.seen: list[tuple[object, dict]] = []

    def emit(self, event_type, data) -> int:
        self.seen.append((event_type, data))
        return 1


def _tool(index: int, size: int) -> dict:
    return {
        "role": "tool",
        "tool_call_id": f"call-{index}",
        "name": "shell",
        "content": "x" * size,
    }


def _transcript(tool_sizes: list[int]) -> list[dict]:
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "req"},
        *[_tool(i, size) for i, size in enumerate(tool_sizes)],
    ]


def test_under_budget_is_untouched(tmp_path):
    messages = _transcript([1000, 1000])
    before = [dict(m) for m in messages]
    _compact_transcript(messages, workspace=_Workspace(tmp_path))
    assert messages == before


def test_prunes_only_oldest_until_budget(tmp_path):
    # 12 x 5000 = 60k chars: exceeds the 48k budget; the keep-tail four
    # are never prunable.
    messages = _transcript([5000] * 12)
    _compact_transcript(messages, workspace=_Workspace(tmp_path))

    tool_msgs = [m for m in messages if m["role"] == "tool"]
    tail = tool_msgs[-COMPACT_KEEP_TAIL:]
    assert all(not m["content"].startswith("[已压缩]") for m in tail)
    pruned = [m for m in tool_msgs[:-COMPACT_KEEP_TAIL] if m["content"].startswith("[已压缩]")]
    intact = [m for m in tool_msgs[:-COMPACT_KEEP_TAIL] if not m["content"].startswith("[已压缩]")]
    # Graduated: stops as soon as the transcript fits — not all 8 prunable.
    assert 0 < len(pruned) < 8 and intact
    total = sum(len(str(m.get("content", ""))) for m in messages)
    assert total <= TRANSCRIPT_CHAR_BUDGET


def test_spill_preserves_full_content(tmp_path):
    messages = _transcript([9000] * 10)
    ws = _Workspace(tmp_path)
    _compact_transcript(messages, workspace=ws)

    spilled_files = list((tmp_path / ".tsagent" / "spill").glob("*.txt"))
    assert spilled_files
    original = 9000
    for m in messages:
        if m.get("role") == "tool" and m["content"].startswith("[已压缩]"):
            assert "外置保存" in m["content"]
            spill = tmp_path / m["content"].split("保存 ")[1].rstrip("）")
            assert spill.read_text(encoding="utf-8") == "x" * original
            break
    else:
        raise AssertionError("expected at least one compacted tool message")


def test_without_workspace_falls_back_to_squash():
    messages = _transcript([9000] * 10)
    _compact_transcript(messages, workspace=None)
    compacted = [
        m for m in messages
        if m.get("role") == "tool" and m["content"].startswith("[已压缩]")
    ]
    assert compacted
    assert all("外置保存" not in m["content"] for m in compacted)


def test_compaction_emits_event(tmp_path):
    events = _Events()
    messages = _transcript([9000] * 10)
    _compact_transcript(
        messages, workspace=_Workspace(tmp_path), event_bus=events
    )
    assert events.seen
    event_type, data = events.seen[0]
    assert event_type == "transcript_compacted"
    assert data["pruned"] > 0 and data["spilled"] == data["pruned"]


def test_oversized_tool_result_spills_before_cap(tmp_path):
    call = ToolCall(id="call-big", name="shell", arguments={})
    big = "y" * (TOOL_RESULT_CHAR_CAP + 5000)
    message = _tool_message(call, big, workspace=_Workspace(tmp_path))

    assert len(message["content"]) <= TOOL_RESULT_CHAR_CAP + 120
    assert "外置保存" in message["content"]
    spill = tmp_path / ".tsagent" / "spill" / "call-big.txt"
    assert spill.read_text(encoding="utf-8") == big


def test_oversized_tool_result_without_workspace():
    call = ToolCall(id="call-big", name="shell", arguments={})
    big = "y" * (TOOL_RESULT_CHAR_CAP + 5000)
    message = _tool_message(call, big, workspace=None)
    assert len(message["content"]) <= TOOL_RESULT_CHAR_CAP + 60
    assert "外置保存" not in message["content"]
