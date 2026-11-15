# 九工作台功能修复计划

日期：2026-11-14。代码基线：`98e2f6d8`。状态：计划，未实施。

## 目标与证据边界

把 Mercury、Venus、Earth、Mars、Jupiter、Saturn、Uranus、Neptune、Pluto 修成能够独立完成“选择上下文 → 发起操作 → 查看进度 → 处理结果”的工作台。保留现有星图、布局和视觉风格。

本计划来自源码逐台核对，以及上一轮只读运行记录与离线探针。已复现：前端拒绝同会话并行提交；新建 Neptune 任务不带 originPlanet；没有 workspace 时文件调用返回 UNKNOWN_TOOL，而提供 scoped workspace 后同一调用成功；授权 shell 但未注册 schema 时目录仍不包含 shell。其他条目标明代码事实或待验证场景，不表述为浏览器实测。

本轮不改生产代码、不安装 code-server、不调用真实 Provider、不重启用户正在运行的 Sidecar。旧 FE 档案“审核项关闭”不等于九工作台业务闭环已验收；本计划单独跟踪功能缺口。

## 产品与数据口径

1. **多任务**：不设置“同会话只能有一个运行中任务”的产品限制。选中某个 Run 仅影响查看与控制，不阻止新任务接纳。暂停/取消必须指向具体 Run。
2. **独立任务与委派**：普通发送始终创建独立任务；只有明确的“委派/基于此任务继续”操作才携带父任务。不得根据全局 activeRun 是否完成，暗中改变新任务的父子关系。
3. **三个不同概念**：工作台是功能入口（planet）；会话是交互范围（session）；项目目录是文件执行范围（workspace root）。切换工作台不自动更换项目或把当前任务改归属。
4. **显示范围**：Omni 是总览；九工作台的任务区默认展示当前会话中属于本工作台的任务，支持明确选择其他会话。Archive 的全局检索、Genesis 的租户级母题保留其现有合同，并显式标注范围。不要把这两种全局资源误改成会话私有。
5. **目录建议口径**：所有会产生文件/执行程序的任务在接纳前获得确定的目录。已有用户绑定优先；未绑定会话首次提交时创建应用管理的默认 workspace，并登记为可见会话绑定，用户可以在提交前更换。它是明确的产品默认目录，不是失败后退回 cwd/最近使用目录。该默认口径属于本计划建议，实施时同步写入 workspace 合同。
6. **目录生命周期**：接纳时记录 root，执行与恢复沿用记录；之后改绑只影响新任务。子任务沿用其委派合同中的 root。已有运行中且记录为空的 Run 不静默改绑；修复后在明确目录下重新提交。
7. **事实来源**：Task head 的 origin_planet 决定任务归属；Run head 决定状态和 session；接纳记录决定执行目录；artifact 记录决定产物身份。前端只投影，不根据当前路由猜归属。
8. **历史数据**：没有归属证据的旧任务显示“未分类”，不按当前页面补写 origin。不要为展示问题进行推测性迁移。

## 共用修复（先做）

### WS-01 / P1 — 并行接纳与任务归属

**已确认问题**

- `src/app/delegate.ts` 直接拒绝同会话 active Run；无 active Run 的启动分支丢失 targetPlanet。
- `RunController.startRun` 在全局 operationState 非 idle 时可能返回已有 Run，调用方却当作本次提交成功。
- `App` 向所有工作台传同一 activeRun；Composer 仅按 session 判断，造成跨工作台任务/按钮串用。
- 现有 RunView/RunSnapshot 没有完整的任务归属字段，单靠页面过滤无法准确完成分区。

**实施**

- 新建路径统一传 originPlanet；显式委派才走 delegateRun。更新所有 Composer、全局 Dock 和工作台操作调用方。
- Controller 按实际请求处理接纳结果；删除用已有 Run 冒充本次结果的返回路径。操作状态按被操作 Run/提交请求维护，复用现有服务与轮询。
- 以 task head 为源，在 RunSnapshot 投影 taskId、originPlanet；Python DTO、mapper、RunView、Mock 同步变更。无需第二套“前端归属表”。
- 各工作台保存自己的选中 runId；选中状态按 tenant/session/planet 区分。公共任务区列出该范围所有任务，状态、失败摘要、结果入口和暂停/取消操作均使用行内 Run 身份。
- 提交后留在原工作台，选中新接纳的 Run；“在 Omni 查看完整记录”作为明确操作。

