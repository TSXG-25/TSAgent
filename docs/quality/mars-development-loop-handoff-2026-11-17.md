# Mars 开发闭环 — 实施交接（MC-01..06）

日期：2026-11-17。配套计划：`mars-development-loop-plan-2026-11-17.md`。

**状态：代码与离线门禁完成；真实任务评测 R1–R3 已跑（§3）。** 剩余未覆盖项见 §4，
离线测试结果不构成真实环境验证结论。

## 1. 交付内容（按工单）

### MC-01 固定失败用例（已完成）

`tests/test_mars_devloop.py` — 三个固定 fixture（修 bug / 小功能 / 跨文件重构），
每个都是真实 git 仓库（基线提交 + 任务前未提交修改 + 未跟踪文件），经真实
`AgentLoop` + 真实工具面（`shell` 走 workspace 作用域沙箱，`edit_file`/
`write_file` 走 `WorkspaceService`）驱动，Provider 为脚本化 `ChatTurn`。

记录合同：验收结果、人工介入次数（0）、工具错误、复测次数、耗时、全部命令结果
（含首个断点的真实报错尾部）。断言锁定完整闭环：首次验证失败 → 失败输出原样回传
模型 → 文件修改 → 受影响测试复测通过 → 验收函数核对真实文件。

发现并修复的测试自身缺陷：脚本化轮次的 `calls[:-1]` 切片丢弃了复测轮（已修）；
同尺寸同秒 mtime 的旧 pyc 导致复测偶发读到旧字节码（`python -B` 消除，双沙箱路径
各两轮验证确定性）。

### MC-02 自主验证授权对齐（已完成）

- `agent/cognition/effect_authorization.py`（前轮 `8711eb62`）：`requested_outcomes`
  保持“用户声明”语义（`validate_plan` 的计划要求不变），`command_execution_allowed`
  在授予层由 FILE_MUTATION 推定，`prohibited_effects` 显式禁止仍优先。
- `skills/coding/SKILL.md`：旧的“只有用户明确要求才调用 shell”与验证合同矛盾，改为
  “改代码后应运行项目已有测试/构建；装依赖、git 提交/推送等不可逆操作先获确认”。
- `agent/loop/prompt.py`：在通用规则区补同一条确认边界（不加载 coding skill 的 run
  也受约束）。
- 未新增权限档位；shell 仍为 R0 无审批门，确认边界当前在指令层——这是与既有模式
  一致的取舍，命令级分类属于后续工作（需要有证据再动）。

### MC-03 失败→修正→复测链路（已完成，无 runner 缺口）

MC-01 三个 fixture 证明：工具失败作为普通 observation 进入模型消息流
（`ERROR[code]: ...` + 完整 stderr），模型随后修改、复测通过。普通失败未升级为
结构性恢复路径，无需补丁。

### MC-04 任务改动预览与验证记录（已完成）

- `agent/workspace_diff.py`（新）：基线快照（sha256 + 限量文本保留）→ 终态快照 →
  归因分类。`agent_files`（基线干净、本轮变更）/`ambiguous_files`（基线已脏又改动，
  明确标“无法归因”）/`user_dirty_preserved`（任务前修改原样保留）。unified diff 由
  difflib 产出，git 仅用于 `status --porcelain` 判脏；不 init/stash/reset/commit。
  上限：20000 文件、8MB 哈希上限、64KB/文件与 4MB 总量文本保留、diff 40 文件/20KB。
- `agent/loop/runner.py`：`_run_impl` 授权判定后、首个工具前捕获基线；仅
  FILE_MUTATION/COMMAND_EXECUTION 授权的 run 付快照成本。
- `agent/runtime.py`：终态时 `workspace_changes` 并入 `last_run_evidence`；
  `action_observations` 透传步骤 `input`（shell 的 `cmd` 等）。
- `agent/orchestrator/executor.py`：inbox 步骤记录命令参数摘要。
- `agent/service/runtime_launcher.py`：`workspace_changes` 进终态事件
  `continuation` 与中断 `terminal_payload`。
- `apps/desktop/src/app/WorkspaceThread.tsx`：`RunEvidence` 卡片——按归因分组的
  文件清单（新增/修改/删除/无法归因徽标）、可折叠 diff、验证命令通过/失败徽标 +
  失败输出尾部。失败永不渲染成功徽标（徽标直接由 `result.ok` 决定）。
- 已知限制：真实崩溃（`BaseException` 路径）不携带 workspace_changes；非 Loop
  路径（planner/workflow 执行器）的 run 不捕获基线，证据按“缺失”处理而非伪空。

修复的实现缺陷：`getattr(root, "root", root)` 对 `Path` 返回锚点 `/`（曾导致全
文件系统扫描），改为按类型分派；`UniversalAgent.__new__` 手工构造的测试对象缺
`_workspace`，按仓库既有测试模式补容错。

### MC-05 Mars 前端交互回归（已完成）

`apps/desktop/tests/dom/workspaceRail.test.tsx`（vitest + jsdom +
@testing-library/react，沿用既有 `tests/dom/` 基建与 `MockAgentServiceClient`）：

