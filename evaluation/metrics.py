"""Shared metric contracts.

All metric families use the same pipeline:

    MetricDefinition -> MetricCollector -> MetricReport -> TrendGate

The v1/v2 dataclasses remain public compatibility facades, but new metrics
should register definitions here instead of creating another ``metrics_vN``
module.
"""
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional


@dataclass(frozen=True)
class MetricDefinition:
    """Semantic contract for one metric."""

    name: str
    direction: str = "ge"  # ge: higher is better; le: lower is better
    default: float = 0.0
    capability: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if self.direction not in ("ge", "le"):
            raise ValueError(f"unsupported metric direction: {self.direction!r}")


@dataclass(frozen=True)
class MetricReport:
    """Normalized metric output consumed by reporters and trend gates."""

    values: Mapping[str, float]
    definitions: tuple[MetricDefinition, ...]

    def to_dict(self) -> dict[str, float]:
        return {name: round(float(value), 6) for name, value in self.values.items()}


@dataclass(frozen=True)
class TrendResult:
    passes: bool
    failures: tuple[str, ...] = ()


class MetricCollector:
    """Collect a stable set of named metrics from arbitrary evaluators."""

    def __init__(self, definitions: Iterable[MetricDefinition]):
        self.definitions = tuple(definitions)
        names = [d.name for d in self.definitions]
        if len(names) != len(set(names)):
            raise ValueError("duplicate metric definition")

    def collect(self, values: Optional[Mapping[str, Any]] = None) -> MetricReport:
        values = values or {}
        normalized = {
            definition.name: float(values.get(definition.name, definition.default))
            for definition in self.definitions
        }
        return MetricReport(normalized, self.definitions)


class MetricReporter:
    """Format metric reports without embedding evaluation policy."""

    @staticmethod
    def to_dict(report: MetricReport) -> dict[str, float]:
        return report.to_dict()

    @staticmethod
    def to_lines(report: MetricReport) -> list[str]:
        return [f"{name}: {value:.3f}" for name, value in report.values.items()]


class TrendGate:
    """Compare two reports using their metric definitions."""

    @staticmethod
    def evaluate(
        current: MetricReport,
        previous: Optional[MetricReport],
    ) -> TrendResult:
        if previous is None:
            return TrendResult(True)

        previous_values = previous.values
        failures = []
        for definition in current.definitions:
            if definition.name not in previous_values:
                continue
            cur = float(current.values.get(definition.name, definition.default))
            prev = float(previous_values[definition.name])
            if definition.direction == "ge" and cur < prev - 1e-9:
                failures.append(f"{definition.name}: {prev:.3f} → {cur:.3f}（下降）")
            elif definition.direction == "le" and cur > prev + 1e-9:
                failures.append(
                    f"{definition.name}: {prev:.3f} → {cur:.3f}（上升，应为 0）"
                )
        return TrendResult(not failures, tuple(failures))