**验收**

1. 同会话连续提交两个任务：两个 start 请求均发出、返回不同 Run，两个可同时保持 RUNNING；取消其中一个不影响另一个。
2. 从 Neptune 与 Venus 提交，持久 task origin 各自正确；切页面不显示另一个工作台任务为本台当前任务。
3. 重开页面后由后端投影恢复归属；全局已有完成任务不会把普通新请求自动变成它的子任务。

### WS-02 / P1 — 目录分配、文件与执行工具

**已确认问题**

- 默认 context factory 在 session 未绑定且没有进程 root 时返回 None；请求仍被接纳。
- 本地失败 Run 及两个子 Run 的接纳记录 root 为空；PlanExecutor 因 workspace=None 落入全局 registry 查找，报 UNKNOWN_TOOL。
- Sidecar 当前只预加载 web；shell 的模型目录依赖 registry。离线探针中，即使授权命令执行，未加载 shell schema 仍没有 shell。
- 对应失败 Run 的 requested_outcomes 只有 FILE_MUTATION、USER_VISIBLE_OUTPUT，未包含 COMMAND_EXECUTION。

**实施**

- Service 接纳路径统一完成目录解析/默认 workspace 建立与绑定，再记录 root；任务重放复用接纳记录，不新建目录。
- 九工作台共享同一个项目选择入口，显示“当前提交目录”；查看旧 Run 时另外显示“本次 Run 的执行目录”，避免与当前绑定混淆。
- 正常文件调用使用 scoped filesystem 路径；不以注册旧全局 filesystem 工具来掩盖缺目录。
- 真实 Sidecar 在首次目录消费前完成已支持的 shell/python schema 注册，是否暴露仍遵循任务授权；不无条件授权命令执行，不把权限判断重复放到 UI。
- 检查媒体生成意图如何得出执行需求：针对“实际渲染并保存 MP4”补语义回归，不能仅靠前端拼接“你有权限”来绕过策略。无法执行时返回具体缺失能力。

**验收**

1. 未绑定会话的新文件任务得到可见默认目录，实际写入该目录；显式绑定目录保持不变；重放不新增目录。
2. 从真实 `python -m agent.service.local_sidecar` 入口用假 Provider 调 read/list/write，验证磁盘结果；子 Run 同样成功。不能用 create_service 默认 bootstrap 参数代替。
3. 授权命令执行时目录包含 shell；未授权时不包含。离线假执行器验证视频请求到执行能力的传递；真实 ffmpeg 单独验收。

### WS-03 / P1 — 上下文切换与结果读取

**已确认问题**

- Mars 文件列表 effect 只依赖 service；Earth 评估刷新只依赖 service；service 在会话切换时复用，因此列表不会跟随重载。
- Venus/Neptune 只扫描全局已加载 runs 的 artifacts；范围混用且刷新后可能漏历史产物。
- 多处 catch 将失败伪装成空列表；部分选中项保存旧对象，列表更新不会更新详情。

**实施**

- 每个会话级请求捕获明确 sessionId；切会话/绑定目录时按其实际范围刷新。只有跨切换的异步写入增加归属检查，复用已有有效的序号模式。
- 资源选择保存 ID、从最新列表取对象；列表为空、读取中、读取失败分别显示。
- 任务/产物使用后端可重载的查询；按 task/run 归属过滤。先复用 listTasks/listRuns/searchArtifacts，确实缺字段时补现有 DTO/过滤参数，不建全局缓存框架。
- 所有 task/run/artifact 跳转传自己的身份；保留已修的 Pluto resolutionSessionId 和 Jupiter preview gate。

**验收**

