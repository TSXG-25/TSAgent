# tools/patch.py
import subprocess
from pathlib import Path
import uuid
from agent.registry.tool_registry import registry

ROOT = Path(__file__).parent.parent.resolve()
PATCH_DIR = ROOT / "patches"
PATCH_DIR.mkdir(exist_ok=True)


def propose_patch(diff: str) -> str:
    """
    生成一个 unified diff 格式的 patch，保存到 patches/ 目录。
    旧文件和文件中的绝对行号范围和总行数须正确。
    """
    patch_id = uuid.uuid4().hex[:8]
    patch_path = PATCH_DIR / f"{patch_id}.patch"
    patch_path.write_text(diff, encoding="utf-8")
    return f"Patch 已保存：{patch_path.relative_to(ROOT)}"


def apply_patch(patch_path: str) -> str:
    """将 unified diff patch 应用到项目文件。patch_path 相对于项目根目录。"""
    full = (ROOT / patch_path).resolve()
    if not full.exists():
        return f"错误：patch 文件不存在 {patch_path}"
    if not full.is_relative_to(ROOT):
        return "错误：patch 路径超出项目范围"

    result = subprocess.run(
        ["patch", "-p1", "--forward", "-i", str(full)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return f"Patch 已成功应用：{patch_path}\n{result.stdout}".strip()
    return f"Patch 应用失败：{result.stderr or result.stdout}"


registry.register(propose_patch, category="filesystem", tags=["patch", "code"])
registry.register(apply_patch, category="filesystem", tags=["patch", "code"])