"""Known FeatureScript standard-library symbols, for the layer-2 warning.

Purpose: catch **hallucinated function names**, the characteristic failure when
a model writes a niche language it saw little of in training. It is not a
type-checker and not a parser.

The list is deliberately treated as *incomplete*. An unknown name produces a
warning, never a refusal, because Onshape's standard library is large and
versioned and a false rejection of valid code is worse than a missed catch.
Onshape's evaluate endpoint is the authority — it returns
`PARAMETER_EXPRESSION_UNKNOWN_FUNCTION` naming the exact symbol.

Regenerate the fixture with `scripts/extract_std_symbols.py` when the
FeatureScript version bumps.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["is_known_symbol", "known_symbols"]

_FIXTURE = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "fs_symbols.json"

#: Core symbols, hand-curated so the check works even without the fixture.
#: Confirmed callable through the evaluate endpoint on 2026-08-07.
_BUILTIN: frozenset[str] = frozenset({
    # queries
    "qEverything", "qCreatedBy", "qNothing", "qUnion", "qSubtraction",
    "qIntersection", "qBodyType", "qEntityFilter", "qGeometry", "qParallelPlanes",
    "qCapEntity", "qOwnedByBody", "qContainsPoint", "qClosestTo", "qLargest",
    "qNthElement", "qFarthestAlong", "qAdjacent", "qSubtractionExcess",
    # evaluation
    "evBox3d", "evaluateQuery", "evPlane", "evAxis", "evLine", "evVertexPoint",
    "evArea", "evVolume", "evLength", "evDistance", "evEdgeTangentLine",
    "evFaceTangentPlane", "evMateConnector", "evCurveDefinition", "evOwnerSketch",
    # operations
    "opExtrude", "opHole", "opBoolean", "opFillet", "opChamfer", "opPattern",
    "opSplitPart", "opShell", "opDeleteBodies", "opTransform", "opRevolve",
    "opLoft", "opSweep", "opThicken", "opMoveFace", "opDraft", "opPlane",
    "opPoint", "opFitSpline", "opMateConnector", "opCreatePartStudio",
    # sketching
    "newSketch", "newSketchOnPlane", "skLineSegment", "skCircle", "skRectangle",
    "skArc", "skPolyline", "skPoint", "skEllipse", "skSolve", "skFillet",
    # features and framework
    "defineFeature", "annotation", "precondition", "makeId", "transform",
    "toWorld", "fromWorld", "setErrorEntities", "setFeatureComputedParameter",
    "reportFeatureInfo", "reportFeatureWarning", "reportFeatureError",
    # errors
    "regenError", "regenWarning",
    # types, math and utility
    "isLength", "isAngle", "isReal", "isInteger", "isQuery", "isAtVersion",
    "size", "toString", "print", "abs", "sqrt", "sin", "cos", "tan", "atan2",
    "floor", "ceil", "round", "min", "max", "sum", "mean", "sort", "reverse",
    "concatenateArrays", "append", "resize", "replace", "match", "split",
    "vector", "normalize", "cross", "dot", "norm", "squareNorm", "stringToNumber",
    "mergeMaps", "keys", "values", "sqr", "clamp", "lookupTable", "range",
    "coordSystem", "plane", "line", "circle", "box3d", "yAxis", "xAxis", "zAxis",
})


@lru_cache(maxsize=1)
def known_symbols() -> frozenset[str]:
    """Curated builtins, plus the extracted fixture when present."""
    symbols = set(_BUILTIN)
    try:
        if _FIXTURE.is_file():
            extracted = json.loads(_FIXTURE.read_text(encoding="utf-8"))
            names = extracted.get("symbols") if isinstance(extracted, dict) else extracted
            symbols.update(str(n) for n in (names or []))
            logger.debug("loaded %d std symbols from fixture", len(symbols))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        # A missing or broken fixture must not break validation; the curated
        # set still works and the layer only ever emits warnings.
        logger.warning("could not read %s: %s", _FIXTURE.name, exc)
    return frozenset(symbols)


def is_known_symbol(name: str) -> bool:
    return name in known_symbols()