- 首页发送 → 新建本台记录会话 → 接纳后落在其 thread；
- 发送失败 → 草稿、当前会话、首页视图全部保留；
- 点击 RECENT 记录 → 显式进 thread（含当前记录重开）；
- 迟到的接纳响应 → 草稿只在落地后清除；
- 隔离 → mars 发送恢复 Omni 全局选中 run，且只写 mars 选中槽；
- `WorkspaceThread` 证据渲染：归因文件清单 + diff + 验证命令成败徽标。

### MC-06 离线门禁（已完成）

| 门禁 | 结果 |
| --- | --- |
| Python 全量（屏蔽 provider key，ollama 环境） | 983 passed, 17 skipped（DeepSeek 不可达项）；含评测修复回归与 Bad Apple 否定语境用例 |
| `tests/test_mars_devloop.py` | 4/4（seatbelt 与默认沙箱路径各两轮） |
| desktop `tsc` | 干净（src + tests 双 tsconfig） |
| desktop `vitest run` | 21 文件 147/147 |
| desktop `npm run build` | 通过（chunk 体积警告为既有状况） |
| cargo test（src-tauri） | 前轮 3/3，本轮未改动 Rust |

## 2. 真实任务评测候选（交接给独立评测）

三个固定候选与离线 fixture 同源但物化为**真实磁盘项目 + 真实模型 + 真实
sidecar/Desktop**。评测者自行选择 Provider（建议 `TSAGENT_LLM_PROVIDER=ollama`
`qwen3:8b`；需 DeepSeek 时单独注明）。

| 候选 | 项目与修订 | 确切请求 | 验收命令 | 允许操作 |
| --- | --- | --- | --- | --- |
| R1 修 bug | 将 `FIX_BUG` fixture（`test_mars_devloop.py`）物化到独立目录并 git init；基线提交含错误 `subtract` | “`calc.subtract` 结果错误：`subtract(3, 5)` 应为 -2。修复实现并用 `python -m pytest -q` 验证。” | `python -m pytest -q` 全绿 | 编辑文件、运行已有测试；禁止装依赖/git 写操作 |
| R2 小功能 | `SMALL_FEATURE` fixture 同上物化 | “给 `greet.py` 增加 `shout(name)`，返回问候的大写形式；先写测试再实现，并用 `python -m pytest -q` 验证。” | 同上 | 同上 |
| R3 跨文件重构 | `REFACTOR` fixture 同上物化 | “把 `util.trim` 重命名为 `util.normalize`，更新全部引用，并用 `python -m pytest -q` 确认通过。” | 同上 | 同上 |

每个候选的评测要点：

1. Mars 绑定该目录后发任务；核对请求记录不含 IDE 前缀正文泄漏。
2. 运行中观察：是否自行读文件、运行测试、按报错修改、复测。
3. 终态核对：验收命令实际结果、`workspace_changes` 归因（任务前的未提交修改与
   未跟踪文件必须原样保留并如实标注）、证据卡片与真实文件一致。