1. 会话 A 请求延迟，切 B 后 A 响应不覆盖 B；Mars 文件列表和 Earth 裁决记录随 B 刷新。
2. 冷启动可查到已有本台任务及产物，不依赖先访问 Omni 将 Run 加载到内存。
3. 服务报错显示错误而非“没有数据”；刷新后选中详情使用最新值。

## 九工作台专项修复

| 工单 | 工作台 | 代码确认的不足 | 最小修复与交付 | 入口级验收 |
|---|---|---|---|---|
| WS-04 / P2 | Mercury / Navigator | 搜索会话 bind 仅停止在途请求，不清 panes、query、steps；新增 pane 只是空槽，已有结果不会填入。全局任务头与搜索无关。 | 搜索结果保留完整 sources，pane 从当前 sources 派生；新增 pane 显示已有对应来源。搜索状态按 session 保存或切换清空，本计划采用切换清空。复用既有 Stop/逆序保护；显示本台任务区。 | 四条结果：先一 pane 搜索，再增加 pane，第二条来源可见；切 B 后不残留 A 的来源/日志；Stop 后旧响应不落地。 |
| WS-05 / P2 | Venus / Muse | 文稿列表混用所有 runs；顶部 activeRun 与选中文稿生产 Run 可不一致；style/preset 仅组件本地状态，离开页面丢失；当前文稿没有自动进入后续修改请求。 | 文稿按所选范围查询，详情绑定生产 Run；style/preset 按 tenant/session/venus 保存；“基于此文稿修改”显式附带已有 artifact 引用。区分下一次生成偏好与已生成文稿，错误态不继续当正文渲染。 | 文稿 A 的修改请求引用 A，即使全局选中 B；切换页面返回后偏好保留；请求失败保留修改草稿。 |
| WS-06 / P1 | Earth / Organic Lab | 裁决直接取全局 activeRun；会话切换后评估列表不刷新；没有实验 Run 选择器。静态脑图不对应执行中的模型。 | 增加本台实验 Run 列表与选择；裁决使用所选 Run 的 session/run/参数快照。保留已修的请求快照语义；外部 Run 可通过明确选择导入，参数未知如实显示；静态示意图标注为示意，不宣称实时仿真。 | 并行实验 A/B 使用不同参数，切换当前全局任务不改变正在评价的实验；记录携带所选 Run 身份，A/B 记录隔离。 |
| WS-07 / P1 | Mars / Forge | 文件列表不随会话更新；行级 textarea 不适合普通多行代码编辑；Code/Data/Eng/CAD 仅改模式状态，操作未改变。离开路由后文档状态丢失。 | 修文件列表与绑定切换，沿用 A22 digest/root/read-only 合同；FE-04 未接入前使用一个受控多行文本编辑区，保存文档草稿或在离开时明确处理未保存内容。未接通模式标明不可用，不当作已支持。完整 IDE 仍独立排期。 | A/B 同名文件切换时列表/文档身份正确；多行粘贴和编辑可保存；切页面不静默丢草稿；旧 A22 保存收据/过期/脱敏回归保持通过。 |
| WS-08 / P2 | Jupiter / Archive | 全局 artifact、会话 history、用户 facts 混在同页但切换语义不足；加载仅在挂载时触发；事实读取失败被吞成空列表；列表 key 没含 fact scope。 | 显式显示每个 tab 的范围；history 随会话切换重载，artifact 保留全局检索选项；按合同字段组成 fact 身份（含 scope）。刷新与删除后选中详情同步，保留 ReMe 删除边界说明与序号预览保护。 | 同 category/key、不同 scope 的两条 fact 可独立选中删除；切会话后 history 正确；读取失败可见。 |
| WS-09 / P1 | Saturn / Chronos | sel 保存 ScheduleSummary 旧对象：刷新列表不更新详情，暂停后按钮可能仍显示暂停，nextFireAt/lastRunId 也不更新；自然语言 Composer 看似新建计划，实际只发普通 Run；时间字符串直接截取 UTC。 | 改选中 scheduleId 派生最新详情；计划创建走现有结构化表单，普通 Composer 明确为即时任务，不冒充计划创建；本地化显示触发时间。详情关联 schedule 的 lastRunId，不用全局 activeRun。对未实现“重试/完整日志”修正文案或提供现有真实 Run 入口。 | 创建→暂停→恢复完整链路，按钮与列表即时一致；模拟触发后详情出现新的 Run 链接；显示时间按用户时区转换。 |
| WS-10 / P1 | Uranus / Laboratory | 仍用 read_file/llm/approval 等工具名作为节点模板，存成 task.request_text，不能编辑任务内容或选择 profile；runGraph 立即返回空 Run ID 的 PENDING，前端不再刷新；后台节点结果只保存在局部变量。 | 节点编辑器使用已定模型：委派任务文本、可选 profile、成功依赖边；先支持可编辑顺序链并明确显示依赖。保存后可重新打开编辑。补图执行状态查询：以执行 requestId 标识一次执行，持久关联 graph/node 与已有 handoff/run；状态从 Run head 投影，未接纳节点记录待执行/跳过及原因。前端查询进度并显示真实子 Run 链接。父 Run 明确选择且显示所属会话，不默认借用任意全局任务。 | UI 构造两节点图→保存重开→执行；第一节点运行时可看到其 Run ID，完成后第二节点启动；失败依赖显示 SKIPPED；不依赖 runGraph 最初的 PENDING 数组。 |
| WS-11 / P1 | Neptune / Media Studio | 任务只在 Omni 可见；媒体列表混用全局 runs；仅 SVG 有图像预览，PNG/MP4 等是占位；“渲染 MP4”请求只有文本路径/Run ID，未传产物 contextRef。 | 本台任务区接通并行接纳、进度、失败与产物。生成/渲染必须携带真实源 artifact 引用和目标目录；能力状态来自可用执行工具/渲染程序，缺失时明确呈现。新增受约束的本地产物媒体读取/URL 能力，支持图片与视频播放器，沿用现有 artifact 定位与文件边界；不把二进制塞进文本 readArtifact，不暴露任意文件 URL。 | 假 Provider 从本台生成 SVG 后原页可预览；渲染请求正确引用源产物；本地 fixture PNG/MP4 可预览；缺执行能力/ffmpeg 时明确失败而非假成功。真实编码另列外部验收。 |
| WS-12 / P1 | Pluto / Genesis | 母题虽有 assignedTaskId，但汇报取全局 activeRun，切换到其他任务后会提交错误证据并被后端拒绝；“自动”勾选仅改变 source 字段，没有自动探索实现。 | 展示母题所属 task 的探索 Run 列表，汇报只能从该列表明确选择证据；沿用现有 report/approve/reject 状态机与 resolutionSessionId。source 标签如实显示来源；没有自动探索流程时移除误导性“自动”开关，不新建自动系统。 | 同时探索两个母题，选中另一全局 Run 后汇报仍绑定正确探索 Run；无证据不显示可完成汇报；认可/驳回/再次汇报正常，跨会话证据链接保持有效。 |

