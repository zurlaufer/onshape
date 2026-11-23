# Findings from use

What happened when someone actually used this, and what changed as a result.

`STATUS.md` records **what is true now**. This records **what we learned, and
when**. Different in kind, and this one grows faster.

One entry per real session. Run `scripts/analyse_session.py` for the numbers;
write the interpretation by hand, because the numbers do not contain it.

> **The counts say where to look. They do not say what it means.** Nothing below
> was concluded by counting. Every real finding came from reading the
> transcript, from research, or from the user correcting a premise. Treat
> "MCP usage %" as a prompt to ask why, never as a target — if the model got
> further by writing its own script, that is information about the tools.

---

## 2026-11-23 — market review after a 3-month break

Desk research, not a usage session: Chief Delphi, the Onshape and SolidWorks AI
offerings, and a source-level read of ReCalc (`tervay/recalc`, main @ 2026-11-23).

**Nobody has built this niche.** No Chief Delphi thread or public repo combines
Onshape access, FRC mechanism physics, team shop rules and kernel verification.
The closest work is generative ("Astra": GPT + Inventor, visually checked only)
or assembly hygiene (FRCShaper). The one engineering critique of Astra asked
exactly our question: *"how do you know that any of the decisions it makes are
wise?"*

**Onshape Labs shipped an official FeatureScript MCP (2026-08-11).** It covers
our write-probe-fix loop. Not yet measured against ours; protocol in
`docs/eval-onshape-fs-mcp.md`.

**FRCShaper (FRC 7415, closed source, ~400 users by 2026-11-16)** drives the
Onshape web page through hidden document copies, with no REST API. Its Assembly
check finds mesh errors ("centres are 0.025\" too close"), bolts without nuts,
belt/chain fit (0.010" / 0.020"), doubled parts. It has no physics beyond
tooth-ratio kinematics and no design rules (only an "In Stock" fastener list).
An **AutoPocket** (Shift+P) is in its shortcut table but unreleased. That will
overlap our pocket checks. Our standards are a natural rule set for it.

**ReCalc already does most of `motors.py`, better.** It uses WPILib DCMotor
with stator *and supply* limits, battery sag, an RKDP integrator, about 25
motors, and vendor-bore-aware ratio search. It does **not** model an over-centre
climber, static hold at the worst fold angle, N-stage gear-to-next-shaft
clearance, bore walls, or plate layout. Its arm model is the point-mass model
`climber.py` rejects. Those parts are ours alone.

**Two defects ReCalc's data exposed in our physics:**

1. **The committed climber stack cannot be built.** `climber_plate.STACK` puts
   11T 20DP pinions on stages 2 and 3, which are intermediate shafts. The root
   diameter of an 11T 20DP gear is (11 − 2.5)/20 = 0.425", and 1/2" hex is
   0.577" across corners, so no hex bore fits inside the gear. ReCalc's
   scraped vendor data agrees: 20DP 11–13T are sold only with motor bores
   (8 mm, Falcon spline, SplineXS), and the smallest 1/2" hex 20DP is 14T.
   `search_stacks` lets any pinion be ≥ 11T on any stage. The shaft-clearance
   check exists; a pinion-bore-fits check does not.
2. **The NEO is about 25–29 % optimistic under a current limit.** REV's page
   (2.6 N·m / 105 A) gives Kt ≈ 0.025 N·m/A. REV's own Kv (473 rpm/V) implies
   ≈ 0.020, and ReCalc's dyno-sourced constants give 0.0196. "Use the vendor"
   was the right instinct, but this vendor page is internally inconsistent.
   This does not affect the current design, which uses the Kraken X60 FOC.
   Kraken free currents are also low: ours is 2.0 A, the CTRE dyno shows
   2.8–3.5 A.

**What to take from ReCalc:** add a supply-current limit and battery sag to
`simulate_fold` (a fixed 12 V is optimistic for a 2-motor, 80 A climb); take
the vendor SKU and bore data in place of the hand-written `COTS_TEETH`; and
add the caveat that profile-shifted small gears (WCP 16T on an 18T centre
distance) break the standard-pitch centre distance.

---

## 2026-08-11 — compared against `ReshefElisha/jarvis-onshape-mcp`

