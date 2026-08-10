"""What goes in the worked-example corpus, and where each example comes from.

`CLAUDE.md` is loaded on every request and tells a session to **grep
`featurescript/`** before writing FeatureScript. Until now that directory did not
exist: `ProbeCache.promote()` was written, documented and never called. An
always-loaded instruction pointing at an empty directory is worse than no
instruction, because it costs a tool call to discover there is nothing there.

WHY A MODULE AND NOT FOUR HAND-WRITTEN FILES
--------------------------------------------
Two of these examples are *generated* — they come out of the same renderers the
tools use. Hand-copying them into `.fs` files would create the exact drift this
project keeps designing against: `verify_pocket_geometry.py` splices the
template's own grid source in rather than copying it, "so this cannot pass
against a stale copy". Same reasoning here. `scripts/build_corpus.py` regenerates
the directory, and `tests/test_corpus.py` fails if what is committed no longer
matches what the sources produce.

The other two are literals, and they have to be: an isolated idiom has no
renderer, and a failure has no source of truth other than what Onshape said.

WHAT EARNS A PLACE
------------------
The corpus is **grepped, never loaded whole** — one example is ~500 tokens, so
twenty would not fit the budget. Four entries is not a starting point to grow
from; it is roughly the right size. Add one only when a session would have been
saved by finding it.

Failures earn their place as much as successes. `box` being a reserved word was
hit twice: once here, and again by a later session that had read the fact in
`STATUS.md` and not retained it. A long document read once does not change
behaviour.

HONESTY ABOUT WHAT "VERIFIED" MEANS
-----------------------------------
`ok=True` means *this exact body was sent to Onshape and came back clean*. The
composed idiom below is extracted from verified source but has never been probed
standalone, and its note says so rather than borrowing credit from its parent.
This project has already been bitten by a "verified live" claim that only meant
Onshape accepted it.

No `print()` — stdout is the MCP protocol channel (see src/mcp/server.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.skills.climber_plate import plate_geometry
from src.skills.climber_plate import render_probe as render_climber_probe
from src.skills.probe_cache import CORPUS_DIR, ProbeCache
from src.skills.templates import TEMPLATES

__all__ = ["Example", "EXAMPLES", "build_corpus"]


@dataclass(frozen=True)
class Example:
    """One worked example, and the claim being made about it."""

    name: str
    #: Deferred so importing this module renders nothing.
    body: Callable[[], str]
    note: str
    ok: bool
    diagnostics: str = ""


#: Parameters for the pocket example. These are the ones checked against the
#: kernel on 2026-08-09 (76,835 mm3 predicted, 76,835 mm3 measured), so the
#: example carries a number a reader can go and re-verify.
_POCKET_PARAMS = {
    "plate_width": 150.0,
    "plate_length": 150.0,
    "plate_thickness": 6.35,
    "columns": 3,
    "rows": 3,
    "rib_width": 8.0,
    "border_width": 12.0,
}


def _pocket_probe() -> str:
    return TEMPLATES["plate_pocket_pattern"].render_probe(_POCKET_PARAMS)


def _climber_probe() -> str:
    return render_climber_probe(plate_geometry())


#: The idiom on its own. Everything else in the corpus is long enough that a
#: grep for `evBox3d` lands in the middle of a 60-line body; this is what a
#: reader wants to find first. Composed from `plate_pocket_pattern._grid_source`.
_FACE_IN_OWN_PLANE = """function(context is Context, queries)
{
    // Measure a planar face IN ITS OWN PLANE. `evBox3d` accepts a cSys, so
    // passing the face's own plane turns the result into clean u/v extents with
    // w = 0 -- which is what lets a pattern be laid out inside the face's real
    // boundary instead of inside dimensions the caller claimed.
    var faces = evaluateQuery(context, qGeometry(
        qOwnedByBody(qBodyType(qEverything(EntityType.BODY), BodyType.SOLID),
                     EntityType.FACE),
        GeometryType.PLANE));
    if (size(faces) == 0)
    {
        throw regenError("No planar face on any solid body.");
    }

    var facePlane = evPlane(context, { "face" : faces[0] });
    var faceBox = evBox3d(context, {
        "topology" : faces[0],
        "cSys" : planeToCSys(facePlane)
    });

    // `box` is a reserved word -- hence `faceBox`. See failures/.
    // Report whole millimetres: toString() on a length renders
    // "0.15000000000000002 meter", which reads as a broken tool.
    return {
        "u_mm" : round((faceBox.maxCorner[0] - faceBox.minCorner[0]) / millimeter),
        "v_mm" : round((faceBox.maxCorner[1] - faceBox.minCorner[1]) / millimeter),
        "w_mm" : round((faceBox.maxCorner[2] - faceBox.minCorner[2]) / millimeter)
    };
}"""


#: The failure, in the smallest shape that reproduces it.
_RESERVED_WORD_BOX = """function(context is Context, queries)
{
    var solid = qBodyType(qEverything(EntityType.BODY), BodyType.SOLID);

    // THIS LINE IS THE BUG. `box` is a reserved word in FeatureScript.
    var box = evBox3d(context, { "topology" : solid });

    return { "x_mm" : (box.maxCorner[0] - box.minCorner[0]) / millimeter };
}"""


EXAMPLES: tuple[Example, ...] = (
    Example(
        name="plate_pocket_grid",
        body=_pocket_probe,
        note=(
            "Lightening pockets in a plate: find the largest planar face, refuse "
            "a sheet body, measure the face in its own plane, solve the grid "
            "against what was measured rather than what was claimed. Generated by "
            "src/skills/templates/plate_pocket_pattern.py. Its grid arithmetic "
            "was checked against Onshape's kernel on 2026-08-09: 76,835 mm3 "
            "predicted, 76,835 mm3 measured, 0.0000% apart."
        ),
        ok=True,
    ),
    Example(
        name="build_cut_and_measure_plate",
        body=_climber_probe,
        note=(
            "THE PATTERN WORTH COPYING: the evaluate endpoint runs real modelling "
            "operations and persists nothing, so one request can sketch, extrude, "
            "boolean and measure. `makeId(\"...\")` supplies the Id a feature would "
            "normally receive. This body builds the climber gearbox plate, cuts "
            "four bearing bores, and reports the volume before and after. "
            "Generated by src/skills/climber_plate.py; measured 31,128.5100 mm3 "
            "against 31,128.4796 mm3 predicted on 2026-08-09, 0.0001% apart."
        ),
        ok=True,
    ),
    Example(
        name="measure_face_in_own_plane",
        body=lambda: _FACE_IN_OWN_PLANE,
        note=(
            "The evBox3d + planeToCSys idiom on its own, for when a grep lands "
            "here first. COMPOSED, NOT PROBED: every line comes from source that "
            "Onshape has accepted, but this exact lambda has never been sent. "
            "Check it for one request with frc_probe_featurescript before relying "
            "on it, against a Part Studio that holds a solid body."
        ),
        ok=True,
    ),
    Example(
        name="reserved_word_box",
        body=lambda: _RESERVED_WORD_BOX,
        note=(
            "`box` is a reserved word in FeatureScript. Naming any variable that "
            "fails the whole module, and through the Feature Studio write path it "
            "fails with no reason given at all -- only the probe reports this. "
            "Use tubeBox, faceBox, plateBounds. Hit twice: once by this project, "
            "and again by a session that had read the fact in STATUS.md and not "
            "retained it."
        ),
        ok=False,
        # The message verbatim, without a line/column. Both recorded sightings
        # were in longer bodies (one reported line 43), so a position quoted
        # here would be invented — and inventing a measurement in a project that
        # keeps correcting generalised ones would be its own small joke.
        diagnostics="[PARSE] mismatched input 'box' expecting ID",
    ),
)


def build_corpus(corpus_dir: Path | str = CORPUS_DIR) -> list[Path]:
    """Write every example. Idempotent — safe to re-run after a renderer changes."""
    return [
        ProbeCache.promote(
            example.name,
            example.body(),
            note=example.note,
            ok=example.ok,
            diagnostics=example.diagnostics,
            corpus_dir=corpus_dir,
        )
        for example in EXAMPLES
    ]