### Uranus 状态补齐的范围约束

这里需要新增的是已存在后台操作的可查询结果，不是新增通用工作流引擎。保留当前执行算法、成功依赖和 Service 任务生命周期。能从已有 handoff/Run 可靠投影的字段不重复存储；只持久保存无法重建的执行与节点关联、待执行/跳过事实。进程重启后明确显示中断/已有终态，不承诺自动恢复未接纳节点，不引入图版本管理或后台补跑。

### Neptune 媒体预览的范围约束

预览新增能力用于当前真实图片/视频产物。实现前先检查 Tauri 已有资源协议及浏览器开发桥接方式，选用一种与现有 transport 匹配的受约束读取路径。保留 SVG 文本预览；不引入转码服务、媒体资产管理系统或云存储。若本批不实现二进制预览，必须将 WS-11 标为部分交付，不能以占位文案关闭。

## 实施批次与依赖

1. **第一批：WS-01 + WS-02**。修能够启动多个正确归属、正确目录的任务，以及文件/执行工具链。后端合同变更先落测试，再迁移所有调用方。
2. **第二批：WS-03 + WS-11 的任务闭环**。建立共用项目/任务选择与可靠结果读取，先让 Media Studio 的实际报错场景闭环。
3. **第三批：WS-06、WS-07、WS-09、WS-12**。修错对象操作、旧详情和丢草稿，这些直接影响用户结果。
4. **第四批：WS-04、WS-05、WS-08、WS-10、WS-11 媒体预览**。完善独立页面链路；Uranus 状态查询作为独立合同提交，不夹进文案改动。
5. **收尾**：九台逐项离线入口验收、跨端字段检查、更新本计划交付状态和旧 frontend-delivery 档案的范围描述。FE-04 code-server 接入仍走原前置验证，不阻塞当前文件编辑修复。

