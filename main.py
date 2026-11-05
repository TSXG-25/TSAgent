"""Interactive CLI adapter for the public AgentService boundary."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from agent.bootstrap import init_workspace, load_all, load_all_async
from agent.interruption import CancelRunRequest
from agent.service import (
    AgentService,
    AgentServiceError,
    DecideApprovalRequest,
    EventStreamRequest,
    ListApprovalsRequest,
    EventType,
    ResumeRunRequest,
    RunLookupRequest,
    RunSnapshot,
    RunStatus,
    StartRunRequest,
    create_default_agent_service,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TSAgent AgentService CLI")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("TSAGENT_RUNTIME_DB", ".tsagent/runtime.sqlite")),
        help="durable SQLite Runtime Store path",
    )
    parser.add_argument(
        "--tenant-id",
        default=os.getenv("TSAGENT_TENANT_ID", "cli-tenant"),
    )
    parser.add_argument(
        "--user-id",
        default=os.getenv("TSAGENT_USER_ID", "cli-user"),
    )
    parser.add_argument(
        "--session-id",
        default=os.getenv("TSAGENT_SESSION_ID", "cli-session"),
    )
    return parser


class ServiceCLI:
    """Small presentation adapter; all execution goes through AgentService."""

    def __init__(
        self,
        service: AgentService,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> None:
        self._service = service
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._session_id = session_id
        self._active_run_id: str | None = None

    @property
    def active_run_id(self) -> str | None:
        """Return the Run currently being watched by this CLI instance."""

        return self._active_run_id

    @staticmethod
    def _print_snapshot(
        snapshot: RunSnapshot, *, streamed_text: str = ""
    ) -> None:
        """Render the durable result, not only the Run lifecycle status."""
        print(f"🤖 状态: {snapshot.status.value}")
        if snapshot.output is not None and snapshot.output.text.strip():
            text = snapshot.output.text
            streamed = streamed_text.strip()
            if streamed and text.startswith(streamed):
                # The answer already streamed live; only the residual
                # manifest/disclosures need the final panel.
                text = text[len(streamed):].lstrip()
            if text.strip():
                print("\n📝 输出:")
                print(text)

        verified_artifacts = tuple(
            artifact
            for artifact in snapshot.artifacts
            if artifact.exists and artifact.verified
        )
        if verified_artifacts:
            names = tuple(
                artifact.display_name or artifact.artifact_id
                for artifact in verified_artifacts
            )
            if snapshot.status is RunStatus.CANCELLED:
                print("\n✅ 已保留完成的产物: " + "、".join(names))
            else:
                print("\n✅ 已验证产物:")
                for name in names:
                    print(f"  • {name}")

        if snapshot.failure_summary is not None:
            print(
                f"\n⚠️ {snapshot.failure_summary.code}: "
                f"{snapshot.failure_summary.message}"
            )
            failure_detail = snapshot.failure_summary.details.get("failure_detail", "")
            if failure_detail:
                print(f"详情: {failure_detail}")
        elif snapshot.status is RunStatus.CANCELLED:
            print("后续任务未执行。")
        elif snapshot.status is RunStatus.COMPLETED:
            # A completed Run without a public output is a projection/runtime
            # regression; make it visible instead of presenting a blank success.
            if snapshot.output is None or not snapshot.output.text.strip():
                print("⚠️ Run 已完成，但没有可展示的用户输出。")

    @staticmethod
    def _print_event(event) -> None:
        """Render durable progress and lifecycle facts without re-planning."""

        payload = event.payload or {}

        if event.event_type is EventType.STAGE_STARTED:
            phase = str(payload.get("phase", event.stage_id or "stage"))
            labels = {
                "planning": "正在规划任务...",
                "execution": "正在执行任务...",
                "finalization": "正在整理结果...",
            }
            print(f"  ↳ {labels.get(phase, f'开始 {phase}...')}")
        elif event.event_type is EventType.STAGE_COMPLETED:
            phase = str(payload.get("phase", event.stage_id or "stage"))
            labels = {
                "planning": "任务规划完成",
                "execution": "任务执行阶段完成",
                "finalization": "结果整理完成",
            }
            print(f"  ↳ {labels.get(phase, f'{phase} 完成')}")
        elif event.event_type is EventType.TASK_STARTED:
            task_id = str(payload.get("task_id", event.task_id or "task"))
            executor = str(payload.get("executor", ""))
            suffix = f" ({executor})" if executor else ""
            print(f"  ↳ 开始任务 {task_id}{suffix}")
        elif event.event_type is EventType.TASK_COMPLETED:
            task_id = str(payload.get("task_id", event.task_id or "task"))
            status = str(payload.get("status", "completed"))
            print(f"  ↳ 任务 {task_id}: {status}")

        elif event.event_type is EventType.RUN_CANCELLING:
            print("🛑 已接受取消请求，等待安全边界...")
        elif event.event_type is EventType.RUN_CANCELLED:
            print("🛑 Run 已取消")
        elif event.event_type is EventType.RUN_TIMED_OUT:
            print("⏱️ Run 已超时")
        elif event.event_type is EventType.RUN_FAILED:
            print("❌ Run 失败")
        elif event.event_type is EventType.RUN_BLOCKED:
            print("⛔ Run 已阻塞")
        elif event.event_type is EventType.RUN_WAITING_USER:
            print("⏸ Run 等待用户确认")
        elif event.event_type is EventType.RUN_COMPLETED:
            print("✅ Run 完成")

    def _event_stream_request(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> EventStreamRequest:
        return EventStreamRequest(
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            session_id=self._session_id,
            run_id=run_id,
            request_id=f"stream-{uuid.uuid4().hex}",
            after_sequence=after_sequence,
        )

    async def _watch_run(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> EventType | None:
        """Consume events through a terminal outcome or a user-input boundary."""

        self._streamed_text = ""
        stream = self._service.stream_events(
            self._event_stream_request(run_id, after_sequence=after_sequence)
        )
        iterator = stream.__aiter__()
        try:
            async for event in iterator:
                if event.event_type is EventType.ASSISTANT_DELTA:
                    delta = str((event.payload or {}).get("delta", ""))
                    self._streamed_text += delta
                    sys.stdout.write(delta)
                    sys.stdout.flush()
                    continue
                print(f"  • {event.sequence_number}: {event.event_type.value}")
                self._print_event(event)
                if event.is_terminal or event.event_type is EventType.RUN_WAITING_USER:
                    return event.event_type
        finally:
            close_stream = getattr(iterator, "aclose", None)
            if close_stream is not None:
                await close_stream()
        return None

    @staticmethod
    def _print_resume_instruction(snapshot: RunSnapshot) -> None:
        if snapshot.status is RunStatus.WAITING_USER:
            print(
                f"可用 /resume {snapshot.run_id} [JSON 证据对象] 继续；"
                "请只提交已核实的结构化证据。"
            )

    async def run_request(self, request_text: str) -> None:
        request_id = f"cli-{uuid.uuid4().hex}"
        run_id = f"run-{uuid.uuid4().hex}"
        request = StartRunRequest(
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            session_id=self._session_id,
            run_id=run_id,
            request_id=request_id,
            request_text=request_text,
        )
        try:
            handle = await self._service.start_run(request)
        except AgentServiceError as error:
            print(f"❌ 无法启动 Run: {error.code.value}: {error.message}")
            conflicting = error.details.get("active_run_id")
            if conflicting:
                print(f"冲突 Run: {conflicting}")
            return
        self._active_run_id = handle.run_id
        print(f"\n📌 Run: {handle.run_id}")
        print("📡 处理中...")

        try:
            await self._watch_run(handle.run_id)
            snapshot = await self._service.get_run(
                RunLookupRequest(
                    tenant_id=self._tenant_id,
                    user_id=self._user_id,
                    session_id=self._session_id,
                    run_id=handle.run_id,
                    request_id=f"lookup-{uuid.uuid4().hex}",
                )
            )
            self._print_snapshot(
                snapshot, streamed_text=getattr(self, "_streamed_text", "")
            )
            self._print_resume_instruction(snapshot)
        finally:
            if self._active_run_id == handle.run_id:
                self._active_run_id = None

    async def resume_request(
        self,
        run_id: str,
        evidence: dict[str, object] | None = None,
    ) -> None:
        """Resume an existing Run using only explicitly structured evidence."""

        values: dict[str, object] = {
            "tenant_id": self._tenant_id,
            "user_id": self._user_id,
            "session_id": self._session_id,
            "run_id": run_id,
            "request_id": f"cli-resume-{uuid.uuid4().hex}",
            "request_text": "Resume the persisted Run from its checkpoint",
        }
        for name in (
            "checkpoint_id",
            "action",
            "external_state_evidence",
            "no_effect_confirmation",
        ):
            if evidence is not None and name in evidence:
                values[name] = evidence[name]
        request = ResumeRunRequest.from_dict(values)
        try:
            handle = await self._service.resume_run(request)
        except AgentServiceError as error:
            print(f"❌ 无法恢复 Run: {error.code.value}: {error.message}")
            conflicting = error.details.get("active_run_id")
            if conflicting:
                print(f"冲突 Run: {conflicting}")
            return
        self._active_run_id = handle.run_id
        print(f"\n📌 恢复 Run: {handle.run_id}")
        try:
            await self._watch_run(handle.run_id)
            snapshot = await self._service.get_run(
                RunLookupRequest(
                    tenant_id=self._tenant_id,
                    user_id=self._user_id,
                    session_id=self._session_id,
                    run_id=handle.run_id,
                    request_id=f"lookup-{uuid.uuid4().hex}",
                )
            )
            self._print_snapshot(
                snapshot, streamed_text=getattr(self, "_streamed_text", "")
            )
            self._print_resume_instruction(snapshot)
        finally:
            if self._active_run_id == handle.run_id:
                self._active_run_id = None

    async def cancel_run(self, run_id: str | None = None) -> RunSnapshot | None:
        """Request durable cancellation for the active or explicitly named Run."""

        target_run_id = run_id or self._active_run_id
        if target_run_id is None:
            print("当前没有可取消的 Run。")
            return None

        request = CancelRunRequest(
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            session_id=self._session_id,
            run_id=target_run_id,
            request_id=f"cli-cancel-{uuid.uuid4().hex}",
            requested_by=self._user_id,
        )
        try:
            snapshot = await self._service.cancel_run(request)
        except AgentServiceError as error:
            print(f"取消失败: {error.code.value}: {error.message}")
            return None

        print("🛑 取消请求已持久化；等待 Runtime 在安全边界收敛。")
        self._print_snapshot(
            snapshot, streamed_text=getattr(self, "_streamed_text", "")
        )
        return snapshot

    async def approve(self, approval_id: str | None = None, *, deny: bool = False) -> None:
        """List pending approvals, or decide one by id (the CLI channel for
        the WAITING_USER approval gate)."""
        if approval_id is None:
            try:
                pending = await self._service.list_approvals(
                    ListApprovalsRequest(
                        tenant_id=self._tenant_id,
                        user_id=self._user_id,
                        session_id=self._session_id,
                        request_id=f"cli-approvals-{uuid.uuid4().hex}",
                        status="PENDING",
                    )
                )
            except AgentServiceError as error:
                print(f"审批查询失败: {error.code.value}: {error.message}")
                return
            if not pending:
                print("当前没有待审批的操作。")
                return
            for record in pending:
                print(
                    f"  {record.approval_id}  [{record.risk_level}] "
                    f"{record.action}  {record.scope}  run={record.run_id}"
                )
            return

        run_id = approval_id.rsplit(":", 1)[0]
        try:
            summary = await self._service.decide_approval(
                DecideApprovalRequest(
                    tenant_id=self._tenant_id,
                    user_id=self._user_id,
                    session_id=self._session_id,
                    run_id=run_id,
                    approval_id=approval_id,
                    decision="deny" if deny else "approve",
                    request_id=f"cli-decide-{uuid.uuid4().hex}",
                    decided_by=self._user_id,
                )
            )
        except AgentServiceError as error:
            print(f"审批决策失败: {error.code.value}: {error.message}")
            return
        print(f"审批 {summary.approval_id} → {summary.status}（run 继续执行）")


_HELP_TEXT = """\
可用命令:
  /help                          显示本帮助
  /model                         查看当前模型与提供商
  /model auto|deepseek|ollama    切换提供商模式
  /apikey                        输入 DeepSeek API Key（隐藏输入，推荐）
  /apikey <provider>             输入指定提供商 Key（隐藏输入）
  /apikey <provider> <key>       直接设置指定提供商 Key
  /session                       查看当前 session
  /session <id>                  切换到指定 session（不存在则新建）
  /session new                   新建并切换到一个随机 session
  /approve                       列出待审批操作
  /approve <approval_id>         批准（--deny 拒绝）
  /cancel [run_id]               取消当前或指定 Run
  /resume <run_id> [JSON 证据]   续接等待用户的 Run
  exit / quit                    退出\
