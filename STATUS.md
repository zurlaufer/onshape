# Project Status

**Last verified: 2026-08-05** — against commit `4e3da29`, working tree clean apart from `.mcp.json`.

> Maintained by Claude. Updated at the end of any session that changes project state, so picking
> the project back up doesn't require re-scanning the codebase or re-running discovery. If
> something here contradicts the code, the code wins — say so and it gets corrected.

---

## Where we are in one paragraph

The walking skeleton is **complete and verified live**: signed requests reach Onshape, the MCP
server runs, and one tool works end to end. The motor physics module is built and tested. The
next milestone is **Step 3 — the architecture spikes**, which are the last things that can still
tell us the design needs to change. Two of the three spikes are unblocked right now; the third
(FeatureScript) needs a wider API key scope.

---

## Gate status

| Gate | What it proves | Status |
|---|---|---|
| 0 — key hygiene | `.env` ignored before keys hit disk | **PASS** — `.env` untracked, ignored, verified |
| 1 — live auth | The HMAC signature is accepted by Onshape | **PASS** — 200 on `GET /documents?limit=1` |
| 2 — MCP wiring | Claude Code ↔ server ↔ Onshape | **PASS (technical)** — tool returns live data via an MCP client; `.mcp.json` present but the round trip through the Claude Code UI has not been confirmed by hand |
| 3a — payload size | Is the "~20 KB spatial graph" claim real? | **NOT STARTED** — unblocked, assemblies exist |
| 3b — FeatureScript errors | Can regeneration errors be read programmatically? | **BLOCKED** — needs `OAuth2Write` (see Blockers) |
| 3c — rate limit ceiling | How much headroom does the retry budget have? | **NOT STARTED** — unblocked |
| 4 — hardened client | Retry/backoff + signing regression tests | **NOT STARTED** — deliberately after the spikes |

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

---

## Blockers

**Spike 3b needs `OAuth2Write`.** No document currently contains a `FEATURESTUDIO` element, and
creating or modifying one is a write. The current key is read-only.

Two ways forward, to decide when 3b starts:
- Add `OAuth2Write` to the existing key (still never `OAuth2Delete` — deleting a feature is a
  write, so the copilot never needs Delete, and its blast radius is whole documents).
- Or create the Feature Studio by hand in the Onshape UI first, and see how far a read-only
  spike gets. This probably only reaches the *compile* half; reading regeneration errors is the
  half that matters and likely needs the write scope anyway.

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

**2026-08-05 — status check.** Everything re-verified green. Found `.mcp.json` created, four
scratch documents with assemblies available for spike 3a, and no Feature Studio anywhere — which
is what blocks 3b. This file created.

---

## Next action

Run **spike 3a**: fetch bounding boxes, mass properties, and transforms for `asm-try1` and
measure the actual byte count against the plan's ~20 KB claim. Read-only, unblocked, and it tests
the assumption the whole data-reduction architecture rests on.
