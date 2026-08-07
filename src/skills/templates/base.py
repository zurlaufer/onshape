"""Template protocol and the diagnostic type the skill speaks in.

A template renders two things from one set of parameters:

  `render_probe(params)`  a bare lambda holding the computation and its
                          assertions, which Onshape's evaluate endpoint accepts
                          and reports on with line numbers
  `render_module(params)` the full Feature Studio module, same computation
                          wrapped in the fixed `defineFeature` boilerplate

Splitting them is what makes real diagnostics possible. Measured 2026-08-07: the
evaluate endpoint rejects `annotation`, `export` and `import` outright, but for a
bare lambda it returns PARSE / SEMANTIC / EXECUTION notices with messages and
line numbers — including "query matched nothing", the failure class that matters
most. The `defineFeature` wrapper is boilerplate we author once and never vary,
so it is not a plausible source of error; the body is.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

__all__ = ["Problem", "Severity", "Template", "TemplateError", "ParamSpec"]


class TemplateError(ValueError):
    """A template was asked for something it cannot render."""


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    """One diagnostic.

    Written to be *actionable by the model without further tool calls*: it names
    the parameter, states the rule that failed with the actual numbers, and
    suggests a fix. When a check runs locally there is no Onshape message to
    fall back on, so this text is the entire feedback channel.
    """

    severity: Severity
    message: str
    parameter: str | None = None
    suggestion: str | None = None
    #: 1-based source position, when Onshape reported one.
    line: int | None = None
    column: int | None = None

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"severity": self.severity.value, "message": self.message}
        if self.parameter:
            out["parameter"] = self.parameter
        if self.suggestion:
            out["suggestion"] = self.suggestion
        if self.line:
            out["line"] = self.line
            if self.column:
                out["column"] = self.column
        return out


@dataclass(frozen=True)
class ParamSpec:
    """One template parameter, described well enough to choose a value blind."""

    name: str
    type: str
    description: str
    unit: str | None = None
    default: Any = None
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "required": self.required,
        }
        if self.unit:
            out["unit"] = self.unit
        if self.default is not None:
            out["default"] = self.default
        return out


@runtime_checkable
class Template(Protocol):
    """A parameterised, hand-verified FeatureScript generator."""

    name: str
    summary: str

    def parameters(self) -> list[ParamSpec]:
        """Parameter schema, for the model to fill without guessing."""

    def validate(self, params: dict[str, Any]) -> list[Problem]:
        """Arithmetic and dimensional checks. No geometry kernel, no network.

        This is the honest semantic validation: it catches designs that are
        impossible on paper (a hole wider than the tube) for free, before any
        request is spent. It cannot catch topological failure — nothing local
        can — which is what the Onshape probe is for.
        """

    def render_probe(self, params: dict[str, Any]) -> str:
        """A bare lambda for Onshape's evaluate endpoint.

        Must contain no `import`, `annotation` or `export`; the endpoint rejects
        all three. The standard library is available implicitly.
        """

    def render_module(self, params: dict[str, Any]) -> str:
        """The full Feature Studio module."""


def merge_defaults(specs: list[ParamSpec], params: dict[str, Any]) -> dict[str, Any]:
    """Apply declared defaults, leaving explicit values untouched."""
    merged = dict(params)
    for spec in specs:
        if spec.name not in merged and spec.default is not None:
            merged[spec.name] = spec.default
    return merged


def check_required(specs: list[ParamSpec], params: dict[str, Any]) -> list[Problem]:
    """Layer 4: the template contract — required present, numbers numeric."""
    problems: list[Problem] = []
    known = {s.name for s in specs}

    for spec in specs:
        if spec.required and params.get(spec.name) is None:
            problems.append(
                Problem(
                    Severity.ERROR,
                    f"Missing required parameter {spec.name!r}: {spec.description}",
                    parameter=spec.name,
                    suggestion=(
                        f"Supply {spec.name}"
                        + (f" in {spec.unit}" if spec.unit else "")
                        + "."
                    ),
                )
            )
            continue

        value = params.get(spec.name)
        if value is not None and spec.type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(
                    Problem(
                        Severity.ERROR,
                        f"Parameter {spec.name!r} must be a number, got "
                        f"{type(value).__name__} ({value!r}).",
                        parameter=spec.name,
                    )
                )
            elif value <= 0:
                problems.append(
                    Problem(
                        Severity.ERROR,
                        f"Parameter {spec.name!r} must be positive, got {value}.",
                        parameter=spec.name,
                    )
                )

    for name in params:
        if name not in known:
            problems.append(
                Problem(
                    Severity.WARNING,
                    f"Unknown parameter {name!r} will be ignored.",
                    parameter=name,
                    suggestion=f"Valid parameters: {', '.join(sorted(known))}.",
                )
            )
    return problems
