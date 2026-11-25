# FRC Onshape CAD Copilot

An engineering assistant for FIRST Robotics teams: physics that decides numbers,
and verification against Onshape's own kernel. **Purpose is robot quality and
speed, not teaching.**

This file is loaded on every request, so it holds only what changes behaviour.
Evidence lives in `STATUS.md`; what happened when people used it lives in
`FINDINGS.md`. Keep this under ~100 lines — a fact buried in a long document
does not change behaviour. (Measured: `box` below was documented in STATUS.md,
a session read STATUS.md twice, and still hit it.)

## Running things

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q      # offline, ~389 tests
./.venv/Scripts/python.exe scripts/check_no_print.py
./.venv/Scripts/python.exe scripts/smoke_auth.py    # 1 request
```

Windows venv path is hardcoded in `.mcp.json`; on macOS/Linux it is `.venv/bin/python`.

## Hard rules

- **Never `print()` under `src/`.** stdout is the MCP protocol channel; a stray
  write corrupts JSON-RPC and the in-flight reply is lost with no error. There is
  no runtime guard — `check_no_print.py` enforces it. Scripts may print.
- **Writes go to allowlisted scratch documents only.** Never a team's robot
  (`8d51b49e4b833c57f7d33bf0` is a real Citrus Circuits robot — read-only).
- **Never grant `OAuth2Delete`.** Removing a feature is a write; the copilot
  never needs Delete, whose blast radius is whole documents.
- **Quota is metered per endpoint, not globally.** The FeatureScript evaluate
  bucket is **~100/day, shared**. It is the scarcest thing we call. Check before
  looping; `client.quota` reports it.

## Prefer the community's geometry

The FRC community maintains **37 FeatureScripts** (FRCDesign.org) — Tube
Converter, CheeseIt!, Spur Gear, Gusset Generator. **Do not write geometry that
already exists there.** They can be driven from the API; namespace is

    d{documentId}::v{versionId}::e{elementId}::m{microversionId}

Read the spec through a **version**, not a workspace, or the insert 404s.
See `scripts/spike_community_featurescript.py`.

This project's own value is the physics: `src/physics/motors.py` (vendor-verified
constants), `climber.py` (over-centre lever), `geartrain.py` (shaft clearance).

## FeatureScript traps, all measured

- **`box` is a reserved word.** `var box = ...` fails with
  `mismatched input 'box' expecting ID`. Use `tubeBox`, `faceBox`.
- **A `Query` parameter's annotation MUST carry a `"Filter"`**, or the module
  fails to compile and the write path reports no reason.
- **The evaluate endpoint rejects `import`, `annotation` and `export`** — send a
  bare `function(context is Context, queries) {...}`. std is available implicitly.
- **It returns HTTP 200 even on failure.** Errors are in `notices[]`; never use
  the status code as the signal.
- **`evBox3d` accepts a `cSys`** — that is how a planar face is measured in its
  own plane (`planeToCSys(evPlane(...))`), giving clean u/v with w = 0.
  **But the plane's x is the sketch's, not the face's:** a 150x75 plate drawn
  at 30 deg on Top measured 167x140. Box along each straight edge and keep the
  smallest — `src/skills/face_dimensions.py`.
- **The evaluate endpoint runs real modelling ops and persists nothing.** Use
  `makeId("...")` for the Id. So a probe can build a body, cut it and measure —
  see `scripts/verify_pocket_geometry.py`.
- **Restoring a Feature Studio strands any feature inserted from it.** A custom
  feature is pinned to the microversion that defined it. When persisting, do not
  restore, and repoint the inserted feature's namespace after every rewrite.
- **Report lengths in whole millimetres.** `toString()` on a length renders
  `0.15000000000000002 meter`, which reads as a broken tool.

Before writing FeatureScript, `grep featurescript/` for a worked example. Four
are committed there — the face idiom, a build-cut-measure probe, a pocket grid,
and the `box` failure with its diagnostic. Regenerate with
`scripts/build_corpus.py`; `tests/test_corpus.py` fails if they drift.

## The team's numbers beat generic FRC ones

`standards.toml` holds the shop rules the checks enforce — minimum rib, border,
web, end mill radius. **Never invent one of these numbers.** If the team has not
set a value the declared default applies and says so; a value they *have* set is
quoted in the refusal. Add a new settable rule to `SPECS` in
`src/skills/standards.py`, never as a bare constant in a template.

Physical facts (density, bearing OD, hex circumradius) are **not** settable —
a team cannot vote on what aluminium weighs.

## Conventions

- Comments explain *why*, and cite the measurement or vendor source. Numbers a
  student cannot check by hand are the ones worth citing.
- Prefer measuring over assuming. Four architectural decisions here were wrong
  because a claim was generalised from one sample — record what was measured.
- Update `STATUS.md` when project state changes; add to `FINDINGS.md` after a
  real usage session.
- The user writes Hebrew; reply in Hebrew. **Code, comments, docs and commit
  messages stay English.**