An independent project in the same space, examined so we can tell what a
second team learned without us. It drives Onshape CAD *construction* —
sketches, extrudes, fillets, mates, ~70 tools, renders, vision, drawing
OCR, Variable Studios, custom FeatureScript. We drive physics that decides
numbers and verification against the kernel. The niches do not overlap; the
findings do.

**Independent convergence on facts we charge money for.** All of these were
settled facts in `STATUS.md` first, and here they are again, found by
someone else at another lab:

- The custom-feature namespace is `e{eid}::m{mv}` with prefixes glued to
  ids (their `_build_namespace`). They also learned the microversion pins a
  feature to its Feature Studio — their fix is to create a *fresh* FS
  element per feature and never restore, which is our "do not restore"
  rule reached from the other side.
- The `/featurescript` evaluate endpoint is the real diagnostic channel:
  on a `REGEN_ERROR` they re-run the feature *body* through evaluate and
  append `notices[]` to the failure — our layer 5, done on the apply path.
  They also call `getFeatureStatus(context, id)` through it to enrich
  status with machine-readable enums like `SKETCH_DIMENSION_MISSING_PARAMETER`.
- FS version drift is a real failure class: they bumped 2242 → 2909 → 2931
  and noticed stale versions "silently compile to an empty symbol table" —
  the same class of silent staleness as our `x-api-version` finding.
- "Solver silence is a platform limitation" — over-constrained sketches
  return `SKETCH_SOLVE_FAILED` with no per-constraint diagnostics, so they
  bisect constraints client-side by id. Same shape as our "the write path
  reports no reason" finding, on a different endpoint.

**The benchmark we should cite.** Their `RESEARCH.md` is a real eval of
Claude Opus 4.7 on CAD: hand-written spec beats autonomous image→build by
**2–10×** in score and 4–7× in time. Their v003–v008 tool variants —
render-compare loops, drawing OCR, crop-and-read — all landed within noise
or made things worse. Conclusion: *CAD execution is solved conditional on a
good spec; image understanding is the wall.* This is the strongest external
evidence yet for our design: the human supplies intent, the copilot
executes and verifies. It also says the render/vision tooling we decided
not to build was the right call — someone else built it and measured it.

**What we should borrow (both cheap, both offline-friendlier than theirs):**

1. **Runtime FS library-version discovery.** They query the public std
   document's `/versions` and take the newest `"<n>.0"` name. We hardcode
   `libraryVersion: 3029` in the probe. If Onshape ships a newer std our
   probes would silently use stale symbols. One GET from the cheap
   `/documents/{did}/versions` bucket.
2. **`getFeatureStatus(context, id)` enrichment on the apply path.** Our
   `persist_feature` reports non-OK status as a bare string. Enriching the
   failure with the status enum (and re-running the body through evaluate
   for notices) turns "REGEN_ERROR" into a fixable handle. Costs scarce
   evaluate calls only on failure — their happy path never pays.

**Where they are weaker, in the order that matters for a team robot:**

- **No write boundary.** They expose `delete_document` and no policy; the
  client has no `robot.toml` allowlist and no `OAuth2Delete`-scope hygiene.
  Fine for a personal plugin, an incident on a team robot. Our FINDINGS
  entry from the first real session measured exactly this hazard: a raw
  DELETE returned 200 when guarded only by courtesy.
- **No quota governor.** Their `eval_featurescript` would hammer the ~84/day
  evaluate bucket with no ledger, and their error-enrichment POSTs add to
  it. They never hit the wall because they don't measure.
- **No cache.** Assembly definitions are fetched whole on demand; they have
  never met a 1.2 MB FRC robot. Our microversion-keyed cache exists because
  we measured one.
- **Basic auth, no retry classification.** A 429 raises. Their
  `raise_for_status` path has none of our read-timeout / connect / quota
  distinctions, all of which were measured facts here first.
- **Test claim is stale.** `TEST_RESULTS.md` says 73 tests "production-ready"
  but predates the current surface (30+ test files, coverage gate 80% vs a
  documented 28%). The suite is a work in progress, which is fine — the
  claim in the file is the problem.

**What we keep not doing, now with evidence.** We do not build sketches,
mates, or renders; they do. Our `frc_list_checks` refusal phrasing and
their regen-hints rotation serve the same purpose through different
mechanisms, and ours is standards-driven where theirs is enum-driven.
A general AABB interference check (their `analysis/interference.py`) is
real work we have deliberately skipped — the shaft-clearance collision our
`geartrain.py` catches sits between the plates and AABB would miss it, and
the settled fact in `STATUS.md` already names why bearing-based layout
checks pass all the failures.

