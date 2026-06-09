from pathlib import Path
import uuid

PATCH_DIR = Path("patches")

def save_patch(diff: str) -> str:
    PATCH_DIR.mkdir(exist_ok=True)
    patch_id = uuid.uuid4().hex[:8]
    patch_path = PATCH_DIR / f"{patch_id}.patch"

    patch_path.write_text(diff)
    return f"Patch 已保存：{patch_path}"