每张单包含问题、最终合同、改动入口、测试命令与结果、未覆盖外部场景。不要只记“测试全绿/页面完成”。新状态字段或操作必须有实际消费路径；删除已替换的旧路径，不保留双轨 fallback。

## 验证策略

### 本项目执行的离线检查

- 前端优先测试真实组件/Controller/客户端调用链，注入确定性的 service 或 transport；不能只测 helper 而绕过有问题的调用方。
- 共用范围用三个场景覆盖：同会话两个并行 Run；两个会话同名文件；两个工作台分别提交任务。各专项再补表格中直接相关用例，不为每台复制所有排列。
- 新增 UI 集成测试工具只为上述真实交互服务；采用单一测试方式，不同时搭建多套框架。已有可用工具优先。
- Python 使用临时 DB/workspace、假 Provider，真实入口子进程验证工具目录和执行。不访问真实用户缓存，不修改用户 runtime.db。
- 相关测试通过后统一运行 npm test、orbits、scenes、build；共享 Service/contract 变更完成后运行离线 Python 全量，屏蔽所有 Provider key，并使用不可达 Ollama URL与 host_integration 排除。
- 日常局部改动只运行相关回归；不为文案变化重复全量后端测试。

### 外部真实场景验收（单独报告）

1. 浏览器与 Tauri：九台切换、两个任务并行、项目目录选择/改绑、冷启动重载、任务与产物跳转；确认不会跑到其他任务或目录。
2. 本地媒体环境：安装可用 ffmpeg，真实 Provider 发起一次 MP4 生成/渲染；确认实际文件存在、可播放、所属 Run 和目录正确。默认优先 Ollama，不默认调用 DeepSeek。
3. Mars 原生编辑与未来 code-server：键盘、剪贴板、终端与退出生命周期；原 FE-04 条件未满足前保持阻塞，不把纯前端测试当作替代。

## 完成定义

- 九台均能说明“正在操作哪个任务、属于哪个会话、写入哪个目录”。
- 多个运行中任务可独立查看与控制，新提交不会被已有 Run 冒充。
- 本台任务从接纳到结果/失败可在本台追踪，Omni 提供聚合视图与完整日志。
- 所有可点击的功能有真实合同；未实现能力明确标注，不能用静态状态或空成功响应替代。
- 逐项验收证据入档；离线完成与外部实测完成分别记录。

---

## 交付状态（2026-11 修复批次，基线 98e2f6d8 → d753cddb）

离线验证口径：Desktop `npm test`（typecheck + 全部 subtest）、`test:orbits`、`test:scenes`、`npm run build` 全部通过；Python 相关回归 `test_desktop_sidecar.py` + `test_desktop_local_protocol.py` 56 项通过（屏蔽 Provider key、强制 Ollama）。浏览器/Tauri/provider/真实 ffmpeg 验收均未执行，见末节。

