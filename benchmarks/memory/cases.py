"""Memory Fuzz cases — 程序化生成记忆测试用例（v2.1B-0）。

设计：
    - 每个 case 是独立的多轮对话（同一 user_id 内连续运行）。
    - text case 的 expected 是最终回答关键字；runtime_contract case 使用结构化 evidence。
    - 数据来自参数池，避免用例完全重复。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Existing positive Memory Fuzz cases receive domain-specific, case-local
# negative fixtures. A correct-abstention case should still provide its own
# negative example rather than relying on a global fallback.
_DEFAULT_NEGATIVE_EXAMPLES = {
    "fact": ["该事实没有记录。", "当前会话中没有这个信息。"],
    "conversation": ["该任务没有执行完成。", "没有找到上一条任务结果。"],
    "temporal": ["上一轮没有可用的答案。", "无法确定上一条结果。"],
    "interference": ["当前会话中没有匹配记录。", "没有找到对应的历史内容。"],
}


@dataclass
class MemoryCase:
    id: str
    group: str       # fact | conversation | temporal | interference | continuation
    sub: str         # 子类
    turns: List[str]  # 用户输入序列（同一会话）
    expected: "str | List[str]"  # text case 期望；runtime_contract 可为空
    note: str = ""
    continuation_contract: str = ""  # CONTINUE_PLAN / CONTINUE_CHAT / CONTINUE_REFERENCE
    expected_any_of: Optional[List[List[str]]] = None
    forbidden_any_of: Optional[List[List[str]]] = None
    positive_examples: Optional[List[str]] = None
    negative_examples: Optional[List[str]] = None
    validation_mode: str = "text"  # text | runtime_contract
    contract_expectations: Dict[str, Any] = field(default_factory=dict)
    metric_scope: str = "memory_recall"  # memory_recall | continuation

    def __post_init__(self) -> None:
        """Materialize case-local validation examples.

        ``None`` uses a domain-specific fixture; explicitly supplied examples
        are preserved for cases with a different validation policy.
        """
        if self.positive_examples is None:
            if self.expected_any_of is not None:
                terms = [
                    term
                    for group in self.expected_any_of
                    for term in ([group] if isinstance(group, str) else group)
                ]
            elif isinstance(self.expected, str):
                terms = [self.expected]
            else:
                terms = list(self.expected or [])
            self.positive_examples = [
                f"答案是 {term}。" for term in terms if str(term).strip()
            ]
        if self.negative_examples is None:
            self.negative_examples = list(
                _DEFAULT_NEGATIVE_EXAMPLES.get(
                    self.group, ["本轮没有可验证的结果。", "结果为空。"]
                )
            )


# ── 数据池 ──
CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安", "南京", "重庆"]
NAMES = ["张三", "李四", "王五", "赵六", "钱七", "孙八", "周九", "吴十", "郑十一", "陈十二"]
COLORS = ["蓝色", "红色", "绿色", "黄色", "紫色", "白色", "黑色", "橙色", "粉色", "灰色"]
LANGS = ["Rust", "Go", "Python", "Java", "C++", "TypeScript", "C", "Ruby", "Swift", "Kotlin"]
JOBS = ["工程师", "老师", "医生", "设计师", "产品经理"]
# ADR-0014：期望为同义词集合（快排=快速排序、素数=质数…），校验器任一命中即 PASS。
TASKS = [
    ("帮我写一个 Python 快排。", ["快排", "快速排序", "快速排序算法"]),
    ("帮我把 output/a.py 改成使用 argparse。", ["argparse"]),
    ("帮我写一个冒泡排序函数保存到 output/bubble.py。", ["冒泡", "bubble sort", "bubble_sort"]),
    ("帮我把 output/solution.py 重构一下。", ["重构", "refactor"]),
    ("帮我写一个读取 CSV 的脚本。", ["csv"]),
    ("帮我写一个二分查找函数。", ["二分", "binary search"]),
    ("帮我把代码改成大小写不敏感。", ["大小写", "case insensitive"]),
    ("帮我写一个判断素数的函数。", ["素数", "质数"]),
    ("帮我写一个统计词频的脚本。", ["词频", "word count"]),
    ("帮我写一个反转链表的函数。", ["反转链表", "reverse"]),
]
MATH_QA = [
    ("1+1是多少？", "2", "3+5是多少？", "8"),
    ("2+3是多少？", "5", "6+7是多少？", "13"),
    ("4*5是多少？", "20", "7*8是多少？", "56"),
    ("10-3是多少？", "7", "20-9是多少？", "11"),
    ("6/2是多少？", "3", "12/4是多少？", "3"),
    ("9*9是多少？", "81", "15-8是多少？", "7"),
    ("5+5是多少？", "10", "3*7是多少？", "21"),
    ("100-1是多少？", "99", "2*12是多少？", "24"),
    ("8/4是多少？", "2", "16/2是多少？", "8"),
    ("11+9是多少？", "20", "30-12是多少？", "18"),
]
STATE_REF = [
    ("把 output/a.py 改成全大写。", "全小写", ["小写", "lowercase"]),
    ("把 output/b.py 改成用空格缩进。", "用制表符缩进", ["制表符", "tab"]),
    ("把 output/c.py 改成英文注释。", "中文注释", ["中文", "chinese"]),
    ("把 output/d.py 改成用双引号。", "用单引号", ["单引号", "single quote"]),
    ("把 output/e.py 改成严格模式。", "宽松模式", ["宽松", "loose"]),
    ("把 output/f.py 改成同步写法。", "异步写法", ["异步", "async"]),
    ("把 output/g.py 改成明文存储。", "加密存储", ["加密", "encrypt"]),
    ("把 output/h.py 改成默认参数。", "必填参数", ["必填", "required"]),
    ("把 output/i.py 改成英文变量名。", "中文变量名", ["中文", "chinese"]),
    ("把 output/j.py 改成递归实现。", "迭代实现", ["迭代", "iterative"]),
]
FILLERS = [
    "今天天气怎么样？",
    "2+2等于几？",
    "介绍一下你自己。",
    "推荐一首歌。",
    "1+1等于几？",
    "什么是递归？",
]


def _fill_turns(prefix: List[str], n: int) -> List[str]:
    """在 prefix 之后插入 n 个无关填充轮。"""
    return prefix + [FILLERS[i % len(FILLERS)] for i in range(n)]


def _build_plan_resume_cases(n_per_sub: int = 10) -> List[MemoryCase]:
    """Build explicit plan-resume cases without a natural-language answer key."""
    cases = []
    for i in range(n_per_sub):
        cases.append(MemoryCase(
            id=f"cont-plan-{i:02d}", group="continuation", sub="plan_resume",
            turns=[
                f"帮我写一个{LANGS[i % len(LANGS)]}函数处理这个需求。",
                "需求是：读入一个整数并输出它的平方。",
                "继续执行未完成的任务",
            ],
            expected="",
            note="CONTINUE_PLAN：验证 Runtime plan 恢复与真实执行结果",
            continuation_contract="CONTINUE_PLAN",
            validation_mode="runtime_contract",
            contract_expectations={
                "intent": "continue_plan",
                "requires_execution": True,
                "progress_required": True,
                "verification_required": True,
            },
            positive_examples=[],
            negative_examples=[],
            metric_scope="continuation",
        ))
    return cases


_CHAT_RESUME_TOPICS = [
    ("解释一下 Python 列表推导式。", "列表推导式"),
    ("解释一下 HTTP 状态码。", "HTTP"),
    ("介绍一下数据库索引。", "数据库索引"),
    ("解释一下递归函数。", "递归"),
    ("介绍一下 Git 分支。", "Git"),
    ("解释一下 REST API。", "REST"),
    ("介绍一下向量数据库。", "向量数据库"),
    ("解释一下异常处理。", "异常处理"),
    ("介绍一下单元测试。", "单元测试"),
    ("解释一下消息队列。", "消息队列"),
]


_REFERENCE_RESUME_TARGETS = [
    ("请分析 output/ref0.py 中的 calculate 函数。", "继续刚才那个函数", "calculate"),
    ("请分析 output/ref1.py 中的 parse 函数。", "继续刚才那个函数", "parse"),
    ("请读取 output/ref2.py。", "继续刚才那个文件", "output/ref2.py"),
    ("请读取 output/ref3.py。", "继续刚才那个文件", "output/ref3.py"),
    ("请分析 output/ref4.py 中的 build 函数。", "继续刚才那个函数", "build"),
    ("请分析 output/ref5.py 中的 run 函数。", "继续刚才那个函数", "run"),
    ("请读取 output/ref6.py。", "继续刚才那个文件", "output/ref6.py"),
    ("请分析 output/ref7.py 中的 main 函数。", "继续刚才那个函数", "main"),
    ("请读取 output/ref8.py。", "继续刚才那个文件", "output/ref8.py"),
    ("请分析 output/ref9.py 中的 save 函数。", "继续刚才那个函数", "save"),
]


def build_continuation_cases(n_per_sub: int = 10) -> List[MemoryCase]:
    """Build the separate continuation benchmark (plan/chat/reference)."""
    cases = _build_plan_resume_cases(n_per_sub)
    for i, (prompt, anchor) in enumerate(_CHAT_RESUME_TOPICS[:n_per_sub]):
        cases.append(MemoryCase(
            id=f"cont-chat-{i:02d}", group="continuation", sub="chat_resume",
            turns=[prompt, "继续讲"],
            expected="",
            note="CONTINUE_CHAT：验证回答延续 last_answer",
            continuation_contract="CONTINUE_CHAT",
            validation_mode="runtime_contract",
            contract_expectations={
                "intent": "continue_chat",
                "requires_execution": False,
                "last_answer_required": True,
                "answer_anchor": anchor,
            },
            positive_examples=[],
            negative_examples=[],
            metric_scope="continuation",
        ))
    for i, (prompt, follow_up, target) in enumerate(_REFERENCE_RESUME_TARGETS[:n_per_sub]):
        cases.append(MemoryCase(
            id=f"cont-ref-{i:02d}", group="continuation", sub="reference_resume",
            turns=[prompt, follow_up],
            expected="",
            note="CONTINUE_REFERENCE：验证 Resolver 目标映射/澄清",
            continuation_contract="CONTINUE_REFERENCE",
            validation_mode="runtime_contract",
            contract_expectations={
                "intent": "continue_reference",
                "requires_execution": False,
                "reference_target": target,
                "clarify_on_conflict": True,
            },
            positive_examples=[],
            negative_examples=[],
            metric_scope="continuation",
        ))
    return cases


def build_cases(n_per_sub: int = 10, fill_turns: int = 4) -> List[MemoryCase]:
    """生成测试用例。

    Args:
        n_per_sub: 每个子类生成的用例数。
        fill_turns: interference/long_context 等使用的填充轮数。
    """
    cases: List[MemoryCase] = []

    # ── fact / single_fact ──
    for i in range(n_per_sub):
        city = CITIES[i % len(CITIES)]
        cases.append(MemoryCase(
            id=f"fact-single-{i:02d}", group="fact", sub="single_fact",
            turns=[f"我住在{city}。", "我住哪里？"], expected=city,
            note="单事实召回",
        ))
    # fact / multi_fact
    for i in range(n_per_sub):
        name = NAMES[i % len(NAMES)]
        city = CITIES[(i + 3) % len(CITIES)]
        color = COLORS[i % len(COLORS)]
        setup = f"我叫{name}，住在{city}，喜欢{color}。"
        cases.append(MemoryCase(
            id=f"fact-multi-{i:02d}", group="fact", sub="multi_fact",
            turns=[setup, f"我叫什么名字？"], expected=name, note="多事实-名字",
        ))
        cases.append(MemoryCase(
            id=f"fact-multi-city-{i:02d}", group="fact", sub="multi_fact",
            turns=[setup, f"我住在哪个城市？"], expected=city, note="多事实-城市",
        ))
    # fact / conflict（latest wins）
    for i in range(n_per_sub):
        c1 = COLORS[i % len(COLORS)]
        c2 = COLORS[(i + 1) % len(COLORS)]
        cases.append(MemoryCase(
            id=f"fact-conflict-{i:02d}", group="fact", sub="conflict",
            turns=[f"我喜欢{c1}。", f"我喜欢{c2}。", "我喜欢什么颜色？"],
            expected=c2, note=f"冲突覆盖 {c1}->{c2}，latest wins",
        ))

    # ── conversation / recent_goal / previous_instruction ──
    for i in range(n_per_sub):
        task, kw = TASKS[i % len(TASKS)]
        cases.append(MemoryCase(
            id=f"conv-goal-{i:02d}", group="conversation", sub="recent_goal",
            turns=_fill_turns([task], fill_turns) + ["刚才让我做什么？"],
            expected=kw, note="近期目标召回",
        ))
    for i in range(n_per_sub):
        task, kw = TASKS[i % len(TASKS)]
        cases.append(MemoryCase(
            id=f"conv-prev-{i:02d}", group="conversation", sub="previous_instruction",
            turns=_fill_turns([task], fill_turns) + ["我刚才让你做什么？"],
            expected=kw, note="上一条指令召回",
        ))
    # Keep the original 130-case envelope stable, but make the former
    # unfinished_task cases structured continuation cases. Recall metrics must
    # exclude group=continuation; the dedicated continuation benchmark adds
    # chat/reference siblings via build_continuation_cases().
    cases.extend(_build_plan_resume_cases(n_per_sub))

    # ── temporal ──
    for i in range(n_per_sub):
        q1, a1, q2, a2 = MATH_QA[i % len(MATH_QA)]
        cases.append(MemoryCase(
            id=f"temp-ans-{i:02d}", group="temporal", sub="answer_reference",
            turns=[q1, q2, "刚才答案是多少？"], expected=a2, note="答案指代（latest）",
        ))
    for i in range(n_per_sub):
        cases.append(MemoryCase(
            id=f"temp-act-{i:02d}", group="temporal", sub="action_reference",
            turns=[f"把 hello 保存到 output/x{i}.py。", f"把 world 保存到 output/y{i}.py。",
                   "刚才让我做什么？"],
            expected=f"y{i}", note="动作指代（latest 目标）",
        ))
    for i in range(n_per_sub):
        s1, s2, kw = STATE_REF[i % len(STATE_REF)]
        cases.append(MemoryCase(
            id=f"temp-state-{i:02d}", group="temporal", sub="state_reference",
            turns=[s1, s2, "我刚才让你怎么改？"],
            expected=kw, note="状态指代（latest 状态）",
        ))

    # ── interference ──
    for i in range(n_per_sub):
        name = NAMES[i % len(NAMES)]
        cases.append(MemoryCase(
            id=f"int-long-{i:02d}", group="interference", sub="long_context",
            turns=_fill_turns([f"我叫{name}。"], fill_turns) + ["我叫什么？"],
            expected=name, note="长上下文中的事实",
        ))
    for i in range(n_per_sub):
        city = CITIES[i % len(CITIES)]
        cases.append(MemoryCase(
            id=f"int-irel-{i:02d}", group="interference", sub="irrelevant_turns",
            turns=_fill_turns([f"我住在{city}。"], fill_turns) + ["我住哪里？"],
            expected=city, note="无关轮次干扰",
        ))
    for i in range(n_per_sub):
        c_mine = COLORS[i % len(COLORS)]
        c_other = COLORS[(i + 2) % len(COLORS)]
        cases.append(MemoryCase(
            id=f"int-collide-{i:02d}", group="interference", sub="similar_fact_collision",
            turns=[f"我喜欢{c_mine}。", f"我妹妹喜欢{c_other}。", "我喜欢什么颜色？"],
            expected=c_mine, note="相似事实碰撞（须消歧到'我'）",
        ))

    return cases


def summarize(cases: List[MemoryCase]) -> dict:
    from collections import Counter
    return Counter((c.group, c.sub) for c in cases)
