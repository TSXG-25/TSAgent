# agent/validators/python_syntax.py
"""PythonSyntaxValidator — 验证文件是有效的 Python 代码。"""
import ast
from pathlib import Path


class PythonSyntaxValidator:
    """验证 Python 文件语法正确。"""
    
    def validate(self, task: dict, deliverable: dict) -> tuple:
        path = deliverable.get("path", "")
        if not path:
            return False, "未指定文件路径"
        
        from tools.filesystem import _resolve_path, ROOT
        full = _resolve_path(path) if not Path(path).is_absolute() else Path(path)
        
        if not full.exists():
            return False, f"文件不存在: {path}"
        
        try:
            content = full.read_text(encoding="utf-8")
            ast.parse(content)
            return True, f"Python 语法正确: {path}"
        except SyntaxError as e:
            return False, f"Python 语法错误: {e}"