# Changelog

## [v2.2.0] — 2026-08-06

Run-Level Workflow Resume 正式发布。该版本将可恢复执行从单 Workflow 提升为同一
Run 内多 Workflow 的确定性恢复，并完成 P0 真实 Provider 验证。

- v2.2A：Run Checkpoint Contract、canonical codec、digest、外部状态 Guard。
- v2.2B：单 Workflow Exact Resume、Stage Replay、副作用安全边界。
- v2.2C：Run-level activation、跨 Workflow 恢复、Artifact hydration、进程重启恢复。
- P0 C01–C08：8/8 PASS；Duplicate Side Effect=0；Unsafe Resume Acceptance=0。

完整说明见 [RELEASE_NOTES_v2.2.md](RELEASE_NOTES_v2.2.md)。

## Previous releases

- [v2.0 Release Notes](RELEASE_NOTES_v2.0.md)
