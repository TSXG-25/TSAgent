"""输出当前程序所在目录的父目录中所有文件夹和子文件（递归）。

默认跳过隐藏系统目录（.git、.pytest_cache、venv 等），
可用 --all 参数显示全部。
"""
import sys
from pathlib import Path

# 默认跳过的噪音目录
_SKIP_DIRS = {
    ".git", ".pytest_cache", ".repo_index", ".vscode",
    "venv", "env", ".env", "__pycache__",
    "node_modules", "dist", "build", ".DS_Store",
}


def list_parent_directory(show_all: bool = False):
    """获取当前文件所在目录的父目录，递归输出其下的所有文件夹和文件。"""
    current_dir = Path(__file__).parent.resolve()  # output/
    parent_dir = current_dir.parent.resolve()      # TSAgent/

    print(f"当前文件: {__file__}")
    print(f"当前目录: {current_dir}")
    print(f"父目录: {parent_dir}")
    if show_all:
        print("模式: 显示全部（含隐藏目录）")
    else:
        print("模式: 跳过隐藏目录（.git、venv 等，用 --all 显示全部）")
    print("=" * 60)

    folder_count = 0
    file_count = 0

    def walk(path: Path, prefix: str = ""):
        """递归遍历目录树。"""
        nonlocal folder_count, file_count

        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        # 过滤噪音目录
        visible = [
            e for e in entries
            if show_all or not (e.is_dir() and e.name in _SKIP_DIRS)
        ]

        for i, entry in enumerate(visible):
            is_last = (i == len(visible) - 1)
            connector = "└── " if is_last else "├── "

            if entry.is_dir():
                folder_count += 1
                print(f"{prefix}{connector}{entry.name}/")
                sub_prefix = prefix + ("    " if is_last else "│   ")
                walk(entry, sub_prefix)
            else:
                file_count += 1
                print(f"{prefix}{connector}{entry.name}")

    walk(parent_dir)

    print("=" * 60)
    print(f"总计: {folder_count} 文件夹, {file_count} 文件")


if __name__ == "__main__":
    show_all = "--all" in sys.argv or "-a" in sys.argv
    list_parent_directory(show_all=show_all)