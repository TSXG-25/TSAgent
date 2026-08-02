# agent/validators/__init__.py
"""Validators — Success Condition 验证器。

Executor 调用 validator.validate(task) 验证任务是否真正完成，
而非仅依赖 Facts 的 bool 值判断。
"""
from .file_exists import FileExistsValidator
from .python_syntax import PythonSyntaxValidator
from .min_length import MinLengthValidator


class CombinedValidator:
    """组合验证器，支持多个验证规则链式调用。"""
    
    def __init__(self):
        self._validators = {
            "file_exists": FileExistsValidator(),
            "python_syntax": PythonSyntaxValidator(),
            "min_length": MinLengthValidator(),
        }
    
    def validate(self, task: dict) -> tuple:
        """验证任务是否成功完成。
        
        Args:
            task: Task dict，包含 facts 和可能有的 expected_output/deliverable
        
        Returns:
            (is_success: bool, reason: str)
        """
        # 1. 如果 task 有 deliverable/expected_output，基于它验证
        deliverable = task.get("deliverable", task.get("expected_output", {}))
        if deliverable:
            kind = deliverable.get("kind", deliverable.get("type", ""))
            path = deliverable.get("path", "")
            validator_name = deliverable.get("validator", "")
            
            # 如果指定了 validator，按 validator 验证
            if validator_name and validator_name in self._validators:
                valid, reason = self._validators[validator_name].validate(task, deliverable)
                if not valid:
                    return False, reason
                # 验证通过 → 成功
                return True, reason
            
            # 如果指定了路径但没指定 validator，默认检查文件存在
            if path:
                valid, reason = self._validators["file_exists"].validate(task, deliverable)
                if valid:
                    # 检查最小长度（如果 deliverable 中有指定）
                    min_len = deliverable.get("min_length", 0)
                    if min_len > 0:
                        return self._validators["min_length"].validate(task, deliverable)
                    return True, f"交付物已存在: {path}"
                return False, reason
        
        # 2. 回退到 Facts 检查
        facts = task.get("facts", {})
        goal = (task.get("goal", "") or "").lower()
        
        if facts.get("deliverable_verified"):
            return True, "Deliverable 已验证通过"
        
        # 读取类 goal
        if any(w in goal for w in ["读取", "读", "阅读", "打开", "查看", "read", "open", "view"]):
            if facts.get("question_loaded"):
                return True, "问题已加载"
        
        # 写入类 goal — 确保不是空文件
        if any(w in goal for w in ["写入", "写", "创建", "输出", "write", "create", "output"]):
            if facts.get("file_written"):
                # 检查是否有路径可验证
                fpath = facts.get("question_path", deliverable.get("path", ""))
                if fpath:
                    valid, reason = self._validators["file_exists"].validate(task, {"path": fpath})
                    if valid:
                        return self._validators["min_length"].validate(task, {"path": fpath, "min_length": 50})
                return True, "文件已写入"
        
        # 代码执行类 goal
        if any(w in goal for w in ["执行", "运行", "run", "execute"]):
            if facts.get("code_executed"):
                return True, "代码已执行"
        
        # 搜索/列出类 goal
        if any(w in goal for w in ["列出", "搜索", "search", "list"]):
            if facts.get("directory_listed"):
                return True, "目录已列出"
        
        # 3. 兜底规则：如果 goal 不是任何已知模式，默认通过
        # 这是为了避免"获取天气"等非结构化任务陷入 finish 死循环
        known_patterns = [
            "读取", "读", "阅读", "打开", "查看", "read", "open", "view",
            "写入", "写", "创建", "输出", "write", "create", "output",
            "执行", "运行", "run", "execute",
            "列出", "搜索", "search", "list",
        ]
        if not any(w in goal for w in known_patterns):
            # 未知模式的任务：如果有任何成功 observation 就通过
            obs = task.get("observations", [])
            if obs and any(o.get("status") == "succeeded" for o in obs):
                return True, "任务已完成（兜底通过）"
            # 完全没有 observation → 不通过（防止空转）
            return False, "验证条件未满足"
        
        return False, "验证条件未满足"


# 单例
validator = CombinedValidator()