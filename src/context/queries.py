"""Narrow queries over an AssemblyGraph, with an enforced response ceiling.

Everything a tool returns passes through `fit_response`. That is deliberate:
the entire reason this package exists is that a size assumption went unchecked
and turned into a 1.09 MB surprise. A ceiling that lives in a docstring is a
ceiling that gets exceeded, so this one degrades the payload in code and tells
the caller which argument to narrow.

No `print()` — stdout is the MCP protocol channel (see src/mcp/server.py).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from .graph import MAX_DEPTH, AssemblyGraph, PathNotFound

logger = logging.getLogger(__name__)

__all__ = ["assembly_overview", "find_parts", "fit_response", "MAX_RESPONSE_BYTES"]

#: Ceiling for any single tool result. Reference points from the real Citrus
#: robot: depth 1 is 397 B, depth 2 is 32 KB. So this comfortably admits the
#: intended default and firmly refuses the payload that motivated the redesign.
MAX_RESPONSE_BYTES = 8192


def _encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def fit_response(
    candidates: list[Callable[[], dict[str, Any]]],
    *,
    narrow_hint: str,
) -> str:
    """Return the first candidate that fits the ceiling, degrading in order.

    Args:
        candidates: Payload builders ordered richest-first. Each is only called
            if the previous one was too large, so building is lazy.
        narrow_hint: Concrete advice naming the argument to change. Attached
            whenever a fallback was used, so the caller learns how to ask better
            rather than silently receiving less than it requested.

    Returns:
        Serialized JSON, always valid and always within the ceiling.
    """
    last: dict[str, Any] | None = None
    for index, build in enumerate(candidates):
        payload = build()
        text = _encode(payload)
        if len(text) <= MAX_RESPONSE_BYTES:
            if index > 0:
                payload = {
                    **payload,
                    "truncated": True,
                    "note": (
                        f"Response reduced to stay under {MAX_RESPONSE_BYTES} bytes. "
                        f"{narrow_hint}"
                    ),
                }
                text = _encode(payload)
                # The note itself could tip it over; fall through if so.
                if len(text) > MAX_RESPONSE_BYTES:
                    continue
            logger.debug("response %d bytes (candidate %d)", len(text), index)
            return text
        last = payload

    # Everything overflowed. Emit a minimal, valid, actionable result rather
    # than a slice of broken JSON.
    fallback = {
        "truncated": True,
        "error": "Result too large to return even at minimum detail.",
        "note": narrow_hint,
    }
    if last is not None and "path" in last:
        fallback["path"] = last["path"]
    return _encode(fallback)


def assembly_overview(
    graph: AssemblyGraph,
    path: str | None = None,
    depth: int = 1,
) -> str:
    """Structural view of an assembly, or of one subtree within it.

    Repeated components collapse to a single entry with a count, so four
    identical swerve modules read as `Swerve Module <1>, count: 4` rather than
    four near-identical blocks.
    """
    depth = max(0, min(int(depth), MAX_DEPTH))

    try:
        graph.resolve(path)
    except PathNotFound as exc:
        return _encode({"error": str(exc)})

    def build(d: int) -> Callable[[], dict[str, Any]]:
        def _build() -> dict[str, Any]:
            payload = graph.subtree(path, depth=d)
            if not path:
                payload["summary"] = graph.summary()
            payload["depth"] = d
            return payload

        return _build

    # Degrade by depth first, since depth is what explodes the payload.
    candidates = [build(d) for d in range(depth, -1, -1)]
    return fit_response(
        candidates,
        narrow_hint=(
            "Pass a deeper `path` to focus on one subassembly, or a smaller `depth`. "
            "Navigating down beats asking for more depth at the top."
        ),
    )


def find_parts(graph: AssemblyGraph, query: str, limit: int = 25) -> str:
    """Search component names, deduplicated by definition.

    A component used in four places returns once with `instance_count: 4`,
    rather than filling the result with repeats of the same thing.
    """
    if not (query or "").strip():
        return _encode({"error": "Provide a non-empty search query."})

    matches = graph.search(query, limit=limit)
    if not matches:
        return _encode(
            {
                "query": query,
                "matches": [],
                "note": "No component name contains that text. Try a shorter fragment.",
            }
        )

    def build(n: int) -> Callable[[], dict[str, Any]]:
        def _build() -> dict[str, Any]:
            shown = matches[:n]
            return {
                "query": query,
                "match_count": len(matches),
                "matches": [
                    {
                        "name": m.name,
                        "type": m.type,
                        "path": m.path,
                        "instance_count": m.instance_count,
                    }
                    for m in shown
                ],
            }

        return _build

    candidates = [build(n) for n in (len(matches), 10, 5, 1)]
    return fit_response(
        candidates,
        narrow_hint="Use a more specific query to see fewer, more relevant matches.",
    )
