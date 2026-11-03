#!/usr/bin/env python3
"""TSAgent 复杂结合意图真实 API 测试 — 20 例（编程+搜索+金融研究+报告+混合链）。

本轮聚焦"复杂结合意图"：
  fin_research   金融研究 + 实时搜索（推荐今天的股票 / 热点板块 / 财经新闻）
  program        编程 + 数据/金融（写股票分析程序 / 均线 / 基金收益）
  program_search 编程 + 搜索（搜方法后写示例程序）
  research       搜索 + 研究报告落盘（quantum / LLM / 语言对比）
  mixed          搜索 + 编程 + 建议（天气穿衣 / 免费股票 API 示例）

用法:
  TSAGENT_SNAPSHOT=/private/tmp/tsagent-complex20 \
  TSAGENT_RESULTS=/private/tmp/complex20_results.json \
  python -B tsagent_harness_complex20.py
"""
import asyncio
import contextlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

SNAPSHOT = os.environ.get("TSAGENT_SNAPSHOT", "/private/tmp/tsagent-complex20")
os.chdir(SNAPSHOT)
sys.path.insert(0, SNAPSHOT)

os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

_RESULTS = os.environ.get("TSAGENT_RESULTS", "/private/tmp/complex20_results.json")
from agent.bootstrap import load_all
from agent.runtime import UniversalAgent
from agent.registry.tool_registry import registry

TIMEOUT = 200

# ── 时间接地（Temporal Grounding）判据（round-4 修订）──
# 请求含时间限定词（今天/最近/最新/本周/当前/实时/今日/本月/近期）时，
# Agent 必须实际调用 web 工具读取当前时间的外部数据；否则计为 BUG。
WEB_TOOLS = ("web_search", "web_deep_search", "web_news_search", "web_fetch")
TEMPORAL_RE = re.compile(r"今天|今日|最近|本周|本月|最新|当前|实时|近期")
_WEB_CALLS: dict[str, list] = {}


def install_web_recorder():
    """在 ToolRegistry 层包裹 web 工具，记录真实调用（不改变行为）。"""
    from langchain_core.tools import StructuredTool
    for name in WEB_TOOLS:
        tool = registry.get(name)
        if tool is None:
            continue
        original_coro = getattr(tool, "coroutine", None)

        async def wrapper(*args, **kwargs):
            _WEB_CALLS.setdefault(name, []).append(
                {"args": [str(a)[:200] for a in args],
                 "kwargs": {k: str(v)[:200] for k, v in kwargs.items()}}
            )
            if original_coro is not None:
                return await original_coro(*args, **kwargs)
            return await asyncio.get_event_loop().run_in_executor(None, tool.func, *args)

        registry._tools[name] = StructuredTool.from_function(
            coroutine=wrapper,
            name=name,
            description=tool.description or name,
        )

# 失败措辞：出现则视为 Agent 未能完成任务（假失败/拒绝/内部错误）
FAILURE_PHRASES = [
    "内部错误", "请换一种说法", "无法完成", "无法生成", "无法为您",
    "抱歉", "对不起", "暂不支持", "未能完成", "I cannot", "I'm sorry",
    "i cannot", "i'm sorry", "sorry,", "内部异常", "出了点问题",
]

# Provider 网络类错误措辞（不计入 Agent 能力失败）
PROVIDER_PHRASES = [
    "connection", "timeout", "超时", "网络", "network", "dns",
    "无法连接", "连接失败", "搜索失败", "暂时无法访问",
]

SCENARIOS: list[tuple[str, str, str, dict]] = []


def S(sid: str, cat: str, inp: str, **extra) -> None:
    SCENARIOS.append((sid, cat, inp, extra))


# ── F. 金融研究 + 实时搜索（6）──
S("F01", "fin_research", "推荐些今天的股票",
  min_len=60, keywords=("股票", "板块", "推荐", "关注", "涨", "风险"))
S("F02", "fin_research", "最近一周 A 股有哪些热门板块",
  min_len=60, keywords=("板块", "A股", "科技", "资金", "热点"))
S("F03", "fin_research", "今天有哪些值得关注的财经新闻",
  min_len=60, keywords=("财经", "市场", "新闻", "经济", "涨", "跌"))
S("F04", "fin_research", "美股最近的表现怎么样",
  min_len=60, keywords=("美股", "纳斯达克", "标普", "道琼斯", "指数"))
S("F05", "fin_research", "最近的加密货币行情如何",
  min_len=60, keywords=("比特币", "加密", "币", "行情", "价格"))
S("F06", "fin_research", "帮我分析一下最近新能源板块的投资机会",
  min_len=80, keywords=("新能源", "板块", "机会", "风险", "投资"))

# ── P. 编程 + 数据/金融（5）──
S("P01", "program", "写一个股票分析程序，保存到 output/stock_analyzer.py",
  file="output/stock_analyzer.py", codekw="def")
