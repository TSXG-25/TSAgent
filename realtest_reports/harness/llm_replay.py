"""LLM cassette record/replay layer for the shared ``agent.llm`` router.

Between "real Provider" and "deterministic fixture", this layer records the
Provider exchanges of one real run into a JSON cassette, then replays the
same requests offline.  Replayed evidence is deterministic regression
evidence about the Runtime's request sequence — it is not fresh Provider
evidence and must be reported as such.

Seams (all traffic converges on the Provider boundary):

- ``record`` wraps ``LLMRouter._get_active_provider`` so every Provider call
  — ``invoke``/``ainvoke``/``achat`` plus ``with_structured_output``
  runnables used by memory learning — is observed once, and wraps
  ``agent.llm._deepseek_achat`` which bypasses ``provider.achat``.
- ``replay`` swaps the two ``_provider_*`` properties for a cassette-backed
  Provider and replaces ``_deepseek_achat``.  The router's own invoke path —
  time injection, provider selection, retry and fallback — keeps running, so
  a recorded ``error, error, success`` failover sequence replays exactly as
  it happened.  ``_retryable_provider_error`` is patched so a cassette miss
  is never retried; it surfaces as a ProviderError carrying the miss.

Digest keys cover the request only: call kind, canonical messages, tool
specs, scalar kwargs, and the structured-output schema id.  Provider
identity is stored as evidence, not as a matching key.  ``[当前时间: ...]``
hints are normalized so replay matches regardless of wall clock; replay at
the router seam re-applies the same time injection first so the canonical
request matches what the Provider seam recorded.

Known gap: ``bind_tools`` runnables are outside coverage — no production
caller routes through them; replaying one raises immediately.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

CASSETTE_VERSION = 1
# Every wall-clock injection found in prompt construction must be
# normalized, or replay can never match a recording from another minute.
# Sources: agent.llm time hint, ContextBuilder.render_system_context,
# answer_generator search synthesis, memory history/recall time tags.
_TIME_HINT_PATTERNS = (
    re.compile(r"\[当前时间: [^\]]+\]"),
    re.compile(r"当前时间[:：]\s*\d{4}年\d{1,2}月\d{1,2}日[^\n\]]*"),
    re.compile(r"当前时间[:：]\s*\d{4}-\d{2}-\d{2}[^\n\]]*"),
    re.compile(
        r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
        r"(?:Z|[+-]\d{2}:?\d{2})?\]"
    ),
)
_LC_ROLE = {"human": "user", "ai": "assistant"}


class ReplayMissError(RuntimeError):
    """A request was replayed that has no cassette entry."""


def _normalize_text(text: Any) -> str:
    if not isinstance(text, str):
        return json.dumps(text, ensure_ascii=False, sort_keys=True, default=str)
    for pattern in _TIME_HINT_PATTERNS:
        text = pattern.sub("<TIME>", text)
    return text


def _canonical_calls(calls: Any) -> list[dict[str, Any]]:
    result = []
    for call in calls or ():
        if isinstance(call, Mapping):
            result.append(
                {
                    "id": str(call.get("id", "")),
                    "name": str(call.get("name", "")),
                    "arguments": call.get("arguments", call.get("args", {})),
                }
            )
        else:
            result.append(
                {
                    "id": str(getattr(call, "id", "")),
                    "name": str(getattr(call, "name", "")),
                    "arguments": getattr(
                        call, "arguments", getattr(call, "args", {})
                    ),
                }
            )
    return result


def canonical_message(message: Any) -> dict[str, Any]:
    """Project a transcript entry (dict or LangChain message) to one shape."""
    if isinstance(message, Mapping):
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        calls = message.get("tool_calls")
        extra = {
            key: str(message[key])
            for key in ("tool_call_id", "name")
            if message.get(key)
        }
    else:
        message_type = str(getattr(message, "type", "user"))
        role = _LC_ROLE.get(message_type, message_type)
        content = getattr(message, "content", "")
        calls = getattr(message, "tool_calls", None)
        extra = {
            key: str(getattr(message, key))
            for key in ("tool_call_id", "name")
            if getattr(message, key, None)
        }
    canonical = {
        "role": role,
        "content": _normalize_text(content),
        "tool_calls": _canonical_calls(calls),
    }
    canonical.update(extra)
    return canonical


def canonical_messages(messages: Sequence[Any]) -> list[dict[str, Any]]:
    return [canonical_message(message) for message in messages]


def _sanitized_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in kwargs.items()
        if isinstance(value, (str, int, float, bool, type(None)))
    }


def _schema_id(schema: Any) -> str:
    qualname = getattr(schema, "__qualname__", None)
    if qualname:
        return f"{getattr(schema, '__module__', '')}.{qualname}"
    if isinstance(schema, Mapping):
        return str(schema.get("title") or sorted(schema)[:8])
    return type(schema).__name__


def request_digest(
    kind: str,
    messages: Sequence[Any],
    tools: Sequence[Any] | None,
    kwargs: Mapping[str, Any],
    schema: Any = None,
) -> str:
    payload = {
        "kind": kind,
        "messages": canonical_messages(messages),
        "tools": list(tools or ()),
        "kwargs": _sanitized_kwargs(kwargs),
        "schema": _schema_id(schema) if schema is not None else "",
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    return str(value)


def _response_payload(kind: str, response: Any) -> dict[str, Any]:
    if kind == "chat":
        return {
            "content": str(getattr(response, "content", "")),
            "tool_calls": _canonical_calls(
                [
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in getattr(response, "tool_calls", ())
                ]
            ),
        }
    if kind == "structured":
        return {"data": _jsonable(response)}
    return {
        "content": str(getattr(response, "content", "")),
        "metadata": _jsonable(getattr(response, "response_metadata", {})),
    }


def _error_payload(error: BaseException) -> dict[str, Any]:
    from agent.execution_errors import stable_error_message

    return {
        "error": {
            "type": type(error).__name__,
            "message": stable_error_message(
                error, fallback="provider call failed"
            )[:500],
        }
    }


def _build_response(entry: Mapping[str, Any]) -> Any:
    from agent.llm import ChatTurn, ToolCall

    kind = str(entry["request"]["kind"])
    response = entry["response"]
    error = response.get("error")
    if error:
        raise RuntimeError(
            f"{error.get('type', 'ProviderError')}: {error.get('message', '')}"
        )
    if kind == "chat":
        return ChatTurn(
            content=str(response.get("content", "")),
            tool_calls=tuple(
                ToolCall(
                    id=str(call.get("id", "")),
                    name=str(call.get("name", "")),
                    arguments=dict(call.get("arguments") or {}),
                )
                for call in response.get("tool_calls", ())
            ),
        )
    return SimpleNamespace(
        content=str(response.get("content", "")),
        response_metadata=dict(response.get("metadata") or {}),
    )


class Cassette:
    """Ordered list of recorded Provider exchanges persisted as one JSON."""

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries: list[dict[str, Any]] = entries or []

    @classmethod
    def load(cls, path: Path) -> "Cassette":
        if not path.exists():
            raise ReplayMissError(f"cassette not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("version", 0)) != CASSETTE_VERSION:
            raise ReplayMissError(
                f"unsupported cassette version {payload.get('version')}: {path}"
            )
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ReplayMissError(f"cassette has no entries: {path}")
        return cls(list(entries))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CASSETTE_VERSION,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "entries": self.entries,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


@dataclass
class ReplaySummary:
    mode: str
    cassette: str
    recorded: int = 0
    replayed: int = 0
    misses: list[dict[str, str]] = field(default_factory=list)
    unconsumed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "cassette": self.cassette,
            "recorded": self.recorded,
            "replayed": self.replayed,
            "misses": list(self.misses),
            "unconsumed": self.unconsumed,
        }


class _RecordingRunnable:
    """Wrap a with_structured_output runnable; record each invocation."""

    def __init__(self, inner: Any, schema: Any, provider: str, session: "ReplaySession") -> None:
        self._inner = inner
        self._schema = schema
        self._provider = provider
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        try:
            response = self._inner.invoke(messages, **kwargs)
        except Exception as error:
            self._session.record_error(
                "structured", self._provider, messages, None, {}, error,
                schema=self._schema,
            )
            raise
        self._session.record(
            "structured", self._provider, messages, None, {}, response,
            schema=self._schema,
        )
        return response

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        try:
            response = await self._inner.ainvoke(messages, **kwargs)
        except Exception as error:
            self._session.record_error(
                "structured", self._provider, messages, None, {}, error,
                schema=self._schema,
            )
            raise
        self._session.record(
            "structured", self._provider, messages, None, {}, response,
            schema=self._schema,
        )
        return response


class _ReplayRunnable:
    def __init__(self, schema: Any, session: "ReplaySession") -> None:
        self._schema = schema
        self._session = session

    def _take(self, messages: Any) -> Any:
        entry = self._session.take(
            "structured", messages, None, {}, schema=self._schema
        )
        data = entry["response"].get("data")
        validate = getattr(self._schema, "model_validate", None)
        if callable(validate):
            return validate(data)
        return data

    def invoke(self, messages: Any, **_kwargs: Any) -> Any:
        return self._take(messages)

    async def ainvoke(self, messages: Any, **_kwargs: Any) -> Any:
        return self._take(messages)


class _RecordingProvider:
    """Provider proxy that records each boundary call then delegates."""

    def __init__(self, inner: Any, provider: str, session: "ReplaySession") -> None:
        self._inner = inner
        self._provider = provider
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        try:
            response = self._inner.invoke(messages, **kwargs)
        except Exception as error:
            self._session.record_error(
                "invoke", self._provider, messages, None, kwargs, error
            )
            raise
        self._session.record(
            "invoke", self._provider, messages, None, kwargs, response
        )
        return response

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        try:
            response = await self._inner.ainvoke(messages, **kwargs)
        except Exception as error:
            self._session.record_error(
                "invoke", self._provider, messages, None, kwargs, error
            )
            raise
        self._session.record(
            "invoke", self._provider, messages, None, kwargs, response
        )
        return response

    async def achat(self, messages: Any, tools: Any, **kwargs: Any) -> Any:
        try:
            response = await self._inner.achat(messages, tools, **kwargs)
        except Exception as error:
            self._session.record_error(
                "chat", self._provider, messages, tools, kwargs, error
            )
            raise
        self._session.record(
            "chat", self._provider, messages, tools, kwargs, response
        )
        return response

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        return _RecordingRunnable(
            self._inner.with_structured_output(schema, **kwargs),
            schema,
            self._provider,
            self._session,
        )


class _ReplayProvider:
    """Stand-in Provider answering from the cassette at the boundary."""

    model = "replay"
    model_name = "replay"
    base_url = "replay://cassette"
    openai_api_base = "replay://cassette"
    openai_api_key = "replay"
    timeout = 0.0
    request_timeout = 0.0

    def __init__(self, session: "ReplaySession") -> None:
        self._session = session

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        return _build_response(
            self._session.take("invoke", messages, None, kwargs)
        )

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        return _build_response(
            self._session.take("invoke", messages, None, kwargs)
        )

    async def achat(
        self, messages: Any, tools: Any, on_delta: Any = None, **kwargs: Any
    ) -> Any:
        turn = _build_response(
            self._session.take("chat", messages, tools, kwargs)
        )
        if callable(on_delta) and turn.content:
            on_delta(turn.content)
        return turn

    def with_structured_output(self, schema: Any, **_kwargs: Any) -> Any:
        return _ReplayRunnable(schema, self._session)

    def bind_tools(self, _tools: Any, **_kwargs: Any) -> Any:
        raise ReplayMissError("LLM replay does not cover bind_tools runnables")


class ReplaySession:
    """Installed record/replay state; ``close()`` restores all patches."""

    def __init__(self, mode: str, path: Path, router: Any) -> None:
        if mode not in {"record", "replay"}:
            raise ValueError(f"invalid replay mode {mode!r}")
        self.mode = mode
        self.path = Path(path)
        self.router = router
        self.cassette = (
            Cassette.load(self.path) if mode == "replay" else Cassette()
        )
        self._index: dict[str, deque] = defaultdict(deque)
        for entry in self.cassette.entries:
            self._index[str(entry.get("digest", ""))].append(entry)
        self.summary = ReplaySummary(mode=mode, cassette=str(self.path))
        self._patches: list[tuple[Any, str, Any]] = []

    # ── patch bookkeeping ──

    def _patch(self, owner: Any, name: str, replacement: Any) -> None:
        self._patches.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    def _wrap_provider(self, provider: Any, name: str) -> Any:
        # _get_active_provider and the patched _provider_* properties both
        # feed providers through here; wrapping must be idempotent or every
        # call would be recorded once per proxy layer.
        if isinstance(provider, (_RecordingProvider, _ReplayProvider)):
            return provider
        if self.mode == "replay":
            return _ReplayProvider(self)
        return _RecordingProvider(provider, name, self)

    def install(self) -> "ReplaySession":
        import agent.llm as llm_module

        router = self.router
        provider = _ReplayProvider(self)

        if self.mode == "record":
            original_get = router._get_active_provider

            def recording_get() -> tuple[Any, str]:
                inner, name = original_get()
                return self._wrap_provider(inner, name), name

            self._patch(router, "_get_active_provider", recording_get)

            original_achat = llm_module._deepseek_achat
            session = self

            async def recording_achat(prov, messages, tools, kwargs):
                try:
                    turn = await original_achat(prov, messages, tools, kwargs)
                except Exception as error:
                    session.record_error(
                        "chat", "deepseek", messages, tools, dict(kwargs), error
                    )
                    raise
                session.record(
                    "chat", "deepseek", messages, tools, dict(kwargs), turn
                )
                return turn

            self._patch(llm_module, "_deepseek_achat", recording_achat)

            for prop_name, provider_name in (
                ("_provider_deepseek", "deepseek"),
                ("_provider_ollama", "ollama"),
            ):
                original_prop = getattr(type(router), prop_name)

                def make_prop(orig, pname):
                    def wrapped(router_self):
                        return self._wrap_provider(orig.fget(router_self), pname)

                    return property(wrapped)

                self._patch(
                    type(router),
                    prop_name,
                    make_prop(original_prop, provider_name),
                )
            return self

        session = self

        # Provider-boundary replay: the router's own select/retry/fallback
        # path stays live, so recorded failover sequences reproduce exactly.
        original_retryable = llm_module._retryable_provider_error

        def retryable(error: object) -> bool:
            if isinstance(error, ReplayMissError):
                return False
            return original_retryable(error)

        self._patch(llm_module, "_retryable_provider_error", retryable)

        async def replay_deepseek_achat(prov, messages, tools, kwargs):
            turn = _build_response(
                session.take("chat", messages, tools, dict(kwargs))
            )
            on_delta = kwargs.get("on_delta")
            if callable(on_delta) and turn.content:
                on_delta(turn.content)
            return turn

        self._patch(llm_module, "_deepseek_achat", replay_deepseek_achat)

        for prop_name in ("_provider_deepseek", "_provider_ollama"):
            original_prop = getattr(type(router), prop_name)
            self._patch(
                type(router), prop_name, property(lambda _self: provider)
            )
        return self

    # ── cassette operations ──

    def record(
        self,
        kind: str,
        provider: str,
        messages: Any,
        tools: Any,
        kwargs: Mapping[str, Any],
        response: Any,
        *,
        schema: Any = None,
    ) -> None:
        digest = request_digest(kind, messages, tools, kwargs, schema)
        entry = {
            "digest": digest,
            "request": {
                "kind": kind,
                "provider": provider,
                "messages": canonical_messages(messages),
                "tools": _jsonable(list(tools or ())),
                "kwargs": _sanitized_kwargs(kwargs),
                "schema": _schema_id(schema) if schema is not None else "",
            },
            "response": _response_payload(kind, response),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.cassette.entries.append(entry)
        self.summary.recorded += 1
        # Write-through: a killed real run still leaves complete evidence.
        self.cassette.save(self.path)

    def record_error(
        self,
        kind: str,
        provider: str,
        messages: Any,
        tools: Any,
        kwargs: Mapping[str, Any],
        error: BaseException,
        *,
        schema: Any = None,
    ) -> None:
        """Record a Provider failure; replay re-raises it as evidence."""
        digest = request_digest(kind, messages, tools, kwargs, schema)
        entry = {
            "digest": digest,
            "request": {
                "kind": kind,
                "provider": provider,
                "messages": canonical_messages(messages),
                "tools": _jsonable(list(tools or ())),
                "kwargs": _sanitized_kwargs(kwargs),
                "schema": _schema_id(schema) if schema is not None else "",
            },
            "response": _error_payload(error),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.cassette.entries.append(entry)
        self.summary.recorded += 1
        self.cassette.save(self.path)

    def take(
        self,
        kind: str,
        messages: Any,
        tools: Any,
        kwargs: Mapping[str, Any],
        *,
        schema: Any = None,
    ) -> Mapping[str, Any]:
        digest = request_digest(kind, messages, tools, kwargs, schema)
        queue = self._index.get(digest)
        if not queue:
            self.summary.misses.append({"digest": digest, "kind": kind})
            raise ReplayMissError(
                f"no cassette entry for {kind} digest {digest[:16]}… "
                f"({len(canonical_messages(messages))} messages); "
                f"the request sequence diverged from the recording"
            )
        self.summary.replayed += 1
        return queue.popleft()

    def close(self) -> ReplaySummary:
        for owner, name, original in reversed(self._patches):
            setattr(owner, name, original)
        self._patches.clear()
        if self.mode == "record":
            self.cassette.save(self.path)
        self.summary.unconsumed = sum(len(queue) for queue in self._index.values())
        return self.summary

    def __enter__(self) -> "ReplaySession":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def install(
    mode: str,
    cassette_path: str | Path,
    *,
    router: Any = None,
) -> ReplaySession:
    """Install record/replay on the shared router; restore via ``close()``."""
    if router is None:
        from agent.llm import llm as router

    return ReplaySession(mode, Path(cassette_path), router).install()
