"""Interactive CLI adapter for the public AgentService boundary."""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from pathlib import Path

from agent.bootstrap import load_all, load_all_async
from agent.interruption import CancelRunRequest
from agent.service import (
    AgentService,
    AgentServiceError,
    EventStreamRequest,
    EventType,
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
    def _print_snapshot(snapshot: RunSnapshot) -> None:
        """Render the durable result, not only the Run lifecycle status."""
        print(f"🤖 状态: {snapshot.status.value}")
        if snapshot.output is not None and snapshot.output.text.strip():
            print("\n📝 输出:")
            print(snapshot.output.text)
        elif snapshot.failure_summary is not None:
            print(
                f"⚠️ {snapshot.failure_summary.code}: "
                f"{snapshot.failure_summary.message}"
            )
        elif snapshot.status is RunStatus.CANCELLED:
            if snapshot.artifacts:
                names = tuple(
                    artifact.display_name or artifact.artifact_id
                    for artifact in snapshot.artifacts
                    if artifact.exists and artifact.verified
                )
                if names:
                    print("已保留完成的产物: " + ", ".join(names))
            print("后续任务未执行。")
        elif snapshot.status is RunStatus.COMPLETED:
            # A completed Run without a public output is a projection/runtime
            # regression; make it visible instead of presenting a blank success.
            print("⚠️ Run 已完成，但没有可展示的用户输出。")

    @staticmethod
    def _print_event(event) -> None:
        """Render cancellation/terminal facts without invoking a Finalizer."""

        if event.event_type is EventType.RUN_CANCELLING:
            print("🛑 已接受取消请求，等待安全边界...")
        elif event.event_type is EventType.RUN_CANCELLED:
            print("🛑 Run 已取消")
        elif event.event_type is EventType.RUN_TIMED_OUT:
            print("⏱️ Run 已超时")
        elif event.event_type is EventType.RUN_FAILED:
            print("❌ Run 失败")
        elif event.event_type is EventType.RUN_BLOCKED:
            print("⛔ Run 已阻塞")
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

    async def _watch_run(self, run_id: str, *, after_sequence: int = 0) -> None:
        """Consume durable events until the Run reaches a terminal event."""

        async for event in self._service.stream_events(
            self._event_stream_request(run_id, after_sequence=after_sequence)
        ):
            print(f"  • {event.sequence_number}: {event.event_type.value}")
            self._print_event(event)

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
        handle = await self._service.start_run(request)
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
            self._print_snapshot(snapshot)
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
        self._print_snapshot(snapshot)
        return snapshot


async def main() -> None:
    args = _parser().parse_args()
    # Bootstrap remains an application concern.  The CLI itself only talks to
    # the stable Service DTOs and never imports Runtime/Orchestrator/EventBus.
    load_all()
    await load_all_async()

    service = create_default_agent_service(args.db)
    cli = ServiceCLI(
        service,
        tenant_id=args.tenant_id,
        user_id=args.user_id,
        session_id=args.session_id,
    )
    print("🤖 TSAgent AgentService CLI 启动（输入 exit 退出）")
    active_task: asyncio.Task[None] | None = None
    try:
        while True:
            if active_task is not None and active_task.done():
                try:
                    await active_task
                except Exception as error:
                    print(f"❌ Run 监视失败: {error}")
                active_task = None

            try:
                user_input = await asyncio.to_thread(input, "\n你: ")
            except EOFError:
                user_input = "exit"
            if user_input.strip().lower() in {"exit", "quit"}:
                if active_task is not None and not active_task.done():
                    print("当前 Run 仍在执行，请先输入 /cancel。")
                    continue
                break
            if not user_input.strip():
                continue

            parts = user_input.strip().split()
            if parts[0] == "/cancel":
                if len(parts) > 2:
                    print("用法: /cancel 或 /cancel <run_id>")
                    continue
                await cli.cancel_run(parts[1] if len(parts) == 2 else None)
                continue

            if active_task is not None and not active_task.done():
                print("已有 Run 正在执行；请先等待完成或输入 /cancel。")
                continue
            active_task = asyncio.create_task(
                cli.run_request(user_input),
                name="tsagent-cli-run",
            )
    finally:
        if active_task is not None:
            await active_task
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
