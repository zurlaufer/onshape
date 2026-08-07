"""Local validation — the free layers, run before any request is spent.

Layer 1  structural sanity   truncated or malformed output
Layer 2  symbol check        hallucinated function names
Layer 3  parameter check     dimensionally impossible designs  (in the template)
Layer 4  template contract   missing or mistyped parameters    (in the template)

Deliberately NOT a parser. There is no published FeatureScript grammar — Onshape
has declined to release one — so a parser would mean reverse-engineering a
proprietary, versioned language to catch the error class LLMs are already best
at. Worse, a partial parser rejecting valid code would silently block the tool.
False positives cost more than false negatives here, so every check below is
either certain (delimiter balance) or emitted as a warning (unknown symbols).

The real syntax authority is Onshape's evaluate endpoint, which returns PARSE
and SEMANTIC notices with line numbers. See `src/skills/apply.py`.

No `print()` — stdout is the MCP protocol channel.
"""

from __future__ import annotations

import re
from typing import Any

from .symbols import is_known_symbol
from .templates import TEMPLATES, Problem, Severity, Template

__all__ = ["validate_source", "validate_params", "worst_severity"]

_HEADER = re.compile(r"^\s*FeatureScript\s+\d+\s*;", re.MULTILINE)
_IMPORT = re.compile(r"^\s*import\s*\(", re.MULTILINE)

# Identifiers in call position. Deliberately crude: this feeds a *warning*, so a
# few misses are fine and a few false hits would not be.
_CALL = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")

# Words that look like calls but are language constructs.
_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "function", "throw",
    "annotation", "precondition", "export", "import", "const", "var", "is",
    "as", "in", "try", "operator", "new", "defineFeature",
})


def _strip_noise(source: str) -> str:
    """Remove comments and string literals so they cannot skew the checks."""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    source = re.sub(r"//[^\n]*", " ", source)
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', source)


def validate_source(source: str, *, is_module: bool) -> list[Problem]:
    """Layers 1 and 2 over generated FeatureScript.

    Args:
        source: The generated text.
        is_module: True for a full Feature Studio module (header and import
            required), False for a bare lambda destined for the evaluate
            endpoint (where all three are rejected).
    """
    problems: list[Problem] = []
    if not source or not source.strip():
        return [Problem(Severity.ERROR, "Generated source is empty.")]

    clean = _strip_noise(source)

    # --- Layer 1: structural sanity -------------------------------------
    for opener, closer, label in (("{", "}", "braces"),
                                  ("(", ")", "parentheses"),
                                  ("[", "]", "brackets")):
        delta = clean.count(opener) - clean.count(closer)
        if delta:
            problems.append(Problem(
                Severity.ERROR,
                f"Unbalanced {label}: {abs(delta)} unclosed {opener!r}"
                if delta > 0 else
                f"Unbalanced {label}: {abs(delta)} unexpected {closer!r}",
                suggestion="Output was probably truncated. Regenerate it whole."))

    if is_module:
        if not _HEADER.search(clean):
            problems.append(Problem(
                Severity.ERROR,
                "Missing the `FeatureScript <version>;` header line.",
                suggestion='A module must open with e.g. "FeatureScript 3029;".'))
        if not _IMPORT.search(clean):
            problems.append(Problem(
                Severity.ERROR,
                "Missing an `import(...)` statement.",
                suggestion='Add: import(path : "onshape/std/geometry.fs", version : "3029.0");'))
    else:
        # The evaluate endpoint rejects all three outright (measured 2026-08-07).
        for pattern, name in ((_IMPORT, "import"), (_HEADER, "FeatureScript header")):
            if pattern.search(clean):
                problems.append(Problem(
                    Severity.ERROR,
                    f"A probe lambda must not contain a {name}; "
                    f"Onshape's evaluate endpoint rejects it with a PARSE error.",
                    suggestion="Send only `function(context is Context, queries) { ... }`."))
        for word in ("annotation", "export"):
            if re.search(rf"\b{word}\b", clean):
                problems.append(Problem(
                    Severity.ERROR,
                    f"A probe lambda must not contain `{word}`; "
                    f"the evaluate endpoint rejects it with a PARSE error.",
                    suggestion="Send only the bare lambda."))

    # --- Layer 2: symbol check (warnings only) ---------------------------
    unknown = sorted({
        name for name in _CALL.findall(clean)
        if name not in _KEYWORDS and not is_known_symbol(name)
    })
    for name in unknown[:8]:
        problems.append(Problem(
            Severity.WARNING,
            f"{name!r} is not in the known FeatureScript standard library.",
            suggestion="Check the spelling, or confirm it is defined locally. "
                       "The symbol table may also be out of date.",
        ))
    if len(unknown) > 8:
        problems.append(Problem(
            Severity.WARNING,
            f"{len(unknown) - 8} further unrecognised names were not listed."))

    return problems


def validate_params(template_name: str, params: dict[str, Any]) -> list[Problem]:
    """Layers 3 and 4, delegated to the template."""
    template: Template | None = TEMPLATES.get(template_name)
    if template is None:
        return [Problem(
            Severity.ERROR,
            f"Unknown template {template_name!r}.",
            suggestion=f"Available templates: {', '.join(sorted(TEMPLATES))}.")]
    return template.validate(params)


def worst_severity(problems: list[Problem]) -> Severity | None:
    if any(p.is_error for p in problems):
        return Severity.ERROR
    if problems:
        return Severity.WARNING
    return None
