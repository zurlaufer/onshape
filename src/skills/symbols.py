"""Known FeatureScript standard-library symbols, for the layer-2 warning.

Original purpose: catch **hallucinated function names**, the characteristic
failure when a model writes a niche language it saw little of in training.

HONEST STATUS OF THIS LAYER (reassessed 2026-08-09)
---------------------------------------------------
It has not caught a real error, and it has produced false positives. On
`plate_pocket_pattern` it flagged `planeToCSys`, `qOwnerBody` and `qSketchRegion`
— all three valid, all three compiled and ran against Onshape the same minute.

Two things are true and both were quietly assumed away:

1. **The premise moved.** This layer was designed when the model was going to
   author FeatureScript. It does not: it picks a template and fills parameters,
   and the template bodies are hand-written and verified against Onshape. So the
   only source this layer ever examines is source we already know is good.
2. **The list can never be complete as built.** The docstring here used to
   promise `scripts/extract_std_symbols.py` and a `fs_symbols.json` fixture.
   Neither was ever written, so `known_symbols()` has always returned just the
   hand-curated set below against a standard library of thousands of names.

A warning that is usually wrong is worse than no warning: it trains the reader
to skip the one time it is right. So the layer stays — it costs nothing and it
matters again the day any unverified body is validated — but its message now
says plainly that an unknown name is not evidence of an error, and the real
authority is named. Onshape's evaluate endpoint returns
`PARAMETER_EXPRESSION_UNKNOWN_FUNCTION` with the exact symbol, which is a fact,
not a guess.

If model-authored bodies ever come back, write the extractor before trusting
this layer again.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "is_known_symbol",
    "is_rejected",
    "known_symbols",
    "observe_compiled",
    "observe_rejected",
]

_FIXTURE = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "fs_symbols.json"

#: Core symbols, hand-curated so the check works even without a fixture.
#: Confirmed callable through the evaluate endpoint on 2026-08-07; the three
#: marked below were added 2026-08-09 after they compiled and ran, having been
#: wrongly flagged as unknown. Only add a name here once Onshape has accepted
#: it — an invented entry would turn this from noisy into actively misleading.
_BUILTIN: frozenset[str] = frozenset({
    # queries
    "qEverything", "qCreatedBy", "qNothing", "qUnion", "qSubtraction",
    "qIntersection", "qBodyType", "qEntityFilter", "qGeometry", "qParallelPlanes",
    "qCapEntity", "qOwnedByBody", "qContainsPoint", "qClosestTo", "qLargest",
    "qNthElement", "qFarthestAlong", "qAdjacent", "qSubtractionExcess",
    "qOwnerBody", "qSketchRegion",  # confirmed live 2026-08-09
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
    "planeToCSys",  # confirmed live 2026-08-09
})


#: Names Onshape has confirmed by compiling them, and names it has rejected.
#: Learned from use rather than extracted, the way `QuotaLedger` learns ceilings:
#: zero cost, correct by construction, and it improves every time the probe runs.
#: This is the answer to the extractor the old docstring promised and nobody
#: wrote — and it matters now only because the model authors FeatureScript
#: again, which it did not when this layer was declared dead.
_OBSERVED: set[str] = set()
_REJECTED: set[str] = set()


def observe_compiled(names: set[str] | frozenset[str]) -> None:
    """Every name in a body Onshape accepted is a name that exists."""
    added = {n for n in names if n not in _OBSERVED}
    if added:
        _OBSERVED.update(added)
        known_symbols.cache_clear()
        logger.debug("learned %d symbol(s) from a compiling body", len(added))


def observe_rejected(name: str) -> None:
    """A name Onshape named in PARAMETER_EXPRESSION_UNKNOWN_FUNCTION.

    A fact, not a guess — worth more than the whole hand-typed list below.
    """
    if name and name not in _REJECTED:
        _REJECTED.add(name)
        logger.info("Onshape rejected the symbol %r", name)


def is_rejected(name: str) -> bool:
    return name in _REJECTED


@lru_cache(maxsize=1)
def known_symbols() -> frozenset[str]:
    """Curated builtins, plus the extracted fixture when present."""
    symbols = set(_BUILTIN) | _OBSERVED
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
