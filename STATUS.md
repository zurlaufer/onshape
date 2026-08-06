# Project Status

**Last verified: 2026-08-05** — against commit `79550a2`. Untracked: `.mcp.json`, `.vscode/`.

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
| 3b — FeatureScript errors | Can regeneration errors be read programmatically? | **BLOCKED** — no Feature Studio exists, and creating one is a write |
| 3c — rate limit ceiling | How much headroom does the retry budget have? | **PASS** — measured; Onshape advertises remaining quota in a header |
| 4 — hardened client | Retry/backoff + signing regression tests | **NOT STARTED** — now has real numbers to design against |

---

## What exists

| Path | State |
|---|---|
| `src/api/onshape_client.py` | HMAC signing + signed async requests. **No retry/backoff yet** (Step 4). Verified live. |
| `src/mcp/server.py` | FastMCP server, 1 tool (`onshape_check_connection`) of an 8-tool budget. |
| `src/physics/motors.py` | Kraken X60 (trapezoidal + FOC), NEO V1.1. Dependency-free. Vendor-verified constants. |
| `src/skills/` | Empty — FeatureScript work, gated behind spike 3b. |
| `tests/test_motors.py` | 41 tests, no network or credentials needed. |
| `scripts/smoke_auth.py` | Gate 1 check. |
| `scripts/check_no_print.py` | Enforces the stdout rule via AST. |
| `.mcp.json` | Untracked. Registers the server for Claude Code. **Decide whether to commit it** — see Open decisions. |

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

**Conclusion: reduction must be semantic, not format-level.** Fetching a "lightweight spatial
graph" per call does not work at robot scale. The workable shape is the one the original brief's
own diagram implied but its payload claim ignored — a **local cache plus a query layer**: pull
the 1.2 MB once, hold it, and answer narrow questions from it ("bounding box of this
subassembly", "the 8 parts in the intake", "total mass") without ever returning the occurrence
list. See Open decisions.

**Two mass-properties endpoints time out at 30 s** on a real robot. That is a latency problem
independent of size, and it rules those endpoints out of any interactive tool call as-is.

---

## Blockers

**Spike 3b — two things missing, and the first is the cheap one.**

1. **No `FEATURESTUDIO` element exists in any document.** Create one by hand in the Onshape UI in
   a scratch document (`asm-try1`). No API, no scope change, no risk — and it immediately allows
   testing the *read* half: can we retrieve Feature Studio contents, and is there a structured
   notices/errors field?
2. **Writing to it needs `OAuth2Write`.** Reading regeneration errors — the half that actually
   matters (FM1) — probably needs it too.

Never grant `OAuth2Delete`. Removing a feature is a *write*, so the copilot never needs Delete,
and Delete's blast radius is whole documents and workspaces.

**Spike 3a is closed** — see Settled facts. A real robot assembly (`1678 2026`) measured 1.2 MB.
The consequence is an architecture change, not a blocker.

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

**Commit `.mcp.json`?** Committing it makes registration automatic for the next person, which
directly attacks the onboarding-friction failure mode. It hardcodes a Windows venv path
(`.venv/Scripts/python.exe`), which breaks on macOS/Linux. Leaning: commit it, note the path
assumption in the README.

**One shared API key vs. one per student.** Deferred deliberately — costs nothing in code, since
`from_env()` already reads per-machine and a stdio server is inherently one-process-one-user.
Per-student keys are a provisioning step, not a feature. Revisit when a second person starts
using it. Two non-code consequences: attribution can't be reconstructed retroactively, and one
key is very likely one rate-limit bucket (spike 3c will confirm).

**Physics scope.** `motors.py` is done. `stress.py` (beam deflection, safety factors) is
unstarted and fully unblocked — pure maths, no auth, good work to hand to a student.

**How to replace the falsified "lightweight spatial graph" (decide before Step 5).** Measured
options, given that the assembly definition is 1.2 MB and 83% format-level reduction still leaves
215 KB:

- **Cache + query layer (recommended).** Fetch the assembly definition once, hold it in the
  server, and expose narrow tools that answer questions from it. The model never sees the
  occurrence list. Costs one 1.2 MB fetch per document per session against a ~1000-request
  budget, which is affordable; the brief's own architecture diagram already had a "Local State
  Cache & Data Reducer" box, so this is closer to honouring the design than abandoning it.
- **Subassembly-level aggregation.** Return 20 subassembly bounding boxes rather than 1,320
  occurrence transforms. Much smaller, but loses per-part placement.
- **Never: pass the definition through.** 215 KB is ~54k tokens even after reduction.

Also needs deciding: the two mass-properties endpoints time out at 30 s on a real robot. Either
raise the timeout for those specific calls, compute mass from the cached `parts` block, or drop
them from the tool surface.

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

**2026-08-05 — status check, then spikes 3a and 3c.** Everything re-verified green. Gate 2 closed
properly (tool called through the real Claude Code MCP path). New API keys installed; they carry
`OAuth2ReadPII`, which the previous ones did not. Ran spike 3c: found Onshape advertises
`x-rate-limit-remaining`, so backoff can be proactive rather than reactive. Ran spike 3a: the
raw-geometry ratio confirms the architecture's principle, but every assembly is empty so the
claim itself is still open.

---

## Next action

**You:** create a Feature Studio by hand in `asm-try1`, and put a few parts into one of the
assemblies. Both are UI actions, no scope change, and between them they unblock the remaining
half of 3a and the read half of 3b.

**Then:** re-run `scripts/spike_3a_payload.py` for per-instance cost, and probe Feature Studio
reads for a structured error field. After that, Step 4 (hardened client) can finally be designed
against measured numbers instead of guesses.
