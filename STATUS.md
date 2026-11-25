# Project Status

**Last verified: 2026-11-23** — Climber stack replaced (11T-on-hex was unbuildable); current-limited torque now uses the smaller Kt. Before that, 2026-08-10: Tool surface reshaped around `where`; writes persist; the
probe is cached against a ~100/day bucket. The worked-example corpus exists at last, and
shop rules moved out of the templates into `standards.toml`. 389 tests.

> **Planning context, and it outranks everything below.** The team still CADs in
> **SolidWorks** and expects to through the 2027 season; Onshape is a 2028 decision this
> tool is meant to inform. So *"put it in front of one student"* — the next action this
> file has carried since Step 4 — **cannot run**, because no student does Onshape CAD
> here. What happens next is an evaluation demo, which measures something else entirely.
> Work that pays off on a SolidWorks team today now outranks work that only pays off
> after a migration.

> Maintained by Claude. Updated at the end of any session that changes project state, so picking
> the project back up doesn't require re-scanning the codebase or re-running discovery. If
> something here contradicts the code, the code wins — say so and it gets corrected.

---

## Where we are in one paragraph

**The project found its niche, and it is not geometry generation.** The FRC community maintains
37 FeatureScripts that already do what our three templates did, better — and we proved we can
drive them from the API. What nothing in that list does is model the torque of a rotating-arm
climber or search gear stacks for shaft clearance. So: **the community owns geometry; this project
owns the physics that decides the numbers, and verification against the kernel.**

Everything through Step 4 is verified live. Seven MCP tools (~1,688 tokens/request) all take one
`where` instead of three hex ids. A 1.09 MB assembly answers in 308 bytes. Writes **persist** now,
inside a boundary enforced in the client itself. 331 offline tests.

**The recurring lesson, three times over: measure the specific thing, and do not generalise from
one sample.** Spike 3b concluded "Onshape never reports why" from one endpoint — a different one
reports in full. Spike 3c concluded "the budget is ~1000" from one endpoint — quota is
per-endpoint and the one we depend on most is **~84**. And "mass properties times out" was never
about Onshape at all; it was our own flat 30 s. Each of those sat in this file as settled fact
until it was checked.

The project also stopped only ever asking *"did Onshape accept it?"* and started asking *"is the
number we told the student true?"* — and for the figures a team cannot eyeball, the answer is yes
to four decimal places, checked against Onshape's own kernel.

**The remaining gap is not technical: nobody outside this project has used it.** One real design
session happened, and reading its transcript reshaped the whole tool surface. That is the loop
worth repeating — see `FINDINGS.md`.

---

## Gate status

| Gate | What it proves | Status |
|---|---|---|
| 0 — key hygiene | `.env` ignored before keys hit disk | **PASS** — `.env` untracked, ignored, verified |
| 1 — live auth | The HMAC signature is accepted by Onshape | **PASS** — 200 on `GET /documents?limit=1` |
| 2 — MCP wiring | Claude Code ↔ server ↔ Onshape | **PASS** — `onshape_check_connection` called through the real Claude Code MCP path, returned live data |
| 3a — payload size | Is the "~20 KB spatial graph" claim real? | **DONE — claim FALSIFIED.** A real robot assembly is 1.2 MB, 63× the claim. Requires an architecture change (see Settled facts) |
| 3b — FeatureScript errors | Can regeneration errors be read programmatically? | **DONE, then REVISED 2026-08-07.** The Feature Studio write path is silent, but the **evaluate endpoint returns full diagnostics** with line/column. A correction loop *is* possible |
| 3c — rate limit ceiling | How much headroom does the retry budget have? | **PASS** — measured; Onshape advertises remaining quota in a header |
| 4 — hardened client | Retry/backoff + signing regression tests | **PASS** — classified retries, per-bucket quota governor, per-endpoint timeouts, 50 new tests. Mass properties fixed |

---

## What exists