"""

# Provider name → canonical env var.  Unknown providers fall back to the
# <NAME>_API_KEY convention so the command stays forward-compatible with
# anything that reads env credentials (ReMe's LLM backend, future routers).
_APIKEY_PROVIDER_ENV = {
    "deepseek": "OPENAI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
}


def _handle_model_command(parts: list[str]) -> None:
    from agent import llm as llm_module

    router = llm_module.llm
    if len(parts) == 1:
        try:
            provider, name = router._get_active_provider()
            model = getattr(provider, "model", None) or getattr(
                provider, "model_name", ""
            )
            print(
                f"当前: 模式={router._provider_mode} 活跃={name} 模型={model}"
            )
        except Exception as exc:
            print(f"当前: 模式={router._provider_mode} (提供商暂不可用: {exc})")
        print("切换: /model auto|deepseek|ollama")
        return
    mode = parts[1].lower()
    if mode not in {"auto", "deepseek", "ollama"}:
        print("用法: /model [auto|deepseek|ollama]")
        return
    router._provider_mode = mode
    print(f"提供商模式已切换为 {mode}")


async def _handle_apikey_command(parts: list[str]) -> None:
    if len(parts) > 3:
        print("用法: /apikey [provider] [key]")
        return
    provider = "deepseek"
    key = ""
    if len(parts) == 3:
        provider = parts[1].lower()
        key = parts[2]
    elif len(parts) == 2:
        # One argument: a known provider name prompts for its key;
        # anything else is the DeepSeek key itself (legacy inline form).
        if parts[1].lower() in _APIKEY_PROVIDER_ENV:
            provider = parts[1].lower()
        else:
            key = parts[1]
    env_name = _APIKEY_PROVIDER_ENV.get(
        provider, f"{provider.upper().replace('-', '_')}_API_KEY"
    )
    if not key:
        import getpass

        key = await asyncio.to_thread(
            getpass.getpass, f"{provider} API Key: "
        )
    key = key.strip()
    if not key:
        print("API Key 为空，未修改。")
        return
    os.environ[env_name] = key
    if env_name == "OPENAI_API_KEY":
        from agent import llm as llm_module

        llm_module.DEEPSEEK_API_KEY = key
        # Reset the cached provider so the next request uses the new key.
        llm_module.llm._deepseek = None
        llm_module.llm._deepseek_available = True
    print(f"{provider} API Key 已写入 {env_name}（仅本次会话有效，未写入磁盘）。")


async def main() -> None:
    args = _parser().parse_args()
    # Bootstrap remains an application concern.  The CLI itself only talks to
    # the stable Service DTOs and never imports Runtime/Orchestrator/EventBus.
    load_all(
        include_workspace=False,
        include_workflows=False,
        include_knowledge=False,
    )
    # Repository symbols/vector data are optional enrichment.  Do not delay
    # the first prompt or block event delivery while building them.
    async def _background_bootstrap() -> None:
        await asyncio.to_thread(init_workspace)
        await load_all_async()

    repository_task = asyncio.create_task(
        _background_bootstrap(),
        name="tsagent-repository-enrichment",
    )
    repository_ready = False

    async def _ensure_repository_ready() -> None:
        nonlocal repository_ready
        if not repository_ready:
            await repository_task
            repository_ready = True

    service = create_default_agent_service(
        args.db,
        workspace_root=Path(__file__).resolve().parent,
    )
    cli = ServiceCLI(
        service,
        tenant_id=args.tenant_id,
        user_id=args.user_id,
        session_id=args.session_id,
    )
    print("🤖 TSAgent AgentService CLI 启动（输入 exit 退出；/resume <run_id> 可续接）")
    active_task: asyncio.Task[None] | None = None
    queued_input: str | None = None

    async def _finish_active_task() -> None:
        """Wait for the current Run and clear its task."""

        nonlocal active_task
        if active_task is None:
            return
        try:
            await active_task
        except Exception as error:
            print(f"❌ Run 监视失败: {error}")
        finally:
            active_task = None

    try:
        while True:
            if active_task is not None and active_task.done():
                await _finish_active_task()

            if queued_input is None:
                try:
                    user_input = await asyncio.to_thread(input, "\n你: ")
                except EOFError:
                    # EOF means the input stream is closed, not that the user
                    # requested cancellation. Drain the active Run before exit.
                    if active_task is not None and not active_task.done():
                        print("输入已结束，等待当前 Run 完成后退出。")
                    await _finish_active_task()
                    break
            else:
                user_input = queued_input
                queued_input = None

            if active_task is not None and active_task.done():
                await _finish_active_task()

            if not user_input.strip():
                continue

            parts = user_input.strip().split(maxsplit=2)
            is_exit = parts[0].lower() in {"exit", "quit"}
            if parts[0] == "/approve":
                await cli.approve(
                    parts[1] if len(parts) >= 2 else None,
                    deny="--deny" in parts[2:] if len(parts) > 2 else False,
                )
                continue
            if parts[0] == "/cancel":
                if len(parts) > 2:
                    print("用法: /cancel 或 /cancel <run_id>")
                    continue
                await cli.cancel_run(parts[1] if len(parts) == 2 else None)
                continue
            if parts[0] == "/help":
                print(_HELP_TEXT)
                continue
            if parts[0] == "/model":
                _handle_model_command(parts)
                continue
            if parts[0] == "/apikey":
                await _handle_apikey_command(parts)
                continue
            if parts[0] == "/session":
                if len(parts) == 1:
                    print(f"当前 session: {cli._session_id}")
                    continue
                target = parts[1]
                if target == "new":
                    import uuid

                    target = f"cli-{uuid.uuid4().hex[:8]}"
                cli._session_id = target
                print(f"已切换到 session: {target}")
                continue

            if active_task is not None and not active_task.done():
                if is_exit:
                    print("当前 Run 仍在执行，请先输入 /cancel。")
                    continue
                print("当前 Run 仍在执行；输入已排队，完成后自动处理。")
                queued_input = user_input
                await _finish_active_task()
                continue
            if is_exit:
                break
            if parts[0] == "/resume":
                if len(parts) not in {2, 3}:
                    print("用法: /resume <run_id> [JSON 证据对象]")
                    continue
                evidence: dict[str, object] = {}
                if len(parts) == 3:
                    try:
                        parsed = json.loads(parts[2])
                    except json.JSONDecodeError as error:
                        print(f"JSON 证据格式错误: {error.msg}")
                        continue
                    if not isinstance(parsed, dict):
                        print("JSON 证据必须是对象。")
                        continue
                    evidence = parsed
                active_task = asyncio.create_task(
                    cli.resume_request(parts[1], evidence),
                    name="tsagent-cli-resume",
                )
                continue
            await _ensure_repository_ready()
            active_task = asyncio.create_task(
                cli.run_request(user_input),
                name="tsagent-cli-run",
            )
    finally:
        await _finish_active_task()
        if repository_task is not None:
            if not repository_task.done():
                repository_task.cancel()
            await asyncio.gather(repository_task, return_exceptions=True)
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