4. 结果分四类记录：**Runtime contract**（终态/事件/证据是否符合合同）/
   **Capability**（任务本身是否完成）/**Provider**（模型调用错误、超时、配额）/
   **Environment**（网络、依赖、沙箱不可用）。普通测试失败不得记为进程故障；
   失败 run 不得显示成功徽标。

## 3. 真实评测结果（R1–R3，2026-11-18）

驱动：`scripts/real_eval_mars.py` — 真实 `local_sidecar`（与 Tauri 相同的
stdio JSONL 线协议）+ 真实模型 `ollama/qwen3:8b` + 独立 git 项目目录
`~/Desktop/tsagent-real-eval/r{1,2,3}-*`。报告归档
`realtest_reports/mars-devloop-20261118-14*/report.json`（含 eval.db 事件库）。

| 候选 | 终态 | 验收 | 分类 | 证据 |
| --- | --- | --- | --- | --- |
| R1 修 bug | COMPLETED | ✅ | **capability_met** | 模型先 `EDIT_MATCH_NOT_UNIQUE`（edit 片段不唯一）再 `COMMAND_NONZERO_EXIT`（猜错测试文件名 `calc_test.py`），两个真实工具错误都在 loop 内自行恢复；pytest 最终通过；用户文件全保留；`workspace_changes` 归因正确 |
| R2 小功能 | FAILED_TERMINAL | ✅（文件含 shout） | **capability_gap_honest_refusal** | 模型用 `run_python`（非 shell）验证且两次 `ARTIFACT_OUTPUT_UNVERIFIED`，从未执行 pytest；意图声明了 `CODE_EXECUTION`，goal 校验 `missing:["outcome:CODE_EXECUTION"]` → 如实拒绝未验证的成功声明。**失败 run 同样携带 workspace_changes + diff** |
| R3 跨文件重构 | COMPLETED | ❌（引用未更新） | **capability_gap（含声明层缺口）** | 模型只改 `util.py`，未更新 `app.py`/`test_util.py` 引用、从未跑 pytest 却宣告完成；意图只声明 `FILE_MUTATION`（尽管请求明写"并用 pytest 确认"），校验器无可强制项 → `missing:[]` 放行 |
| R3 修复后复测（qwen3:8b） | FAILED_TERMINAL | ❌ | **capability_gap_honest_refusal** | 修复生效：`missing` 出现 `outcome:COMMAND_EXECUTION`+`outcome:FILE_MUTATION`，门禁正确拦截；但模型幻觉无写权限（"请确认授权"重复输出），连 `filesystem.edit` 都未调——合同语义对了，瓶颈是纯模型能力 |
| R3 qwen3:8b #3（误标为 deepseek，harness 强制 provider） | FAILED_TERMINAL | ❌ | **capability_gap_honest_refusal（另有授权真 bug）** | 模型路径全对（改名→跑测试→读失败文件→尝试改它），但 `edit(test_util.py)` 被 `EFFECT_SCOPE_VIOLATION` 拒：意图 `write_targets=["util.py"]` 把"更新全部引用"收窄成单文件 |
| R3 qwen3:8b #4（误标） | 未到终态（1800s） | 工作区通过 | **deadline_exceeded** | scope 放宽后三个文件全部改成 `agent_files`；最终验证 pytest 被模型误填 `output_paths` 判 `ARTIFACT_OUTPUT_UNVERIFIED`，重试至超时——但磁盘上 pytest 已全绿 |
| R3 qwen3:8b #5（误标） | FAILED_TERMINAL | ❌（import 改了调用点没改） | **capability_gap_honest_refusal** | 工具链全程正确（scope、证据、门禁、用户文件保留）；停滞点定位：模型自述"测试文件仍用旧 trim"却连续 4 次重发同一 pytest 调用，从不发 edit——诊断说对了但不落成工具调用 |
| **R3 deepseek-flash（真实）** | **COMPLETED** | ✅ | **capability_met** | 62.5s 一次通过：改名→grep 清查全部 trim 引用（含 .repo_index/用户文件）→修调用点→`python -m pytest -q` 通过；`goal_missing:[]`，用户文件全保留 |

### 真实评测暴露的问题与修复（按层归因）

1. **声明层缺口（真实，已修）**：验证强制力完全依赖意图分类的
   `requested_outcomes`——同一句式"并用 pytest 验证"在 R2 被声明、R3 没有。
   修复：`EffectAuthorization.from_state` 增加 `_VERIFICATION_DEMAND`
   确定性修复——请求文本含明确的运行/验证指令（真实的工具调用形态如
   `python -m pytest -q`、`npm test`、`./build.sh`，或"跑一下测试"类
   动词+目标组合）且已声明了非纯聊天 outcome 时，补入
   `COMMAND_EXECUTION`；纯聊天 outcome 集合不注入（"解释一下 pytest"
   不会被升级成执行要求），`prohibited_effects` 仍然优先。
   配套：`execution_truth` 的执行类 outcome 改为等价满足——
   shell 验证证据可满足 `CODE_EXECUTION` 声明，反之亦然（验证发生即可，
   不限产生者）。回归测试见 `test_mars_devloop.py` 尾部与
   `test_h4c_execution_evidence.py` 尾部。
2. **能力缺口（真实模型，部分缓解）**：`run_python` 被当验证工具使用且参数
   误用（`ARTIFACT_OUTPUT_UNVERIFIED`×2）；`shell` 可用却未被选。
   修复：`prompt.py` 的验证规则点名 `shell` 运行项目测试/构建命令，
   并说明 `run_python` 只适合小段临时代码；`output_paths` 的 schema
   描述与 prompt 都写明"验证步骤不许填"。剩余风险是模型本身的选择能力。
3. **写范围收窄（真实，已修）**：实跑 #3（qwen3，曾误标 deepseek）证明 `write_targets` 会把
   "更新全部引用"收窄成单文件（`EFFECT_SCOPE_VIOLATION` 拒绝合法的调用点
   修改）。**修复路径经评审后改为源头修正**：不在 `from_state` 做关键词放宽
   ——无条件关键词命中会把"只改 util.py，不要更新全部引用"这类显式限定
   反转成全工作区授权（P1）。改为收紧 intent rule 11：`write_targets` 仅在
   用户明确限定范围时填写，`[]` 语义为授权整个工作区，跨文件请求不得填
   推断目标。残余风险：分类器仍错填窄范围时回退为安全方向的诚实失败
   （合法编辑被拒、模型需请用户确认），而非静默放权。回归用例见
   `test_mars_devloop.py`：否定限定保留窄范围 ×2、空声明→工作区默认。
4. **停滞形态（真实，已修）**：实跑 #5（qwen3）的停滞不是工具没接通——模型
   自述正确诊断却连续重发同一失败调用。修复：runner 对"同一工具+参数连续
   失败且期间无成功修改"注入运行时提示（第 2 次起），prompt 增加"报错指向
   未完成修改时必须调 edit"规则；`_run_fixture` 有首次不提示、二次提示的
   回归用例。
5. **证据噪声（已修）**：`.pytest_cache/*` 曾计入 agent_files →
   `_SKIP_DIRS` 已扩常见生成目录。`.file_symbols.json`/
   `.symbol_index.json` 由 `RepositoryIndexer` 写入用户项目根 →
   已挪进 `.repo_index/`（与 vector store 同目录），旧位置的既有文件
   在索引器加载时一次性迁移，不留兼容读路径。
6. **超时观察**："交接记忆写入超时"在各 run 都出现（非阻断，未修）。

### 结论口径

- Runtime contract：3/3 终态事件结构完整，`workspace_changes` 100% 落地
  （含失败 run），归因与用户文件保留正确。
- Capability：1/3 完成验收（R1）；R2 文件改对但未验证（被合同拦下）；R3
  半重构未验证被放行（声明层缺口）。
- Provider：qwen3:8b 单 run 140–2160s，止步半重构（能力上限）；
  **deepseek-flash 真实 R3 一次通过（62.5s，含 grep 引用清查与 pytest 验证）**。
  ⚠️ 中间三轮标称 deepseek 的 run 实为 qwen3:8b——评测 harness 在
  sidecar spawn 时强制 `TSAGENT_LLM_PROVIDER=ollama`，已修并加
  provider 字段校验（report.json 的 provider/model 现按实际值记录）。
- Environment：无环境失败。

## 4. 未覆盖项（如实声明）

- 真实 Tauri/WebView 交互验收未做（前端证据为 jsdom DOM 测试；真实 run 走了
  与 Tauri 相同的 sidecar JSONL 线协议，但未经桌面壳）。
- 装依赖/git 写操作的确认边界目前只在指令层，无命令级强制；真实模型不遵守指令时
  的表现未评测（候选均未触发需确认操作）。
- 大型工作区（>20000 文件）的 diff 证据按“不可用”记录，未实测性能开销。
- 中断 run 的 `workspace_changes` 通道已接好但 `Runtime` 中断路径暂未填值（部分
  结果以 `partial_execution` 为准）。
- 停滞提示（§3.4）只经离线断言验证；deepseek 一次通过未触发停滞，qwen3
  停滞形态的真实收敛效果仍待观察。

## 5. Omni→Mars 委派接通（本次新增）

### 链路

Omni 显式选 `@Mars` → `delegateFromOmni`（`apps/desktop/src/app/delegate.ts`）
→ `delegate_run` RPC → `_provision_delegation`（子 Task + Handoff + 继承工作区）
→ `start_run`（子 run 落在 Omni 会话内，`origin_planet=mars`，`delegation_depth=1`）。
与 Neptune 的 `delegateToPlanet` 同一条链，无第二路径。

### 合同改动

- `DelegateRunRequest.parent_task_id`：Omni 首发没有父 Run 时的任务锚点；
  `parent_run_id`（活跃 run 锚点）与 `parent_task_id` 二选一，全空报错。
- `start_run` admission：带 provisioned `task_id` 的 run 启动时按 task 回填
  handoff `target_run_id`（此前 UI 委派链没人回填，spawn/graph 路径是手动的）。
- `mockAgentService`/`localAgentServiceClient`/wire fixture 同步。

### UI

- `GlobalChat`：`@planet` 发送走 `delegateFromOmni`，成功后选中子 run 让
  Omni 线程直接看到 Mars 执行；草稿只在受理后清空。
- Omni 子任务卡（`chat-delegated`）：`listHandoffs` 轮询 → 状态徽标 +
  结果摘要 + 选中链接（选中即进入 Mars 的执行视角）。
- Mars 端 `WorkspaceTasks` delegated 区复用既有逻辑展示同一 task/handoff。
- i18n：`chat.target`、`chat.delegated*`、`chat.enterPlanet`。

### 离线证据（非真实场景验证）

- `tests/test_omni_mars_delegation.py`（2 例）：createTask→delegate_run→
  start_run 链、继承 workspace_root 下的真实文件编辑+pytest、task 树/
  handoff 回填/双侧可见性断言。
- `apps/desktop/tests/dom/omniDelegation.test.tsx`（2 例）：`@mars` 发送走
  委派链而非扁平 run（断言 parentTaskId/delegation_depth）；委派卡片渲染
  状态/摘要/进入链接。
- 全量：`989 passed / 17 skipped`（DeepSeek API 不可达跳过，符合离线口径）；
  vitest `22 files / 149 passed`；`tsc`+build 干净。

### 未覆盖

- 真实桌面壳（Tauri）内的 Omni→Mars 人工验收未做。
- `spawn_subtask`（模型侧阻塞委派）未接入 Omni 的 `@` 路径——本阶段只做
  显式选择，自动识别/任务拆解/多工作台编排均未做。

### 审查修复（eb8d368 之后）

- **进入 Mars 定位完整**：原实现只 `navigate` 不切作用域，Mars 侧栏/IDE
  仍指向自己的旧会话。现在点击同时 `selectSessionFor(planet,
  run.sessionId)`（工作台 rail 与绑定项目随会话走）+ `selectRun` + 导航。
- **委派归属不随全局选中漂移**：`ctx.activeRun` 是跨作用域选中，切换
  Omni 会话不会清除。`delegateFromOmni` 只把"当前会话内的 activeRun"
  当作继续锚点；其余一律在 `ctx.sessionId` 下新建 Omni 父任务——明确
  区分"本会话新委派"与"基于选中任务继续"。
- **委派卡片作用域隔离**：`listHandoffs` 轮询沿用 WorkspaceHost 的
  live-scope 模式——切会话即清空、迟到响应按 ref 丢弃、失败清空而非
  保留旧快照；run 解析限定 `sessionId`，跨作用域目录里的同名 run 不再
  被旧卡片匹配。
- 新增 3 个 DOM 用例覆盖上述场景（跨会话锚点拒绝、同会话继续锚点、
  进入 Mars 三步定位、迟到响应丢弃）。

### 口径说明

Omni 的"回来汇报"目前 = Omni 展示子 Run 的进度/结果卡片，父侧没有模型
继续消费结果（`spawn_subtask` 那条阻塞回传链未接进 `@` 路径）。本轮
显式 `@Mars` 范围按此交付；若要"Omni 拿到结果后自动续写"是下一步的
独立决策。

### 第二轮审查修复

- **工作台选择记录**：`ctx.selectRun` 只写全局选中；Mars 的
  `useWorkspaceCurrentRun` 读独立的 `selectWorkspaceRun` 记录
  （tenant/session/planet 键），缺省回退同会话最新 run——同会话两个
  Mars 子任务时点旧任务"进入 Mars"会把状态/暂停/取消指向新任务。
  进入时同步写该记录（DOM 断言 `getWorkspaceRunId` 指向被点击的旧 run）。
- **序号判定替代会话串比较**：`handoffScope` 只比 sessionId，
  A→B→A 逆序返回或同会话轮询重叠时旧响应可覆盖新卡片/旧失败可清空。
  改 `handoffSeq` 请求序号（searchSession 同款）——只有最新请求可写。
  补 A→B→A 逆序用例：新响应先落地，旧响应后到被丢弃。

## 6. 工作台委派链通用化（Mercury 接入）

Omni 修完的链这次接到了所有工作台发送面——`@planet` 跨台发送不再
是"打 originPlanet 标的扁平 run"，而是统一走
`delegateFromPlanet`（sourcePlanet + parentRun|null + sessionId）：

- **AgentComposer**（Mercury/Neptune/各工作台内嵌 composer）：跨台
  `@mars` 走委派链，锚点是 `runHere`（工作台选择记录经同会话校验）；
  同台发送保持扁平任务。"Mercury 查资料→@mars 实现"即锚定研究 run。
- **WorkspaceRail.sendForView**（Mars rail）：thread 视图跨台锚
  `currentRun`；home 视图跨台走 `delegateNewRecord`——新记录会话作
  父任务宿主，子 run 受理后才选中会话（与 sendNewRecord 同语义）。
- **委派出的子任务**：`DelegatedRuns.tsx` 共享组件——`useDelegatedRuns`
  （序号守卫+会话过滤）+ `DelegatedRunCards` + `openDelegatedRun`
  （会话 scope + 全局选择 + 工作台选择记录 + 导航四步）。GlobalChat
  改用共享组件；`WorkspaceTasks` 新增"本会话委派的任务"区，全部渲染
  该组件的工作台（含 Mercury）免费获得委派出的可见性。
- **全局选中不劫持**：`delegateFromPlanet` 内同样恢复 startRun 抢占的
  prevActiveRun（非 sun 目标），Omni 的显示由显式 selectRun 负责。

组合路径现状：`Mercury 查资料 → @mars 实现` 已可显式发起（锚定研究
run，子 run 落在 Mercury 会话，两侧可见同一记录）；"→ Omni 汇报"
依赖跨会话聚合，未在本轮范围。

### 测试（离线）

- `mercuryDelegation.test.tsx`（4 例）：composer @mars 链 + mercury
  父任务锚点、选中 run 续锚、同台扁平保持、delegated-out 卡+进入 Mars
  四步定位。
- `omniDelegation.test.tsx` 复跑全绿（共享组件替换后原断言仍成立）。
- `test_omni_mars_delegation.py` 参数化 source ∈ {sun, mercury}：
  同一 E2E 断言链对两个源工作台均成立（3 passed）。
- vitest 157 passed；tsc 干净。

### 复核修复（d727c195 之后）

- **Mars rail 委派可见性**：`WorkspaceRail` thread 视图补挂
  `DelegatedRunCards`（`delegated.length > 0` 门控）——此前 Mars 能
  委派出去但看不到子任务（rail 的本台过滤把它排除，WorkspaceTasks
  又是 Mars 不用的组件）。补 `workspaceRail.test.tsx` 用例：Mercury
  子任务卡 + 进入四步定位断言。
- **研究内容真交接**：`run:` context ref 此前只是验证过的死 token
  （循环里没有解引用工具）。`delegateFromPlanet` 锚定 parentRun 时
  自动附 `run:<runId>`；`_provision_delegation` 把 `run:` ref 内联成
  该 run 的 durable 输出（含状态，4000 字截断）写进 `child_text`——
  spawn_subtask 路径同样受益。`artifact:`/`task:`/`file:` 仍是引用
  语义（file: 子模型经继承 workspace 的 read_file 实际可读）。
- **E2E 证明交接**：`test_continue_anchor_hands_parent_output_to_child`
  ——Mercury 研究 run 完成并携带 `UNIQUE-FACT-8842` 输出 →
  run 锚委派 → 断言唯一内容进 `request_text`、进 handoff.context_refs、
  且进子模型实际 prompt（`provider.calls` 断言 + 子 turn 以唯一内容
  为 key 才应答）。

### 口径

"Mercury 查资料→Mars 实现"现在内容层面闭环（有离线证明）。仍
未覆盖：spawn_subtask 自动续写、跨会话聚合（Omni 看 Mercury→Mars
结果）、Mercury 搜索/正文/引用质量的独立验收、真实 Tauri 验收。

### Mercury 搜索→正文读取→来源引用 链检查（172a8ae2 之后）

链现状：UI `web_search` RPC → `run_search` → 提供者链
（duckduckgo → deepseek，全失败诚实报 `RESEARCH_TOOL_UNAVAILABLE`）；
正文读取只存在于 run 内（`web_fetch`/`web_deep_search` 经 DDG MCP
`fetch_content`），协议层没有 `web_fetch` RPC；来源引用经"研究任务"
按钮把 `- 标题 — URL` 预填进草稿→进 run 请求文本，
`source_grounding_required` → prompt 要求先搜索。

修复的三处根因：

- **DDG provider 丢结构（P1）**：`DuckDuckGoSearchProvider` 只回
  `content` 原文、`sources=[]`——规范解析器 `parse_search_results`
  就在 `tools/web.py`（模型侧 `web_search` 工具已用），provider 没用。
  后果是 DDG 成功搜索在 UI 显示"无结果"+空 pane（DDG 是默认链首位，
  即主路径）。现在 provider 把 MCP 文本解析成结构化 sources（同一
  规范解析器，content 原文保留）。
- **pane 正文盲区（P2）**：DeepSeek source `snippet:""` 不是 nullish，
  `??` 不落 `content` → pane 正文空；改为 `||` 回落，且 `v.content`
  无 source 时也渲染（content-only 结果不再完全不可见）。
- **引用要求不进 prompt（P2）**：`source_grounding_required` 此前只
  要求"先搜索"，现在同时要求答复标注来源（标题 + URL）——引用链
  最后一环进 prompt。

### 测试（离线）

- `test_search_providers.py` 新增 DDG 解析用例：canonical MCP 文本
  → 结构化 sources（title/url/snippet）；7 passed（含原有 failover/
  归一化用例）。
- `mercurySearch.test.tsx`（3 例）：sources→panes 投影 + 空 snippet
  回落 content + add-view 映射下一条；content-only 响应渲染正文而非
  空态；A→B 会话切换后旧响应不落新 pane（seq 守卫）。
- vitest 161 passed；tsc 干净；search 相关 Python 19 passed。

### 未覆盖（待决策/验收）

- **`web_fetch` RPC**：Navigator pane 直接读正文目前只能进 run 下游
  发生；要不要给 UI 开 pane 级"读取正文"动作是 scope 决策。
- **loop run 结构化 sources**：`plan_executor` 的 `research_sources`
  收集只在工作流路径；agent loop run 的引用只活在输出文本里，没有
  结构化来源记录。
- Mercury 意图分类是否对"研究任务"预填稳定置 `source_grounding_required`
  未单独验证。
- 真实研究质量/正确性、真实 Tauri 验收仍不在本次范围。

### 复核修复（79fae54c 之后）

- **搜索作用域泄漏（P2）**：`MercuryWorkspace` 的 `useMemo` 只依赖
  `service`，内层搜索函数捕获了首发时的 `ctx.sessionId`——A→B 切换
  后 B 的新搜索仍携带 A 的 sessionId（`bind` 只守住响应端）。依赖
  改为 `[service, ctx.sessionId]`：搜索 session 按作用域重建，函数
  不可能携带过期 scope。mercurySearch A→B 用例补断言：`new-q` 请求
  携带 `sess-b`、`old-q` 携带 `sess-a`。

- **研究任务入口 E2E（离线验证）**：`test_mercury_research_chain.py`
  用真实预填模板文本（i18n `mer.research-prefill` zh 版原样）驱动
  run——意图声明 `source_grounding_required` → system prompt 含"答复
  标注来源"规则（`provider.calls` 断言）→ `web_search` 工具经真实
  provider 缝执行（DDG MCP 桩化，`parse_search_results` 真实解析）
  → 抓取来源 URL 进入模型 transcript → 最终答复携带来源 URL 落进
  durable 输出。诚实边界：意图分类的 grounding 判定是脚本化的（证明
  链在标记成立时全通），真实分类器是否稳定对预填文本置标记属真实
  模型验证范畴。

口径修正：上轮 `test_search_providers.py` 实报 6 passed（非 7）。

### 复核修复（79fae54c 之后）

- **搜索作用域泄漏（P2）**：`MercuryWorkspace` 的 `useMemo` 只依赖
  `service`，内层搜索函数捕获了首发时的 `ctx.sessionId`——A→B 切换
  后 B 的新搜索仍携带 A 的 sessionId（`bind` 只守住响应端）。依赖
  改为 `[service, ctx.sessionId]`：搜索 session 按作用域重建，函数
  不可能携带过期 scope。mercurySearch A→B 用例补断言：`new-q` 请求
  携带 `sess-b`、`old-q` 携带 `sess-a`。

- **研究任务入口 E2E（离线验证）**：`test_mercury_research_chain.py`
  用真实预填模板文本（i18n `mer.research-prefill` zh 版原样）驱动
  run——意图声明 `source_grounding_required` → system prompt 含"答复
  标注来源"规则（`provider.calls` 断言）→ `web_search` 工具经真实
  provider 缝执行（DDG MCP 桩化，`parse_search_results` 真实解析）
  → 抓取来源 URL 进入模型 transcript → 最终答复携带来源 URL 落进
  durable 输出。诚实边界：意图分类的 grounding 判定是脚本化的（证明
  链在标记成立时全通），真实分类器是否稳定对预填文本置标记属真实
  模型验证范畴。

口径修正：上轮 `test_search_providers.py` 实报 6 passed（非 7）。

- **正文读取离线用例**：`test_mercury_research_chain.py` 第二例——
  搜索返回 URL → `web_fetch` 经 `fetch_content` 读取正文（MCP 桩化，
  断言 fetch 的 url 来自搜索结果）→ 只存在于正文的
  `UNIQUE-BODY-FACT-7701`（摘要里不含，无法靠 snippet 假阳性）进入
  模型 transcript → 最终答复同时携带独特事实与来源 URL 落 durable
  输出。区别于上例：上一例的 `Summary:` 字段仍是搜索摘要，本例才
  是正文读取验证。

### Mercury 真实评测（qwen3:8b + 真实 DDG，scripts/real_eval_mercury.py）

三任务真实执行，按工具观测+引用准确性分类（`capability_met`/
`capability_gap`/`honest_refusal`/`env`）：

- **t1 预填入口** `capability_gap`：真实 `web_search` RPC 返回 10 条
  真实来源进 `mer.research-prefill` 模板；但真实分类器判为
  `summarize/exec=False`——预填入口**不必然激活 grounding**，模型
  用已给来源直接作答（87s），引用还是模型构造的子路径
  （`duckduckgo.com/privacy` 不在来源集）。
- **t2 正文读取** `capability_gap`：请求"读取正文"分类为
  `action=read` 但 `exec=False`——模型凭记忆作答、零工具调用、
  无引用（21s）。离线链证明 `web_fetch` 可用，但真实分类器不把
  "读取正文"标记为需执行，链路在分类处断开。
- **t3 时效** `capability_met`（URL 级）：`exec=True` → 真实
  `web_news_search`（query 带当日日期）→ 5 个真实来源 → 引用
  `aihot.news/daily` 在证据集内、无虚构 URL（87s）。**但 URL 级
  检查证明不了 claim 级准确**——三条新闻同引一个 daily 页，
  "Gemini 4 Neon"/"Anthropic IPO $83" 等具体声明疑似编造/错配，
  claim 级真实性需来源内容比对，超出本驱动观测面。

**真实评测结论**：检索/正文/引用的工具缝与上下文传递真实可用
（t3 全链走通），但两个真实缺口在分类器——研究意图（预填、正文
读取）不稳定置 grounding/exec，链路在分类处断而非工具处。
报告：realtest_reports/mercury-research-20261119-*/。