| Path | State |
|---|---|
| `src/api/onshape_client.py` | HMAC signing, classified retries, per-endpoint timeouts. Verified live. |
| `src/api/quota.py` | Per-bucket quota ledger. Observational only — reads the header, never simulates. |
| `src/api/errors.py` | `OnshapeError` hierarchy; every type carries one actionable sentence. |
| `src/mcp/server.py` | FastMCP server, **7 tools** of an 8-tool budget, ~1,688 tokens per request. All take `where`. |
| `src/context/cache.py` | Microversion-keyed disk cache + 60 s TTL. Cold = 2 requests, warm = 1, repeat = **0**. |
| `src/context/graph.py` | `AssemblyGraph` — named tree + definition dedup. Pure, no I/O. |
| `src/context/queries.py` | Narrow queries behind a hard 8 KB response ceiling. |
| `src/context/workspace.py` | Resolves `where`: a robot.toml name, a pasted URL, or raw ids. |
| `src/api/write_policy.py` | The write allowlist, enforced inside `client.request()`. |
| `robot.toml` | **Committed.** What the copilot knows about, and where it may write. |
| `src/physics/motors.py` | Kraken X60 (trapezoidal + FOC), NEO V1.1. Dependency-free. Vendor-verified constants. |
| `src/physics/climber.py` | Rotating-arm climber kinematics. Over-centre lever, fold simulation, static-hold sizing. Pure. |
| `src/physics/geartrain.py` | Spur stacks: centre distances, **the shaft-clearance constraint**, stack search, plate folding. Pure. |
| `src/skills/climber_plate.py` | One concrete design + the FeatureScript that draws it. Single source of truth for the three climber scripts. |
| `src/skills/face_dimensions.py` | "FRC Face Size" feature: width x height of a picked planar face, whole mm, minimum box over its edge directions. Probe verified 2026-11-25 (150x75 at 30 deg); the module has not been compiled live. |
| `src/skills/templates/` | `Template` protocol + **three** templates: `tube_hole_pattern`, `gearbox_bore_pattern`, `plate_pocket_pattern`. |
| `src/skills/validate.py` | Layers 1-4: structural, symbol, parameter, contract. All free. |
| `src/skills/symbols.py` | ~130 curated symbols **plus names learned from every probe**. Warnings only. |
| `src/skills/probe_cache.py` | Hash-keyed probe cache + the worked-example corpus. Protects the ~100/day bucket. |
| `src/skills/corpus.py` | **What goes in `featurescript/`, and where each example comes from.** Two are generated from the renderers, so they cannot drift. |
| `featurescript/` | **Committed, and no longer empty.** Four worked examples: the face idiom, build-cut-measure, the pocket grid, and the `box` failure with its diagnostic. Grepped, never loaded whole. |
| `src/skills/standards.py` | **The team's shop rules as enforced numbers.** Declared defaults + `standards.toml` overrides. An unknown key is refused at load, never ignored. |
| `standards.toml` | **Committed, and ships entirely commented out.** The mechanism without invented numbers. |
| `src/skills/apply.py` | Probe, compile, **persist** (idempotent, rolls back a failed insert), FSValue decoding. |
| `scripts/verify_pocket_geometry.py` | **Checks our grams against Onshape's kernel.** 1 request, builds its own test plate. |
| `scripts/size_climber.py` | The whole sizing argument, offline and free. Run it when mass, arm or motor count changes. |
| `scripts/verify_plate_geometry.py` | Probes the climber plate before it is committed. 1 request, persists nothing. |
| `scripts/build_climber_plate.py` | Commits the plate via `persist_feature`. Idempotent; verified live twice. |
| `scripts/spike_community_featurescript.py` | **Proves community FeatureScripts can be driven.** Leaves nothing behind. |
| `scripts/inspect_document.py` | Read-only tour of a document: tabs, feature states, empty Feature Studios. Found the stranded `Boom 1`. |
| `scripts/analyse_session.py` | **What a real session actually did.** Offline; reads the transcript. |
| `CLAUDE.md` | Always loaded, ~1,035 tokens. Only facts that change behaviour. |
| `FINDINGS.md` | What we learned when someone used it, and what changed. |
| `tests/test_motors.py` | 41 tests, no network or credentials needed. |
| `tests/test_climber.py` | 26 tests. Pins over-centre behaviour and the completing-but-cannot-hold trap. |
| `tests/test_geartrain.py` | 25 tests. Pins the **bad** stacks explicitly, not only the good ones. |
| `tests/test_context_graph.py` | 35 tests over a synthetic fixture. |
| `tests/test_skill_validation.py` | 58 tests incl. captured Onshape notice shapes. |
| `tests/test_onshape_client.py` | 60 tests: signing golden vector, retry table, quota, timeouts, TTL, **write policy**. |
| `tests/test_plate_pocket.py` | 26 tests; the mass figure is pinned to a kernel measurement. |
| `tests/test_mcp_tools.py` | 22 tests on the tool surface — the paths that must answer for free. |
| `tests/test_workspace.py` | 25 tests: URL parsing, `where` resolution, the write boundary. |
| `tests/test_persist.py` | 11 tests: no restore, repointing, rollback asymmetry. |
| `tests/test_probe_cache.py` | 19 tests: hashing, cache hits, learned symbols, the corpus. |
| `tests/test_corpus.py` | 8 tests. Pins that the directory exists, that no example has drifted from its renderer, and that every verified one is a sendable bare lambda. |
| `tests/test_standards.py` | 17 tests: overrides, refusal of typo'd names, zero-disables-a-check, and that no file means no behaviour change. **389 total** (12 added 2026-11-23: hex pinion, NEO torque constant). |
| `scripts/build_corpus.py` | Regenerates `featurescript/`. Offline, free, idempotent. |
| `scripts/make_workspace.py` | **Builds the folder people actually use** (`../frc-copilot`), away from the source. Re-run after changing CLAUDE.md or the corpus. |
| `workspace_template/` | The workspace's CLAUDE.md. Named `.template` so it never loads in a session here. |
| `scripts/smoke_auth.py` | Gate 1 check. |
| `scripts/check_no_print.py` | Enforces the stdout rule via AST. |
| `.mcp.json` | **Committed.** Registers the server for Claude Code automatically. **Hardcodes the Windows venv path** `.venv/Scripts/python.exe` — on macOS/Linux this must become `.venv/bin/python`. JSON allows no comments, so the note lives here and in the README. |

## Environment

- venv at `.venv`, Python 3.11.2. Run things as `./.venv/Scripts/python.exe ...`
- `fastmcp` 3.4.5, `httpx` 0.28.1
- Launch the server as a module from the project root: `python -m src.mcp.server`