| 工单 | 状态 | 交付要点 | 主要改动入口 | 测试证据 |
|---|---|---|---|---|
| WS-01 | ✅ 已交付 `c1f3b378` | 并行接纳（每次提交独立 Run）；originPlanet/taskId 上 wire；普通提交与显式委派分离；九台共享 WorkspaceTasks，按 tenant:session:planet 选 Run | `runController.ts`、`delegate.ts`、`workspaceSelection.ts`、`WorkspaceTasks.tsx`、`service.py`/projector | `ws01Admission.test.ts` 6 项；`test_task_entity` 7 项；wire fixture 回归 |
| WS-02 | ✅ 已交付 `6e61c15c` | 接纳时确定 workspace root；未绑定会话注册默认 workspace；进程根解析路径登记为可见绑定；⚠️ SESSION_RUN_ACTIVE 不再拒绝活跃兄弟（per-run fencing 保留） | `service.py`、`context_factory.py` | sidecar/workspace 相关套件 29+71 项；e2e 断言已改为并行接纳 |
| WS-03 | ✅ 已交付 `d753cddb`+`d6584f93` | `selectSession` 触发 `controller.reload()`：停止旧会话轮询并按 listRuns 重建目录；目录提交带批次代次闸门，在途旧响应不能覆盖新会话目录；选中对象按 id 从最新列表派生；读失败可见 | `runController.ts`、`App.tsx`、各工作台 effect | controller 既有回归 + 全量 subtest |
| WS-04 Mercury | ✅ 已交付 `d753cddb` | 切会话清空 panes/query/steps；addPane 从最近一次 sources 填入而非空槽；旧响应序号保护保留 | `searchSession.ts`、`MercuryWorkspace.tsx` | `mercurySearch.test.ts` 既有逆序回归保持通过 |
| WS-05 Venus | ✅ 已交付 `d753cddb` | 文稿仅取本台本会话任务产物；详情绑定生产 Run/session；style/preset 按 tenant:session 持久化并在切会话时正确重载；contextPrefix 携带文稿 artifact+run 引用 | `VenusWorkspace.tsx` | `workspaceCapability.test.ts`（venus 用例） |
| WS-06 Earth | ✅ 已交付 `d753cddb` | 本台实验 Run 选择（WorkspaceTasks + useWorkspaceCurrentRun）；裁决参数取所选 Run 的请求快照；评估按 session 键刷新，错误可见 | `EarthWorkspace.tsx` | `earthEvalForm.test.ts` 7 项 |
| WS-07 Mars | ✅ 已交付 `d753cddb`+`d6584f93` | 文件列表按 session 刷新且响应按活读会话身份闸门；受控多行编辑器；文档会话持久态与挂载订阅分离，缓存按会话选择，离页草稿不丢且重进后通知不投死订阅；未实现模式禁用并标注 | `MarsWorkspace.tsx`、`documentSession.ts` | `documentSession.test.ts` 4 项 |
| WS-08 Jupiter | ✅ 已交付 `d753cddb` | 三个 tab 显式标注范围（会话 scope / 全局检索 / 会话历史）；切会话清空选中与历史；facts 读取失败可见；forget 用记录自身 scope；预览序号闸门 | `JupiterWorkspace.tsx`、`jupiterAccess.ts` | `workspaceCapability.test.ts`（gate 逆序回归） |
| WS-09 Saturn | ✅ 已交付 `d753cddb` | 选中存 scheduleId，详情从最新列表派生；暂停/恢复作用于所选 schedule；结构化表单=计划、Composer=即时任务（不再混淆）；lastRunId 跳转统一入口 | `SaturnWorkspace.tsx` | 全量 subtest |
| WS-10 Uranus | ✅ 已交付 `d753cddb`+`d6584f93` | 节点可编辑任务文本；新增持久化 `graph_executions`/`graph_execution_nodes`：执行→节点→Run/session 关联可重启后查询；节点状态由对应 Run head 投影，支持 SKIPPED（无 handoff 节点）；父 Run 用本台选中而非全局 activeRun。**未实现**：已保存图重新载入画布编辑、按节点选 profile | `UranusWorkspace.tsx` | 全量 subtest |
| WS-11 Neptune | ✅ 已交付 `d753cddb`+`d6584f93` | 本台任务区；产物列表默认 `originPlanet==neptune` scope；委派携带 `artifact:` contextRef（对生产 Run 验证）；`read_artifact_preview` 守卫字节读（8MiB/mime 白名单/base64）；`.svg` 移出字节白名单、保留脱敏文本投影（修复内容绕开 redaction 外泄）；图片/视频/音频真实预览；CSP 增 media-src。**未覆盖**：mock 只回 unsupported；浏览器/Tauri 实际播放与真实 ffmpeg 生成属外部验收 | `service.py`、`workspace_service.py`、`contracts.py`、`NeptuneWorkspace.tsx`、dispatcher/protocol | `test_read_artifact_preview_serves_guarded_binary`（ok/unsupported/too_large） |
| WS-12 Pluto | ✅ 已交付 `d753cddb` | 证据从母题 assignedTaskId 的探索 Run 列表显式选择；汇报绑定所选证据 Run 的 run+session；移除误导性"自动"开关；跨会话 resolutionSessionId 链接保持 | `PlutoWorkspace.tsx` | `test_motif_summary_projects_resolution_session` 等 |