### 研究意图→执行判定修复（377bab85 之后，合同层）

根因不在关键词而在合同表达：rule 8 把网页读取写成"通常只要
USER_VISIBLE_OUTPUT"、rule 10 把 grounding 只解释成"要新资料要搜索"
——"读取指定 URL"和"基于链接继续研究"落在两条款之间，分类器从不
置 grounding → `requires_exec` 恒 false → 直接记忆作答。修复：

- **rule 8** 补一句：网络读取类结果仍是 USER_VISIBLE_OUTPUT，但
  "需要读取来源才能完成"走 rule 10 grounding 判定，不影响执行标记。
- **rule 10** 写入"已有正文 vs 只有链接"区分：贴出完整正文直接作答；
  只给标题/URL 却要求读取/核实/继续研究 → `source_grounding_required`
  （search_query 可空，模型直接 web_fetch 给定 URL）；要新资料 →
  grounding + search_query；今天/最新 → freshness + grounding。
- **loop prompt**：grounding 规则从"必须先 web_search"改为"已给
  URL 可直接 web_fetch，答复须标注来源"。`_FRESH_TOOLS` 本就含
  web_fetch，运行时边界无需改。
- **预填模板**（mer.research-prefill zh/en）："继续研究"改为"阅读
  来源正文后回答"——把"只有链接需读正文"的语义写进产品入口本身，
  这是入口→工具链的真实机制，不是关键词兜底。

