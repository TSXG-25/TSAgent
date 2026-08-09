# agent/validators/file_exists.py
"""FileExistsValidator — 验证文件存在且大小 > 0。"""
from .path_utils import resolve_deliverable_path


class FileExistsValidator:
    """验证文件是否存在且非空。"""
    
    def validate(self, task: dict, deliverable: dict) -> tuple:
        path = deliverable.get("path", "")
        if not path:
            return False, "未指定文件路径"
        
        try:
            full = resolve_deliverable_path(task, deliverable, path)
        except (OSError, ValueError, PermissionError) as exc:
            return False, f"路径不属于当前 workspace: {exc}"
        
        if not full.exists():
            return False, f"文件不存在: {path}"
        
        if full.stat().st_size == 0:
            return False, f"文件为空: {path}"
        
        return True, f"文件存在: {path} ({full.stat().st_size} bytes)"