S("P02", "program", "写一个计算移动平均线的 Python 脚本，保存到 output/ma.py",
  file="output/ma.py", codekw="def")
S("P03", "program", "写一个简单的基金收益计算器，保存到 output/fund_return.py",
  file="output/fund_return.py", codekw="def")
S("P04", "program", "写一个读取 CSV 并做股票日收益统计的程序，保存到 output/return_stats.py",
  file="output/return_stats.py", codekw="def")
S("P05", "program", "写一个 K 线数据生成与简单展示程序，保存到 output/kline.py",
  file="output/kline.py", codekw="def")

# ── C. 编程 + 搜索（4）──
S("C01", "program_search", "搜索 Python 怎么抓取股票行情数据，然后写一个示例程序保存到 output/fetch_stock.py",
  file="output/fetch_stock.py", codekw="def", min_len=40)
S("C02", "program_search", "搜索 asyncio 并发教程，写一个并发下载示例保存到 output/async_download.py",
  file="output/async_download.py", codekw="async def", min_len=40)
S("C03", "program_search", "搜索 pandas 数据处理常用方法，写一个数据清洗示例保存到 output/data_clean.py",
  file="output/data_clean.py", codekw="def", min_len=40)
S("C04", "program_search", "搜索 matplotlib 画股票 K 线图的方法，写一个示例保存到 output/kline_plot.py",
  file="output/kline_plot.py", codekw="def", min_len=40)

# ── R. 搜索 + 研究报告落盘（3）──
S("R01", "research", "搜索量子计算最新进展，写一篇 200 字左右的报告保存到 output/quantum.md",
  file="output/quantum.md", minsize=120, min_len=60)
S("R02", "research", "搜索大语言模型最新动态，写一篇总结保存到 output/llm_news.md",
  file="output/llm_news.md", minsize=120, min_len=60)
S("R03", "research", "搜索 Python 和 Rust 在数据处理上的差异，写一篇对比保存到 output/lang_compare.md",
  file="output/lang_compare.md", minsize=120, min_len=60)

# ── M. 混合 agentic 链（2）──
S("M01", "mixed", "搜索今天北京的天气，然后写一个根据温度给穿衣建议的 Python 脚本保存到 output/weather_advice.py",
  file="output/weather_advice.py", codekw="def", min_len=40)
S("M02", "mixed", "搜索免费的股票数据 API，写一个调用示例保存到 output/stock_api_demo.py",
  file="output/stock_api_demo.py", codekw="def", min_len=40)

assert len(SCENARIOS) == 20, f"期望 20 例，实际 {len(SCENARIOS)}"


# ────────────────────────── 校验器 ──────────────────────────
def has_traceback(text: str) -> bool:
    return "Traceback" in text or "Traceback (most recent call last)" in text


def _is_provider(out: str, stdout: str, exc: str | None) -> bool:
    text = f"{out} {stdout} {exc or ''}".lower()
    if exc:
        if any(
            t in text
            for t in ("connection", "timeout", "dns", "socket", "nodename", "asyncio.timeouterror")
        ):
            return True
    return any(p in text for p in PROVIDER_PHRASES)


def check(scenario, out, stdout, dur, exc, files_before, files_after):
    sid, cat, inp, extra = scenario
    problems: list[str] = []
    warns: list[str] = []
    provider = _is_provider(str(out or ""), stdout, exc)
    if exc and not provider:
        problems.append(f"EXCEPTION: {exc}")
    if dur >= TIMEOUT - 1:
        problems.append("TIMEOUT")
    if out is None or not str(out).strip():
        if provider:
            return problems, warns, "PROVIDER"
        problems.append("EMPTY_OUTPUT")
    if has_traceback(f"{out} {stdout}"):
        problems.append("TRACEBACK_LEAK")
    out_s = str(out or "")
    # 时间接地：含时间限定词但未调用任何 web 工具 → BUG
    if TEMPORAL_RE.search(inp) and not _WEB_CALLS:
        problems.append(
            f"TEMPORAL_NOT_GROUNDED 说了时间限定词但未读取当前时间外部数据 "
            f"(web_calls={sorted(_WEB_CALLS) or 'NONE'})"
        )
    for phrase in FAILURE_PHRASES:
        if phrase in out_s:
            warns.append(f"FAILURE_PHRASE:{phrase}")
            break

    min_len = extra.get("min_len", 0)
    if out_s.strip() and len(out_s.strip()) < min_len:
        warns.append(f"OUTPUT_THIN len={len(out_s.strip())} < {min_len}")

    # 领域关键词
    kws = extra.get("keywords", ())
    hit = [k for k in kws if k in out_s]
    if kws and out_s.strip() and not hit:
        warns.append(f"DOMAIN_KW_MISSING kw={kws}")

    # 文件校验
    f = extra.get("file")
    if f:
        path = Path(f)
        if not path.exists():
            problems.append(f"FILE_NOT_CREATED {f}")
        elif f.endswith(".py"):
            try:
                compile(path.read_text(encoding="utf-8"), f, "exec")
            except Exception as e:
                problems.append(f"FILE_SYNTAX_ERROR {f}: {e}")
            codekw = extra.get("codekw")
            if codekw and codekw not in path.read_text(encoding="utf-8"):
                warns.append(f"CODE_KW_MISSING {codekw} in {f}")
        elif f.endswith(".md"):
            size = path.stat().st_size
            if size < extra.get("minsize", 0):
                warns.append(f"FILE_TOO_SMALL {f} ({size}B)")

    return problems, warns, None