### 真实复测（合同修复后，qwen3:8b + 真 DDG）

- **t2** `capability_met`：`exec=True` → 模型直接 `web_fetch` 指定
  URL（fetched_required_url）→ 真实 fetch 被本机 SSRF 防护拦
  （所有域名解析到 198.18.x.x，环境特征）→ 自适应 web_search →
  答 2008 引用三个真实来源、无虚构 URL。
- **t1** `capability_met`：新模板措辞翻转分类 `exec=True`
  （action=read, target=来源正文）→ 对 7 个提供 URL 逐个 web_fetch
  （同被 SSRF 拦）→ 降级 web_search → 引用 5 个全在证据集内的真实
  URL。旧措辞下仍判 summarize/exec=False（真实复测过一次）。
- **归档更正**：t1 旧 report.json 的黏连 URL 已用修正后的
  ASCII 正则+精确匹配从已有证据重算——cited=
  `duckduckgo.com/privacy`，fabricated_urls 标记为真（模型构造的
  子路径，不在来源集）；分类不变。未重跑模型。
- **评测判据修复**：`classify()` 按任务验收——required_fetch_url
  任务必须 fetch 指定 URL；PROVIDER_TIMEOUT 归 env 而非
  honest_refusal；URL 正则改 ASCII 字符集防黏连。

