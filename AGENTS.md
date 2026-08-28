# 项目协作约定

## 项目职责

本项目主要负责：

- 代码修改、缺陷修复和功能实现；
- 架构设计、架构完善与必要的重构；
- 基础离线测试，包括不依赖真实外部环境的单元测试、集成测试和静态检查；
- 仓库导入、代码接入及相关适配工作。

## 测试边界

本项目不负责真实场景测试或生产环境验证，例如真实业务流程、真实用户操作、真实外部服务、现场设备或线上部署环境中的测试。

如变更需要真实场景验证，应明确提出测试需求，并尽可能说明：

- 需要验证的业务场景和操作步骤；
- 所需环境、账号、数据、设备或外部服务；
- 预期结果、验收标准和风险点。

在缺少真实场景验证条件时，应完成可执行的基础离线测试，并在交付说明中明确未覆盖的真实场景测试项，不将离线测试结果表述为真实环境验证结论。

## Bug 处理原则

- 发现 Bug 时，必须分析并定位底层原因，不能只针对表面现象添加临时补丁或特殊分支。
- 修复方案应优先落在正确的抽象层次，兼顾代码的可维护性、可塑性、扩展性和现有功能，避免为了快速消除当前问题而牺牲未来的发展空间。
- 不得在不考虑后续需求、架构演进或功能扩展的情况下，对 Bug 做出限制性、一次性的特别处理；如确有必要采用权宜方案，必须说明原因、影响、技术债务和后续治理计划。
- 修复后应补充能够覆盖根因的基础离线测试，避免测试只验证当前特例而无法约束同类问题再次出现。

## 测试专用工作模式（真实 API 复跑 / Benchmark）

当以"测试专用"身份工作时，适用以下规则：

- **只负责测试**：真实 API 复跑、Benchmark 搭建与运行、结果采集与归档。
- **不提出修复**：发现 Bug 后只记录问题、证据与影响，不给出修复方案或修复建议，不修改生产 Runtime / Agent 代码。
- **交付物是测试产物**：测试结果、逐例证据、指标口径（Raw E2E / Capability / Provider Error 等）、归档报告。
- **保持被测代码不变**：除测试框架本身（harness / benchmark / 归档文件）外，不触碰 Agent Runtime、工具、工作流等生产代码。
- **只负责真实复跑**：除非需要验证测试框架或归档结果，否则不主动跑离线测试。

## Engineering Style: Prefer Simple, Direct Code

Write the simplest implementation that satisfies the current contract and tests.

Do **not** optimize for hypothetical future misuse, unknown callers, legacy behavior, or impossible states unless the task explicitly requires it.

### Core principle

Prefer:

**clear invariant → direct implementation → fail fast**

over:

**uncertain invariant → validation layers → fallback → compatibility path → silent recovery**

Trust established internal contracts.

If a value is guaranteed by an upstream type, validator, parser, or runtime invariant, do not validate it again.

### Avoid speculative defensive programming

Do not add any of the following unless there is a concrete requirement or an existing demonstrated failure mode:

- compatibility shims for old behavior
- legacy fallback paths
- multiple alternative execution paths "just in case"
- broad `try/except`
- exception swallowing
- silent fallback to defaults
- redundant `None` checks
- redundant type checks for typed internal APIs
- defensive `hasattr` / `getattr(..., default)` for known internal objects
- duplicate validation across layers
- fallback parsing after a canonical parser already exists
- extra feature flags
- wrappers around stable internal APIs
- abstractions with only one current implementation
- recovery logic for states that should be impossible
- code preserving obsolete behavior that can simply be removed

Do not make the system "more robust" unless robustness is part of the requested change.

### Fail fast

When an invariant is violated, prefer an explicit error over silently guessing what the caller intended.

Bad:

```python
value = getattr(obj, "value", None)
if value is None:
    value = getattr(obj, "legacy_value", "")
```

Prefer:

```python
value = obj.value
```

if `obj.value` is part of the current contract.

Bad:

```python
try:
    result = execute_new_path()
except Exception:
    result = execute_old_path()
```

Prefer fixing the new path or allowing the real error to surface.

### One canonical path

There should normally be one authoritative representation and one execution path.

When replacing an old mechanism:

1. migrate callers,
2. remove the old mechanism,
3. update tests,
4. do not leave both paths alive unless backward compatibility is explicitly required.

Prefer deleting obsolete code over keeping it as fallback.

### Respect architectural boundaries

Each layer should enforce only the invariants it owns.

Do not re-check guarantees already established by another layer.

For example:

- parser validates syntax
- contract/model validates structure
- policy decides behavior
- executor executes the decision

The executor should not invent additional parser, policy, compatibility, or recovery behavior "for safety."

### Avoid premature abstraction

Do not introduce:

- factories for a single implementation
- interfaces with one implementation
- helper functions used once unless they materially improve clarity
- generic configuration for behavior that is currently fixed
- extension points with no current consumer

Prefer concrete code until there are at least two real use cases that justify abstraction.

### Scope discipline

Implement exactly what the task requires.

Before adding extra handling, ask:

> What concrete current requirement or failing test requires this code?

If there is no answer, do not add it.

Do not proactively support hypothetical future requirements.

### When defensive handling IS appropriate

Defensive programming is appropriate at actual trust boundaries, such as:

- user input
- network/API responses
- filesystem boundaries
- database boundaries
- external tools/processes
- serialization/deserialization
- security boundaries

Even there, handle known failure modes explicitly rather than catching everything.

Inside trusted application code, rely on contracts and invariants.

### During refactoring

When you discover unnecessary defensive logic:

- simplify it if it is directly related to the task;
- remove obsolete fallback paths when safe;
- do not preserve behavior merely because it currently exists;
- check callers and tests to determine whether the behavior is actually part of the contract.

Compatibility is a requirement, not a default assumption.

### Decision rule

When choosing between:

A. a simple implementation relying on the documented/current invariant

and

B. a more complicated implementation that also handles hypothetical invalid states

choose **A**, unless there is concrete evidence that B is required.

If you believe additional defensive logic is necessary, explain the specific failure mode that requires it before implementing it.

Do not preserve legacy behavior by default.

This repository is under active development. Unless backward compatibility is explicitly requested, prefer changing the contract and migrating callers over introducing compatibility branches, fallback paths, adapters, or dual implementations.

A breaking internal change is preferable to permanent architectural complexity.

Keep the patch structurally minimal. Do not add compatibility, fallback, validation, recovery, or abstraction unless required by an existing contract or demonstrated failing case. Trust established internal invariants and prefer fail-fast behavior.
