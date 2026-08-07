# Project Status

**Last verified: 2026-08-07** — FeatureScript skill v1 built and verified live.

> Maintained by Claude. Updated at the end of any session that changes project state, so picking
> the project back up doesn't require re-scanning the codebase or re-running discovery. If
> something here contradicts the code, the code wins — say so and it gets corrected.

---

## Where we are in one paragraph

The walking skeleton is **complete and verified live** through all three gates: signed requests
reach Onshape, the MCP server runs, and a tool works end to end from Claude Code. The motor
physics module is built and tested.

**Spike 3a did its job and the news is bad in the useful way: the "~20 KB lightweight spatial
graph" premise is false.** A real FRC robot assembly is 1.2 MB, and even 83% reduction leaves
215 KB — far too much to hand a model per call. This does not sink the project, but it does
replace "fetch a small graph each time" with "cache once, answer narrow queries from it". That
redesign is the next real decision.

Spike 3c is done and produced a gift: Onshape advertises remaining quota in a response header,
so backoff can be proactive. Spike 3b remains blocked on a Feature Studio existing at all.

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
| 4 — hardened client | Retry/backoff + signing regression tests | **NOT STARTED** — now has real numbers to design against |

---

## What exists

| Path | State |
|---|---|
| `src/api/onshape_client.py` | HMAC signing + signed async requests. **No retry/backoff yet** (Step 4). Verified live. |
| `src/mcp/server.py` | FastMCP server, **5 tools** of an 8-tool budget. |
| `src/context/cache.py` | Microversion-keyed disk cache. Cold = 2 requests, warm = 1 (49 bytes). |
| `src/context/graph.py` | `AssemblyGraph` — named tree + definition dedup. Pure, no I/O. |
| `src/context/queries.py` | Narrow queries behind a hard 8 KB response ceiling. |
| `src/physics/motors.py` | Kraken X60 (trapezoidal + FOC), NEO V1.1. Dependency-free. Vendor-verified constants. |
| `src/skills/templates/` | `Template` protocol + `tube_hole_pattern`, hand-written and verified. |
| `src/skills/validate.py` | Layers 1-4: structural, symbol, parameter, contract. All free. |
| `src/skills/symbols.py` | Curated std-library symbol table; warnings only, never blocks. |
| `src/skills/apply.py` | Layer 5 probe (real diagnostics) + layer 6 compile, scratch allowlist. |
| `tests/test_motors.py` | 41 tests, no network or credentials needed. |
| `tests/test_context_graph.py` | 35 tests over a synthetic fixture. |
| `tests/test_skill_validation.py` | 39 tests incl. captured Onshape notice shapes; **115 total**. |
| `scripts/smoke_auth.py` | Gate 1 check. |
| `scripts/check_no_print.py` | Enforces the stdout rule via AST. |
| `.mcp.json` | **Committed.** Registers the server for Claude Code automatically. **Hardcodes the Windows venv path** `.venv/Scripts/python.exe` — on macOS/Linux this must become `.venv/bin/python`. JSON allows no comments, so the note lives here and in the README. |

## Environment

- venv at `.venv`, Python 3.11.2. Run things as `./.venv/Scripts/python.exe ...`
- `fastmcp` 3.4.5, `httpx` 0.28.1
- Launch the server as a module from the project root: `python -m src.mcp.server`

## Verify everything (about 30 seconds)

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q          # 41 passed, offline
./.venv/Scripts/python.exe scripts/check_no_print.py    # ok: no print() under src/
./.venv/Scripts/python.exe scripts/smoke_auth.py        # Gate 1 passed
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

**API key scope.** Keys carry `OAuth2Read` only, by choice (least privilege). Consequences:
`/users/*` returns profile data and needs `OAuth2ReadPII`, so identity endpoints 403 even on a
valid signature — this is why the tool reports document visibility rather than a username.

**Key generation is inside Onshape**, not a separate developer portal: *My account → Developer*,
or for enterprise *Enterprise settings → Developer → API keys* (admin only).

**Signing traps** (all handled, with matching comments in `onshape_client.py`): lowercase the
signature only; `formatdate(usegmt=True)` not `strftime`; encode the query exactly once; always
insert `/api/{version}`; re-sign on every retry.

**Rate limits — Onshape tells us directly.** Every response carries
`x-rate-limit-remaining`. Measured 2026-08-05: the budget starts around **1000**, each request
costs exactly **1**, and **nothing refilled over 30 seconds of idle** — so it is an hourly or
daily quota, not a per-second throttle. 150 requests at ~20 req/s produced no 429 at all.

Consequences for the Step 4 retry design:
- Backoff does not have to be purely reactive. Read the header and act *before* hitting 429.
- The budget is the real constraint, not request rate. A 3-retry compile loop is expensive
  against ~1000/window.
- **One key is one budget.** This makes the shared-key question concrete: N students on one key
  share ~1000 requests, they do not get 1000 each.
- These spikes alone consumed ~190. The refill window is still unknown — check
  `x-rate-limit-remaining` at the start of the next session; if it is back near 1000, it
  refills at least daily. That costs one request instead of a burst.

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

**Two mass-properties endpoints time out at 30 s** on a real robot. That is a latency problem
independent of size, and it rules those endpoints out of any interactive tool call as-is.

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

All three spikes are closed. Nothing is currently blocked on access or credentials — what
remains are two design decisions (see Open decisions), both consequences of spike findings.

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

---

## Open decisions

**One shared API key vs. one per student.** Deferred deliberately — costs nothing in code, since
`from_env()` already reads per-machine and a stdio server is inherently one-process-one-user.
Per-student keys are a provisioning step, not a feature. Revisit when a second person starts
using it. Two non-code consequences: attribution can't be reconstructed retroactively, and one
key is very likely one rate-limit bucket (spike 3c will confirm).

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

Also needs deciding: the two mass-properties endpoints time out at 30 s on a real robot. Either
raise the timeout for those calls specifically, compute mass from the cached `parts` block, or
drop them from the tool surface.

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

**2026-08-05 — status check, then spikes 3a and 3c.** Everything re-verified green. Gate 2 closed
properly (tool called through the real Claude Code MCP path). New API keys installed; they carry
`OAuth2ReadPII`, which the previous ones did not. Ran spike 3c: found Onshape advertises
`x-rate-limit-remaining`, so backoff can be proactive rather than reactive. Ran spike 3a: the
raw-geometry ratio confirms the architecture's principle, but every assembly is empty so the
claim itself is still open.

---

## Next action

**FeatureScript skill v1 is done** — templates plus a six-layer validation stack, verified live.

Measured on `asm-try1`:

| Case | Result |
|---|---|
| Hole wider than its tube | Rejected locally, **0 API requests** (confirmed against the quota header) |
| Valid params, wrong stated length | Probe returned `"Tube is 0.15 meter but the pattern was computed for 300 mm"` — real geometry, 1 request |
| Correct params | Probe clean (1 request); full module compiled and Feature Studio restored (5 requests) |
| Citrus robot as a write target | Refused at `write_safety`, **0 requests** |

Two things remain, neither blocked:

1. **Step 4 — harden the client**: quota-aware proactive backoff using `x-rate-limit-remaining`,
   and per-endpoint timeouts (the 30 s default is what makes mass properties fail).
2. **More templates** — the second one is much cheaper than the first now the stack exists.
   A gearbox bore pattern would tie `src/physics/motors.py` into the geometry directly.

Context layer v2, when wanted: mass properties and transforms. Both currently time out at 30 s on
a real robot. The cache and graph built here are the foundation either way.