### 边界（保持诚实）

- SSRF 是本机环境特征（VPN/代理把域名解析到 198.18.x.x）——
  `fetch_content` 在本机对任何域名都不可用。已证明：模型正确尝试
  fetch、被拒后自适应降级检索；未证明：公网环境下 fetch 正文内容
  质量。
- URL 级引用准确可查（cited ⊆ evidence），claim 级准确性仍不可证——
  模型对来源内容的转述可能编造，需正文比对才能判。
- t3 的 capability_met 只证明"真实检索+URL 对齐"，不构成正文读取
  或事实准确性证据。

### 评测口径更正（cb632b21 复核后）

此前 `fetched_body`/`fetched_required_url` 统计的是"调用过"而非
"读成功"——SSRF 拒绝的 fetch 也被计为正文读取，且失败结果 error
文本里的 URL 会混进 evidence_urls。修正（只改评测，不碰生产合同、
不放松 SSRF 防护）：

- `observation_urls` 只收 `result.ok` 的观察——失败结果不贡献证据。
- `web_obs` 记录 `ok`/`error_code`；区分 `fetch_attempted`（尝试过）
  与 `fetched_body`（读成功）/`fetched_required_url`（指定 URL 读成功）。
- t1/t2 按"要求读取正文"验收（`body_required`）：fetch 尝试被环境
  阻断 → `provider_or_environment`；从未尝试 → `capability_gap`；
  搜索替代不算正文读取通过。