---

## 2026-08-09 — first real design session (`7e93b1fc`)

**The task.** A climber gearbox, given in a student's words: driven by a Kraken
X60, must lift the robot in half a second, arm pivoting at the frame edge,
opening 90°, a ratchet, a half-inch hex bore, "make the plate compact."

**What the analyser measured**

| | |
|---|---|
| Tool calls | 136 |
| Ours | **4 — 3%** |
| `frc_apply_template` | **0** |
| Our modules imported directly | **43** (physics 18, skills 14, api 11) |
| Code the model wrote itself | ~168,000 chars, **~42,000 tokens** |

**What that meant.** Everything shaped as a *library* was used hard; everything
shaped as a *gate* was walked around. The decision point is timestamped: at
11:18:05 it called `frc_list_templates` and got a clean answer; at 11:18:45 it
grepped the template's source to judge what the template could actually do; by
11:48 it was writing its own FeatureScript. Correctly — the ask was a mechanism
and the template makes two holes.

It then hit `[PARSE] mismatched input 'box' (line 43)`, patched line 43, and
reached **0.0000%** agreement with its own volume prediction. That is the
automated correction loop spike 3b called impossible and the template library
was built to work around. **We had removed that constraint two days earlier and
never revisited the design it justified.**

**Two safety findings, neither theoretical**

- The write allowlist lived in `apply.py`. The model used the client directly
  and issued a raw `DELETE` that returned **200**. It called the policy first,
  in its own script, voluntarily. Courtesy is not enforcement.
- `apply_module`'s `finally` restored a Feature Studio and **stranded a feature
  inserted from it** — a custom feature is pinned to the microversion that
  defined it. The safety mechanism caused the damage it exists to prevent.

**What changed**

- Write guard moved into `OnshapeApiClient.request()`, covering `DELETE`.
- `persist_feature`: no restore, repoint the namespace, roll back a failed
  insert — and *not* a failed update, because that is somebody's work.
- Writes persist by default; `preview=True` is the dry run.
- Three ids collapsed to one `where`, with `robot.toml` as both the name map and
  the write boundary.
- The probe became a first-class tool; `frc_apply_template` retired.
- `CLAUDE.md` created — see below.

**The measurement that settled an argument.** The `box` trap was already
documented in `STATUS.md`. This session read `STATUS.md` **twice** and still hit
it, because it was fact #23 in an 11,000-token file. A long document read once
does not change behaviour; a short one that is always present does. That is why
`CLAUDE.md` exists and why it is budgeted at ~1,500 tokens.

---

## 2026-08-09 — research, not a session

Not a usage finding, but it belongs in the same log because it changed the
project's direction more than any code did.

**The FRC community maintains 37 FeatureScripts**
([FRCDesign.org](https://frcdesign.org/resources/featurescripts/)). Three of
them are our three templates, better: Tube Converter, CheeseIt! (isogrid, hex),
Spur Gear. **We reinvented a mature ecosystem, worse** — the same
unchecked-assumption failure that already cost us the payload model, the error
model, the quota model and the mass-properties write-off.

`scripts/spike_community_featurescript.py` proved we can drive them:
`d{doc}::v{version}::e{element}::m{microversion}`, read through a *version*.

**But the same list showed the niche.** Nothing among the 37 models the torque
of a rotating-arm climber or searches gear stacks for inter-stage shaft
clearance — which is exactly what that session built. The community owns
geometry generation; this project owns the physics that decides the numbers.

---

## Still unmeasured

**Nobody outside this project has used it.** The one session was run by the
person who wrote the tool, so discoverability and vocabulary fit are unknown.
The three stalls worth watching for, each implying a different fix:

| Where a new user stalls | What it means |
|---|---|
| Never reaches a tool | the descriptions are wrong |
| Reaches it, cannot fill the parameters | our vocabulary is not theirs |
| Gets output and does not know what to do with it | the workflow is wrong |

**Reading a *shared* document is unverified.** Every document these keys see is
owned; `filter=2` returns empty, so it cannot be tested here. Share one with a
second account, or have a student run `scripts/smoke_auth.py` with their own
key. Very likely fine — a check, not a gate.