## Verify everything (about 30 seconds)

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q          # 350 passed, offline
./.venv/Scripts/python.exe scripts/check_no_print.py    # ok: no print() under src/
./.venv/Scripts/python.exe scripts/smoke_auth.py        # Gate 1 passed
```

The one live check worth its request — our arithmetic against Onshape's geometry:

```bash
./.venv/Scripts/python.exe scripts/verify_pocket_geometry.py   # 1 request
```

---

## Settled facts — do not re-investigate

Each of these cost real time to establish. They are recorded so nobody pays twice.

**Onshape API version — use `v26`.** Probed live 2026-08-03: `v0`–`v26` all return 200, `v27`
returns 400, a malformed version returns 404. A stale version *never errors* — it quietly serves
an older contract, so "it works" proves nothing about being current. We were on `v6` (copied from
a docs example) for the first two sessions. Re-probe before bumping.

**stdout under MCP stdio, measured on fastmcp 3.4.5.** A single stray `print()` corrupts the
JSON-RPC stream *and* the in-flight tool reply is lost — the call dies with no error. Repointing
`sys.stdout` at `sys.stderr` is **not** a usable guard: the transport writes to the `sys.stdout`
object, not fd 1, so the redirect kills the protocol too. There is no safety net; discipline is
enforced by `scripts/check_no_print.py`.

**NEO motor constants.** REV publishes **2.6 N·m / 105 A**. The widely-circulated
**3.28 N·m / 181 A** figures are *not* REV's. A regression test pins this.

**API key scope — `OAuth2Read` + `OAuth2Write`, never `OAuth2Delete`.** Write is required and in
use: layer 6 writes a module to a scratch Feature Studio and restores it, verified live.

> This entry previously claimed read-only. That was wrong from the moment the FeatureScript skill
> started writing — corrected 2026-08-07. A stale *security* fact is worth fixing promptly
> precisely because someone would reasonably rely on it.

`/users/*` returns profile data and needs `OAuth2ReadPII`, so identity endpoints 403 even on a
valid signature — this is why the connection tool reports document visibility rather than a
username. Writes are additionally confined to scratch documents by `ScratchPolicy`, in code.

**A chained spur train is limited by shaft clearance, not by reduction.** Found 2026-08-09 while
sizing the climber gearbox. Stage *i*'s driven gear sits on shaft *i*; shaft *i+1* passes straight
through that same plane as a bare shaft, at stage *i+1*'s centre distance. So

    centre_distance(stage i+1)  >=  outer_radius(driven gear of stage i) + shaft_radius + clearance

and the two stages are coupled. A high per-stage ratio needs a small pinion, a small pinion gives a
small centre distance, and a small centre distance is exactly what the *previous* stage's big gear
cannot tolerate. `11:60 x 11:60` interferes by **4.8 mm** with a 1/2 in hex shaft and a standard
spacer — it cannot be built, at any plate size, in any layout, because the clashing distance is a
mesh distance and therefore not free. Driven gears must **grow** along the chain (46 → 54 → 62).

> A layout checker that tests gears against *bearings* will pass all of these. Bearings sit in the
> plate; shafts run between the plates, and that is where the collision is.

**Restoring a Feature Studio's source strands any custom feature inserted from it.** Found
2026-08-09 in `asm-try1`: a `boomFeature` sat permanently in `featureStatus: ERROR` because its
namespace pinned it to a microversion of `Feature Studio 1` whose code the restore step had since
replaced with the empty header. The definition was gone, so the feature could never regenerate
again. Layer 6 is *source*-reverting, not *effect*-reverting — it undoes what it wrote, not what
someone did with it while it was there. Deleting the stranded feature is a plain write
(`DELETE /partstudios/.../features/featureid/{id}` returned 200 on `OAuth2Write`); `OAuth2Delete`
is still not needed and still not granted.

**`x-api-version` reports per-endpoint contract resolution, capped at what you asked for.**
Requesting `v26` comes back as `x-api-version: v16` on `/documents/{did}` — not a downgrade, just
"this endpoint last changed at v16". Measured 2026-08-07 across v1/v6/v16/v17/v26. It also
retroactively justifies the v6 → v26 bump: v6 returns 5,622 bytes where v16+ returns 5,629, so the
old contract really was a different shape. **Requesting the newest version remains correct.**

**Key generation is inside Onshape**, not a separate developer portal: *My account → Developer*,
or for enterprise *Enterprise settings → Developer → API keys* (admin only).

**Signing traps** (all handled, with matching comments in `onshape_client.py`): lowercase the
signature only; `formatdate(usegmt=True)` not `strftime`; encode the query exactly once; always
insert `/api/{version}`; re-sign on every retry.

**Rate limits — quota is PER ENDPOINT, not one budget.** Every response carries
`x-rate-limit-remaining`, including error responses. Measured 2026-08-07:

| Endpoint | Ceiling |
|---|---|
| `POST /partstudios/d/w/e/featurescript` (layer 5) | **~84** |
| `GET /partstudios/d/w/e/features` | 100 |
| `GET /assemblies/d/w/e/massproperties` | 250 |
| `GET /documents/d/w/currentmicroversion` | **400** |
| `GET /documents` (list) | 1000 |
| `GET /assemblies/d/w/e` | 1000 |
| `GET /documents/d/w/elements` | 1500 |
| `GET /documents/{did}`, `/featurestudios/*`, `.../boundingboxes` | 3000 |

> The earlier note here — *"the budget starts around 1000, each request costs exactly 1"* — was
> measured on `/documents` alone and generalised to everything. Same mistake spike 3b made.
> Corrected 2026-08-07.

Two consequences that shaped Step 4:

- **The FeatureScript evaluate endpoint is the scarcest thing we call (~84)** and it is the one a
  correction loop would hammer hardest. Iterating on a template is far more expensive than
  reading assemblies.
- **`currentmicroversion` is 400** and the cache used to spend one per question — the call we made
  cheap in *bytes* is the one we are poorest in. Fixed by a 60 s TTL; three questions about one
  assembly now cost 1 validator request instead of 3.

Within one endpoint the behaviour is clean: exactly 1 per request, **no refill over 45 s of
idling**, and everything back at ceiling the next day — so it is a daily-ish quota, not a
throttle. Across endpoints the bucket grouping is **not inferable** (some routes clearly share a
counter, in patterns we could not pin down). **Therefore the governor never simulates: it reads
the header and acts on it.** Local request counting would be a guess wearing a number's clothes.

**One key is one set of buckets.** N students on one key share ~84 FeatureScript evaluations, not
84 each.

**A timed-out request still consumes quota.** Measured: 249 → 247 across two completed requests
plus one that hit a client-side `ReadTimeout`. The server received it and kept computing, so a
retry pays a second scarce request to duplicate work already in flight. **Read timeouts are
therefore never retried** — a timeout here means the timeout is configured wrong, not that the
network flickered. Connect failures never reach the server and *are* retried.

**We have never seen a real 429**, deliberately: provoking one costs ~1000 requests and disables
whatever depends on that bucket for the window. The reactive path honours `Retry-After` when
present and backs off exponentially otherwise, and that assumption is marked untested in the code.

**A `Query` parameter's annotation MUST carry a `"Filter"`.** Without one the module fails to
compile, and the Feature Studio write path reports no reason — so it looks like nothing happened.
Found by bisecting our own gearbox template:

    annotation { "Name" : "Plate", "Filter" : EntityType.BODY }   // compiles
    annotation { "Name" : "Origin" }                              // FAILS silently
    definition.origin is Query;

Non-Query parameters (`isLength(...)`) need no filter. This is the second time a hand-written
template was wrong in a way only Onshape could reveal, which is the argument for keeping layer 6
in the loop even though layer 5 is now informative.

**The FRC community already maintains 37 FeatureScripts, and we can drive them from the API.**
Found 2026-08-09/10, and it is the most consequential fact in this file because it says what this
project should *not* build.

[FRCDesign.org](https://frcdesign.org/resources/featurescripts/) lists 37 maintained scripts. Three
of them are our three templates, done better: **Tube Converter** (punched tube with hole patterns),
**CheeseIt!** (isogrid, hex and circle lightening — ours does rectangles with square corners),
**Spur Gear**. Also Gusset Generator, Robot Shaft, Robot Spacer, Belt & Chain Gen, Auto Layout.

The namespace for a feature living in someone else's document is:

    d{documentId}::v{versionId}::e{elementId}::m{microversionId}

The inner `e...::m...` half comes back from `featurespecs`; the `d...::v...` half must be prepended.
**Read the spec through a version, not a workspace** — a workspace microversion is not reachable
from another document and the insert returns **404**. That 404 is the whole trap, and it looks like
a wrong URL rather than a wrong reference. Verified end to end by
`scripts/spike_community_featurescript.py`: insert accepted 200, the feature appears in the tree,
cleanup removes it.

Version pinning is a bonus, not a cost: the library cannot change under a design that already
shipped.

**Not yet proven: setting parameters.** Tube Converter has 20 and its spec is 30 KB of
`BTParameterSpec` types. The cheap route, from the Onshape forum, is to add the feature once by hand
in the UI, `GET /features`, and copy the parameter block — the read shape and the write shape match.
Note the size: 30–88 KB per spec is far past the 8 KB response ceiling, so any index of these must
distil the schema rather than carry it.

**Consequence.** Our three templates were reinventing a mature ecosystem, worse — the same
unchecked-assumption failure that already cost us the payload model, the error model, the quota
model and the mass-properties write-off. But the same list shows the niche: **nothing among the 37
models the torque of a rotating-arm climber or searches gear stacks for shaft clearance.** The
community owns geometry generation; this project owns the physics that decides the numbers, and
verification against the kernel.

**Face selection works, and here are the mechanics.** This was the open question the project
deferred for weeks; measured 2026-08-09 and closed.

    var plane = evPlane(context, { "face" : definition.face });
    var box   = evBox3d(context, { "topology" : definition.face,
                                   "cSys" : planeToCSys(plane) });

`evBox3d` takes a coordinate system, so a planar face measured in its **own** plane returns clean
u/v extents with w = 0. That is what lets a pattern be laid out inside a face's real boundary
instead of inside dimensions the caller claimed. In the module the face is a selection carrying
`"Filter" : EntityType.FACE && GeometryType.PLANE`, so a bad pick is refused at selection time.

**Correction, measured 2026-11-25: "own plane" fixes the normal, not the in-plane axes.** The
plane's x comes from how the face was built. A 150 x 75 mm plate sketched at 30 deg on Top keeps
Top's x, and `planeToCSys` measured it **167 x 140 mm** — exactly 150cos30 + 75sin30 by
150sin30 + 75cos30. Boxing along each straight edge and keeping the smallest returned **150 x 75**
(one evaluate request; `face_dimensions.render_probe`). `plate_pocket_pattern` still lays its grid
along the plane's x, so on a plate drawn at an angle its pockets are solved against the wrong box.
Not yet fixed there.

**The evaluate endpoint runs real modelling operations, and persists nothing.** Also 2026-08-09,
and it is the biggest capability found since the diagnostics. A single lambda can build a body,
sketch on it, extrude, boolean, and measure the volume before and after — `makeId("...")` supplies
the Id that a feature would normally receive. So **layer 5 is not limited to syntax and queries: it
can verify the geometric outcome.** `scripts/verify_pocket_geometry.py` uses this to build a plate,
pocket it, and check the grams we report against what the kernel actually removes.

**First end-to-end check of a physics number against the kernel.** `plate_pocket_pattern` predicted
**207.5 g** removed; Onshape measured **76,835 mm³**, which at 2700 kg/m³ is 207.5 g — a
**0.0000%** disagreement. Until this, every "verified live" claim in the project meant *Onshape
accepted it*, never *the number we told the student is true*.

**The scratch Part Studio has NO solid bodies — 3 sheet bodies.** So every earlier template probe
that "passed live" was measuring **surfaces**. The passes were real (the source parses, the queries
resolve, the arithmetic runs) but weaker than they read, and this belongs next to the same lesson
from spike 3a, where every test assembly turned out to be empty.

The specific danger it exposed: **pocketing a sheet body completes the entire
sketch/extrude/boolean chain, removes nothing, and reports success.** A false pass is worse than a
failure because it looks like verification. Both the probe and the module now assert
`BodyType.SOLID` first. **To fully exercise a template that needs a plate, the scratch document
needs a solid one** — currently the only solid geometry in play is what the verification script
builds inside its own probe.

**Layer 2 (the symbol check) was doing net harm, and has been rewritten to say less.** On
`plate_pocket_pattern` it flagged `planeToCSys`, `qOwnerBody` and `qSketchRegion` — all three valid,
all three compiled and ran the same minute. Zero true catches to date. Two reasons, both of which
had been assumed away:

- **Its premise moved.** It was designed for model-authored FeatureScript. The model does not author
  FeatureScript; it fills parameters into hand-verified templates. The only source this layer ever
  sees is source already known to be good.
- **Its list could never be complete.** `symbols.py` promised `scripts/extract_std_symbols.py` and a
  `fs_symbols.json` fixture. **Neither was ever written**, so the check has always run against ~130
  hand-typed names out of thousands.

The layer stays (it costs nothing and matters again if unverified bodies ever return) but its
message now states plainly that an unknown name is not evidence of an error. A warning that is
usually wrong trains the reader to skip the one time it is right.

**`box` is a reserved word in FeatureScript.** `var box = evBox3d(...)` fails with
`mismatched input 'box' expecting ID`. Found when the layer-5 probe rejected our own
hand-written template — which is the layer working as intended. Use `tubeBox` or similar.

**Payload sizes — the "~20 KB spatial graph" claim is FALSIFIED.** Measured 2026-08-05 against
`1678 2026`, a real Citrus Circuits robot assembly (**1,320 occurrences, 20 subassemblies**):

| What | Bytes |
|---|---|
| **Assembly definition** | **1,255,145** (1.2 MB) |
| — of which `occurrences` | 553,598 (1,320 × ~419 B) |
| — of which `subAssemblies` | 377,771 |
| — of which `parts` | 156,917 |
| Assembly bounding box (whole assembly) | 187 |
| Assembly mass properties | **ReadTimeout at 30 s** |
| Part studio mass properties | **ReadTimeout at 30 s** |

That is **63× the claimed 20 KB**, and it is not an outlier — it is one ordinary FRC robot.

**Query parameters barely help.** `excludeSuppressed=true` saves 7% (1.17 MB).
`includeMateFeatures=false` / `includeNonSolids=false` change nothing (they only ever add).

**Our own format-level reduction gets 82.8%** — keeping only visible occurrences with paths and
transforms rounded to 4 dp yields 215,717 bytes. Still ~10× over the claim, and roughly 54k
tokens of JSON: far too large to hand a model per call.

**Conclusion: reduction must be semantic, not format-level.** — **BUILT, see `src/context/`.**
Measured result on the same robot: the default overview is **308 bytes**, a **3,536:1**
reduction, and a warm call costs one 49-byte request instead of 1.09 MB.

**A trap inside the fix, worth knowing before touching `graph.py`:** a component's identity is
`(documentId, elementId, configuration, partId)`. **`partId` is not optional** — one Part Studio
holds many parts, so keying on document+element alone merged 483 distinct parts into 105 groups.
One group fused 90 unrelated items (screws, shims, tread, shafts) and reported a single shaft as
used **360 times** instead of 4. Caught only by sanity-checking a live number that looked
implausible. A regression test now pins it.

**Mass properties was never a hang — it was our timeout. SOLVED 2026-08-07.** The assembly
endpoint returns in **40.8 s** with a **1,567-byte** body; the Part Studio one answers in 0.4 s.
A flat 30 s client timeout was cutting off a slow-but-perfectly-fine endpoint, and the resulting
`ReadTimeout` was recorded here for two sessions as if the endpoint were unusable. It now has a
120 s per-endpoint override and works: the Citrus robot reports **50.13 kg** (~110 lb, against
the 125 lb limit — a sane number, which is the point of checking).

Lesson worth keeping: *a timeout is a statement about us, not about the server.*

**FeatureScript error reporting — measured 2026-08-05, and the news is bad.** Full round trip
tested against a real Feature Studio: write source, define a feature, insert it into a Part
Studio, read back status. Everything restored afterwards.

| Question | Answer |
|---|---|
| Can we write FeatureScript over the API? | **Yes** — POST returns 200 |
| Does a *syntax* error come back? | **No.** POST of broken source returns **200 with no error field** |
| Can we detect a syntax error at all? | Only indirectly: `/featurespecs` count drops (1 → 0) |
| Can we insert a custom feature? | **Yes** — namespace format is `e{elementId}::m{microversion}` |
| Does a *regeneration* error come back? | **Status only** — `featureStates[id].featureStatus == "ERROR"` |
| Is the thrown message returned? | **No.** A `regenError("...")` string appears nowhere in the response |
| Do *API-level* errors have messages? | **Yes** — e.g. a bad namespace returns a clear `message` field |

**PARTLY SUPERSEDED 2026-08-07 — see below.** The table above is accurate for the *Feature
Studio write* path. Generalising it to "Onshape never reports why" was wrong: a different
endpoint reports in full.

**The evaluate endpoint DOES return diagnostics.** Measured 2026-08-07:

    POST /partstudios/d/{did}/w/{wid}/e/{eid}/featurescript
    {"script": "<lambda>", "libraryVersion": 3029}

It returns a `notices[]` array carrying everything the Feature Studio path withholds:

| Failure | What comes back |
|---|---|
| Syntax error | `type: "PARSE"`, message `"no viable alternative at input '+ ;'"`, **line 1 column 52** |
| Unknown function | `type: "SEMANTIC"`, `PARAMETER_EXPRESSION_UNKNOWN_FUNCTION`, argument `"thisFunctionDoesNotExist"` |
| Thrown `regenError` | `type: "EXECUTION"`/`"OTHER"`, the thrown text verbatim |
| **Query matching nothing** | **`"throw query matched nothing"`** — the FM1 error class, with a message |

Note the HTTP status is **200 even on failure**; the errors live in `notices`, so status codes
must not be used as the signal.

**What it accepts** (probed, not assumed):

- a bare lambda `function(context is Context, queries) { ... }` — **std library available
  implicitly**, no import needed
- `import` statements — **rejected**, PARSE error
- `annotation` / `export const ... = defineFeature(...)` — **rejected**, PARSE error

**Consequence for the design.** A custom feature's `defineFeature` wrapper is fixed boilerplate we
author once; the *body* is where query logic and therefore real errors live. Validate the body as
a lambda through this endpoint and we get line-numbered diagnostics for exactly the failures FM1
warned about, then wrap the verified body and write it. So the correction loop the brief wanted is
achievable after all — through a different endpoint than assumed, and only for the body.

---

## Blockers

Nothing is blocked. All spikes closed, all four gates plus Step 4 pass.

The one live constraint worth planning around is **quota, not access**: the FeatureScript
evaluate bucket is ~84 a day and every template iteration spends one. Template *development* is
the expensive activity here; template *use* is cheap.

Never grant `OAuth2Delete`. Removing a feature is a *write*, so the copilot never needs Delete,
and Delete's blast radius is whole documents and workspaces. Confirmed off on the current keys.

## Test material available in Onshape

Four scratch documents, each with a Part Studio, an Assembly, and a BOM. Enough for spike 3a.

| Document | documentId | workspaceId | assembly elementId |
|---|---|---|---|
| `asm-try1` | `e424515893d4f1ab8b23e5c4` | `65294e9343b8524f94ad5093` | `93e19e3cd9ed26c515a64429` |
| `chasis-try1` | `e5b9bb471fadbeb95654f48e` | `a2000a27b200d613470f2727` | `764a27984241816b7fa8ba77` |
| `wheel-try1` | `df012a80f68bc1491818722b` | `d300421248f838749879aea1` | `818326888ed89f3c97e89fb3` |
| `try0` | `2d840757879cdfe89d6ed95f` | `af51f9eeced51e64ccbbce0a` | `dd3ee89cc65e9b08c4fb4daa` |

These are IDs, not secrets — they are useless without a key. Still worth a thought before this
repo goes public (FRC teams often open-source): they identify real documents in your account.

`asm-try1` is the one the copilot may write to — it is the default in `ScratchPolicy`
(`src/skills/apply.py`). The table above records only its assembly, but the scripts address its
other tabs by id too, so all of them are listed here:

| Tab | elementId | Used by |
|---|---|---|
| `Part Studio 1` | `4fa46792842ff31b21179dad` | `verify_pocket_geometry.py`, `spike_fs_evaluate.py` — the Part Studio templates are evaluated against |
| `Assembly 1` | `93e19e3cd9ed26c515a64429` | spike 3a, `onshape_assembly_overview` |
| `Feature Studio 1` | `ec66031db7115bc25dd64e88` | layer-6 compile target; source is written and then restored |
| `BOM : Assembly 1` | `eea8859dc1c9f4ec0d35db8a` | — |
| `Gearbox` | `9667e7d938f0d5482922646e` | Climber gearbox plate, built 2026-08-09 |
| `Climber Gearbox` | `dadd16bcc882a51712fadd5b` | Feature Studio holding `climberGearboxPlate`. **Source must not be restored** — the inserted feature is pinned to its microversion |

---

## Open decisions

**One shared API key vs. one per student.** Still deferred, but the case is now sharper than it
was. Costs nothing in code — `from_env()` already reads per-machine and a stdio server is
inherently one-process-one-user, so this is a provisioning step, not a feature. What changed:
**one key is one set of buckets, and the tightest is ~84 FeatureScript evaluations a day.** Two
students iterating on templates would contend for that immediately. Revisit when a second person
starts. Attribution still can't be reconstructed retroactively.

**Writes never persist, by decision (2026-08-07).** The copilot proves a template compiles and
hands back verified FeatureScript; the student applies it. `apply_module()` restores the original
source in a `finally`. Persisting is a later, separable choice — it needs handling for the
half-applied case (a write that succeeds, then fails to regenerate), which is why it is not free.

**Physics scope.** `motors.py` is done. `stress.py` (beam deflection, safety factors) is
unstarted and fully unblocked — pure maths, no auth, good work to hand to a student.

**How to replace the falsified "lightweight spatial graph" (decide before Step 5).**

This is a well-trodden problem outside CAD: *a large authoritative dataset that a
bandwidth-limited consumer must query.* Four borrowed patterns, in the order they should be
applied. They compose — this is not a menu of alternatives.

**1. Microversion as a cache key (from git / rsync / HTTP ETags).** Onshape microversions are
immutable and directly addressable — swap `/w/{workspaceId}` for `/m/{microversionId}` in any
URL. `sourceMicroversion` comes back on nearly every response. That is a content hash handed to
us for free: cache the 1.2 MB against it, and a cheap check tells us whether anything changed.
The expensive fetch happens once per edit, not once per question. Against a ~1000-request daily
budget this is the difference between viable and not.

**2. Hierarchical level of detail (from game engines and map tile servers).** No renderer ships
a million polygons and no map server ships the planet; both serve coarse data by default and
refine only where the camera is looking. Same move here: **20 subassembly bounding boxes by
default, drill into per-part transforms only for the subassembly in question.** HLOD is the
established name for this. The "camera" is the student's question.

**3. Index and query, don't scan (from databases).** With no server-side query language, build
the equivalent locally — a materialised view over the cached definition, so tools answer "the 8
parts in the intake" without a linear pass over 1,320 occurrences reaching the model.

**4. Structured summaries, not raw state (from the CAD-agent literature).** *Embodied CAD*
(arXiv 2606.31252) grounds LLM agents in exactly this domain and returns **structured solver
feedback — validity checks, volumes, bounding boxes, topology summaries** — rather than raw
geometry. Convergent evidence that summaries beat dumps, from people who tried both.

**Hard rule that falls out: the occurrence list never reaches the model.** 215 KB post-reduction
is ~54k tokens.

~~Also needs deciding: the two mass-properties endpoints time out at 30 s.~~ **Closed 2026-08-07**
— it was our timeout, not their latency. 120 s override, works, 1.5 KB response.

**And for spike 3b's finding — no error messages.** The same literature suggests the fallback:
if Onshape will not say *what* is wrong, generate from a **constrained library of known-good
patterns** rather than free-form FeatureScript. *Embodied CAD*'s L0–L4 skill hierarchy exists
because "a raw CAD scripting space is too large for reliable long-horizon LLM planning". A binary
pass/fail signal is workable against a small library of parameterised templates; it is close to
useless against unconstrained generation.

---

## Session log

**2026-08-02 — scaffolding and signing.** Repo, key hygiene, `OnshapeApiClient` with HMAC
signing, one-tool MCP server. Signature verified offline against an independent re-derivation of
the documented algorithm. Settled the stdout question by measurement; discovered the
`sys.stdout` guard doesn't work and added AST-based enforcement instead.

**2026-08-02 — physics.** `motors.py` with vendor-verified constants and 41 tests. Caught the
wrong NEO figures before they were baked in.

**2026-08-03 — Gate 1 live.** Keys added; signature accepted by the real API. Found and fixed
three issues: 20 API versions behind, a stale tool name in the README, and httpx logging every
request at INFO.

**2026-08-05 — all three spikes closed.** Spike 3b run end to end against a real Feature Studio:
FeatureScript written over the API, a custom feature defined and inserted into a Part Studio,
regeneration status read back, everything restored. Result: failure is detectable but **Onshape
returns no error message** for either compile or regeneration — the correction loop the brief
specified cannot work as written. Researched cross-domain solutions to the payload problem and
recorded four composable patterns. `.mcp.json` committed with a macOS path caveat.

**2026-08-09 — the third template, and the first honest geometry check.** `plate_pocket_pattern`
built and verified; face selection turned out to be tractable and it compiled first try. Two
capabilities found along the way: `evBox3d` accepts a `cSys`, so a face can be measured in its own
plane; and **the evaluate endpoint runs real modelling operations without persisting**, so layer 5
can check geometric outcomes rather than only syntax and queries. Used that to verify our mass
arithmetic against Onshape's kernel — 0.0000% apart. Two uncomfortable findings recorded rather
than smoothed over: the scratch Part Studio holds **no solid bodies**, so earlier template probes
were passing against surfaces, and pocketing a surface reported success while removing nothing;
and the layer-2 symbol check has produced only false positives, because its premise moved and the
extractor its docstring promised was never written.

**2026-08-07 — Step 4, and three corrections to this file.** Hardened the client: classified
retries (not blanket), per-endpoint timeouts, a per-bucket quota governor, a typed error
hierarchy, and 50 offline tests including the signing golden vector that had been missing since
day one. Four cheap live checks paid for themselves several times over — they overturned the
quota model (per-endpoint, ~84 for the FeatureScript loop), proved mass properties was our
timeout rather than Onshape's latency, showed a timed-out request still spends quota, and cleared
the `x-api-version` mystery. Corrected three stale facts here: the single-budget quota claim, the
`OAuth2Read`-only scope claim, and the mass-properties write-off. Replaced the hardcoded request
counts in the tool surface with measured ones — the compile path costs 7, not 5.

**2026-08-05 — status check, then spikes 3a and 3c.** Everything re-verified green. Gate 2 closed
properly (tool called through the real Claude Code MCP path). New API keys installed; they carry
`OAuth2ReadPII`, which the previous ones did not. Ran spike 3c: found Onshape advertises
`x-rate-limit-remaining`, so backoff can be proactive rather than reactive. Ran spike 3a: the
raw-geometry ratio confirms the architecture's principle, but every assembly is empty so the
claim itself is still open.

**2026-08-09 — scratch document tidied, gearbox tab opened.** Inspecting `asm-try1` turned up a
`boomFeature` stuck in `ERROR` since some earlier layer-6 run: its Feature Studio definition had
been restored out from under it. Deleted it, so `Part Studio 1` regenerates clean (`Sketch 1`,
`Extrude 1`, both OK) and template probes no longer run against a document carrying a permanent
error. Recorded the residue as a settled fact — source-reverting is not effect-reverting. Wrote
`asm-try1`'s per-tab element ids into this file; two scripts already hardcoded the Part Studio id
that was never documented.

**Then the first real design job, and the first one driven by a student's requirement rather than
by the codebase.** A rotating-arm climber: 60 kg robot, pivot at the frame edge, CoM at the frame
centre, 90° fold in 0.5 s, Kraken X60, ratchet holding the static load. Three corrections came out
of it, each of which would have been expensive in aluminium:

1. **Putting the robot's mass at the pivot models a different machine.** With the CoM 350 mm
   behind the pivot the assembly is a pendulum about the hook, so it hangs with its CoM below the
   bar and the fold *shrinks* that radius — the shrink is the lift. Peak torque drops 235 → 206
   N·m and moves from the end of the stroke to the middle, then unloads to 50 N·m. Over-centre.
2. **A swing simulation that "passes" can still describe a climber that falls.** One Kraken
   reached 90° at G=110 on momentum while able to hold only 0.92× of the load. Simulating the
   motion answers a different question from sizing the hold; both have to be asked.
3. **The chain shaft-clearance constraint above**, which killed the chosen 11:60 × 11:60 × 12:60
   outright.

Final: `11:46 × 11:54 × 11:62` = 115.7:1, two Kraken X60 FOC at 80 A, 0.40 s, 1.46× torque margin,
86.15 × 86.68 × 6.35 mm plate, four 28.575 mm bores for 1/2 in hex bearings. Probed before
committing — Onshape's kernel measured 31,128.5100 mm³ against 31,128.4796 mm³ predicted,
**0.0001%**. The probe also caught `box` as a reserved word, a rule already in this file, first try.

> **Superseded 2026-11-23.** That stack is unbuildable: its stage 2 and 3 pinions are 11T on
> 1/2 in hex, and no such gear exists. It is now `11:56 × 14:62 × 14:70` = 112.7:1, which
> folds in 0.40 s at a 1.43× margin. The plate is 91.84 × 89.54 × 6.35 mm. The kernel measured
> 35,927.9662 mm³ against 35,927.9445 mm³ predicted, **0.0001%**. The `gearbox` Part Studio was
> rebuilt on 2026-11-24, and the committed solid measured the same volume.

All of it then moved out of scratch and into the repo: `src/physics/climber.py`,
`src/physics/geartrain.py`, `src/skills/climber_plate.py`, four scripts and 51 tests. The two
superseded models were **not** carried over — the mass-at-the-pivot climber and the
gears-versus-bearings layout check are both wrong, and a repo is where wrong models go to be
believed later. What they taught is in the tests, which pin the bad cases by name.

Two costs worth naming. The suite went 5.6 s → 29 s on the first pass, which is a real tax on a
project whose tests are meant to be run constantly; splitting shape from orientation in
`fold_layout` and giving `is_buildable` a path that does not format failure strings brought it to
13 s. And the build script's first drift check was an absolute 1e-3 mm³, which the kernel fails by
rounding on any part this size — tolerances against a CAD kernel have to be relative.

**2026-08-10 — the SolidWorks reframe, and two things that survive it.** Planning the
next round turned up the fact that reorders the whole file: **the team is on SolidWorks
and will be for the 2027 season.** The "put it in front of a student" gate cannot run.
Two pieces of work were chosen precisely because they pay off either way.

**The corpus existed only as an instruction.** `CLAUDE.md` is loaded on every request and
tells each session to grep `featurescript/`. The directory did not exist — `promote()`
was written, documented, and never called. An always-loaded instruction pointing at
nothing costs a tool call to discover. Four examples now live there, two generated from
the renderers themselves so they cannot drift, and a test fails when they do. The `box`
failure is in `failures/` with the message Onshape gives — no line number, because both
recorded sightings were in longer bodies and inventing a position would be its own small
version of the mistake this file keeps correcting.

**Shop rules left the templates.** Eleven numbers were hardcoded across three templates —
minimum rib, border, web, edge, end mill radius, undercut tooth count. They are now
declared in `src/skills/standards.py` with their reasons and overridable from
`standards.toml`. Three decisions worth keeping:

- **Overrides only.** No file means no behaviour change; the other 360 tests are the
  evidence, and two tests state it outright so nobody "fixes" it.
- **A typo'd key is refused at load**, with the valid spelling in the message. A standard
  that silently does nothing is the worst outcome available — it looks configured and is
  not enforced. Zero is refused too, for the same reason.
- **Physical facts stay in code.** Density, bearing OD, hex circumradius. Making those
  editable would invite tuning a density until the mass came out right.

The refusal quotes the team's number *and says it is the team's* — but only when they set
it. A default reads as engineering; a team value needs to be attributable, because a
student who disagrees has to know whether to argue with the team or with us.

`standards.toml` ships **entirely commented out**. The mechanism, with nothing invented.

---

## Next action

**Step 4 is closed.** All four gates plus Step 4 now pass. Measured live on `asm-try1`, 2026-08-07:

| Case | Result |
|---|---|
| Hole wider than its tube | Rejected locally, **0 API requests** |
| Valid params, wrong stated length | Probe returned `"Tube is 0.15 meter but the pattern was computed for 300 mm"` — real geometry, 1 request |
| `tube_hole_pattern`, correct params | Probe clean (1 request); module compiled and Feature Studio restored (**7** requests) |
| `gearbox_bore_pattern`, 14T→50T, 2× Kraken | Compiled and restored (7 requests), with the 732 A stall warning |
| Citrus robot as a write target | Refused at `write_safety`, **0 requests** |
| Three questions about one assembly | **2 requests total** (was 3) — the TTL removed the validator |
| Citrus assembly mass properties | **50.13 kg** in 40.8 s — previously a timeout |

**The compile path costs 7 requests, not the 5 that was hardcoded.** `apply_module` reads before
each write and restores at the end. Counts now come from the client's own ledger, so they cannot
drift from reality again — and a retry is visible in the number rather than hidden by it.

Live results for `plate_pocket_pattern`, 2026-08-09:

| Case | Result |
|---|---|
| 3x3 grid, module | Compiled **first try**, restored, 7 requests |
| Grid arithmetic vs the kernel | 76,835 mm³ predicted, 76,835 mm³ measured — **0.0000%** |
| Sheet body instead of a solid | Refused with a readable reason (previously a silent false pass) |
| 13 columns → 6.0 mm pockets | Refused locally, **0 requests** — the cutter cannot enter |

### The dry run before the student test (2026-08-09)

Rehearsing a realistic request found a defect the student would have hit instead. Imperial input
converts cleanly (`8 x 8 inch gusset, 1/4 inch thick` → sensible pockets, correct 71%-removal
warning), but **`frc_list_templates` was 5,661 bytes for three templates — about 1,900 each,
against an 8 KB ceiling.** It passed `fit_response` a single candidate, so at roughly five
templates it would have fallen straight through to *"Result too large"* with the advice *"ask about
one template by name"* — and no tool took a name. Zero template information and no way forward.

Now: `frc_list_templates(name=...)` exists, and the listing degrades to summaries then to names.
Simulated at 13 templates it stays valid, keeps every name, and the hint points at a real call.

**The lesson generalises: rehearse the path before spending a person's attention on it.**

### What to do next

Ordered by what it unlocks per unit of effort, under the SolidWorks constraint at the top
of this file. Full reasoning and kill triggers in the plan; the short version:

1. **Import one real SolidWorks subsystem into Onshape.** Pack & Go a gearbox or an
   intake, import it, add it to `robot.toml` as read-only, and run the three read tools on
   it. This decides whether the demo runs on *their* robot or on a stranger's. ~5 requests,
   none from the scarce bucket. Research says instance structure survives the translation
   while mates and the feature tree do not — which is exactly the half `graph.py` reads,
   so the expected result is a pass. **Confirm it rather than assume it.**
   **Blocked on input only, as of 2026-08-11:** no Pack & Go zip is on hand. The import
   path is already researched: SolidWorks **Pack and Go → Save to Zip**, then either
   drag-and-drop in the UI or `POST /blobelements/d/{did}/w/{wid}` (multipart, needs
   `OAuth2Write`) + poll `GET /translations/{tid}` until `DONE`. Keep
   `flattenAssemblies=false` / hierarchy intact — the tree is what `graph.py` reads.
   The current client sends JSON only; a multipart spike script must be written when
   the file arrives.
2. **`Constants.java` ↔ physics cross-check.** Parse the team's WPILib constants and check
   the gear ratio, current limit and encoder conversion against `geartrain.py` and
   `motors.py`. Zero Onshape, zero quota, offline, and it works on the 2027 SolidWorks
   robot as it stands. **Blocked on input only:** the repo is not on this machine
   (WPILib 2026 is installed; no `.java` anywhere). Spike first — read one real
   `Constants.java` and judge whether the numbers can be extracted without guessing.
   Do not write the parser before seeing it.
3. **Community FeatureScript parameters.** Still the biggest capability opener, and the
   most persuasive Onshape-specific argument for a migration — the 37 scripts have no
   SolidWorks equivalent. Scope the spike to the half that can fail: setting a **query**
   parameter, not a scalar. `BTMParameterQueryList-148` with `deterministicIds`, or a
   `queryString` carrying `qCreatedBy(...)`; `frc_probe_featurescript` is what resolves an
   entity to an id. If a query parameter needs a human picking geometry in the UI, the
   capability is "scalars only" — record it and **do not build a tool**.
4. **Design intent per subsystem.** Mass budget, envelope, requirement, decisions taken.
   Needs the team's actual numbers; do not invent them. This is what would make a
   mass-properties answer mean something ("4.2 kg against a 4 kg budget") rather than
   being a number the Onshape UI already shows faster.
5. **`stress.py`** — beam deflection and safety factors. Unblocked, pure maths, good work
   to hand to a student. Ranked here honestly: it does not move the migration decision.
   Worth knowing that only *half* of it is kernel-verifiable — second moment of area can
   be measured the way pocket volume was; E and yield for 6061 are vendor data.
6. **Put a solid plate in the scratch Part Studio.** One minute in the UI: 150 x 150 mm on
   the Top plane, extruded 6.35 mm blind — dimensions already verified against the kernel,
   so pass/fail is unambiguous. Without it no plate template can be probed end to end
   against stored geometry.
7. **Discover the FS library version at runtime.** Hardcoded `libraryVersion: 3029` in the
   probe goes stale silently, like our `x-api-version` did. One cheap GET on the public std
   document's `/versions` (jarvis-onshape-mcp does this) removes the class. See
   `FINDINGS.md` 2026-08-11.
8. **Enrich non-OK persists with `getFeatureStatus`.** `persist_feature` reports a bare
   status string on failure; running `getFeatureStatus(context, id)` (or re-probing the
   body) through evaluate turns "REGEN_ERROR" into a fixable enum. Scarce-bucket cost only
   on failure. See `FINDINGS.md` 2026-08-11.
9. **Done 2026-11-25: Onshape Labs' FS MCP vs ours. Keep ours.** Their server cannot
   insert a feature into the user's Part Studio, and it failed the 2 mm rib rule when bare.
   It does not use our evaluate bucket. Our standards and traps transferred to it in arm C.
   Full record: `FINDINGS.md` 2026-11-25. Items 7 and 8 are live again. What it produced:
   - **Done 2026-11-25: a separate workspace.** `scripts/make_workspace.py` builds
     `../frc-copilot`. It holds the server by absolute path, a user-facing CLAUDE.md whose
     traps are copied from ours, the corpus, and Read/Edit/Write denied on the repo. The
     server now pins its cwd to the repo, so it finds `robot.toml` from anywhere (verified:
     9 targets). The deny rules are a guardrail, not a sandbox, because Bash can still reach
     the repo. Not yet tried in a real session.
   - **Fix `plate_pocket_pattern` on angled plates.** It lays its grid along the plane's x;
     `face_dimensions.py` has the edge-direction fix.
   - **Publish the standards and traps as a skill** for the official server. That is the
     part of this project that arm C showed is portable.
   - **Borrow their persistent notes.** Arm A recorded its own lessons mid-run, with
     nobody asking.
10. **Two physics defects found against ReCalc (2026-11-23). Fixed, kernel-verified,
    and committed to the `gearbox` Part Studio (2026-11-24).**
    (a) **Done.** `geartrain.py` now rejects any pinion after stage 1 that is below
    `min_hex_pinion_teeth(dp)` (14T at 20 DP: catalogue, with a geometric fallback).
    `STACK` is now `11:56 × 14:62 × 14:70`; `verify_plate_geometry.py` PASS at 0.0001 %.
    (b) **Done.** `torque_at_current` uses the smaller of the stall-end and back-EMF
    torque constants. REV's published NEO figures are kept and pinned. The NEO is now
    27 % less optimistic under a current limit; the Krakens move by ≤ 2.5 %.
    The rebuild exposed a bug in `build_climber_plate.py`. It judged every feature in the
    shared studio, so the Tube Converter spike's `Extrude 1` at INFO failed a perfect plate.
    It also measured `parts[0]` blind. It now judges only its own feature and measures
    the part closest to the prediction.
    **Left:** confirm that an 11T pinion is sold in the Kraken X60's bore. Still not done
    from ReCalc: supply-current limit and battery sag in `simulate_fold`.

**Not worth doing:** an assembly builder (mates do not survive a SolidWorks import,
`graph.py` deliberately discards the occurrence data it would need, and a wrong mate fails
silently); mass properties as its own tool (40.8 s for a number the UI shows instantly —
make it an argument on the overview tool if at all); Open Alliance RAG (trigger unmet, and
item 2 is a higher-signal version of the same instinct); generic "infrastructure for future
features" (the fourth-template mistake renamed — item 3 is the one with real demand);
extracting the full FeatureScript symbol table, unless model-authored bodies return.

**The 8th tool slot stays empty.** Only item 3 can earn it, and only if its spike passes.
