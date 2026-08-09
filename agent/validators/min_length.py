# agent/validators/min_length.py
"""MinLengthValidator — 验证文件内容长度满足最小要求。"""
from .path_utils import resolve_deliverable_path


class MinLengthValidator:
    """验证文件内容长度 >= min_length。"""
    
    def validate(self, task: dict, deliverable: dict) -> tuple:
        path = deliverable.get("path", "")
        min_length = deliverable.get("min_length", 50)
        
        if not path:
            return True, "无路径需要验证"
        
        try:
            full = resolve_deliverable_path(task, deliverable, path)
        except (OSError, ValueError, PermissionError) as exc:
            return False, f"路径不属于当前 workspace: {exc}"
        
        if not full.exists():
            return False, f"文件不存在: {path}"
        
        content = full.read_text(encoding="utf-8", errors="replace")
        actual = len(content)
        
        if actual < min_length:
            return False, f"文件内容不足: {actual} < {min_length} 字符"
        
        return True, f"内容长度 {actual} >= {min_length}"
