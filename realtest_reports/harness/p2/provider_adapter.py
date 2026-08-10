"""Provider-neutral adapter used by the P2-P portability harness.

The production Runtime currently exposes a process-wide ``agent.llm.llm``
facade.  Portability attempts run in isolated child processes and replace
that facade *before* Runtime modules are imported.  Each process therefore
has exactly one Provider and cannot silently fall back to another Provider.

Only environment-variable *names* are part of a ProviderSpec.  Secret values
are resolved inside the child and are never serialized into benchmark
evidence.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import os
import time
from typing import Any, Callable, Mapping, Protocol


class ProviderErrorCode(str, Enum):
    """Stable, SDK-independent Provider failure taxonomy."""

    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    STRUCTURED_OUTPUT_REJECTED = "STRUCTURED_OUTPUT_REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL = "INTERNAL"


class ProviderUnavailableError(RuntimeError):
    """Raised before execution when a configured Provider is unavailable."""


class MalformedStructuredResponseError(RuntimeError):
    """Deterministic P03 probe failure at the structured-response boundary."""


def classify_provider_error(error: BaseException) -> ProviderErrorCode:
    """Map SDK exceptions to a stable category without persisting messages."""

    if isinstance(error, MalformedStructuredResponseError):
        return ProviderErrorCode.MALFORMED_RESPONSE
    if isinstance(error, ProviderUnavailableError):
        return ProviderErrorCode.UNAVAILABLE
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return ProviderErrorCode.TIMEOUT

    name = type(error).__name__.lower()
    message = str(error).lower()
    combined = f"{name} {message}"
    if any(token in combined for token in ("401", "403", "auth", "api key", "unauthorized")):
        return ProviderErrorCode.AUTH
    if any(token in combined for token in ("429", "rate limit", "ratelimit")):
        return ProviderErrorCode.RATE_LIMIT
    if any(token in combined for token in ("timeout", "timed out", "deadline")):
        return ProviderErrorCode.TIMEOUT
    if any(
        token in combined
        for token in (
            "connection",
            "connecterror",
            "dns",
            "network",
            "name resolution",
        )
    ):
        return ProviderErrorCode.NETWORK
    if any(token in combined for token in ("response_format", "structured output", "json_schema")):
        return ProviderErrorCode.STRUCTURED_OUTPUT_REJECTED
    if any(token in combined for token in ("malformed", "invalid json", "jsondecode", "parse")):
        return ProviderErrorCode.MALFORMED_RESPONSE
    if any(token in combined for token in ("503", "502", "unavailable", "overloaded")):
        return ProviderErrorCode.UNAVAILABLE
    return ProviderErrorCode.INTERNAL


@dataclass(frozen=True)
class ProviderSpec:
    """Secret-free configuration contract for one parity variant."""

    variant: str
    provider_id: str
    api_key_env: str
    model_env: str
    base_url_env: str
    default_model: str = ""
    default_base_url: str = ""
    adapter_kind: str = "openai-compatible"
    endpoint_class: str = "remote"
    structured_output: bool = True

    def __post_init__(self) -> None:
        for name in (
            "variant",
            "provider_id",
            "api_key_env",
            "model_env",
            "base_url_env",
            "adapter_kind",
            "endpoint_class",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"ProviderSpec.{name} must not be empty")

    def resolve(self, environ: Mapping[str, str] | None = None) -> "ResolvedProviderSpec":
        values = os.environ if environ is None else environ
        api_key = str(values.get(self.api_key_env, "") or "").strip()
        model = str(values.get(self.model_env, self.default_model) or "").strip()
        base_url = str(values.get(self.base_url_env, self.default_base_url) or "").strip()
        missing = tuple(
            label
            for label, value in (
                (self.api_key_env, api_key),
                (self.model_env, model),
                (self.base_url_env, base_url),
            )
            if not value
        )
        return ResolvedProviderSpec(
            spec=self,
            api_key=api_key,
            model=model,
            base_url=base_url,
            missing_configuration=missing,
        )

    def to_public_dict(self, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
        resolved = self.resolve(environ)
        return {
            "variant": self.variant,
            "provider_id": self.provider_id,
            "adapter_kind": self.adapter_kind,
            "endpoint_class": self.endpoint_class,
            "model": resolved.model,
            "structured_output": self.structured_output,
            "configured": resolved.available,
            "missing_configuration": list(resolved.missing_configuration),
        }

    def to_config_dict(self) -> dict[str, Any]:
        """Serialize environment indirection, never resolved secret values."""

        return {
            "variant": self.variant,
            "provider_id": self.provider_id,
            "api_key_env": self.api_key_env,
            "model_env": self.model_env,
            "base_url_env": self.base_url_env,
            "default_model": self.default_model,
            "default_base_url": self.default_base_url,
            "adapter_kind": self.adapter_kind,
            "endpoint_class": self.endpoint_class,
            "structured_output": self.structured_output,
        }

    @classmethod
    def from_config_dict(cls, value: Mapping[str, Any]) -> "ProviderSpec":
        return cls(
            variant=str(value.get("variant", "")),
            provider_id=str(value.get("provider_id", "")),
            api_key_env=str(value.get("api_key_env", "")),
            model_env=str(value.get("model_env", "")),
            base_url_env=str(value.get("base_url_env", "")),
            default_model=str(value.get("default_model", "")),
            default_base_url=str(value.get("default_base_url", "")),
            adapter_kind=str(value.get("adapter_kind", "openai-compatible")),
            endpoint_class=str(value.get("endpoint_class", "remote")),
            structured_output=bool(value.get("structured_output", True)),
        )


@dataclass(frozen=True)
class ResolvedProviderSpec:
    spec: ProviderSpec
    api_key: str
    model: str
    base_url: str
    missing_configuration: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return not self.missing_configuration


@dataclass(frozen=True)
class ProviderCallEvidence:
    sequence: int
    call_kind: str
    outcome: str
    latency_ms: float
    error_code: ProviderErrorCode | None = None
    injected_probe: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "call_kind": self.call_kind,
            "outcome": self.outcome,
            "latency_ms": round(self.latency_ms, 3),
            "error_code": self.error_code.value if self.error_code else None,
            "injected_probe": self.injected_probe,
        }


class ProviderRecorder:
    """Collect only stable categories and timings, never raw SDK exceptions."""

    def __init__(self) -> None:
        self._calls: list[ProviderCallEvidence] = []

    @property
    def calls(self) -> tuple[ProviderCallEvidence, ...]:
        return tuple(self._calls)

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(
            call.error_code.value
            for call in self._calls
            if call.error_code is not None
        )

    def record(
        self,
        *,
        call_kind: str,
        outcome: str,
        latency_ms: float,
        error: BaseException | None = None,
        injected_probe: bool = False,
    ) -> None:
        self._calls.append(
            ProviderCallEvidence(
                sequence=len(self._calls) + 1,
                call_kind=call_kind,
                outcome=outcome,
                latency_ms=latency_ms,
                error_code=(classify_provider_error(error) if error else None),
                injected_probe=injected_probe,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_count": len(self._calls),
            "error_count": len(self.error_codes),
            "error_codes": list(self.error_codes),
            "calls": [call.to_dict() for call in self._calls],
        }


class _Runnable(Protocol):
    def invoke(self, messages: Any, **kwargs: Any) -> Any: ...

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any: ...


class _InstrumentedRunnable:
    def __init__(self, inner: _Runnable, recorder: ProviderRecorder, kind: str) -> None:
        self._inner = inner
        self._recorder = recorder
        self._kind = kind

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = self._inner.invoke(messages, **kwargs)
        except BaseException as error:
            self._recorder.record(
                call_kind=self._kind,
                outcome="ERROR",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=error,
            )
            raise
        self._recorder.record(
            call_kind=self._kind,
            outcome="SUCCESS",
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return result

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await self._inner.ainvoke(messages, **kwargs)
        except BaseException as error:
            self._recorder.record(
                call_kind=self._kind,
                outcome="ERROR",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=error,
            )
            raise
        self._recorder.record(
            call_kind=self._kind,
            outcome="SUCCESS",
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return result


class _MalformedProbeRunnable:
    def __init__(self, recorder: ProviderRecorder) -> None:
        self._recorder = recorder

    def _raise(self) -> None:
        error = MalformedStructuredResponseError(
            "deterministic malformed structured-response probe"
        )
        self._recorder.record(
            call_kind="structured_probe",
            outcome="ERROR",
            latency_ms=0.0,
            error=error,
            injected_probe=True,
        )
        raise error

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        self._raise()

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        self._raise()


class _ProviderProxy:
    """Expose the subset used by Planner while preserving call evidence."""

    def __init__(self, router: "FixedProviderRouter", inner: Any) -> None:
        self._router = router
        self._inner = inner

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        if self._router.consume_malformed_structured_probe():
            return _MalformedProbeRunnable(self._router.recorder)
        runnable = self._inner.with_structured_output(schema, **kwargs)
        return _InstrumentedRunnable(runnable, self._router.recorder, "structured")

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        runnable = self._inner.bind_tools(tools, **kwargs)
        return _InstrumentedRunnable(runnable, self._router.recorder, "tool_bound")


ModelFactory = Callable[[ResolvedProviderSpec, float], Any]


def _default_model_factory(config: ResolvedProviderSpec, timeout: float) -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=config.model,
        openai_api_key=config.api_key,
        openai_api_base=config.base_url,
        temperature=0,
        timeout=timeout,
        max_retries=0,
    )


class FixedProviderRouter:
    """One-Provider LLM facade compatible with the current Runtime contract."""

    def __init__(
        self,
        spec: ProviderSpec,
        *,
        environ: Mapping[str, str] | None = None,
        timeout: float = 45.0,
        model_factory: ModelFactory = _default_model_factory,
        inject_malformed_structured_once: bool = False,
    ) -> None:
        self.spec = spec
        self.resolved = spec.resolve(environ)
        if not self.resolved.available:
            raise ProviderUnavailableError(
                "provider configuration is incomplete: "
                + ", ".join(self.resolved.missing_configuration)
            )
        self.recorder = ProviderRecorder()
        self._model = model_factory(self.resolved, timeout)
        self._proxy = _ProviderProxy(self, self._model)
        self._structured_output_available = spec.structured_output
        self._inject_malformed_structured_once = inject_malformed_structured_once
        self._malformed_probe_consumed = False

    @property
    def supports_structured_output(self) -> bool:
        return self._structured_output_available

    def disable_structured_output(self) -> None:
        self._structured_output_available = False

    def consume_malformed_structured_probe(self) -> bool:
        if not self._inject_malformed_structured_once or self._malformed_probe_consumed:
            return False
        self._malformed_probe_consumed = True
        return True

    def arm_malformed_structured_probe(self) -> None:
        """Arm one deterministic P03 structured-response boundary failure."""

        self._inject_malformed_structured_once = True
        self._malformed_probe_consumed = False

    def _get_active_provider(self) -> tuple[Any, str]:
        return self._proxy, self.spec.provider_id

    @staticmethod
    def _with_time(messages: Any) -> Any:
        # Import lazily so the harness can install this router before the rest
        # of the Runtime captures ``agent.llm.llm``.
        from agent.llm import _inject_time_to_system

        return _inject_time_to_system(messages)

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        runnable = _InstrumentedRunnable(self._model, self.recorder, "chat")
        return runnable.invoke(self._with_time(messages), **kwargs)

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        runnable = _InstrumentedRunnable(self._model, self.recorder, "chat")
        return await runnable.ainvoke(self._with_time(messages), **kwargs)

    def bind_tools(self, tools: Any) -> Any:
        return self._proxy.bind_tools(tools)

    @property
    def status(self) -> dict[str, Any]:
        return {
            self.spec.provider_id: {
                "model": self.resolved.model,
                "available": True,
            },
            "fallback_count": 0,
            "last_error": "",
        }

    def public_evidence(self) -> dict[str, Any]:
        evidence = self.recorder.to_dict()
        evidence["fallback_count"] = 0
        evidence["injected_error_count"] = sum(
            1
            for call in self.recorder.calls
            if call.error_code is not None and call.injected_probe
        )
        return evidence


def install_fixed_provider(router: FixedProviderRouter) -> None:
    """Install one Provider before importing Runtime consumers."""

    import agent.llm as llm_module

    llm_module.llm = router  # type: ignore[assignment]


def default_provider_specs() -> tuple[ProviderSpec, ProviderSpec]:
    """Return parity variants without resolving or persisting credentials."""

    return (
        ProviderSpec(
            variant="primary",
            provider_id=os.getenv("P2_PRIMARY_PROVIDER", "deepseek"),
            api_key_env="OPENAI_API_KEY",
            model_env="MODEL_NAME",
            base_url_env="P2_PRIMARY_BASE_URL",
            default_model="deepseek-v4-flash",
            default_base_url="https://api.deepseek.com/v1",
            endpoint_class="openai-compatible-primary",
            structured_output=True,
        ),
        ProviderSpec(
            variant="secondary",
            provider_id=os.getenv("P2_SECONDARY_PROVIDER", "secondary"),
            api_key_env="P2_SECONDARY_API_KEY",
            model_env="P2_SECONDARY_MODEL",
            base_url_env="P2_SECONDARY_BASE_URL",
            endpoint_class="openai-compatible-secondary",
            structured_output=os.getenv(
                "P2_SECONDARY_STRUCTURED_OUTPUT", "1"
            ).strip().lower()
            not in {"0", "false", "no"},
        ),
    )


__all__ = [
    "FixedProviderRouter",
    "MalformedStructuredResponseError",
    "ProviderCallEvidence",
    "ProviderErrorCode",
    "ProviderRecorder",
    "ProviderSpec",
    "ProviderUnavailableError",
    "ResolvedProviderSpec",
    "classify_provider_error",
    "default_provider_specs",
    "install_fixed_provider",
]