- `--recompute` 模式从 eval.db 事件重算报告，零模型调用。

重算后的诚实结论：

| 报告 | 修正后分类 | 含义 |
|---|---|---|
| t1 旧模板（150543） | capability_gap | exec=False 无工具，分类断点 |
| t2 旧合同（150730） | capability_gap | exec=False 零调用 |
| t2 新合同（181121） | **provider_or_environment** | exec=True、按要求 fetch 指定 URL、被 SSRF 拦、降级检索+真引用 |
| t1 旧模板 PROVIDER_TIMEOUT（181517） | provider_or_environment | 环境超时，非诚实拒绝 |
| t1 新模板（182540） | **provider_or_environment** | exec=True、7 个 URL 逐个 fetch、全被 SSRF 拦、降级检索+真引用 |
| t3（150755） | capability_met | 真实检索+URL 对齐（不证明正文/事实准确） |
| t2 驱动超时（153802） | runtime_contract_gap | 驱动 60s 轮询超时崩溃的真实记录 |

**当前可宣称**：分类合同修复在真实环境生效——"读取正文"与"基于
链接继续研究"正确进入执行路径，模型按要求工具调用并在被拒后
自适应。**不可宣称**：正文读取能力已完成——本机 SSRF 防护拦所有
fetch_content，需公网解析正常的环境验证。SSRF 防护不为此放松。

