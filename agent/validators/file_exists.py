# agent/validators/file_exists.py
"""FileExistsValidator — 验证文件存在且大小 > 0。"""
from pathlib import Path


class FileExistsValidator:
    """验证文件是否存在且非空。"""
    
    def validate(self, task: dict, deliverable: dict) -> tuple:
        path = deliverable.get("path", "")
        if not path:
            return False, "未指定文件路径"
        
        from tools.filesystem import _resolve_path, ROOT
        full = _resolve_path(path) if not Path(path).is_absolute() else Path(path)
        
        if not full.exists():
            return False, f"文件不存在: {path}"
        
        if full.stat().st_size == 0:
            return False, f"文件为空: {path}"
        
        return True, f"文件存在: {path} ({full.stat().st_size} bytes)"