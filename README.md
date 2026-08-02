# FRC Onshape CAD Copilot

A FastMCP server that gives Claude Code FRC-specific engineering tooling on top of Onshape:
physics validation, parametric FeatureScript generation, and lightweight CAD context — with the
student staying in control of the design.

**Status: walking skeleton.** Steps 0–2 are built (auth client + one-tool MCP server). The
architecture spikes (Step 3) and the hardened client (Step 4) come next. See the project plan
for the full sequence and the failure-mode analysis.

---

## Setup

Requires Python 3.10+ (developed on 3.11).

```bash
# 1. Create a virtualenv and install dependencies
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt

# 2. Configure credentials
copy .env.template .env           # Windows  (cp on macOS/Linux)
```

Generate an API key pair **inside Onshape**, not in a separate developer portal:

| Account type | Path |
| --- | --- |
| Personal / education | user icon → **My account** → **Developer** |
| Enterprise / Classroom (most FRC teams) | user icon → **Enterprise settings** → **Developer** → **API keys** |

Enterprise keys can only be created by an admin, and are assigned to a named member — the key
inherits that member's permissions.

Grant **`OAuth2Read`** only. That covers everything through Step 3; add `OAuth2Write` when the
FeatureScript compiler loop starts pushing code back. Never grant `OAuth2Delete` — removing a
feature from a part studio is a *write*, so the copilot never needs it, and the scope's blast
radius is whole documents and workspaces.

Paste both values into `.env`. The secret is shown exactly once — if you lose it, generate a new
pair.

`.env` is gitignored. Never commit it.

If your team uses an **enterprise Onshape account**, set `ONSHAPE_BASE_URL` to your company
subdomain (`https://<company>.onshape.com`) rather than `https://cad.onshape.com`.

### Verify authentication

```bash
python scripts/smoke_auth.py
```

Prints your Onshape account name on success. If it fails, see Troubleshooting below.

### Register the MCP server with Claude Code

```bash
claude mcp add frc-cad-copilot -- .venv/Scripts/python.exe -m src.mcp.server
```

Then ask Claude Code to call `onshape_check_connection`. It should confirm the same base URL the
smoke test printed.

It reports document visibility rather than *who* you are: identity lives behind `/users`, which
needs the `OAuth2ReadPII` scope that these keys deliberately don't carry.

---

## Architecture

```
Claude Code  ──MCP(stdio)──>  FastMCP server (this repo)  ──REST──>  Onshape
```

Claude Code is the AI core. **This process never calls the Anthropic API**, so no
`ANTHROPIC_API_KEY` is needed — Claude Code carries its own authentication. The variable is left
commented out in `.env.template` so the server can't refuse to start over a credential it doesn't
use.

```
src/
  api/onshape_client.py   HMAC-SHA256 signing + signed requests
  mcp/server.py           FastMCP server and tool definitions
  physics/                motor curves, gear ratios, deflection  (no Onshape dependency)
  skills/                 FeatureScript generation + correction loop
scripts/                  smoke tests and throwaway spikes
```

### Two standing constraints

**Tool budget: 8 tools maximum, all FRC-specific.** Tool definitions are re-sent on every
request, so a wide surface costs tokens continuously and degrades tool selection. There is
deliberately **no generic `onshape_api_call(method, path)` passthrough** — it would be less code,
but it dissolves the boundary against Onshape's own native AI and throws away every payload
guarantee. Interactive 3D selection, fillets/chamfers, 2D drawings, and general CAD Q&A stay with
Onshape's native AI; we don't reimplement them.

**`src/physics/` stays dependency-free.** Pure Python maths, no Onshape and no MCP imports, so a
new student can open it, read it, and test it without understanding anything else here. FRC teams
turn over completely every year; that module is the part designed to survive it.

```bash
python -m pytest tests/ -q      # no network, no credentials needed
```

Every `Motor` requires a `source` URL — the dataclass refuses to construct without one — and
`describe()` prints the constants it used alongside that URL, so a student can check any answer
by hand:

