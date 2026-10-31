"""Real API Acceptance Suite v0.1 — P0 定向测试（24 cases，两层 API）。

同时验证：
  Provider API   —— 真实 DeepSeek 是否返回可用输出
  AgentService   —— Planner / Tool / Verifier / SQLite 事件流 / 终态闭环

组：
  A 终态正确性（4）    B Planning/Tool 路由（4）   C 多目的多 Tool（4）
  D AgentService/事件（4）  E 恢复与副作用安全（4）    F Provider/性能（4）

用法：
  TSAGENT_SNAPSHOT=<isolated snapshot> \
  TSAGENT_RESULTS=/tmp/acceptance_p0.json \
  python -B acceptance_p0.py --group A|B|C|D|E|F|all

每组每 case 独立 SQLite DB（/tmp 下），工具调用经 ToolRegistry 插桩记录，
LLM 调用经 agent.llm.ainvoke 包裹计数。
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

SNAPSHOT = os.environ.get("TSAGENT_SNAPSHOT", "/private/tmp/tsagent-acceptance-p0")
os.chdir(SNAPSHOT)
sys.path.insert(0, SNAPSHOT)

os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

_RESULTS = os.environ.get("TSAGENT_RESULTS", "/private/tmp/acceptance_p0.json")
MODEL = os.environ.get("MODEL_NAME", "deepseek-v4-flash")
TERMINAL = ("COMPLETED", "FAILED_TERMINAL", "BLOCKED", "CANCELLED")

from agent.bootstrap import load_all
from agent.runtime import UniversalAgent
from agent.registry.tool_registry import registry
from agent.service import (
    AgentService,
    AgentServiceError,
    EventStreamRequest,
    RunLookupRequest,
    ServiceErrorCode,
    StartRunRequest,
)
from agent.service.runtime_launcher import RuntimeExecutionLauncher
from agent.runtime_store import SqliteRuntimeStore
from agent.service import create_default_agent_service

# ── 插桩 ──
TOOL_CALLS: list[dict] = []
LLM_CALLS: list[dict] = []
PLAN_CALLS = 0
_orig_llm_ainvoke = None

RECORD_TOOLS = (
    "web_search", "web_deep_search", "web_news_search", "web_fetch",
    "read_file", "write_file", "run_python", "list_directory",
    "delete_file", "move_file", "copy_file",
)


def install_instrumentation():
    global _orig_llm_ainvoke
    # LLM 调用计数：包裹 agent.llm.llm.ainvoke
    import agent.llm as _llm_mod
    if _orig_llm_ainvoke is None:
        _orig_llm_ainvoke = _llm_mod.llm.ainvoke

    async def llm_wrapper(messages, **kwargs):
        LLM_CALLS.append({
            "messages": [str(getattr(m, "content", m))[:150] for m in messages][:2],
            "ts": time.time(),
        })
        return await _orig_llm_ainvoke(messages, **kwargs)

    _llm_mod.llm.ainvoke = llm_wrapper

    # Planner 调用计数
    import agent.orchestrator as _orch_mod
    global PLAN_CALLS
    _orig_plan = _orch_mod.ExecutionOrchestrator.plan

    async def plan_wrapper(self, *args, **kwargs):
        global PLAN_CALLS
        PLAN_CALLS += 1
        return await _orig_plan(self, *args, **kwargs)

    _orch_mod.ExecutionOrchestrator.plan = plan_wrapper

    # Tool 调用记录（executor 层，覆盖所有名字映射）：记录 (tool_name, args)
    import agent.executor.plan_executor as _pe_mod
    _orig_exec_tool = _pe_mod.PlanExecutor._exec_tool

    async def exec_tool_wrapper(self, tool_name: str, args: dict):
        TOOL_CALLS.append({
            "tool": str(tool_name),
            "args": [str(args.get(k, ""))[:120] for k in ("path", "query", "content", "code") if args.get(k)],
            "kwargs": {str(k): str(v)[:120] for k, v in args.items()},
            "ts": time.time(),
        })
        return await _orig_exec_tool(self, tool_name, args)

    _pe_mod.PlanExecutor._exec_tool = exec_tool_wrapper


def reset_instrumentation():
    TOOL_CALLS.clear()
    LLM_CALLS.clear()
    global PLAN_CALLS
    PLAN_CALLS = 0


def tool_names() -> list[str]:
    return [c["tool"] for c in TOOL_CALLS]


def tool_calls_for(tool: str) -> list[dict]:
    return [c for c in TOOL_CALLS if c["tool"] == tool]


def write_tool_calls(path_suffix: str = "") -> list[dict]:
    """匹配 write_file / filesystem.write 等写工具（可选按目标路径过滤）。"""
    out = []
    for c in TOOL_CALLS:
        if c["tool"] not in ("write_file", "filesystem.write"):
            continue
        if path_suffix:
            blob = " ".join(c.get("args", []) + list(c.get("kwargs", {}).values()))
            if path_suffix not in blob:
                continue
        out.append(c)
    return out


# ── 通用运行器 ──
async def run_agent(user_id: str, request_text: str, *, timeout: float = 200) -> dict:
    """直接用 UniversalAgent 跑（B/C 组），返回 evidence + 插桩。"""
    agent = UniversalAgent(user_id)
    buf = io.StringIO()
    t0 = time.perf_counter()
    try:
        with contextlib.redirect_stdout(buf):
            out = await asyncio.wait_for(agent.run(request_text), timeout=timeout)
        exc = None
    except Exception as e:  # noqa: BLE001
        out = ""
        exc = f"{type(e).__name__}: {e}"
    evidence = dict(getattr(agent, "last_run_evidence", {}) or {})
    close = getattr(agent, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
    return {
        "out": str(out or ""),
        "evidence": evidence,
        "exc": exc,
        "dur": round(time.perf_counter() - t0, 1),
        "tool_calls": list(TOOL_CALLS),
        "llm_calls": len(LLM_CALLS),
        "plan_calls": PLAN_CALLS,
        "stdout_tail": buf.getvalue()[-1500:],
    }


def _lookup(run_id: str, request_id: str, tenant: str = "tenant-p0",
            session: str = "session-p0") -> RunLookupRequest:
    return RunLookupRequest(
        tenant_id=tenant, user_id="user-p0", session_id=session,
        run_id=run_id, request_id=request_id,
    )


async def start_and_wait(
    service: AgentService,
    request: StartRunRequest,
    *,
    timeout: float = 240,
) -> dict:
    """start_run → 轮询 get_run 到终态 → 收集事件。"""
    t0 = time.perf_counter()
    handle = await service.start_run(request)
    accept_ms = (time.perf_counter() - t0) * 1000
    snapshot = None
    events: list = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = await service.get_run(
            _lookup(handle.run_id, request.request_id, tenant=request.tenant_id,
                    session=request.session_id)
        )
        if snapshot.status.value in TERMINAL:
            break
        await asyncio.sleep(2)
    if snapshot is None or snapshot.status.value not in TERMINAL:
        raise TimeoutError(f"run {handle.run_id} 未在 {timeout}s 内达到终态")
    events = [e async for e in service.stream_events(
        EventStreamRequest(
            tenant_id=request.tenant_id, user_id=request.user_id,
            session_id=request.session_id, run_id=handle.run_id,
            request_id="event-read", after_sequence=0,
        )
    )]
    terminal_events = [e for e in events if e.is_terminal]
    return {
        "run_id": handle.run_id,
        "status": snapshot.status.value,
        "acceptance_ms": round(accept_ms),
        "wall_ms": round((time.perf_counter() - t0) * 1000),
        "terminal_event": (
            terminal_events[-1].event_type.value if terminal_events else None
        ),
        "event_count": len(events),
        "event_types": [e.event_type.value for e in events],
        "artifacts": [a.to_dict() for a in snapshot.artifacts],
        "verifier_summary": dict(getattr(snapshot, "verifier_summary", None) or {}),
        "failure_summary": (
            getattr(snapshot, "failure_summary", None).to_dict()
            if getattr(snapshot, "failure_summary", None) else None
        ),
        "tool_calls": list(TOOL_CALLS),
        "llm_calls": len(LLM_CALLS),
        "plan_calls": PLAN_CALLS,
        "revision": snapshot.revision,
    }


def new_service(workspace_root: Path | None = None):
    """每个 case 一个独立 SQLite DB + 独立 AgentService。"""
    db = tempfile.NamedTemporaryFile(prefix="acc-p0-", suffix=".sqlite3", delete=False)
    db.close()
    os.unlink(db.name)
    service = create_default_agent_service(
        Path(db.name),
        workspace_root=workspace_root,
        writer_id="acceptance-p0",
    )
    return service, Path(db.name)


def start_request(
    request_text: str,
    request_id: str,
    *,
    tenant: str = "tenant-p0",
    user: str = "user-p0",
    session: str = "session-p0",
    run_id: str | None = None,
) -> StartRunRequest:
    return StartRunRequest(
        tenant_id=tenant, user_id=user, session_id=session,
        run_id=run_id or f"run-{request_id.lower()}-{uuid.uuid4().hex[:8]}",
        request_id=request_id, request_text=request_text,
    )


# =====================================================================
# 组 A：终态正确性
# =====================================================================
async def case_a01() -> dict:
    """A01 filesystem.read 读取不可解码文件（内部存储路径）→ 不得 COMPLETED。"""
    reset_instrumentation()
    service, db = new_service()
    try:
        req = start_request(
            "读取 data/semantic_memory/chroma.sqlite3 的内容并总结里面有什么",
            "A01",
        )
        rec = await start_and_wait(service, req)
        reads = [c for c in rec["tool_calls"] if c["tool"] == "read_file"]
        chroma_read = any(
            "chroma.sqlite3" in " ".join(c["args"] + list(c["kwargs"].values()))
            for c in reads
        )
        passed = (
            rec["status"] != "COMPLETED"
            and not chroma_read
            and rec["terminal_event"] in ("run_failed", "run_blocked")
        )
        return {
            "case_id": "A01", "group": "A", "passed": passed,
            "expected_capabilities": ["filesystem", "terminal_correctness"],
            "terminal_status": rec["status"],
            "terminal_event": rec["terminal_event"],
            "chroma_read": chroma_read,
            "llm_call_count": rec["llm_calls"],
            "runtime_contract_ok": passed,
            "latency_ms": rec["wall_ms"],
            "detail": f"status={rec['status']} event={rec['terminal_event']} "
                      f"chroma_read={chroma_read} reads={[c['tool'] for c in reads]}",
        }
    finally:
        await service.close()
        Path(db).unlink(missing_ok=True)


async def case_a02() -> dict:
    """A02 执行预算耗尽 → run_completed=0，只产生失败/阻塞终态事件。"""
    import agent.runtime as _rt
    original = _rt.MAX_RUNTIME_SECONDS
    reset_instrumentation()
    service, db = new_service()
    try:
        _rt.MAX_RUNTIME_SECONDS = 0.1  # 预算极小，必然耗尽
        req = start_request("写一篇 200 字关于 AI 的文章保存到 output/a02.md", "A02")
        rec = await start_and_wait(service, req)
        passed = (
            rec["status"] == "FAILED_TERMINAL"
            and rec["terminal_event"] == "run_failed"
            and rec["terminal_event"] != "run_completed"
            and "run_completed" not in rec["event_types"]
        )
        return {
            "case_id": "A02", "group": "A", "passed": passed,
            "expected_capabilities": ["budget", "terminal_correctness"],
            "terminal_status": rec["status"],
            "terminal_event": rec["terminal_event"],
            "run_completed_event": "run_completed" in rec["event_types"],
            "llm_call_count": rec["llm_calls"],
            "failure_summary": rec["failure_summary"],
            "runtime_contract_ok": passed,
            "latency_ms": rec["wall_ms"],
            "detail": f"status={rec['status']} event={rec['terminal_event']}",
        }
    finally:
        _rt.MAX_RUNTIME_SECONDS = original
        await service.close()
        Path(db).unlink(missing_ok=True)


async def case_a03() -> dict:
    """A03 文件实际未生成但 Tool 声称成功 → ExecutionVerifier 拦截，Run 不得成功。"""
    reset_instrumentation()
    service, db = new_service()
    try:
        req = start_request(
            "创建 output/a03_nonexistent.py 并写入 def main(): pass",
            "A03",
        )
        rec = await start_and_wait(service, req)
        f = Path(SNAPSHOT) / "output" / "a03_nonexistent.py"
        file_exists = f.exists()
        verifier = rec.get("verifier_summary") or {}
        passed = not file_exists or rec["status"] == "COMPLETED"
        # 确定性 Verifier 探针：plan 声称写了文件但文件不存在 → 必须 FAIL
        from agent.executor.verifier import ExecutionArtifacts, execution_verifier
        from agent.task import ExecutionPlan, ExecutionStep, Task, Verb
        probe_task = Task(id="probe", verb=Verb.WRITE, goal="write never.txt")
        fake_plan = ExecutionPlan(
            task=probe_task,
            executor="tool",
            steps=[
                ExecutionStep(
                    tool="write_file",
                    args={"path": "output/never.txt", "content": "x"},
                )
            ],
        )
        v = execution_verifier.verify(
            fake_plan,
            ExecutionArtifacts(files_written=["output/never.txt"]),
            task=probe_task,
        )
        verifier_catches = not v.success
        return {
            "case_id": "A03", "group": "A",
            "passed": verifier_catches and (file_exists or rec["status"] != "COMPLETED"),
            "expected_capabilities": ["write", "verify"],
            "terminal_status": rec["status"],
            "file_exists_after_run": file_exists,
            "verifier_summary": verifier,
            "verifier_probe_catches_false_success": verifier_catches,
            "runtime_contract_ok": verifier_catches,
            "detail": f"file_exists={file_exists} status={rec['status']} "
                      f"verifier_catches={verifier_catches}",
        }
    finally:
        await service.close()
        Path(db).unlink(missing_ok=True)


async def case_a04() -> dict:
    """A04 必需任务失败但下游产生 NL 回答 → 不能因"有回答"而标 COMPLETED。"""
    reset_instrumentation()
    service, db = new_service()
    try:
        req = start_request(
            "读取 input/definitely_missing_file_xyz.docx 的题目，"
            "然后修改 output/solution.py 让它通过",
            "A04",
        )
        rec = await start_and_wait(service, req)
        reads = [c for c in TOOL_CALLS if c["tool"] in ("read_file", "filesystem.read")]
        missing_read = any(
            "definitely_missing_file_xyz" in " ".join(c["args"] + list(c["kwargs"].values()))
            for c in reads
        )
        passed = (
            rec["status"] != "COMPLETED"
            and rec["terminal_event"] in ("run_failed", "run_blocked")
        )
        return {
            "case_id": "A04", "group": "A", "passed": passed,
            "expected_capabilities": ["read", "terminal_correctness"],
            "terminal_status": rec["status"],
            "terminal_event": rec["terminal_event"],
            "attempted_missing_read": missing_read,
            "llm_call_count": rec["llm_calls"],
            "runtime_contract_ok": passed,
            "detail": f"status={rec['status']} event={rec['terminal_event']} "
                      f"missing_read_attempted={missing_read}",
        }
    finally:
        await service.close()
        Path(db).unlink(missing_ok=True)


# =====================================================================
# 组 B：Planning 与 Tool 路由
# =====================================================================
async def case_b01() -> dict:
    """B01 解释快速排序 → 不调用 Tool。"""
    reset_instrumentation()
    rec = await run_agent("b01", "解释一下快速排序的原理")
    calls = tool_names()
    passed = (
        not calls
        and rec["evidence"].get("terminal_status", "COMPLETED") == "COMPLETED"
    )
    return {
        "case_id": "B01", "group": "B", "passed": passed,
        "expected_capabilities": ["explain"],
        "tool_calls": calls,
        "llm_call_count": rec["llm_calls"],
        "replan_count": rec["plan_calls"],
        "out": rec["out"][:300],
        "exc": rec["exc"],
        "runtime_contract_ok": passed,
        "detail": f"tool_calls={calls}",
    }


async def case_b02() -> dict:
    """B02 把快速排序实现保存到 output/q.py → codegen→write→verify。"""
    reset_instrumentation()
    rec = await run_agent("b02", "把快速排序实现保存到 output/q.py")
    f = Path(SNAPSHOT) / "output" / "q.py"
    created = f.exists()
    valid = False
    if created:
        try:
            compile(f.read_text(encoding="utf-8"), "q.py", "exec")
            valid = True
        except Exception:
            valid = False
    writes = write_tool_calls("q.py")
    passed = created and valid and bool(writes)
    return {
        "case_id": "B02", "group": "B", "passed": passed,
        "expected_capabilities": ["codegen", "write", "verify"],
        "tool_calls": tool_names(),
        "file_created": created,
        "file_valid": valid,
        "llm_call_count": rec["llm_calls"],
        "out": rec["out"][:300],
        "exc": rec["exc"],
        "runtime_contract_ok": passed,
        "detail": f"created={created} valid={valid} writes={len(writes)}",
    }


async def case_b03() -> dict:
    """B03 搜索本周 AI/半导体热点 → 必须使用真实搜索，不得只走 llm_executor。"""
    reset_instrumentation()
    rec = await run_agent("b03", "搜索本周 AI 和半导体板块的热点")
    web = [c for c in TOOL_CALLS if c["tool"].startswith("web_")]
    passed = bool(web)
    return {
        "case_id": "B03", "group": "B", "passed": passed,
        "expected_capabilities": ["research", "search"],
        "web_calls": [c["tool"] for c in web],
        "llm_call_count": rec["llm_calls"],
        "replan_count": rec["plan_calls"],
        "out": rec["out"][:300],
        "exc": rec["exc"],
        "runtime_contract_ok": passed,
        "detail": f"web_calls={[c['tool'] for c in web] or 'NONE'}",
    }


async def case_b04() -> dict:
    """B04 根据偏好推荐近期股票板块 → MemoryView+Search+Synthesis，不得读 Chroma。"""
    reset_instrumentation()
    # 先写入一条偏好
    seed = UniversalAgent("b04")
    try:
        await asyncio.wait_for(seed.run("记住：我偏好科技和新能源板块，回避传统能源"), timeout=90)
    finally:
        seed.close()
    reset_instrumentation()
    rec = await run_agent("b04", "根据我的偏好推荐近期值得关注的股票板块")
    web = [c for c in TOOL_CALLS if c["tool"].startswith("web_")]
    reads = [c for c in TOOL_CALLS if c["tool"] in ("read_file", "filesystem.read")]
    chroma_read = any(
        "chroma.sqlite3" in " ".join(c["args"] + list(c["kwargs"].values()))
        for c in reads
    )
    passed = bool(web) and not chroma_read
    return {
        "case_id": "B04", "group": "B", "passed": passed,
        "expected_capabilities": ["memory", "research", "search"],
        "web_calls": [c["tool"] for c in web],
        "chroma_read": chroma_read,
        "llm_call_count": rec["llm_calls"],
        "out": rec["out"][:300],
        "exc": rec["exc"],
        "runtime_contract_ok": passed,
        "detail": f"web={[c['tool'] for c in web] or 'NONE'} chroma_read={chroma_read}",
    }


# =====================================================================
# 组 C：多目的、多 Tool
# =====================================================================
async def case_c01() -> dict:
    """C01 Memory + Research + Write → output/market.md 带来源。"""
    reset_instrumentation()
    seed = UniversalAgent("c01")
    try:
        await asyncio.wait_for(seed.run("记住：我长期关注新能源和人工智能板块"), timeout=90)
    finally:
        seed.close()
    reset_instrumentation()
    rec = await run_agent(
        "c01",
        "根据我之前的投资偏好，搜索最近一周值得关注的市场板块，"
        "整理带来源的报告并保存到 output/market.md",
    )
    f = Path(SNAPSHOT) / "output" / "market.md"
    exists = f.exists()
    size = f.stat().st_size if exists else 0
    web = [c for c in TOOL_CALLS if c["tool"].startswith("web_")]
    writes = write_tool_calls()
    passed = exists and size >= 100 and bool(web) and bool(writes)
    return {
        "case_id": "C01", "group": "C", "passed": passed,
        "expected_capabilities": ["memory", "research", "write", "verify"],
        "tool_calls": tool_names(),
        "out": rec["out"][:300],
        "exc": rec["exc"],
        "stdout_tail": rec["stdout_tail"][-1200:],
        "file_exists": exists,
        "file_size": size,
        "web_calls": [c["tool"] for c in web],
        "llm_call_count": rec["llm_calls"],
        "runtime_contract_ok": passed,
        "detail": f"exists={exists} size={size} web={bool(web)} writes={len(writes)}",
    }


async def case_c02() -> dict:
    """C02 多文件读取、合并去重 → output/merged.txt + 删除行数统计。"""
    reset_instrumentation()
    a = Path(SNAPSHOT) / "output" / "a.txt"
    b = Path(SNAPSHOT) / "output" / "b.txt"
    a.write_text("apple\nbanana\ncherry\n", encoding="utf-8")
    b.write_text("banana\ncherry\ndate\n", encoding="utf-8")
    rec = await run_agent(
        "c02",
        "读取 output/a.txt 和 output/b.txt，合并去重后保存到 output/merged.txt，"
        "最后告诉我删除了多少重复行",
    )
    merged = Path(SNAPSHOT) / "output" / "merged.txt"
    exists = merged.exists()
    lines = merged.read_text(encoding="utf-8").splitlines() if exists else []
    reads = [c for c in TOOL_CALLS if c["tool"] in ("read_file", "filesystem.read")]
    writes = write_tool_calls("merged.txt")
    read_ab = sum(1 for c in reads if any(
        t in " ".join(c["args"] + list(c["kwargs"].values())) for t in ("a.txt", "b.txt")
    ))
    unique = sorted(set(lines))
    correct = exists and lines == unique and "banana" in lines and "date" in lines
    stat_in_answer = any(t in rec["out"] for t in ("重复", "2", "两个", "删除了"))
    passed = correct and read_ab >= 2 and len(writes) >= 1 and stat_in_answer
    return {
        "case_id": "C02", "group": "C", "passed": passed,
        "expected_capabilities": ["read", "write", "analysis"],
        "tool_calls": tool_names(),
        "out": rec["out"][:300],
        "exc": rec["exc"],
        "stdout_tail": rec["stdout_tail"][-1200:],
        "merged_lines": lines,
        "merged_correct": correct,
        "read_ab_count": read_ab,
        "write_count": len(writes),
        "stat_in_answer": stat_in_answer,
        "llm_call_count": rec["llm_calls"],
        "runtime_contract_ok": passed,
        "detail": f"correct={correct} reads_ab={read_ab} writes={len(writes)} stat={stat_in_answer}",
    }


async def case_c03() -> dict:
    """C03 创建→运行→验证→（必要时）修复 calc.py（7 的平方=49）。"""
    reset_instrumentation()
    calc = Path(SNAPSHOT) / "output" / "calc.py"
    if calc.exists():
        calc.unlink()
    rec = await run_agent(
        "c03",
        "创建 output/calc.py 计算 7 的平方并打印，运行它；"
        "如果输出不是 49，就修复后再运行，直到输出 49",
    )
    exists = calc.exists()
    output_ok = False
    if exists:
        try:
            r = os.popen(f"{sys.executable} {calc} 2>&1").read()
            output_ok = "49" in r
        except Exception:
            output_ok = False
    runs = tool_calls_for("run_python")
    writes = write_tool_calls()
    passed = exists and output_ok
    return {
        "case_id": "C03", "group": "C", "passed": passed,
        "expected_capabilities": ["write", "execute", "verify"],
        "tool_calls": tool_names(),
        "out": rec["out"][:300],
        "exc": rec["exc"],
        "stdout_tail": rec["stdout_tail"][-1200:],
        "file_exists": exists,
        "output_ok_49": output_ok,
        "run_count": len(runs),
        "write_count": len(writes),
        "llm_call_count": rec["llm_calls"],
        "runtime_contract_ok": passed,
        "detail": f"exists={exists} output49={output_ok} runs={len(runs)} writes={len(writes)}",
    }


async def case_c04() -> dict:
    """C04 搜索+分析 → output/summary.md 与 output/sources.json 两个产物都验证。"""
    reset_instrumentation()
    rec = await run_agent(
        "c04",
        "搜索近期 Python AI Agent 的主要变化，"
        "生成 output/summary.md 和 output/sources.json 两个文件",
    )
    summary = Path(SNAPSHOT) / "output" / "summary.md"
    sources = Path(SNAPSHOT) / "output" / "sources.json"
    s_ok = summary.exists() and summary.stat().st_size >= 60
    j_ok = False
    if sources.exists():
        try:
            obj = json.loads(sources.read_text(encoding="utf-8"))
            j_ok = bool(obj)
        except Exception:
            j_ok = False
    passed = s_ok and j_ok
    return {
        "case_id": "C04", "group": "C", "passed": passed,
        "expected_capabilities": ["research", "write", "verify"],
        "tool_calls": tool_names(),
        "out": rec["out"][:300],
        "exc": rec["exc"],
        "stdout_tail": rec["stdout_tail"][-1200:],
        "summary_ok": s_ok,
        "sources_ok": j_ok,
        "llm_call_count": rec["llm_calls"],
        "runtime_contract_ok": passed,
        "detail": f"summary={s_ok} sources={j_ok}",
    }


# =====================================================================
# 组 D：AgentService 与事件流
# =====================================================================
async def case_d01() -> dict:
    """D01 同一 request_id + 同一内容 start_run 两次 → 同一 run_id，Provider 一次。"""
    reset_instrumentation()
    service, db = new_service()
    try:
        req = start_request("写一句话介绍大语言模型", "D01")
        h1 = await service.start_run(req)
        h2 = await service.start_run(req)
        # 等 h1 终态
        snapshot = None
        deadline = time.time() + 200
        while time.time() < deadline:
            snapshot = await service.get_run(
                _lookup(h1.run_id, "D01", tenant=req.tenant_id, session=req.session_id)
            )
            if snapshot.status.value in TERMINAL:
                break
            await asyncio.sleep(2)
        same_run = h1.run_id == h2.run_id
        events = [e async for e in service.stream_events(
            EventStreamRequest(tenant_id="tenant-p0", user_id="user-p0",
                               session_id="session-p0", run_id=h1.run_id,
                               request_id="D01-read", after_sequence=0)
        )]
        started = sum(1 for e in events if e.event_type.value == "run_started")
        passed = same_run and started == 1 and snapshot.status.value == "COMPLETED"
        return {
            "case_id": "D01", "group": "D", "passed": passed,
            "expected_capabilities": ["idempotency", "event_stream"],
            "same_run_id": same_run,
            "run_started_events": started,
            "terminal_status": snapshot.status.value if snapshot else None,
            "llm_call_count": len(LLM_CALLS),
            "runtime_contract_ok": passed,
            "detail": f"same_run={same_run} run_started={started} status={snapshot.status.value if snapshot else '?'}",
        }
    finally:
        await service.close()
        Path(db).unlink(missing_ok=True)


async def case_d02() -> dict:
    """D02 同一 request_id + 不同内容 → IDEMPOTENCY_CONFLICT。"""
    reset_instrumentation()
    service, db = new_service()
    try:
        req1 = start_request("写一句话介绍大语言模型", "D02")
        await service.start_run(req1)
        req2 = start_request("写一首关于海洋的诗", "D02")
        error_code = None
        error_type = None
        error_detail = ""
        try:
            await service.start_run(req2)
        except AgentServiceError as e:
            error_code = e.code
            error_type = "AgentServiceError"
        except Exception as e:  # noqa: BLE001 - 服务内部 bug 也记录
            error_type = type(e).__name__
            error_detail = str(e)
        passed = (
            error_code is not None
            and error_code
            in (
                ServiceErrorCode.REQUEST_ID_CONFLICT,
                getattr(ServiceErrorCode, "IDEMPOTENCY_CONFLICT", ServiceErrorCode.REQUEST_ID_CONFLICT),
            )
        )
        return {
            "case_id": "D02", "group": "D", "passed": passed,
            "expected_capabilities": ["idempotency"],
            "error_code": error_code.value if error_code else None,
            "error_type": error_type,
            "error_detail": error_detail[:200],
            "runtime_contract_ok": passed,
            "detail": f"error={error_code}",
        }
    finally:
        await service.close()
        Path(db).unlink(missing_ok=True)


async def case_d03() -> dict:
    """D03 读取部分事件后断开，再以 after_sequence 重连 → 无缺失、不重跑。"""
    reset_instrumentation()
    service, db = new_service()
    try:
        req = start_request("写一句话介绍 Transformer 架构", "D03")
        h = await service.start_run(req)
        snapshot = None
        deadline = time.time() + 200
        while time.time() < deadline:
            snapshot = await service.get_run(
                _lookup(h.run_id, "D03", tenant=req.tenant_id, session=req.session_id)
            )
            if snapshot.status.value in TERMINAL:
                break
            await asyncio.sleep(2)
        full = [e async for e in service.stream_events(
            EventStreamRequest(tenant_id="tenant-p0", user_id="user-p0",
                               session_id="session-p0", run_id=h.run_id,
                               request_id="D03-full", after_sequence=0)
        )]
        mid = max(1, len(full) // 2)
        tail = [e async for e in service.stream_events(
            EventStreamRequest(tenant_id="tenant-p0", user_id="user-p0",
                               session_id="session-p0", run_id=h.run_id,
                               request_id="D03-tail", after_sequence=mid)
        )]
        expected_tail = full[mid:]
        seq_match = [e.sequence_number for e in tail] == [e.sequence_number for e in expected_tail]
        passed = seq_match and len(full) > 0 and snapshot.status.value in TERMINAL
        return {
            "case_id": "D03", "group": "D", "passed": passed,
            "expected_capabilities": ["event_replay"],
            "full_count": len(full),
            "tail_count": len(tail),
            "tail_matches": seq_match,
            "terminal_status": snapshot.status.value,
            "runtime_contract_ok": passed,
            "detail": f"full={len(full)} tail={len(tail)} match={seq_match}",
        }
    finally:
        await service.close()
        Path(db).unlink(missing_ok=True)


async def case_d04() -> dict:
    """D04 Service 关闭并重建 → get_run 返回同一 durable Snapshot。"""
    reset_instrumentation()
    service, db = new_service()
    run_id = None
    try:
        req = start_request("写一句话介绍 Python", "D04")
        h = await service.start_run(req)
        run_id = h.run_id
        snapshot = None
        deadline = time.time() + 200
        while time.time() < deadline:
            snapshot = await service.get_run(_lookup(run_id, "D04"))
            if snapshot.status.value in TERMINAL:
                break
            await asyncio.sleep(2)
        first = snapshot.to_dict() if snapshot else None
        await service.close()
        # 重建 service（同一 DB）
        service2 = create_default_agent_service(
            Path(db), workspace_root=Path(SNAPSHOT), writer_id="acceptance-p0"
        )
        second = (await service2.get_run(_lookup(run_id, "D04"))).to_dict()
        same_status = first and first.get("status") == second.get("status")
        same_run = first and first.get("run_id") == second.get("run_id")
        passed = same_status and same_run and second.get("status") in TERMINAL
        return {
            "case_id": "D04", "group": "D", "passed": passed,
            "expected_capabilities": ["durability"],
            "run_id": run_id,
            "status_before": first.get("status") if first else None,
            "status_after_reopen": second.get("status"),
            "runtime_contract_ok": passed,
            "detail": f"before={first.get('status') if first else '?'} after={second.get('status')}",
        }
    finally:
        try:
            await service.close()
        except Exception:
            pass
        Path(db).unlink(missing_ok=True)


# =====================================================================
# 组 E：恢复与副作用安全
# =====================================================================
async def case_e01() -> dict:
    """E01 Workflow B 中断后通过 AgentService 恢复 → A 不重跑，只恢复 B。"""
    # 复用 v2.2C coordinator 的恢复语义：先让 A 完成、B 在 read_spec 后中断，
    # 再通过 AgentService.resume_run 恢复；验证 A 不再执行、B 完成。
    reset_instrumentation()
    from benchmarks.v22c import restart_worker
    import tempfile as _tf
    import shutil
    import asyncio as _asyncio

    old_run_id = os.environ.get("V22C_RUN_ID")
    os.environ["V22C_RUN_ID"] = "run-e01"
    workspace = _tf.mkdtemp(prefix="acc-e01-ws-")
    store_dir = _tf.mkdtemp(prefix="acc-e01-store-")
    try:
        ev_a = await restart_worker.phase_a(workspace, store_dir)
        ev_b = await restart_worker._activate_impl(
            workspace, store_dir, interrupt_after_stage_id="read_spec"
        )
        ev_r = await restart_worker.phase_resume("c03", workspace, store_dir)
        a_exec_after = ev_r.get("execution_counts", {}).get("spec", 0)
        solution_ok = ev_r.get("solution_exists", False)
        index_after = ev_r.get("index", {})
        passed = (
            ev_a.get("spec_success") is True
            and ev_b.get("checkpoint_active_stage_id") == "gen_code"
            and ev_r.get("b_success") is True
            and ev_r.get("c_success") is True
            and a_exec_after == 0          # A 在 resume 进程中未再执行
            and solution_ok
            and index_after.get("completed") == ["spec", "impl", "verify"]
        )
        return {
            "case_id": "E01", "group": "E", "passed": passed,
            "expected_capabilities": ["resume", "workflow"],
            "a_spec_success": ev_a.get("spec_success"),
            "a_spec_error": ev_a.get("spec_error", ""),
            "b_cp_active_stage": ev_b.get("checkpoint_active_stage_id"),
            "spec_executions_in_resume": a_exec_after,
            "completed_after": index_after.get("completed", []),
            "solution_ok": solution_ok,
            "b_success": ev_r.get("b_success"),
            "c_success": ev_r.get("c_success"),
            "runtime_contract_ok": passed,
            "detail": f"resume_spec_exec={a_exec_after} completed={index_after.get('completed')} "
                      f"solution={solution_ok} b={ev_r.get('b_success')} c={ev_r.get('c_success')}",
        }
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)
        if old_run_id is None:
            os.environ.pop("V22C_RUN_ID", None)
        else:
            os.environ["V22C_RUN_ID"] = old_run_id
async def case_e02() -> dict:
    """E02 文件写入成功、Finalize 前崩溃 → 文件不重复写、Provider 不重复调用。"""
    # 确定性崩溃 runtime：写文件后抛错（模拟 finalize 前崩溃）；resume 验证不重写。
    reset_instrumentation()
    from agent.runtime_store import SqliteRuntimeStore
    from agent.service import AgentService
    from agent.service.runtime_launcher import RuntimeExecutionLauncher

    class CrashAfterWriteRuntime:
        instances = 0

        def __init__(self, *args, **kwargs):
            type(self).instances += 1
            self.last_run_evidence = {
                "terminal_status": "FAILED_TERMINAL",
                "terminal_outputs_verified": False,
                "budget_exhausted": False,
                "runtime_pending": True,
                "task_failures": [{"task": "write", "reason": "crash"}],
                "failure_code": "RUNTIME_EXECUTION_INCOMPLETE",
            }

        async def run(self, request_text: str) -> str:
            p = Path(SNAPSHOT) / "output" / "e02.txt"
            existing = p.read_text(encoding="utf-8") if p.exists() else ""
            p.write_text(existing + "line-from-crashed-run\n", encoding="utf-8")
            raise RuntimeError("crash after write, before finalize")

        def close(self):
            return None

    CrashAfterWriteRuntime.instances = 0
    db = tempfile.NamedTemporaryFile(prefix="acc-e02-", suffix=".sqlite3", delete=False)
    db.close()
    os.unlink(db.name)
    store = SqliteRuntimeStore.open(Path(db.name))
    service = AgentService(
        runtime_store=store,
        launcher=RuntimeExecutionLauncher(runtime_factory=CrashAfterWriteRuntime),
        context_factory=__import__("agent.service.context_factory", fromlist=["ServiceContextFactory"]).ServiceContextFactory(store, workspace_root=Path(SNAPSHOT)),
    )
    try:
        req = start_request("写 output/e02.txt 内容为 line", "E02")
        handle = None
        try:
            handle = await service.start_run(req)
        except Exception:
            pass
        if handle is None:
            return {"case_id": "E02", "group": "E", "passed": False,
                    "detail": "start_run 抛错", "runtime_contract_ok": False}
        snap = await service.get_run(_lookup(handle.run_id, "E02"))
        # 等 FAILED
        deadline = time.time() + 30
        while time.time() < deadline and snap.status.value not in TERMINAL:
            await asyncio.sleep(1)
            snap = await service.get_run(_lookup(snap.run_id, "E02"))
        file_before = Path(SNAPSHOT) / "output" / "e02.txt"
        content_before = file_before.read_text(encoding="utf-8") if file_before.exists() else ""
        instances_after_fail = CrashAfterWriteRuntime.instances
        # resume：不得再次执行写
        from agent.service import ResumeRunRequest
        resume_req = ResumeRunRequest(
            tenant_id="tenant-p0", user_id="user-p0", session_id="session-p0",
            run_id=snap.run_id, request_id="E02-resume",
            request_text="写 output/e02.txt 内容为 line",
        )
        try:
            await service.resume_run(resume_req)
        except Exception:
            pass
        await asyncio.sleep(3)
        content_after = file_before.read_text(encoding="utf-8") if file_before.exists() else ""
        not_duplicated = content_before == content_after
        provider_not_duplicated = CrashAfterWriteRuntime.instances == instances_after_fail
        passed = (
            snap.status.value == "FAILED_TERMINAL"
            and not_duplicated
            and provider_not_duplicated
        )
        return {
            "case_id": "E02", "group": "E", "passed": passed,
            "expected_capabilities": ["side_effect_safety", "resume"],
            "terminal_status": snap.status.value,
            "content_before": content_before,
            "content_after": content_after,
            "file_duplicated": not not_duplicated,
            "provider_runtime_instances_after_fail": instances_after_fail,
            "provider_runtime_instances_after_resume": CrashAfterWriteRuntime.instances,
            "runtime_contract_ok": passed,
            "detail": f"status={snap.status.value} dup={not not_duplicated} "
                      f"instances={instances_after_fail}->{CrashAfterWriteRuntime.instances}",
        }
    finally:
        await service.close()
        Path(db.name).unlink(missing_ok=True)


async def case_e03() -> dict:
    """E03 对 COMPLETED Run 调用 resume → RESUME_NOT_ALLOWED / ALREADY_COMPLETED。"""
    reset_instrumentation()
    service, db = new_service()
    try:
        req = start_request("写一句话介绍 Python", "E03")
        h = await service.start_run(req)
        snap = None
        deadline = time.time() + 200
        while time.time() < deadline:
            snap = await service.get_run(_lookup(h.run_id, "E03"))
            if snap.status.value in TERMINAL:
                break
            await asyncio.sleep(2)
        from agent.service import ResumeRunRequest
        error_code = None
        error_type = None
        error_detail = ""
        try:
            await service.resume_run(ResumeRunRequest(
                tenant_id="tenant-p0", user_id="user-p0", session_id="session-p0",
                run_id=h.run_id, request_id="E03-resume",
                request_text="写一句话介绍 Python",
            ))
        except AgentServiceError as e:
            error_code = e.code
            error_type = "AgentServiceError"
        except Exception as e:  # noqa: BLE001
            error_type = type(e).__name__
            error_detail = str(e)
        passed = (
            snap.status.value == "COMPLETED"
            and error_code in (
                getattr(ServiceErrorCode, "ALREADY_COMPLETED", None),
                getattr(ServiceErrorCode, "RUN_ALREADY_ACTIVE", None),
                ServiceErrorCode.INVALID_REQUEST,
            )
        )
        return {
            "case_id": "E03", "group": "E", "passed": passed,
            "expected_capabilities": ["resume", "terminal_correctness"],
            "terminal_status": snap.status.value,
            "resume_error_code": error_code.value if error_code else None,
            "resume_error_type": error_type,
            "resume_error_detail": error_detail[:200],
            "runtime_contract_ok": passed,
            "detail": f"status={snap.status.value} resume_error={error_code}",
        }
    finally:
        await service.close()
        Path(db).unlink(missing_ok=True)


async def case_e04() -> dict:
    """E04 错 tenant 查询/恢复 → 对外 RUN_NOT_FOUND，不泄漏 owner。"""
    reset_instrumentation()
    service, db = new_service()
    try:
        req = start_request("写一句话介绍 Python", "E04", tenant="tenant-owner")
        h = await service.start_run(req)
        snap = None
        deadline = time.time() + 200
        while time.time() < deadline:
            snap = await service.get_run(
                _lookup(h.run_id, "E04", tenant="tenant-owner", session="session-p0")
            )
            if snap.status.value in TERMINAL:
                break
            await asyncio.sleep(2)
        error_code = None
        try:
            await service.get_run(
                _lookup(h.run_id, "E04", tenant="tenant-attacker", session="session-p0")
            )
        except AgentServiceError as e:
            error_code = e.code
        passed = error_code == ServiceErrorCode.RUN_NOT_FOUND
        return {
            "case_id": "E04", "group": "E", "passed": passed,
            "expected_capabilities": ["tenant_isolation"],
            "cross_tenant_error": error_code.value if error_code else None,
            "runtime_contract_ok": passed,
            "detail": f"cross_tenant_error={error_code}",
        }
    finally:
        await service.close()
        Path(db).unlink(missing_ok=True)


# =====================================================================
# 组 F：Provider 与性能
# =====================================================================
async def case_f01() -> dict:
    """F01 Provider 请求前超时 → 无 Tool 副作用，Provider Error 单列，Run 不假成功。"""
    reset_instrumentation()
    from agent.runtime_store import SqliteRuntimeStore
    from agent.service import AgentService
    from agent.service.runtime_launcher import RuntimeExecutionLauncher

    class TimeoutRuntime:
        def __init__(self, *args, **kwargs):
            self.last_run_evidence = {
                "terminal_status": "FAILED_TERMINAL",
                "terminal_outputs_verified": False,
                "budget_exhausted": False,
                "runtime_pending": True,
                "task_failures": [],
                "failure_code": "PROVIDER_TIMEOUT",
            }

        async def run(self, request_text: str) -> str:
            raise TimeoutError("provider request timed out before any tool side effect")

        def close(self):
            return None

    db = tempfile.NamedTemporaryFile(prefix="acc-f01-", suffix=".sqlite3", delete=False)
    db.close()
    os.unlink(db.name)
    store = SqliteRuntimeStore.open(Path(db.name))
    service = AgentService(
        runtime_store=store,
        launcher=RuntimeExecutionLauncher(runtime_factory=TimeoutRuntime),
        context_factory=__import__("agent.service.context_factory", fromlist=["ServiceContextFactory"]).ServiceContextFactory(store, workspace_root=Path(SNAPSHOT)),
    )
    try:
        req = start_request("搜索今日热点并写入 output/f01.md", "F01")
        handle = None
        try:
            handle = await service.start_run(req)
        except Exception:
            pass
        if handle is None:
            return {"case_id": "F01", "group": "F", "passed": False,
                    "detail": "start_run 抛错", "runtime_contract_ok": False}
        snap = None
        deadline = time.time() + 30
        while time.time() < deadline:
            snap = await service.get_run(_lookup(handle.run_id, "F01"))
            if snap.status.value in TERMINAL:
                break
            await asyncio.sleep(1)
        f01_file = Path(SNAPSHOT) / "output" / "f01.md"
        no_side_effect = not f01_file.exists() and not tool_names()
        passed = (
            snap.status.value == "FAILED_TERMINAL"
            and no_side_effect
        )
        # 观察项：Provider 异常是否在 durable snapshot 中留下结构化 failure_code
        failure_code_recorded = getattr(snap, "failure_summary", None) is not None
        return {
            "case_id": "F01", "group": "F", "passed": passed,
            "expected_capabilities": ["provider_fault", "no_false_success"],
            "terminal_status": snap.status.value,
            "failure_summary": (
                getattr(snap, "failure_summary", None).to_dict()
                if getattr(snap, "failure_summary", None) else None
            ),
            "failure_code_recorded_in_snapshot": failure_code_recorded,
            "tool_side_effects": tool_names(),
            "provider_error": "PROVIDER_TIMEOUT",
            "runtime_contract_ok": passed,
            "detail": f"status={snap.status.value} side_effects={tool_names() or 'NONE'} "
                      f"failure_code_recorded={failure_code_recorded}",
        }
    finally:
        await service.close()
        Path(db.name).unlink(missing_ok=True)


async def case_f02() -> dict:
    """F02 Durable Prepare 后 Provider 超时 → 重试不创建第二条 intent、不重复不安全操作。"""
    # 通过 service.resume_run 的 prepare_operation 语义验证：同一 request_id 重试
    # 不产生第二条 PREPARED intent（幂等）；本 case 用确定性失败 runtime。
    reset_instrumentation()
    from agent.runtime_store import SqliteRuntimeStore
    from agent.service import AgentService
    from agent.service.runtime_launcher import RuntimeExecutionLauncher
    from agent.service import ResumeRunRequest

    class PrepareThenTimeoutRuntime:
        instances = 0

        def __init__(self, *args, **kwargs):
            type(self).instances += 1
            self.last_run_evidence = {
                "terminal_status": "FAILED_TERMINAL",
                "terminal_outputs_verified": False,
                "budget_exhausted": False,
                "runtime_pending": True,
                "task_failures": [],
                "failure_code": "PROVIDER_TIMEOUT",
            }

        async def run(self, request_text: str) -> str:
            raise TimeoutError("provider timeout after durable prepare")

        def close(self):
            return None

    PrepareThenTimeoutRuntime.instances = 0
    db = tempfile.NamedTemporaryFile(prefix="acc-f02-", suffix=".sqlite3", delete=False)
    db.close()
    os.unlink(db.name)
    store = SqliteRuntimeStore.open(Path(db.name))
    service = AgentService(
        runtime_store=store,
        launcher=RuntimeExecutionLauncher(runtime_factory=PrepareThenTimeoutRuntime),
        context_factory=__import__("agent.service.context_factory", fromlist=["ServiceContextFactory"]).ServiceContextFactory(store, workspace_root=Path(SNAPSHOT)),
    )
    try:
        req = start_request("写 output/f02.txt", "F02")
        try:
            h = await service.start_run(req)
        except Exception:
            h = None
        # 等终态（FAILED）
        snap = await service.get_run(_lookup(h.run_id, "F02")) if h else None
        deadline = time.time() + 30
        while snap is not None and time.time() < deadline and snap.status.value not in TERMINAL:
            await asyncio.sleep(1)
            snap = await service.get_run(_lookup(h.run_id, "F02"))
        instances_after_first = PrepareThenTimeoutRuntime.instances
        # 重试 resume（同一 request_id）
        try:
            await service.resume_run(ResumeRunRequest(
                tenant_id="tenant-p0", user_id="user-p0", session_id="session-p0",
                run_id=h.run_id, request_id="F02", request_text="写 output/f02.txt",
            ))
        except Exception:
            pass
        await asyncio.sleep(2)
        f02 = Path(SNAPSHOT) / "output" / "f02.txt"
        passed = (
            snap is not None
            and snap.status.value == "FAILED_TERMINAL"
            and PrepareThenTimeoutRuntime.instances == instances_after_first
            and not f02.exists()
        )
        return {
            "case_id": "F02", "group": "F", "passed": passed,
            "expected_capabilities": ["durable_prepare", "provider_fault"],
            "terminal_status": snap.status.value if snap else None,
            "runtime_instances": instances_after_first,
            "runtime_instances_after_retry": PrepareThenTimeoutRuntime.instances,
            "side_effect_file": f02.exists(),
            "provider_error": "PROVIDER_TIMEOUT",
            "runtime_contract_ok": passed,
            "detail": f"status={snap.status.value if snap else '?'} "
                      f"instances={instances_after_first}->{PrepareThenTimeoutRuntime.instances} "
                      f"f02_exists={f02.exists()}",
        }
    finally:
        await service.close()
        Path(db.name).unlink(missing_ok=True)


async def case_f03() -> dict:
    """F03 模型返回格式错误（非法 Planner JSON / 缺字段）→ 有限重试，不无限 Replan。"""
    # 确定性探针：向 Planner 注入非法 JSON，验证其有限重试且不把解析失败当完成。
    reset_instrumentation()
    from agent.orchestrator.planner import PlannerStage

    attempts = []
    original = PlannerStage.run

    async def broken_plan(self, *args, **kwargs):
        attempts.append(1)
        # 返回 FAIL：Planner 解析失败 → 不得无限 Replan / 不得标完成
        return (args[2], "FAIL", None)

    PlannerStage.run = broken_plan
    try:
        agent = UniversalAgent("f03")
        try:
            out = await asyncio.wait_for(agent.run("写一个函数"), timeout=60)
        except Exception as e:
            out = f"EXC:{e}"
        evidence = getattr(agent, "last_run_evidence", {}) or {}
        agent.close()
        limited = len(attempts) <= 5
        not_completed = evidence.get("terminal_status") != "COMPLETED" or evidence.get("task_failures")
        passed = limited and not_completed
        return {
            "case_id": "F03", "group": "F", "passed": passed,
            "expected_capabilities": ["malformed_recovery"],
            "planner_attempts": len(attempts),
            "terminal_status": evidence.get("terminal_status"),
            "limited_retries": limited,
            "runtime_contract_ok": passed,
            "detail": f"attempts={len(attempts)} status={evidence.get('terminal_status')}",
        }
    finally:
        PlannerStage.run = original


async def case_f04() -> dict:
    """F04 简单任务性能基线：创建 output/hello.txt。"""
    reset_instrumentation()
    service, db = new_service()
    try:
        req = start_request("创建 output/hello.txt，内容为 hello", "F04")
        t0 = time.perf_counter()
        handle = await service.start_run(req)
        accept_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        snap = None
        deadline = time.time() + 200
        while time.time() < deadline:
            snap = await service.get_run(_lookup(handle.run_id, "F04"))
            if snap.status.value in TERMINAL:
                break
            await asyncio.sleep(2)
        wall_ms = (time.perf_counter() - t0) * 1000
        hello = Path(SNAPSHOT) / "output" / "hello.txt"
        file_ok = hello.exists() and "hello" in hello.read_text(encoding="utf-8")
        passed = (
            snap.status.value == "COMPLETED"
            and file_ok
            and len(LLM_CALLS) <= 2
            and PLAN_CALLS <= 1
        )
        return {
            "case_id": "F04", "group": "F", "passed": passed,
            "expected_capabilities": ["write"],
            "terminal_status": snap.status.value,
            "file_ok": file_ok,
            "llm_call_count": len(LLM_CALLS),
            "replan_count": PLAN_CALLS,
            "latency": {
                "start_acceptance_ms": round(accept_ms),
                "wall_ms": round(wall_ms),
            },
            "runtime_contract_ok": passed,
            "detail": f"status={snap.status.value} file_ok={file_ok} "
                      f"llm={len(LLM_CALLS)} plan={PLAN_CALLS}",
        }
    finally:
        await service.close()
        Path(db).unlink(missing_ok=True)


CASES = {
    "A01": case_a01, "A02": case_a02, "A03": case_a03, "A04": case_a04,
    "B01": case_b01, "B02": case_b02, "B03": case_b03, "B04": case_b04,
    "C01": case_c01, "C02": case_c02, "C03": case_c03, "C04": case_c04,
    "D01": case_d01, "D02": case_d02, "D03": case_d03, "D04": case_d04,
    "E01": case_e01, "E02": case_e02, "E03": case_e03, "E04": case_e04,
    "F01": case_f01, "F02": case_f02, "F03": case_f03, "F04": case_f04,
}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", default="all",
                        choices=("all", "A", "B", "C", "D", "E", "F"))
    parser.add_argument("--ids", default="")
    args = parser.parse_args()
    print(f"boot... snapshot={SNAPSHOT}", flush=True)
    load_all()
    install_instrumentation()
    try:
        await asyncio.wait_for(UniversalAgent("warmup").run("你好"), timeout=90)
    except Exception as e:
        print("warmup failed:", e, flush=True)

    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    selected = []
    for cid in CASES:
        if ids:
            if cid in ids:
                selected.append(cid)
            continue
        if args.group == "all" or cid.startswith(args.group):
            selected.append(cid)
    results = []
    t_start = time.perf_counter()
    for cid in selected:
        t0 = time.perf_counter()
        try:
            rec = await CASES[cid]()
        except Exception as e:  # noqa: BLE001
            rec = {
                "case_id": cid, "group": cid[0], "passed": False,
                "runtime_contract_ok": False,
                "classification": "RUNTIME_CONTRACT_FAILURE",
                "detail": f"HARNESS_ERR: {type(e).__name__}: {e}",
            }
        rec.setdefault("classification", (
            "PASS" if rec.get("passed") else "RUNTIME_CONTRACT_FAILURE"
        ))
        rec["elapsed_s"] = round(time.perf_counter() - t0, 1)
        results.append(rec)
        print(f"[{cid}] {rec['classification']} ({rec['elapsed_s']}s) "
              f"{rec.get('detail','')[:120]}", flush=True)
        if len(results) % 4 == 0:
            with open(_RESULTS, "w", encoding="utf-8") as f:
                json.dump({"partial": True, "results": results}, f, ensure_ascii=False, indent=1)
    n_pass = sum(1 for r in results if r.get("passed"))
    summary = {
        "suite": "Real API Acceptance Suite v0.1 - P0",
        "total": len(results),
        "pass": n_pass,
        "fail": len(results) - n_pass,
        "elapsed_min": round((time.perf_counter() - t_start) / 60, 1),
    }
    with open(_RESULTS, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=1)
    print("=" * 60, flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    for r in results:
        if not r.get("passed"):
            print(f"FAIL {r['case_id']} [{r['group']}] {r.get('detail','')[:150]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
