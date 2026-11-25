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

## 2026-11-25 — second workspace session, after the studio and selection fixes

Transcript `137859bc`: T1 then T2, from `../frc-copilot`, with the fixes from the
first session in place. 22 tool calls for both tasks; arm B used 49 for T1
alone. 0 Bash, and reads only of the workspace corpus.

**Both tasks finished, measured on the real part.**

- T1: the plate was retried once (the model's own first module regenerated as
  ERROR and was rolled back cleanly). Then the pocket grid went in with
  `selections={"face": "top"}`, and a probe of the Part Studio read **221 g**
  (82,030 mm3).
- T2: the same feature was updated in place and read **271 g**. Each feature
  has its own studio, and nothing was stranded.

**What it exposed:**

1. **T2 built first and warned after.** It ran `frc_check_design`, got both
   rule warnings, applied the 2 mm ribs anyway (the user approved the write
   prompt), then reported the warnings. Arm B refused before building, but B
   had read the answer key in the repo, which framed T2 as a refusal test. So
   **build-then-warn is the uncontaminated behaviour**. Nothing in the
   workspace CLAUDE.md says to stop when a team rule fails.
2. **The check does not say where a limit came from.** The session wrote: "The
   check doesn't say whether the 3 mm limit is your team's own rule or a
   built-in default." It is right. `team_note` is empty for a default, by
   design. B and C knew it was a default only because they read
   `standards.toml` in the repo. Isolation removed the file, and with it the
   information.
3. **On update, `top` is re-resolved on geometry that already includes this
   feature.** T2 selected face `KMmE`, 9,582 mm2: the pocketed top face that the
   feature itself had made. It worked, and the session flagged it on its own
   as fragile. The selection should come from the model before this feature.
   On an update, the simplest fix is to keep the query the feature already
   has.
4. **Leftover sheet bodies.** The Part Studio holds 1 solid plus 3 sheet
   bodies, probably the features' sketches. Not verified which feature leaves
   them.

---

## 2026-11-25 — first session from the separate workspace (T1 only)

Transcript `2259470f`, run from `../frc-copilot` with Bash/Edit/Write denied.
There were 20 tool calls: 0 Bash, and Read/Grep only on the workspace's
`featurescript/`.

**Isolation did what it was for.** The session answered correctly: 221.5 g,
probed in the kernel, with 78 faces confirming all 36 corner fillets. When it
got stuck it stopped and wrote up the tool's gaps for the team instead of
scripting around them. Arm B did the opposite in the repo.

**It found a defect no earlier test could.** Every test so far had applied
one feature. This one applied a plate, then a pocket grid, and both went into
the single per-document Feature Studio. The second write replaced the
plate's source, the plate went to ERROR, and the Part Studio was left with no
solid. Fixed the same day: one studio per feature.

**And it found a limit.** The pocket feature needs a face selection, and the
apply tool cannot set one. Measured afterwards: a feature with an empty
required selection is ERROR even when its body does nothing. So the
roll-backs were not the model's code. The tool's own message said "the module
compiles; the failure is in what it was asked to operate on", which was true
but not actionable. Fixed the same day: `selections={"face": "top"}`, set
through `deterministicIds`. On a real plate it left 60,350.4 mm3, matching the
prediction exactly.

**A self-report that was half right.** The session said each save cost "about
10 requests from the ~100/day bucket". About 11 requests is right. But none of
them come from the evaluate bucket; 2 come from `GET .../features`, which
turned out to be nearly as tight (~94 remaining). It is another reason to
count from the ledger rather than from what a session says.

---

## 2026-11-25 — Onshape Labs FS MCP vs ours: arms A, B and C

Protocol: `docs/eval-onshape-fs-mcp.md`. Arm A = their server alone, from an
empty folder. Arm B = ours alone. Transcripts were audited for separation: no A
session could see our server, and no B session could see theirs. Counts come
from the transcripts, not from memory.

| Task | A (theirs) | B (ours) |
|---|---|---|
| T1 pocket plate | Correct (221.48 g), but **5 turns, 3 of them nudges**: the user inserted the feature by hand and fixed wrong defaults. 26 tool calls. | Correct, 1 turn, measured in the kernel. 49 tool calls, **36 of them Bash**. |
| T2 ribs 2 mm | **Partial.** Recomputed by hand without running it. Warned only that ribs may "flex or chatter". No minimum, no area limit. | **Refused to build without approval.** Quoted both rules and said they are defaults because `standards.toml` is unset. 1 tool call, 23 s. |
| T3 face size | **Hit `box`** (and in T1). `test_feature` reports every parse error as "could not find Trojan feature spec", with no line. 4 turns, 24 calls. | Did not hit `box`, because it `cat`ed `featurescript/failures/` first. 1 turn, 46 calls. **Edited the repo** (below). |
| T4 bores | 46.990 mm, 1 turn. **Written to the server's own document**, not the URL given. | 46.990 mm in tab B, kernel-measured, 1 turn. |
| T5 assembly | Correctly declined, invented nothing. | Answered, and found that no part has a material, so there is no mass. |

