"""D4b real-provider discovery harness for D401, D408 and D409.

The harness intentionally uses the public AgentService path.  D401 invokes
the CLI cancellation adapter so the discovery covers the D4a control-plane
entry point without calling ``asyncio.Task.cancel`` directly.

Required environment variables:

``D4B_DATABASE``
    Per-case SQLite database path.
``D4B_WORKSPACE``
    Per-case workspace root.
``D4B_RESULT``
    JSON result path.
``OLLAMA_MODEL``
    Ollama model name, for example ``qwen2.5:14b``.

Optional:

``D4B_CASE``
    ``D401`` (default), ``D408`` or ``D409``.
``D4B_HEAD``
    Source commit recorded in the result.

The harness does not archive credentials or full provider responses.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path


async def main() -> None:
    from langchain_openai import ChatOpenAI

    from agent.bootstrap import load_all, load_all_async
    from agent.llm import llm
    from agent.service import (
        EventStreamRequest,
        RunLookupRequest,
        RunStatus,
        StartRunRequest,
        create_default_agent_service,
    )
    from main import ServiceCLI

    case_id = os.environ.get("D4B_CASE", "D401")
    is_cancel = case_id == "D401"
    is_run_timeout = case_id == "D408"
    is_provider_timeout = case_id == "D409"
    if case_id not in {"D401", "D408", "D409"}:
        raise ValueError(f"unsupported discovery case: {case_id}")

    load_all()
    await load_all_async()

    provider_started = asyncio.Event()
    provider_started_at: float | None = None
    original_ainvoke = ChatOpenAI.ainvoke

    async def traced_ainvoke(self, *args, **kwargs):
        nonlocal provider_started_at
        model_name = getattr(self, "model_name", getattr(self, "model", ""))
        if model_name == os.environ["OLLAMA_MODEL"]:
            provider_started_at = time.monotonic()
            provider_started.set()
        return await original_ainvoke(self, *args, **kwargs)

    ChatOpenAI.ainvoke = traced_ainvoke
    llm._deepseek_available = False
    llm._ollama_available = True

    database = Path(os.environ["D4B_DATABASE"])
    workspace = Path(os.environ["D4B_WORKSPACE"])
    database.parent.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    service = create_default_agent_service(database, workspace_root=workspace)

    run_id = f"run-{case_id.lower()}-discovery"
    request_id = f"{case_id.lower()}-start-1"
    metadata = {"run_timeout_seconds": 5.0} if is_run_timeout else {}
    request_text = (
        "请生成一份非常详细的长篇技术分析，至少分成十个章节，"
        "每章包含多个小节和具体例子；在所有内容完成前不要提前结束。"
    )
    if is_provider_timeout:
        request_text = "请生成一段详细的技术说明。"

    start_request = StartRunRequest(
        tenant_id="d4b-tenant",
        user_id="d4b-user",
        session_id="d4b-session",
        run_id=run_id,
        request_id=request_id,
        request_text=request_text,
        metadata=metadata,
    )
    started_at = time.monotonic()
    handle = await service.start_run(start_request)
    await asyncio.wait_for(provider_started.wait(), timeout=90)

    cancel_requested_at: float | None = None
    cancel_snapshot = None
    if is_cancel:
        cancel_requested_at = time.monotonic()
        cli = ServiceCLI(
            service,
            tenant_id="d4b-tenant",
            user_id="d4b-user",
            session_id="d4b-session",
        )
        # ServiceCLI.cancel_run is the public adapter operation; this only
        # supplies the currently active Run that an interactive CLI would own.
        cli._active_run_id = run_id
        cancel_snapshot = await cli.cancel_run()

    events = []
    stream = service.stream_events(
        EventStreamRequest(
            tenant_id="d4b-tenant",
            user_id="d4b-user",
            session_id="d4b-session",
            run_id=run_id,
            request_id=f"{case_id.lower()}-stream-1",
            after_sequence=0,
        )
    )
    async for event in stream:
        events.append(event.to_dict())

    terminal_at = time.monotonic()
    final_snapshot = await service.get_run(
        RunLookupRequest(
            tenant_id="d4b-tenant",
            user_id="d4b-user",
            session_id="d4b-session",
            run_id=run_id,
            request_id=f"{case_id.lower()}-lookup-1",
        )
    )
    await service.close()
    ChatOpenAI.ainvoke = original_ainvoke

    terminal_events = [
        event
        for event in events
        if event["event_type"]
        in {
            "run_completed",
            "run_failed",
            "run_blocked",
            "run_cancelled",
            "run_timed_out",
        }
    ]
    failure_codes = tuple(
        str(event.get("payload", {}).get("failure_code", ""))
        for event in terminal_events
        if str(event.get("payload", {}).get("failure_code", "")).strip()
    )
    result = {
        "case_id": case_id,
        "head": os.environ.get("D4B_HEAD", ""),
        "provider": "ollama",
        "model": os.environ["OLLAMA_MODEL"],
        "fallback_disabled": is_cancel or is_run_timeout,
        "run_id": handle.run_id,
        "service_cancel_status": (
            None if cancel_snapshot is None else cancel_snapshot.status.value
        ),
        "final_status": final_snapshot.status.value,
        "events": events,
        "terminal_events": terminal_events,
        "failure_codes": failure_codes,
        "provider_started": provider_started.is_set(),
        "provider_fallback_count": llm.status["fallback_count"],
        "timestamps": {
            "run_started_monotonic": started_at,
            "provider_wait_started_monotonic": provider_started_at,
            "cancel_requested_monotonic": cancel_requested_at,
            "terminal_monotonic": terminal_at,
        },
        "runtime_correctness": {
            "provider_started": provider_started.is_set(),
            "cancel_accepted_as_cancelling": (
                cancel_snapshot is not None
                and cancel_snapshot.status is RunStatus.CANCELLING
            ),
            "terminal_cancelled": (
                None if not is_cancel else final_snapshot.status is RunStatus.CANCELLED
            ),
            "terminal_timed_out": (
                None
                if not is_run_timeout
                else final_snapshot.status is RunStatus.TIMED_OUT
            ),
            "provider_timeout_not_run_timeout": (
                None
                if not is_provider_timeout
                else final_snapshot.status is not RunStatus.TIMED_OUT
            ),
            "cancelled_event_count_matches": (
                sum(event["event_type"] == "run_cancelled" for event in events)
                == (1 if is_cancel else 0)
            ),
            "timed_out_event_count_matches": (
                sum(event["event_type"] == "run_timed_out" for event in events)
                == (1 if is_run_timeout else 0)
            ),
            "no_completed_event_after_interruption": (
                not any(event["event_type"] == "run_completed" for event in events)
                if (is_cancel or is_run_timeout or is_provider_timeout)
                else True
            ),
            "no_provider_fallback_after_cancel": (
                not is_cancel or llm.status["fallback_count"] == 0
            ),
        },
    }
    output = Path(os.environ["D4B_RESULT"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
