#!/usr/bin/env python3
"""failboard_v2 — Diagnostic Backbone（v2.0 统一诊断基础设施）。

定位：Fail Board 是整个 Agent 的 Diagnostic Backbone，统一承载所有 Capability 的
    失败事件（不可变）、证据（结构化）、根因（统一映射）、修复状态（事件溯源）与回归历史。

设计原则（v2.0 最终冻结）：
    1. Event 不可变（Event Sourcing，仅诊断层）：
       FailureEvent 生成后永不修改；状态变化 = 追加 FailureTransition。
       → 可推导 Bug Lifetime / Regression Count（"这个 Bug 修过几次？"）。
    2. Evidence 结构化：
       Evidence(source, location, expected, actual) —— Reflection 直接消费
       expected/actual 生成 Diagnosis，无需 NLP 解析。
    3. Root Cause 唯一来源 = SYMPTOM_MAP：
       Benchmark 只输出 symptom + evidence；FailBoard 统一映射
       symptom → root_cause → correction，防止各 Benchmark 用词漂移。

闭环：Capability → Evaluation → FailureEvent → FailBoard → Reflection → Regression → Trend Gate

CLI:
    python evaluation/benchmark/failboard_v2.py   # 渲染当前全部事件 + 生命周期聚合
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

# ── Root Cause Taxonomy（Symptom → Root Cause → Correction 统一映射） ──
SYMPTOM_MAP: Dict[str, dict] = {
    "timeout":            {"root_cause": "tool",       "correction": "switch_tool"},
    "wrong_answer":       {"root_cause": "decision",   "correction": "re_decide"},
    "missing_constraint": {"root_cause": "planning",   "correction": "replanning"},
    "hallucination":      {"root_cause": "grounding",  "correction": "re_ground"},
    "context_drift":      {"root_cause": "planning",   "correction": "replanning"},
    "unknown":            {"root_cause": "unknown",    "correction": "ask_user"},
}
VALID_SYMPTOMS = set(SYMPTOM_MAP.keys())

# ── 生命周期状态机 ──
STATUS_FLOW = {
    "NEW":        {"CONFIRMED", "FIXED", "REGRESSION"},
    "CONFIRMED":  {"FIXED", "REGRESSION"},
    "FIXED":      {"CLOSED", "REGRESSION"},
    "REGRESSION": {"CONFIRMED", "FIXED"},
    "CLOSED":     {"REGRESSION"},
}
VALID_STATUSES = set(STATUS_FLOW.keys())


@dataclass(frozen=True)
class Evidence:
    """结构化证据 —— Reflection 直接消费 expected/actual，无需 NLP。"""
    source: str        # planner / plan_validator / semantic_validator / verify / trace
    location: str      # task[2] / parser.py:17 / verify.py:31
    expected: str      # 期望值（约束/目标/行为）
    actual: str        # 实际值（违反/缺失/错误）


@dataclass(frozen=True)
class FailureEvent:
    """不可变失败事件。生成后永不修改（Event Sourcing）。"""
    benchmark: str
    scenario: str
    layer: str
    dimension: str
    failure: str
    evidence: List[Evidence] = field(default_factory=list)
    symptom: str = "unknown"
    detected_at: str = ""

    def __post_init__(self):
        if self.symptom not in VALID_SYMPTOMS:
            raise ValueError(f"非法 symptom: {self.symptom!r}（合法: {sorted(VALID_SYMPTOMS)}）")

    @property
    def id(self) -> str:
        # 同一 benchmark+scenario+dimension = 同一 Bug 生命周期
        return f"{self.benchmark}:{self.scenario}:{self.dimension}"


class FailBoard:
    """Diagnostic Backbone。collect / transition / query / render / aggregate。"""

    def __init__(self, events: Optional[List[FailureEvent]] = None):
        self._events: Dict[str, FailureEvent] = {}
        self._transitions: Dict[str, List[FailureTransition]] = {}
        if events:
            self.collect(events)

    # ── 写入 ──

    def collect(self, events: List[FailureEvent]) -> int:
        """收集事件（Benchmark 只产 symptom+evidence；root_cause 由 resolve 统一补全）。"""
        added = 0
        for e in events:
            if e.id in self._events:
                continue  # 同一 Bug 生命周期已存在，不重复建档
            self._events[e.id] = e
            self._transitions[e.id] = [FailureTransition(
                event_id=e.id, status="NEW",
                at=e.detected_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            )]
            added += 1
        return added

    def transition(self, event_id: str, status: str, reason: str = "", commit: str = "") -> None:
        """追加状态转换（校验合法性）。FIXED 可携带 FixCommit（commit hash）。"""
        if status not in VALID_STATUSES:
            raise ValueError(f"非法状态: {status!r}")
        last = self.current_status(event_id)
        if status not in STATUS_FLOW[last]:
            raise ValueError(f"非法转换: {last} → {status}")
        self._transitions[event_id].append(FailureTransition(
            event_id=event_id, status=status,
            at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            reason=reason, commit=commit,
        ))

    # ── 读取 ──

    def current_status(self, event_id: str) -> str:
        if event_id not in self._transitions:
            raise KeyError(event_id)
        return self._transitions[event_id][-1].status

    # ── FixCommit（约束 5：生命周期闭环） ──

    def fix_commits(self, event_id: str) -> List[str]:
        """所有 FIXED 状态对应的修复 commit（含多次回归后的多次修复）。"""
        return [t.commit for t in self._transitions.get(event_id, [])
                if t.status == "FIXED" and t.commit]

    def first_fix_commit(self, event_id: str) -> str:
        """首次修复 commit（REGRESSION 时用于定位谁重新引入）。"""
        commits = self.fix_commits(event_id)
        return commits[0] if commits else ""

    def resolve(self) -> List[dict]:
        """统一补全 root_cause + correction（SYMPTOM_MAP 唯一来源）。"""
        out = []
        for eid, e in self._events.items():
            m = SYMPTOM_MAP.get(e.symptom, SYMPTOM_MAP["unknown"])
            out.append({
                "id": eid,
                "benchmark": e.benchmark,
                "scenario": e.scenario,
                "layer": e.layer,
                "dimension": e.dimension,
                "failure": e.failure,
                "evidence": [ev.__dict__ for ev in e.evidence],
                "symptom": e.symptom,
                "root_cause": m["root_cause"],
                "correction": m["correction"],
                "status": self.current_status(eid),
                "detected_at": e.detected_at,
            })
        return out

    def query(self, benchmark=None, layer=None, dimension=None, status=None) -> List[dict]:
        """定位入口：Long Horizon FAIL → Fail Board → query(layer, dimension, status)。"""
        return [
            r for r in self.resolve()
            if (benchmark is None or r["benchmark"] == benchmark)
            and (layer is None or r["layer"] == layer)
            and (dimension is None or r["dimension"] == dimension)
            and (status is None or r["status"] == status)
        ]

    def by_layer(self) -> Dict[str, List[dict]]:
        grouped: Dict[str, List[dict]] = {}
        for r in self.resolve():
            grouped.setdefault(r["layer"], []).append(r)
        return grouped

    # ── 生命周期聚合（Bug Lifetime / Regression Count / Capability Stability） ──

    def aggregate(self) -> Dict[str, dict]:
        agg = {"bug_count": len(self._events), "by_status": {}, "by_layer": {},
               "regression_counts": {}, "transitions_total": 0}
        for eid, trs in self._transitions.items():
            st = trs[-1].status
            agg["by_status"][st] = agg["by_status"].get(st, 0) + 1
            agg["transitions_total"] += len(trs)
            rc = sum(1 for t in trs if t.status == "REGRESSION")
            if rc:
                agg["regression_counts"][eid] = rc
        for layer, items in self.by_layer().items():
            agg["by_layer"][layer] = len(items)
        return agg

    # ── 渲染 ──

    def render(self) -> str:
        lines = ["Fail Board v2 — Diagnostic Backbone", "=" * 78]
        for layer, items in self.by_layer().items():
            lines.append(f"\n[{layer.upper()}]")
            lines.append(f"  {'ID':28s} {'Status':10s} {'Symptom':20s} Failure")
            for r in items:
                lines.append(
                    f"  {r['id']:28s} {r['status']:10s} {r['symptom']:20s} {r['failure'][:48]}"
                )
                for ev in r["evidence"]:
                    lines.append(
                        f"      └ {ev['source']}:{ev['location']} "
                        f"expected={ev['expected']!r} actual={ev['actual']!r}"
                    )
        agg = self.aggregate()
        lines.append("── Aggregate ──")
        lines.append(f"  events={agg['bug_count']} status={agg['by_status']} "
                     f"layers={agg['by_layer']} regressions={agg['regression_counts']}")
        return "\n".join(lines)


@dataclass
class FailureTransition:
    """状态演进（append-only，Event Sourcing）。

    FIXED 时记录 commit（FixCommit）：REGRESSION 可关联首次修复 commit → 定位谁重新引入。
    """
    event_id: str
    status: str
    at: str
    reason: str = ""
    commit: str = ""   # FixCommit：FIXED 时的修复 commit hash

# ── 持久化（FailBoard 存为 JSON 资产） ──

FAILBOARD_PATH = os.path.join("evaluation", "failboard_v2.json")


def save(board: FailBoard, path: str = FAILBOARD_PATH) -> None:
    data = {
        "events": [{**e.__dict__, "id": e.id} for e in board._events.values()],
        "transitions": [
            {**t.__dict__} for trs in board._transitions.values() for t in trs
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def load(path: str = FAILBOARD_PATH) -> FailBoard:
    if not os.path.exists(path):
        return FailBoard()
    with open(path) as f:
        data = json.load(f)
    board = FailBoard()
    for ed in data.get("events", []):
        ed = dict(ed)
        ed.pop("id", None)
        evs = [Evidence(**e) for e in ed.pop("evidence", [])]
        board.collect([FailureEvent(evidence=evs, **ed)])
    for td in data.get("transitions", []):
        eid = td.get("event_id", "")
        if eid not in board._transitions:
            continue
        if len(board._transitions[eid]) == 1 and td.get("status") == "NEW":
            continue  # NEW 已在 collect 建立
        try:
            board.transition(eid, td["status"], td.get("reason", ""), td.get("commit", ""))
        except ValueError:
            pass
    return board


def main():
    board = load()
    print(board.render() if board._events else "Fail Board v2 为空（尚无 FailureEvent）。")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