**What the decision rule says so far.** A is worse than B on T1 and T3, and
fails T2. So keep our FeatureScript path, and treat standards-driven
checking as the differentiator. Arm C is still to run.

**What their server does that ours does not:**

- **Notes that persist across sessions** (`read/edit_featurescript_notes`).
  Mid-run, arm A wrote down the `box` trap, the rotated-face trick and five
  more lessons, unprompted. That is our `CLAUDE.md` + `featurescript/`, but
  self-maintaining. It also means every later session inherits them, arm C
  included.
- Zero local setup: OAuth, no API keys, no venv, no `robot.toml`. 14 tools,
  including `search_featurescript_documentation` and `get_api_usage`.

**What it lacks:**

- It does not insert a feature into the user's Part Studio. It writes the
  Feature Studio and leaves insertion to the human, and `create_geometry`
  goes to its own document.
- Its errors are weak: "Trojan feature spec" is returned for every parse error.
- No physics, no team rules, no assembly reads.

**What arm B exposed about us:**

1. **A session edited the copilot's own source.** T3 added
   `src/skills/face_dimensions.py`, its tests, a `CLAUDE.md` trap and a
   `STATUS.md` section, all from a one-line user prompt. The finding is real:
   `planeToCSys` keeps the sketch's x axis, so a 150 × 75 plate drawn at 30°
   measures 167 × 140, and `plate_pocket_pattern` lays its grid on that wrong
   box. But a student's session must not rewrite the tool. Today the copilot
   runs inside its own development repo, and `CLAUDE.md` tells it to update
   `STATUS.md`. Real use needs a separate workspace that exposes only the MCP
   server.
2. **It still reaches past its tools.** That was 36 Bash and 10 Write calls in
   T1, scripts importing `src.skills` directly. It is the same pattern as the
   first real session (2026-08-09), and it costs about 2× the tool calls of
   arm A.

### Arm C: their server, run inside this repo (our server disabled)

Separation held: no C session could see our server. Every task took one turn,
or two in T4, where the second message was a pasted URL. That took 4 to 7
minutes each, with no `box` hit.

| Task | Arm C |
|---|---|
| T1 | 221.48 g, kernel-measured, but **in the Labs sandbox document, not tab C**. The user has to insert the feature by hand. |
| T2 | **Refused to build.** Cited `min_rib_mm` and `max_removed_fraction` from `standards.toml`, said they are defaults, and suggested 7 mm ribs. The feature it wrote **warns on the team rules at regeneration**. Our standards carried over into their FeatureScript. |
| T3 | 150 × 75 on the rotated plate, while the naive method gave 167 × 140. Whole mm. |
| T4 | 46.990 + 0.076 mm running clearance = 47.066, stated openly. Again not inserted into tab C. |
| T5 | Used neither MCP server. It **wrote a script on our `src.api` client** and answered from it. |

**Quota.** The evaluate bucket read 96 before arm C and 95 after; the 1 is the
reading itself. Arm C called their `test_feature`/`test_featurescript`
throughout, so **their server does not draw on our per-endpoint evaluate
bucket.** It does draw on the user's annual API allowance, which their
`get_api_usage` reported at about 1,120 of 2,500 used.

**Permissions** (Onshape → My Account → Applications): "create and modify
Feature Studios; can create a memory document and a working document; can
delete workspaces from the working document." So it does hold a delete
capability. As far as we know, Onshape's OAuth scopes are per type and not
per document, so "only its working document" is the server's own promise,
not something Onshape enforces. That has not been verified here.

### Protocol flaw: B and C could read the answer key

The reference numbers live in `docs/eval-onshape-fs-mcp.md`, inside the repo
that B and C ran in. Both read it (`cat`), and C also read the A/B results in
`FINDINGS.md`; two C sessions flagged this themselves. Arm A, in an empty
folder, could not. B's numbers come from tool outputs (`frc_check_design`,
kernel probes), so they stand. C's clean wins are weaker evidence than they
look. Next time, keep the answer key outside every arm's working directory,
and run B and C from a separate workspace too, which is the same fix as
finding 1 above.

### Verdict

- **Keep our FeatureScript path.** The deciding gap is not knowledge but
  capability: their server cannot insert a feature into the user's Part
  Studio, and no amount of `CLAUDE.md` changed that in arm C. Ours inserts,
  verifies in the kernel, and measures the committed part.
- **Our knowledge layer is portable.** `standards.toml`, the traps and the
  corpus made their server avoid `box` and refuse 2 mm ribs. Their notes had
  already taught it `box`, so only the standards result is cleanly ours. That
  is the piece worth publishing as a skill other teams can load next to the
  official server.
- **Before any real user touches this, give it a separate workspace.** Both
  B and C sessions read and, in B's case, edited the copilot's own source.

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