### 审核修正·第二轮（`d6584f93`）

第二轮代码调用链审查（离线探针复现三项）提出的 P1/P2 缺口，逐项修正：

| 问题 | 根因 | 修正 |
|---|---|---|
| [P1] 切会话后旧目录响应覆盖新会话 | `reload()` 停轮询但无批次代次，已发出的 listRuns 返回仍提交 | 目录提交按加载代次闸门，仅最新批次落盘；回归：B 完成后迟到的 A 响应被丢弃 |
| [P1] Composer 隐藏发送/跨台控制 | `runHere` 仍取全局 activeRun，只校验 session | 发送常驻独立入口；暂停/停止绑定本台 WorkspaceTasks 选中 Run |
| [P1] Mars 文档通知投死订阅 | 缓存的 `ForgeDocumentSession` 保留首次挂载回调；缓存键不含 session；列表闸门读闭包值 | 持久态与订阅者分离（订阅表按挂载注册）；缓存按 tenant:session；响应提交前活读会话身份 |
| [P1] 冷启动授权 shell 不可见 | `bootstrap_tools=False` 只预载 web，授权工具 schema 不装载 | 按已授予能力惰性装载对应工具 schema；子进程回归断言 COMMAND_EXECUTION → shell 可见 |
| [P1] `.svg` 字节预览绕开脱敏 | svg 在 mime 白名单内直接返回原始 base64 | `.svg` 移出白名单返回 unsupported，继续走 read_artifact 的 redaction 投影；探针断言合成 key 不经二进制路径外泄 |
| [P2] Uranus 无执行—节点—Run 持久关联 | lastSteps 仅保留 PENDING 接纳快照；handoff 扫描无 graph/执行/node 归属；子会话字符串推导不可靠 | `graph_executions`/`graph_execution_nodes` 表记录执行、节点、目标 Run/session 与终态；`get_graph_execution`/`list_graph_executions` 上 wire；UI 按执行记录刷新 |
| [P2] Neptune 产物混他台 | `runs.flatMap` 不按 originPlanet 过滤 | 默认列表只取本台 Run；跨台引用保留为显式操作 |

新增子进程离线验收：`test_sidecar_coldstart.py`（授权工具可见性）与 `test_subprocess_ws02_workspace_and_delegation_chain`（真实 sidecar stdio 上的 workspace 绑定、read/write、委派链）——即计划要求的"真实 Sidecar 子进程 + 假 Provider"项，不再归入外部验收。

### 审核修正·第三轮（`b81c6a3d`）

| 问题 | 根因 | 修正 |
|---|---|---|
| [P1] 旧 refreshRun 响应在 reload 后重混入目录 | catalogSeq 只闸目录批量，applySnapshot 不检查归属 | applySnapshot 仅当 snapshot.sessionId 等于当前身份才写目录/开轮询；跨会话刷新仍返回 view 供 openRun 读 sessionId，openRun 改为 reload 落定后再 selectRun |
| [P1] 图执行状态不投影 head，重启永久 RUNNING | get_graph_execution 直读存储行；驱动退出/崩溃无终态写入 | run 绑定节点读路径投影 run head；驱动退出/取消/异常标记未完节点 INTERRUPTED/FAILED；启动收敛（converge_orphaned_graph_execution_nodes）把孤儿 open 行标 INTERRUPTED |
| [P2] Uranus 执行详情响应串扰 + 不随会话刷新 | setExecution 无归属检查；effect 无 session 依赖 | {executionId, sessionId} token 闸门；切会话清空选中并重载列表；INTERRUPTED 计为终态停轮询 |
| [P2] 子进程测试缺假 Provider 工具执行链 | ws02 链只走文件 RPC，provider 允许不可用 | 新增 test_subprocess_fake_provider_agent_tool_chain：脚本化 Ollama /api/chat（意图 JSON + write_file tool_call + done 轮）驱动父子 Run 真实工具执行；工具结果轮 ≥2，产物落盘；并暴露一个真 bug——`llm.py` 流式分支引用未定义 `response`（usage 改从终态 chunk 取），该 bug 会崩所有带 event_bus 的 run |

