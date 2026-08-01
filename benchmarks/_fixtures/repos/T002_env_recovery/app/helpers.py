# app/helpers.py
# 环境问题：导入了不存在的模块，导致 main.py 无法运行。
# 修复目标：让 main.py 能正常输出 "Hello, TSAgent!"。
import nonexistent_dep  # 这个导入会导致 ModuleNotFoundError


def greet(name: str) -> str:
    return f"Hello, {name}!"