### 评测判据第二轮更正（78a2f17c 复核后）

探针复现的两个同型盲区（以及修复时发现的第三个）：

- `searched` 未查 `ok`——失败的搜索也算"检索过"；现只对成功观察
  置真。
- 所有 fetch 失败都归环境——`INVALID_ARGUMENT` 等模型参数错误也
  得 `provider_or_environment`；新增 `fetch_env_blocked`：全部 fetch
  失败且每个失败带 env 签名（UNAVAILABLE/TIMEOUT/PROVIDER/
  NETWORK/CONNECTION 错误码，或内容含 ssrf/non-public/
  private-internal/refusing to fetch）才算环境阻断，参数错误归
  `capability_gap`。
- 成功 `web_fetch` 的目标 URL 之前不进证据集（result 只有正文
  字符串），模型引用刚读的页面会被误算 fabricated；现与 executor
  的 `_sources` 语义一致：`ok` 的 web_fetch 把 `input.url` 计入证据。

回归：`tests/test_real_eval_mercury.py` 4 例纯函数用例（失败搜索
不满足检索、参数错误非环境、SSRF 签名是环境、成功 fetch 指定 URL
可达 met）。七份报告重算后分类不变——修正收紧判据，没有翻案。

项目结论保持：分类合同修复在真实请求中生效（exec=True、按约调用
工具、被拒自适应）；公网正文读取仍受本机 SSRF 环境阻断，未验收。