### 审核修正·第四轮（`60032a1b`）

| 问题 | 根因 | 修正 |
|---|---|---|
| [P1] 启动收敛误伤其他存活实例 | 无条件把所有 open 节点标 INTERRUPTED，不区分驱动归属（多实例共享库合同） | 节点行携带 `driver_writer_id`；收敛仅当：无绑定 Run（有绑定的读路径投影 head）、driver writer 无未过期 worker 租约、同执行内无宽限期内更新的行。新增存活实例保护回归 + 探针场景 |
| [P2] 跨会话链接打不开分页外历史 Run | listRuns 只回最近页，目标不在页内时 selectRun 静默无效 | openRun 在 reload 后对明确目标再 refreshRun——归属闸门放行写入目录，不扩大分页；新增 page-boundary 回归 |
| 子进程测试非确定性 | `TSAGENT_LLM_TIMEOUT` 是无效变量名（真名 `TSAGENT_OLLAMA_TIMEOUT`），且未固定 OLLAMA_BASE_URL；本机 Ollama 存活时流式修复让真实推理拖慢 Run 终态，shutdown 等待超时 | 死端口 + 零重试冷却固定 provider 不可达，ws02 链 60s+ → 3.4s 确定性通过 |
| [P2] 宽限期内重启的孤儿不收敛（`9ff395f8`） | 启动收敛只跑一次，被宽限期跳过的行之后再无入口重评 | `converge_orphaned_graph_execution_nodes` 加可选 execution_id 范围并在 `get_graph_execution` 读路径惰性重评同一谓词——新鲜行/租约持有者的保护不变，过期孤儿经正常查询收敛；新回归覆盖"立即重启→PENDING→宽限期结束→查询得 INTERRUPTED" |

### 已知未闭环项

- **FE-04 code-server**：已安装并在 `127.0.0.1:8080` 运行（password auth）——嵌入前置满足；Tauri WebView/终端/快捷键实测与 FE-04B 宿主适配仍未做，Mars 维持受控编辑器路径，不声明 IDE 能力。
- **Uranus**：已保存工作流图不可重新载入画布编辑；节点级 profile 选择未实现（后端合同已支持 `profile_id`/`target_planet` 字段）。
- **Neptune**：`glb`/3D 仍是不支持格式（如实显示 unsupported）；Mock client 的 `readArtifactPreview` 恒回 unsupported，预览链路只经真实 sidecar 离线验证。
- ~~WS-02 外部验证~~：已在 `d6584f93` 闭环——`test_subprocess_ws02_workspace_and_delegation_chain` 以真实 `python -m agent.service.local_sidecar` 子进程 + 假 Provider 覆盖 workspace 绑定、read/write 与委派子 Run；`test_sidecar_coldstart.py` 覆盖冷启动授权工具可见性。

### 外部真实场景验收（本轮均未执行，需单独报告）

1. 浏览器 + Tauri 手工：九台切换、并行任务、目录绑定/改绑、冷启动、跨会话 Run/证据跳转、图片视频实际播放。
2. 本地 ffmpeg + provider（优先 Ollama）：真实发起一次 MP4 生成/渲染，核对文件存在、可播放、所属 Run/目录正确。
3. Mars 原生编辑键盘/剪贴板/终端与 code-server 生命周期：前置已满足（code-server 运行于 8080），待真实环境嵌入实测。