```
4x Kraken X60 (trapezoidal) at 15.00:1 (90% efficient)
  output free speed  : 400 RPM
  output stall torque: 382.86 N.m
  stall current      : 1,464 A total across 4 motors
...
  peak power    : 1114 W computed, 1108 W published
  source        : https://docs.wcproducts.com/...
```

The tests pin each vendor's published figures and check the linear model against each vendor's
own peak-power number (agreement is within 6%; both Krakens inside 1.5%).

> **Careful with the NEO.** `3.28 N·m` / `181 A` circulate widely in FRC materials and are **not**
> what REV publishes — REV's page gives **2.6 N·m / 105 A**. There's a regression test pinning
> this so nobody "corrects" it back.

---

## The stdout rule (read before adding code)

Under MCP stdio transport, **stdout is the JSON-RPC channel**. Both of the following were
measured against `fastmcp 3.4.5` on 2026-08-02, not assumed:

| Question | Answer |
|---|---|
| Does one stray `print()` break things? | **Yes.** It injects a raw line into the stream, and the in-flight tool reply never reaches the client — the call is silently lost. |
| Does `sys.stdout = sys.stderr` guard against it? | **No.** The transport writes to the `sys.stdout` *object*, not fd 1. Repointing it silences stray prints and the protocol alike: zero replies get through. |

There is no safety net, so the rule is absolute: **never `print()` under `src/`.** Use the module
logger, which is bound to stderr. `mcp.run(show_banner=False)` keeps FastMCP's own startup banner
off the channel too.

Because discipline is the *only* mitigation, it's enforced rather than requested:

```bash
python scripts/check_no_print.py     # exit 1 on any print() under src/
```

It parses the AST, so docstrings and comments mentioning `print()` don't trip it. A deliberate
call can be exempted with an `allow-print` comment on the same line. Wire it to a pre-commit
hook, and it joins the test suite in Step 4.

Standalone scripts under `scripts/` are exempt — they're CLIs, not MCP servers.

---

## Onshape request signing

Implemented in `src/api/onshape_client.py` against the
[official scheme](https://onshape-public.github.io/docs/auth/apikeys/). Five traps it exists to
avoid, each with a matching comment in the source:

1. **Lowercasing applies to the signature only.** The `Date` header sent on the wire keeps its
   RFC 1123 casing; only the copy fed to the HMAC is folded.
2. **`strftime` breaks auth under a non-English locale** — it emits localised month and weekday
   names, producing an invalid `Date` and a 401. We use `email.utils.formatdate(usegmt=True)`,
   which is locale-independent by contract.
   ([Onshape support case](https://forum.onshape.com/discussion/14077/changing-locale-breaks-authenticating-to-api-for-some-unknown-reason))
3. **The signed query string must be byte-identical to the one sent.** It's encoded once and
   appended to the URL by hand; `params=` is never passed to httpx, which would re-encode it.
4. **Omitting the API version silently selects `v0`, the oldest API** — no error, just
   wrong-shaped responses. `build_path()` always inserts `/api/{version}`.
5. **Every retry re-signs.** `Date` expires after 5 minutes and nonces must be unique, so
   signing will live *inside* the Step 4 retry loop, never above it.

---

## Troubleshooting

**`401` from `smoke_auth.py`** — the client checks for clock skew automatically and will say so
in the error if your system clock is more than ~2 minutes off Onshape's. Onshape rejects any
request whose `Date` is more than 5 minutes out, and the resulting 401 is otherwise
indistinguishable from a bad key. Otherwise: re-check both keys in `.env` for stray whitespace.

**`404` on an endpoint that should exist** — likely an API version mismatch. Try bumping
`ONSHAPE_API_VERSION`.

**Server connects then dies mid-call** — something wrote to stdout. See the stdout rule above.

---

## Notes

The original project brief specified **Claude 3.7** in its architecture diagram; that model is
long superseded. The current model is **Claude Opus 5** (`claude-opus-5`). Claude Code selects
its own model, so this changes no code here.
