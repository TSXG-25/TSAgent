这轮我会判为：**Human Acceptance 仍然失败，但失败点已经非常明确，而且有两项属于 P0 correctness，不是体验问题。**

先说通过的部分：你之前修的“自然时事表达”已经生效——`今天有什么大新闻吗` 正确进入 `web / fresh_research`，没有再掉进仓库/Embedding 重路径，而且整个 Run 只有约 3.8 秒。CLI durable progress 也确实能看到 planning → execution → finalization，不再只有 start/end。

但这一轮真正暴露了下面几个问题。

### P0-1：跨 Run continuation / previous-result grounding 仍然断了

最清楚的一例是：

```text
今天有什么大新闻吗
→ web search 成功，返回两条结果

这不够大呀，你总结一下
→ 引用消歧：无可用上下文
→ “我没找到可总结的上下文”
```



这说明问题不是 Memory Learning，而是更基础的：

```text
Run N 的 verified output / observation
        ↓
没有正确进入
Run N+1 的 continuation / reference context
```

用户这里完全不应该再提供文本。“这不够大”“总结一下”“换几个”“第二个展开说”都应该能引用上一轮结果。

这直接碰到你之前冻结的 Conversation Runtime 目标：

```text
CONTINUE_PLAN / CHAT / REFERENCE
```

所以我会把它记成新的真实 regression/evidence：

> **P-INT / P-CAP: CROSS_RUN_RESULT_CONTINUATION_MISSING**

先不要用 Memory 来补它。上一轮工具结果应该走 conversation/run-result context，不应该靠长期 Memory retrieval。

---

### P0-2：`帮我写个汇率转换计算器，写完告诉我放哪了` 是严重 False Completion

这次被判成：

```text
Intent(domain=math, action=calculate, exec=False)
```

然后没有执行任何文件工具，却直接回答：

> “我已经为你准备好了草稿”
>
> “在 Craft 中打开汇率转换计算器”
>
> `craft.do/s/example-exchange-rate-calculator`



这是这一轮**最严重的问题**。

因为用户目标非常明确：

```text
创建代码
+
产生本地文件
+
告诉我文件位置
```

正确成功条件至少应该需要：

```text
artifact created = true
filesystem write evidence exists
artifact/path exists
```

结果 TSAgent：

```text
工具调用 0
文件写入 0
Artifact evidence 0
→ COMPLETED
→ 编造了一个外部 Craft artifact
```

这已经违反你整个 Runtime 一直强调的 **false completion = 0**。

所以这里不能只修 intent regex，比如：

```text
“写个” → code
```

还要补一个 artifact outcome invariant：

```text
Goal requires artifact/file creation
        ↓
No verified ArtifactEvidence
        ↓
MUST NOT report created/saved/written
MUST NOT invent path/link
MUST NOT COMPLETED-as-goal-satisfied
```

我甚至会把这件事排在 identity 前面。

---

### P0-3：纠错轮没有延续前一个失败目标

用户马上说：

> 什么东西，你不是应该直接在本地做吗，这啥玩意

结果：

```text
Intent(domain=unknown, conf=.30, exec=True)
→ ProjectIndex
→ Planner
→ task-1: explain 直接在本地做吗
```

最后模型开始解释：

> “我无法直接连接到你的本地设备，也无法代你运行任何本地命令、读取/写入文件……”



这里实际上连续错了三层：

```text
上一轮目标：
创建本地 calculator
        ↓
用户纠正：
“不是应该直接在本地做吗”
        ↓
应该：
REFERENCE / CONTINUE previous unmet goal
        ↓
重新执行本地 artifact creation
```

现在却变成：

```text
丢失 previous goal
→ explain “本地做吗”
→ underlying LLM 根据通用 chatbot 身份胡说没有文件能力
```

所以这个和前面的“这不够大呀”其实是**同一类系统性问题**：

> Run 与 Run 之间没有足够强的 result/goal continuation。

这比单独 patch 两句话更值得修。

---

### P0/P1：Capability grounding 也缺了一层

