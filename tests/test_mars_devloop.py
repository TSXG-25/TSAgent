"""MC-01 — Mars dev-loop failure fixtures driven through the real toolchain.

Each case materializes an isolated git fixture (baseline commit + a
pre-existing uncommitted user edit + an untracked file), then drives
``AgentLoop`` with scripted ``ChatTurn``s — a fake provider — while real
filesystem and workspace-scoped shell tools execute. The record contract
per fixture: acceptance met, human interventions, tool errors, retests,
elapsed, and the observed command results.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.cognition.effect_authorization import EffectAuthorization
from agent.cognition.execution_need import RequestedOutcome
from agent.goal import GoalState
from agent.llm import ChatTurn, ToolCall
from agent.loop.runner import AgentLoop
from agent.loop.toolset import build_toolset

PY = sys.executable


def _turn(*tool_calls, content="", prefix="c"):
    return ChatTurn(
        content=content,
        tool_calls=tuple(
            ToolCall(id=f"{prefix}-{index}", name=name, arguments=dict(arguments))
            for index, (name, arguments) in enumerate(tool_calls)
        ),
    )


def _orchestrator(workspace_root: Path):
    from agent.event_bus import EventBus
    from agent.executor.contract import executor_factory  # noqa: F401  (registry side effects)
    from agent.orchestrator.executor import ExecutionStage
    from agent.services.workspace_service import WorkspaceService

    workspace = WorkspaceService.scoped(
        workspace_root, event_bus=EventBus(), build_index=False,
    )
    orch = SimpleNamespace(
        _timings={},
        run_budget=None,
        run_context=SimpleNamespace(
            workspace=workspace,
            cancellation_view=None,
            event_bus=EventBus(),
            artifacts=SimpleNamespace(),
            repository_indexer=None,
        ),
    )
    orch._executor = ExecutionStage(orch)
    orch.verify_goal = lambda state, answer: SimpleNamespace(
        missing=list(state.get("_scripted_gaps", [])),
    )
    return orch


def _mutation_state(request: str, **extra):
    state = {
        "requested_outcomes": ["USER_VISIBLE_OUTPUT", "FILE_MUTATION"],
        "artifact_required": True,
        "goal_state": GoalState(objective=request, max_rounds=3).to_dict(),
        "inbox": {"next_step": [], "next_turn": []},
        "resolved_request": request,
    }
    state.update(extra)
    return state


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True,
    )


def _materialize(spec: dict, root: Path) -> Path:
    """Baseline commit, then the user-owned uncommitted edit and the
    untracked file — the loop must not touch either."""
    root.mkdir(parents=True)
    for rel, text in spec["files"].items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=devloop@test", "-c", "user.name=devloop",
         "add", "-A")
    _git(root, "-c", "user.email=devloop@test", "-c", "user.name=devloop",
         "commit", "-qm", "baseline")
    for rel, text in spec["user_edit"].items():
        (root / rel).write_text(text)
    for rel, text in spec["untracked"].items():
        (root / rel).write_text(text)
    return root


def _run_fixture(spec: dict, root: Path, monkeypatch) -> dict:
    orch = _orchestrator(root)
    calls = list(spec["turns"])
    assert calls, "fixture script must contain at least one turn"
    scripted = [_turn(*step, prefix=f"t{i}") for i, step in enumerate(calls)] + [
        _turn(content=spec["answer"])
    ]
    queue = list(scripted)
    call_names = {
        call.id: call.name
        for turn in scripted
        for call in turn.tool_calls
    }
    observations: list[dict] = []

    async def achat(messages, tools=None, **_kwargs):
        # Every tool result the model would see — the full loop, not just
        # the last step inbox still holds.
        seen = {o["call_id"] for o in observations}
        for message in messages:
            if message.get("role") != "tool":
                continue
            call_id = message.get("tool_call_id") or message.get("name") or ""
            if call_id in seen:
                continue
            output = str(message.get("content") or "")
            observations.append({
                "call_id": call_id,
                "tool": call_names.get(call_id, str(message.get("name") or "?")),
                "ok": not output.startswith(("ERROR[", "错误")),
                "tail": output[-240:],
            })
        assert queue, "scripted achat exhausted"
        return queue.pop(0)

    monkeypatch.setattr("agent.llm.llm.achat", achat)

    state = _mutation_state(spec["task"])
    started = time.monotonic()
    answer = asyncio.run(
        AgentLoop(orch).run(state, user_input=spec["task"], user_id="u")
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)

    # The same baseline → diff path the Service runs at terminal time.
    from agent.workspace_diff import diff_workspace, snapshot_workspace

    baseline = state.get("_workspace_baseline")
    assert isinstance(baseline, dict), (
        "mutation run must capture a workspace baseline before tools execute"
    )
    changes = diff_workspace(baseline, snapshot_workspace(root))

    record = {
        "fixture": spec["name"],
        "task": spec["task"],
        "answer": str(answer)[:160],
        "accepted": bool(spec["accept"](root)),
        "interventions": 0,
        "tool_errors": sum(1 for o in observations if not o["ok"]),
        "retests": sum(1 for o in observations if o["tool"] == "shell"),
        "elapsed_ms": elapsed_ms,
        "commands": [
            {"tool": o["tool"], "ok": o["ok"], "tail": o["tail"]}
            for o in observations
        ],
        "workspace_changes": changes,
    }
    print("\nDEVLOOP " + json.dumps(record, ensure_ascii=False))
    return record


def _acceptance_unchanged(spec: dict, root: Path) -> None:
    for rel, text in spec["user_edit"].items():
        assert (root / rel).read_text() == text, f"user edit clobbered: {rel}"
    for rel, text in spec["untracked"].items():
        assert (root / rel).read_text() == text, f"untracked file touched: {rel}"


def test_file_mutation_grant_exposes_shell_for_self_verification():
    """The authorization contract MC-02 builds on — visible here so the
    fixture runs fail loudly if the implied grant ever regresses."""
    authorization = EffectAuthorization(
        requested_outcomes=(
            RequestedOutcome.USER_VISIBLE_OUTPUT,
            RequestedOutcome.FILE_MUTATION,
        ),
        write_scopes=(".",),
    )
    assert "shell" in set(build_toolset(authorization))


FIX_BUG = {
    "name": "fix_bug",
    "task": (
        "calc.subtract 结果错误：subtract(3, 5) 应为 -2。"
        "修复实现并用 python -m pytest -q 验证。"
    ),
    "files": {
        "calc.py": (
            "def add(a, b):\n    return a + b\n\n\n"
            "def subtract(a, b):\n    return a + b\n\n\n"
            "def multiply(a, b):\n    return a * b\n"
        ),
        "test_calc.py": (
            "from calc import add, subtract, multiply\n\n\n"
            "def test_add():\n    assert add(2, 3) == 5\n\n\n"
            "def test_subtract():\n    assert subtract(3, 5) == -2\n\n\n"
            "def test_multiply():\n    assert multiply(2, 4) == 8\n"
        ),
        "README.md": "# calc fixture\n",
    },
    "user_edit": {"README.md": "# calc fixture\nuser: keep this uncommitted note\n"},
    "untracked": {"scratch.txt": "local scratch — never tracked\n"},
    "turns": [
        (("shell", {"cmd": f"{PY} -B -m pytest -q"}),),
        (("edit_file", {
            "path": "calc.py",
            "old_string": "def subtract(a, b):\n    return a + b",
            "new_string": "def subtract(a, b):\n    return a - b",
        }),),
        (("shell", {"cmd": f"{PY} -B -m pytest -q"}),),
    ],
    "answer": "已修复 subtract 并通过 pytest。",
    "accept": lambda root: "return a - b" in (root / "calc.py").read_text(),
}

SMALL_FEATURE = {
    "name": "small_feature",
    "task": (
        "给 greet.py 增加 shout(name)，返回问候的大写形式；"
        "先写测试再实现，并用 python -m pytest -q 验证。"
    ),
    "files": {
        "greet.py": 'def greet(name):\n    return f"Hello, {name}!"\n',
        "test_greet.py": (
            "from greet import greet\n\n\n"
            "def test_greet():\n    assert greet(\"Ada\") == \"Hello, Ada!\"\n"
        ),
        "NOTES.md": "# notes\n",
    },
    "user_edit": {"NOTES.md": "# notes\nuser: work in progress\n"},
    "untracked": {"draft.py": "# local draft\n"},
    "turns": [
        (("write_file", {
            "path": "test_greet.py",
            "content": (
                "from greet import greet, shout\n\n\n"
                "def test_greet():\n    assert greet(\"Ada\") == \"Hello, Ada!\"\n\n\n"
                "def test_shout():\n    assert shout(\"ada\") == \"HELLO, ADA!\"\n"
            ),
        }),),
        (("shell", {"cmd": f"{PY} -B -m pytest -q"}),),
        (("write_file", {
            "path": "greet.py",
            "content": "\n\ndef shout(name):\n    return greet(name).upper()\n",
            "mode": "append",
        }),),
        (("shell", {"cmd": f"{PY} -B -m pytest -q"}),),
    ],
    "answer": "新增 shout 并通过 pytest。",
    "accept": lambda root: "def shout" in (root / "greet.py").read_text(),
}

REFACTOR = {
    "name": "refactor",
    "task": (
        "把 util.trim 重命名为 util.normalize，更新全部引用，"
        "并用 python -m pytest -q 确认通过。"
    ),
    "files": {
        "util.py": (
            'def trim(s):\n    """Collapse surrounding whitespace."""\n'
            '    return " ".join(s.split())\n'
        ),
        "app.py": (
            "from util import trim\n\n\n"
            "def clean_title(s):\n    return trim(s).title()\n"
        ),
        "test_util.py": (
            "from util import trim\nfrom app import clean_title\n\n\n"
            "def test_trim():\n    assert trim(\"  a   b \") == \"a b\"\n\n\n"
            "def test_title():\n    assert clean_title(\"  hi there \") == \"Hi There\"\n"
        ),
        "CHANGELOG.md": "# changelog\n",
    },
    "user_edit": {"CHANGELOG.md": "# changelog\nuser: unreleased notes\n"},
    "untracked": {"local_run.sh": "python -m pytest -q\n"},
    "turns": [
        (("edit_file", {
            "path": "util.py",
            "old_string": "def trim(s):",
            "new_string": "def normalize(s):",
        }),),
        (("shell", {"cmd": f"{PY} -B -m pytest -q"}),),
        (
            ("edit_file", {
                "path": "app.py",
                "old_string": "from util import trim",
                "new_string": "from util import normalize",
            }),
            ("edit_file", {
                "path": "app.py",
                "old_string": "return trim(s).title()",
                "new_string": "return normalize(s).title()",
            }),
            ("edit_file", {
                "path": "test_util.py",
                "old_string": "from util import trim",
                "new_string": "from util import normalize",
            }),
            ("edit_file", {
                "path": "test_util.py",
                "old_string": 'assert trim("  a   b ") == "a b"',
                "new_string": 'assert normalize("  a   b ") == "a b"',
            }),
        ),
        (("shell", {"cmd": f"{PY} -B -m pytest -q"}),),
    ],
    "answer": "trim 已重命名为 normalize 并通过 pytest。",
    "accept": lambda root: (
        "def normalize" in (root / "util.py").read_text()
        and "trim" not in (root / "app.py").read_text()
    ),
}


@pytest.mark.parametrize("spec", [FIX_BUG, SMALL_FEATURE, REFACTOR],
                         ids=lambda s: s["name"])
def test_devloop_fail_then_pass(spec, tmp_path, monkeypatch):
    root = _materialize(spec, tmp_path / spec["name"])
    record = _run_fixture(spec, root, monkeypatch)

    # The closed loop: a real failing command fed back, a real fix, a real
    # re-run passing — then the acceptance check on the actual file.
    shell_runs = [
        index for index, command in enumerate(record["commands"])
        if command["tool"] == "shell"
    ]
    assert len(shell_runs) >= 2, "expected an initial run and a retest"
    first, last = record["commands"][shell_runs[0]], record["commands"][shell_runs[-1]]
    assert not first["ok"], "initial verification run must fail"
    assert last["ok"], "final retest must pass"
    evidence = first["tail"].lower()
    assert "failed" in evidence or "error" in evidence or "assert" in evidence, (
        "model must receive the real failure output, not a bare exit code"
    )
    assert any(
        command["tool"] in {"write_file", "edit_file"} and command["ok"]
        for command in record["commands"][shell_runs[0] : shell_runs[-1] + 1]
    ), "no successful mutation between the failing run and the retest"
    assert record["answer"], "loop must end with a model answer, not silence"
    assert record["accepted"], record["task"]

    # Attribution: agent edits are attributable, the pre-existing user edit
    # and untracked file are untouched, and nothing is silently clobbered.
    changes = record["workspace_changes"]
    assert changes["agent_files"], "run must attribute its own edits"
    assert not changes["ambiguous_files"], (
        "fixture never touches user-dirty files — anything here means the "
        "attribution boundary regressed"
    )
    assert set(changes["user_dirty_preserved"]) == {
        *spec["user_edit"], *spec["untracked"]
    }, changes
    assert all(
        path in changes["agent_files"]
        for path in (*changes["added"], *changes["modified"], *changes["deleted"])
        if not path.startswith(".tsagent/")
    ), changes
    _acceptance_unchanged(spec, root)
    report = tmp_path / spec["name"] / "devloop-report.json"
    report.write_text(json.dumps(record, ensure_ascii=False, indent=2))


def test_verification_demand_repairs_missing_execution_declaration():
    """Real-eval R3: the intent classifier dropped the explicit
    "并用 python -m pytest -q 确认通过" requirement, so the completion gate
    had no execution outcome to enforce and an unverified claim completed.
    The repair restores the declared outcome deterministically."""
    authorization = EffectAuthorization.from_state(
        {
            "artifact_required": True,
            "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
            "intent_write_targets": ["util.py"],
            "goal_state": {},
        },
        "把 util.trim 重命名为 util.normalize，更新全部引用，"
        "并用 python -m pytest -q 确认通过。",
    )
    assert RequestedOutcome.COMMAND_EXECUTION in authorization.requested_outcomes


def test_verification_demand_respects_prohibited_execution():
    authorization = EffectAuthorization.from_state(
        {
            "artifact_required": True,
            "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
            "goal_state": {"prohibited_effects": ["COMMAND_EXECUTION"]},
        },
        "修改代码并用 npm test 验证，但不要运行任何命令",
    )
    assert RequestedOutcome.COMMAND_EXECUTION not in authorization.requested_outcomes


def test_verification_demand_never_injects_execution_into_pure_chat():
    """Precision guard: mentioning a tool name in a question must not turn a
    read-only request into an execution-requiring run."""
    authorization = EffectAuthorization.from_state(
        {"requested_outcomes": ["USER_VISIBLE_OUTPUT"], "goal_state": {}},
        "解释一下 pytest 和 unittest 的区别",
    )
    assert RequestedOutcome.COMMAND_EXECUTION not in authorization.requested_outcomes


def test_verification_demand_does_not_shadow_a_declared_code_execution():
    """The classifier declared CODE_EXECUTION — keep its choice; the gate
    accepts either execution evidence for either declared outcome."""
    authorization = EffectAuthorization.from_state(
        {
            "requested_outcomes": ["USER_VISIBLE_OUTPUT", "CODE_EXECUTION"],
            "goal_state": {},
        },
        "跑一下 python -m pytest -q",
    )
    assert RequestedOutcome.CODE_EXECUTION in authorization.requested_outcomes
    assert RequestedOutcome.COMMAND_EXECUTION not in authorization.requested_outcomes


def test_verification_demand_ignores_tool_mentions_without_run_intent():
    """"改一下 pytest 配置" mentions pytest but demands no execution — the
    bare-name-with-args pattern must not fire on prose around it."""
    authorization = EffectAuthorization.from_state(
        {
            "artifact_required": True,
            "requested_outcomes": ["FILE_MUTATION"],
            "intent_write_targets": ["pytest.ini"],
            "goal_state": {},
        },
        "改一下 pytest 配置里的超时参数",
    )
    assert RequestedOutcome.COMMAND_EXECUTION not in authorization.requested_outcomes


def test_verification_demand_respects_negated_clauses():
    """"不要运行程序" contains a run verb + target — the demand detector must
    read the clause, not the tokens (test_ha_r4_gate_a's Bad Apple case)."""
    authorization = EffectAuthorization.from_state(
        {
            "artifact_required": True,
            "requested_outcomes": ["FILE_MUTATION"],
            "intent_write_targets": ["output/"],
            "goal_state": {"prohibited_effects": ["CODE_EXECUTION"]},
        },
        "做一个 Bad Apple 本地播放器，交付程序和使用说明；"
        "素材缺失就说明缺口，不要下载素材，也不要运行程序。",
    )
    assert RequestedOutcome.COMMAND_EXECUTION not in authorization.requested_outcomes


def test_verification_demand_survives_earlier_negated_clause():
    """"不要提交；然后用 pytest 验证" — the negation lives in the earlier
    clause, the demand in the later clause still counts."""
    authorization = EffectAuthorization.from_state(
        {
            "artifact_required": True,
            "requested_outcomes": ["FILE_MUTATION"],
            "intent_write_targets": ["."],
            "goal_state": {},
        },
        "修完先不要 git 提交；然后用 python -m pytest -q 验证。",
    )
    assert RequestedOutcome.COMMAND_EXECUTION in authorization.requested_outcomes
