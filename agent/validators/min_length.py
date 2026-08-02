# agent/validators/min_length.py
"""MinLengthValidator — 验证文件内容长度满足最小要求。"""
from pathlib import Path


class MinLengthValidator:
    """验证文件内容长度 >= min_length。"""
    
    def validate(self, task: dict, deliverable: dict) -> tuple:
        path = deliverable.get("path", "")
        min_length = deliverable.get("min_length", 50)
        
        if not path:
            return True, "无路径需要验证"
        
        from tools.filesystem import _resolve_path, ROOT
        full = _resolve_path(path) if not Path(path).is_absolute() else Path(path)
        
        if not full.exists():
            return False, f"文件不存在: {path}"
        
        content = full.read_text(encoding="utf-8", errors="replace")
        actual = len(content)
        
        if actual < min_length:
            return False, f"文件内容不足: {actual} < {min_length} 字符"
        
        return True, f"内容长度 {actual} >= {min_length}"