# ────────────────────────── 运行器 ──────────────────────────
async def run_turn(agent, user_id, inp, timeout=TIMEOUT):
    buf = io.StringIO()
    t0 = time.perf_counter()
    try:
        with contextlib.redirect_stdout(buf):
            out = await asyncio.wait_for(agent.run(inp), timeout=timeout)
        return {"out": out, "stdout": buf.getvalue(), "dur": time.perf_counter() - t0, "exc": None}
    except asyncio.TimeoutError:
        return {"out": "", "stdout": buf.getvalue(), "dur": time.perf_counter() - t0, "exc": "TIMEOUT"}
    except Exception as e:
        return {"out": "", "stdout": buf.getvalue(), "dur": time.perf_counter() - t0,
                "exc": f"{type(e).__name__}: {e}"}


async def run_scenario(idx, sid, cat, inp, extra):
    _WEB_CALLS.clear()
    user_id = f"complex{idx}"
    agent = UniversalAgent(user_id)
    r = await run_turn(agent, user_id, inp)
    scenario = (sid, cat, inp, extra)
    problems, warns, provider = check(
        scenario, r["out"], r["stdout"], r["dur"], r["exc"], None, None
    )
    if provider:
        status = "PROVIDER"
    elif problems:
        status = "BUG"
    elif warns:
        status = "WARN"
    else:
        status = "PASS"
    return {
        "id": sid, "cat": cat, "input": inp, "status": status,
        "problems": problems, "warns": warns, "dur": round(r["dur"], 1),
        "out": str(r["out"] or "")[:500], "exc": r["exc"],
        "web_calls": {name: len(items) for name, items in _WEB_CALLS.items() if items},
    }


async def main():
    print(f"boot... total={len(SCENARIOS)} snapshot={SNAPSHOT}", flush=True)
    load_all()
    install_web_recorder()
    try:
        a0 = UniversalAgent("warmup")
        await asyncio.wait_for(a0.run("你好"), timeout=TIMEOUT)
    except Exception as e:
        print("warmup failed:", e, flush=True)

    results = []
    t_start = time.perf_counter()
    for idx, (sid, cat, inp, extra) in enumerate(SCENARIOS):
        t0 = time.perf_counter()
        try:
            rec = await run_scenario(idx, sid, cat, inp, extra)
        except Exception as e:
            rec = {"id": sid, "cat": cat, "input": inp, "status": "BUG",
                   "problems": [f"HARNESS_ERR: {type(e).__name__}: {e}"],
                   "warns": [], "dur": 0, "out": "", "exc": str(e)}
        results.append(rec)
        print(
            f"[{idx:02d}] {sid} {cat} -> {rec['status']} "
            f"({round(time.perf_counter() - t0, 1)}s) {rec['problems'][:2]}{rec['warns'][:2]}",
            flush=True,
        )
        if (idx + 1) % 5 == 0:
            with open(_RESULTS, "w", encoding="utf-8") as f:
                json.dump({"summary": {"total": len(results), "partial": True}, "results": results},
                          f, ensure_ascii=False, indent=1)

    elapsed = time.perf_counter() - t_start
    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_warn = sum(1 for r in results if r["status"] == "WARN")
    n_bug = sum(1 for r in results if r["status"] == "BUG")
    n_provider = sum(1 for r in results if r["status"] == "PROVIDER")
    n_effective = len(results) - n_provider
    summary = {
        "total": len(results),
        "pass": n_pass, "warn": n_warn, "bug": n_bug, "provider": n_provider,
        "raw_e2e_rate": round(n_pass / len(results), 3),
        "capability_rate": round(n_pass / n_effective, 3) if n_effective else 0.0,
        "provider_rate": round(n_provider / len(results), 3),
        "elapsed_min": round(elapsed / 60, 1),
    }
    with open(_RESULTS, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=1)
    print("=" * 60, flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    for r in results:
        if r["status"] != "PASS":
            print(f"{r['status']} {r['id']} [{r['cat']}] {r['problems'][:3]}{r['warns'][:3]} "
                  f"| out={r['out'][:100]!r}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
