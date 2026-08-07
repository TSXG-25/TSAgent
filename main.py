"""Interactive CLI adapter for the public AgentService boundary."""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from pathlib import Path

from agent.bootstrap import load_all, load_all_async
from agent.service import (
    AgentService,
    EventStreamRequest,
    EventType,
    RunLookupRequest,
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
        print(f"\n📌 Run: {handle.run_id}")
        print("📡 处理中...")

        stream_request = EventStreamRequest(
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            session_id=self._session_id,
            run_id=handle.run_id,
            request_id=f"stream-{uuid.uuid4().hex}",
            after_sequence=0,
        )
        async for event in self._service.stream_events(stream_request):
            print(f"  • {event.sequence_number}: {event.event_type.value}")
            if event.event_type is EventType.RUN_FAILED:
                print("❌ Run 失败")
            elif event.event_type is EventType.RUN_COMPLETED:
                print("✅ Run 完成")

        snapshot = await self._service.get_run(
            RunLookupRequest(
                tenant_id=self._tenant_id,
                user_id=self._user_id,
                session_id=self._session_id,
                run_id=handle.run_id,
                request_id=f"lookup-{uuid.uuid4().hex}",
            )
        )
        print(f"🤖 状态: {snapshot.status.value}")


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
    try:
        while True:
            user_input = await asyncio.to_thread(input, "\n你: ")
            if user_input.strip().lower() in {"exit", "quit"}:
                break
            if not user_input.strip():
                continue
            await cli.run_request(user_input)
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
