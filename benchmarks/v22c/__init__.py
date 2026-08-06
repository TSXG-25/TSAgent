"""v2.2C Run 级真实 API 测试（跨 Workflow 恢复 + 副作用安全）。

验证：真实 Provider 参与的多 Workflow Run，在进程中断 / 外部状态变化 /
副作用不确定窗口下，恢复到正确 Workflow 且不重复已确认副作用。
"""