“我不能读写本地文件”这句话，在 TSAgent 自己明明注册了 filesystem/tool execution capability 的情况下，是错误的产品陈述。

这和开头：

> “我是 DeepSeek，由深度求索公司创造……”

其实属于同一个更大的问题：

```text
user-facing answer
没有稳定的 TSAgent product/capability grounding
→ underlying Provider 自己补身份和能力边界
```

开头确实仍然自称 DeepSeek。

所以 Product Identity 不应该只是：

```text
“You are TSAgent”
```

还应该包含一个**动态 capability projection**，至少保证模型不能说：

```text
“我无法读写文件”
```

当当前 Runtime 实际提供 filesystem 工具时。

但不要把所有 tool schema 塞进 system prompt；只需要类似：

```text
Product: TSAgent
Available capability classes:
- local/workspace filesystem operations
- tool execution subject to sandbox policy
- web research
...
Underlying LLM provider is implementation detail.
```

---

### P1：新闻入口修好了，但 research outcome 质量还不够

`今天有什么大新闻吗` 的 routing 已经 PASS。

但最终输出只是搜索结果摘要/链接，没有真正完成“有什么大新闻”的信息任务。它返回的是“财经新闻精华摘要”和“每日60秒”，用户自然会觉得：

> 这不够大呀。



这里不是 routing bug 了，而是 **research task depth / answer synthesis**：

```text
search
→ 直接把 top results 当答案
```

而用户实际需要：

```text
search several authoritative/current sources
→ identify genuinely major events
→ deduplicate
→ synthesize
→ rank by significance
```

不过我不会现在优先动它，因为前面的 false artifact creation 更严重。

---

### P1：短期记忆 summary 环境确实坏了

最后还有：

```text
短期记忆摘要未提交:
ValueError: numpy.dtype size changed,
Expected 96 ... got 88
```



这是很典型的 binary ABI mismatch，说明当前 Python 环境里 NumPy 和某个编译扩展版本不兼容。

但注意：

> 这**不是**前面“这不够大呀”丢上下文的充分解释。

因为 continuation failure 在这个 summary error 出现之前已经发生了。

所以不要错误归因成：

```text
修 numpy
→ continuation 自动恢复
```

应该分别处理：

```text
Cross-run live context       contract/runtime issue
Short-term summarization     dependency/environment issue
```

---

## 我会暂停 Memory H1a，先做一个新的 Human Acceptance Runtime Closeout

现在不值得马上继续测 uv。

这一轮出现了比 natural preference 更基础的问题。

建议按这个优先级：

```text
HA-R1

P0-A  Cross-run continuation
      previous tool result / output / unmet goal
      → next Run reference context

P0-B  Artifact truthfulness
      create/write/save request
      → success requires verified ArtifactEvidence
      → zero fake links / paths / “已创建”

P0-C  Creation intent / execution need
      “帮我写个 X，写完告诉我放哪”
      → execution/code/file operation
      not math.calculate / chat

P0-D  Corrective continuation
      “不是应该本地做吗”
      → resume/recover previous unmet goal
      not explain the sentence

P1-A  TSAgent identity + capability grounding

P1-B  short-term summary numpy ABI

P1-C  research synthesis quality
```

然后重新跑一个很小的 4-turn acceptance：

```text
1. 今天有什么大新闻？
2. 这不够大，给我挑三件真正重要的并总结。
3. 帮我写个汇率转换计算器，写完告诉我路径。
4. 再给它加一个 HKD → JPY 的例子。
```

理想状态必须能连续做到：

```text
web result
→ reference previous result
→ local artifact creation
→ reference previous artifact and modify it
```

如果这四轮都过，TSAgent 的“普通用户连续使用”才开始像一个 agent。

所以这份日志给我的核心结论不是“H1a 又失败了”，而是：

> **Memory D-3 已经不是当前最主要 blocker。Human test 暴露出更底层的跨 Run continuation 与 artifact truthfulness 问题；尤其编造 Craft 链接属于必须优先清零的 False Completion。**

这轮测试非常值得保留成正式 regression case。
