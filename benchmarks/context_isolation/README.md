# v2.3A Runtime Context Isolation Dataset

这是 ADR-0019 的失败导向、确定性 Dataset。它定义要证明的 ownership
不变量，不把 Dataset schema PASS 误报为生产 Runtime 已完成。

## 内容

- 12 个唯一 case；
- Artifact、Session memory、Event scope、subscription lifecycle、Run close 和
  context identity；
- 每个 case 都声明 scope、invariant、expected result 和 failure signal；
- `metadata.py` 输出 benchmark version、contract version、case count 和 dataset hash。

## 校验

```bash
python -m benchmarks.context_isolation.validate
pytest -q tests/test_context_isolation_benchmark.py
mypy benchmarks/context_isolation tests/test_context_isolation_benchmark.py
```

当前 Dataset hash：

```text
a689dbefd7bfd6ed7306c322e264d4e541ca298ed814bb86f055f864d170a91f
```

Runtime 隔离能力仍需由 `tests/test_runtime_context_isolation.py` 及后续并发、
Workspace handle 和 Architecture Verification 门禁证明。
