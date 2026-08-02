"""factory/generators — 参数化任务生成器。

ADR-0006: 能力先有 Dataset 再实现。本模块从模板生成带完整元数据
（fixture + task.json + verify + grounding）的 Dataset。

首版覆盖 navigation（文件定位）类，验证 factory 机制；
后续按需扩展 refactor/bugfix/multifile 生成器。
"""
import json
import os
import shutil
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]          # 项目根
DATASETS = ROOT / "evaluation" / "datasets"

# 基础函数池（每个任务选一个作为目标函数）
_FUNCTIONS = [
    ("compute_total", "calculate total of numbers"),
    ("normalize_text", "normalize whitespace"),
    ("extract_tokens", "extract comma tokens"),
    ("parse_config", "parse key=value lines"),
    ("sort_records", "sort by score"),
    ("merge_lists", "merge two lists dedupe"),
]


def _write_fixture(task_dir: Path, fn_name: str, desc: str) -> None:
    """生成一个小型 fixture repo：utils.py（含目标函数）+ data.py + main.py。"""
    src = task_dir / "src"
    src.mkdir(parents=True, exist_ok=True)
    (task_dir / "src" / "__init__.py").write_text("", encoding="utf-8")
    (task_dir / "src" / "data.py").write_text(
        "# src/data.py\nSCORES = {\"alice\": 90, \"bob\": 75}\n",
        encoding="utf-8",
    )
    (task_dir / "src" / "utils.py").write_text(
        f'# src/utils.py\n"""Utility module with target function {fn_name}."""\n'
        f'def {fn_name}(x):\n    """{desc}."""\n    return x\n\n'
        f'def helper_dummy():\n    return 1\n',
        encoding="utf-8",
    )
    (task_dir / "main.py").write_text(
        "# main.py\nfrom src.utils import *\n",
        encoding="utf-8",
    )


def gen_navigation(n: int = 6, dataset_root: Optional[Path] = None) -> list[str]:
    """生成 n 个 navigation 任务（文件定位）。返回生成的 task id 列表。"""
    root = dataset_root or (DATASETS / "navigation")
    root.mkdir(parents=True, exist_ok=True)
    created = []
    for i in range(n):
        fn_name, desc = _FUNCTIONS[i % len(_FUNCTIONS)]
        tid = f"NAV{i+1:02d}"
        task_dir = root / tid
        if task_dir.exists():
            shutil.rmtree(task_dir)
        task_dir.mkdir(parents=True)
        _write_fixture(task_dir, fn_name, desc)

        target = f"src/utils.py"
        rel_dir = str(task_dir.relative_to(ROOT))
        prompt = (
            f"仓库 {rel_dir} 中实现了函数 {fn_name}"
            f"（{desc}）。请找到包含该函数实现的文件，输出该文件的相对路径。"
        )
        task_dir.joinpath("task.json").write_text(json.dumps({
            "id": tid,
            "name": f"navigation_{fn_name}",
            "category": "navigation",
            "cwd": rel_dir,
            "prompt": prompt,
            "verify": f"{rel_dir}/verify.py",
            "grounding_targets": [target],
            "grounding_keys": [fn_name],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        task_dir.joinpath("verify.py").write_text(
            f'#!/usr/bin/env python3\n'
            f'"""NAV verify: 目标文件 src/utils.py 已被 agent 定位（答案含路径）。"""\n'
            f'import sys\n'
            f'answer = ""\n'
            f'if len(sys.argv) > 1:\n'
            f'    try:\n'
            f'        answer = open(sys.argv[1]).read()\n'
            f'    except Exception:\n'
            f'        pass\n'
            f'ok = "src/utils.py" in answer\n'
            f'sys.exit(0 if ok else 1)\n',
            encoding="utf-8",
        )
        created.append(tid)
    return created


if __name__ == "__main__":
    print("generated:", gen_navigation())
