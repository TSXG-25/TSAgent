"""ExecutionVerifier — Execution Runtime 的验证层（Verifier 阶段）。

ADR-0012 Execution Runtime Contract:
    Executor            负责尝试执行
    Verifier            负责确认结果
    ExecutionResult     只能由 Verifier 产生
    禁止 Tool → 直接 success=True

Pipeline:
    ExecutionPlan → Executor → ExecutionArtifacts → ExecutionVerifier → ExecutionResult

以后 Write / Copy / Move / Delete / Patch / Python / Notebook / Docker
全部注册进 ExecutionVerifier registry，作为 Execution Runtime 的统一验证层。
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ExecutionArtifacts:
    """工具执行后留下的世界状态痕迹（Verifier 的唯一输入）。

    Executor 只负责收集；Verifier 只负责核验；两者通过本对象解耦。
    """
    files_written: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    # Deterministic filesystem mutations other than write.  Each entry is a
    # JSON-shaped record so the verifier never trusts a tool's prose result.
    file_operations: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class VerificationResult:
    """一次验证的结论。success 只能来自 Verifier，不能来自 Tool。"""
    success: bool
    verifier: str = ""
    detail: str = ""
    checks: List[str] = field(default_factory=list)


# ── 基础核验原语（纯函数，以文件系统为准） ──

def verify_write(path: str, *, min_size: int = 1,
                 expect_content: Optional[str] = None,
                 workspace: Optional[Any] = None) -> bool:
    """文件确实存在且非空（可选内容匹配）。"""
    resolved = _resolve_evidence_path(path, workspace)
    if resolved is None or not resolved.exists() or resolved.is_dir():
        return False
    try:
        if resolved.stat().st_size < min_size:
            return False
    except OSError:
        return False
    if expect_content is not None:
        try:
            with resolved.open(encoding="utf-8", errors="replace") as f:
                if expect_content not in f.read():
                    return False
        except OSError:
            return False
    return True


def verify_absent(path: str) -> bool:
    """目标已不存在（Delete 语义）。"""
    return not os.path.exists(path)


def verify_updated(path: str, original_mtime_ns: Optional[int],
                   original_size: Optional[int]) -> bool:
    """文件确实被修改（mtime 变化为主信号，size 变化为补充信号）。"""
    if not os.path.exists(path):
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    if original_mtime_ns is not None and st.st_mtime_ns != original_mtime_ns:
        return True
    if original_size is not None and st.st_size != original_size:
        return True
    return False


# ── 验证器（每类世界状态变更一个） ──

class BaseVerification:
    name: str = "base"

    def verify(self, artifacts: ExecutionArtifacts,
               task: Optional[Any] = None,
               workspace: Optional[Any] = None) -> VerificationResult:
        raise NotImplementedError


class WriteVerification(BaseVerification):
    """Write 成功 = 声明的文件全部存在且非空（ADR-0010 行为验收）。"""
    name = "write"

    def verify(self, artifacts: ExecutionArtifacts,
               task: Optional[Any] = None,
               workspace: Optional[Any] = None) -> VerificationResult:
        if not artifacts.files_written:
            return VerificationResult(
                False, self.name, "计划含 write 步骤但未声明写入目标")
        missing = [p for p in artifacts.files_written if not verify_write(p)]
        if missing:
            return VerificationResult(
                False, self.name,
                f"写入校验失败（文件未创建或为空）: {missing[0]}",
                checks=[f"exists+nonempty: {p}" for p in artifacts.files_written])
        return VerificationResult(
            True, self.name,
            f"已确认 {len(artifacts.files_written)} 个写入目标",
            checks=[f"exists+nonempty: {p}" for p in artifacts.files_written])


def _resolve_evidence_path(value: str, workspace: Optional[Any]) -> Optional[Any]:
    """Resolve evidence through the current Run workspace.

    An unscoped absolute path remains available for the small standalone
    verifier helpers used by migration tests.  Relative paths without a
    RunContext are rejected rather than resolved against cwd/ROOT.
    """
    if not value:
        return None
    if workspace is not None:
        return workspace.resolve_path(value)
    candidate = Path(value)
    return candidate if candidate.is_absolute() else None


def _workspace_path(value: str, workspace: Optional[Any] = None):
    resolved = _resolve_evidence_path(value, workspace)
    if resolved is None:
        raise ValueError(f"目标路径为空: {value}")
    return resolved


class FileOperationVerification(BaseVerification):
    """Verify copy/move/delete from observed filesystem state."""

    def verify(self, artifacts: ExecutionArtifacts,
               task: Optional[Any] = None,
               workspace: Optional[Any] = None) -> VerificationResult:
        operations = [
            operation for operation in artifacts.file_operations
            if operation.get("operation") == self.name
        ]
        if not operations:
            return VerificationResult(
                False,
                self.name,
                f"{self.name} 步骤没有可验证的副作用记录",
            )

        try:
            for operation in operations:
                if self.name == "delete":
                    target = _workspace_path(operation.get("path", ""), workspace)
                    if target.exists():
                        return VerificationResult(
                            False,
                            self.name,
                            f"删除校验失败：目标仍然存在: {target}",
                        )
                    continue

                source = _workspace_path(operation.get("source", ""), workspace)
                destination = _workspace_path(operation.get("destination", ""), workspace)
                if not destination.exists() or not destination.is_file():
                    return VerificationResult(
                        False,
                        self.name,
                        f"{self.name} 校验失败：目标不存在或不是文件: {destination}",
                    )
                if self.name == "copy":
                    if not source.exists() or not source.is_file():
                        return VerificationResult(
                            False,
                            self.name,
                            f"复制校验失败：源文件不存在: {source}",
                        )
                    if source.read_bytes() != destination.read_bytes():
                        return VerificationResult(
                            False,
                            self.name,
                            "复制校验失败：源文件与目标内容不一致",
                        )
                elif source.exists():
                    return VerificationResult(
                        False,
                        self.name,
                        f"移动校验失败：源文件仍然存在: {source}",
                    )
        except (OSError, ValueError) as exc:
            return VerificationResult(False, self.name, f"{self.name} 校验异常: {exc}")

        return VerificationResult(
            True,
            self.name,
            f"已确认 {len(operations)} 个 {self.name} 操作",
        )


class DeleteVerification(FileOperationVerification):
    """Delete 成功 = 目标已从 workspace 中消失。"""
    name = "delete"


class CopyVerification(FileOperationVerification):
    name = "copy"


class MoveVerification(FileOperationVerification):
    name = "move"


class ExecutionVerifier:
    """统一验证层：按 task.verb 选择对应验证器。

    用法：
        verifier = ExecutionVerifier()
        result = verifier.verify(plan, artifacts)
        # ExecutionResult.success = result.success
    """

    def __init__(self) -> None:
        self._registry: Dict[str, BaseVerification] = {}
        self.register(WriteVerification())
        self.register(DeleteVerification())
        self.register(CopyVerification())
        self.register(MoveVerification())

    def register(self, verification: BaseVerification) -> None:
        self._registry[verification.name] = verification

    def get(self, name: str) -> Optional[BaseVerification]:
        return self._registry.get(name)

    def verify(self, plan: Any, artifacts: ExecutionArtifacts,
               task: Optional[Any] = None,
               workspace: Optional[Any] = None) -> VerificationResult:
        """对一次执行做最终验证。

        Args:
            plan: ExecutionPlan（取其 task verb 决定验证器）。
            artifacts: Executor 收集的世界状态痕迹。
            task: 可选，直接提供 Task（优先于 plan.task）。
        """
        t = task or getattr(plan, "task", None)
        verb = getattr(t, "verb", None)
        vname = str(getattr(verb, "value", verb) or "")  # Verb enum → str
        verification = self._registry.get(vname)
        if verification is None:
            # 非受管 verb（read/explain/search…）不强制验证，默认 success
            return VerificationResult(True, "none", f"verb={vname} 无专用验证器")
        if isinstance(verification, WriteVerification):
            if not artifacts.files_written:
                return verification.verify(artifacts, task=t)
            missing = [
                path for path in artifacts.files_written
                if not verify_write(path, workspace=workspace)
            ]
            if missing:
                return VerificationResult(
                    False,
                    verification.name,
                    f"写入校验失败（文件未创建或为空）: {missing[0]}",
                    checks=[
                        f"exists+nonempty: {path}"
                        for path in artifacts.files_written
                    ],
                )
            return VerificationResult(
                True,
                verification.name,
                f"已确认 {len(artifacts.files_written)} 个写入目标",
                checks=[
                    f"exists+nonempty: {path}"
                    for path in artifacts.files_written
                ],
            )
        return verification.verify(artifacts, task=t, workspace=workspace)


# 全局单例（与 plan_executor 同级，Execution Runtime 共享）
execution_verifier = ExecutionVerifier()
