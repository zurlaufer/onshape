# Evaluation: Onshape Labs FeatureScript MCP vs. our FeatureScript path

**Why this exists.** On 2026-08-11 Onshape Labs launched an official FeatureScript
MCP server. It covers the write-probe-fix loop we built ourselves
(`frc_probe_featurescript`, `frc_apply_featurescript`, `src/skills/validate.py`,
the `featurescript/` corpus). If it does that job as well as ours, we should stop
maintaining our version and spend the effort on what nobody else does: the
physics and the shop-rule checks. This protocol produces that decision with
measurements, not impressions.

**Cost.** About 2 hours. About 2 calls from our evaluate bucket (~100/day), plus
whatever their server uses. Results go in `FINDINGS.md`.

---

## Before you start

1. **Make a new, empty Onshape document called `fs-mcp-eval`.** Do not reuse
   `scratch`. Their server is **not** bound by `robot.toml`; our write boundary
   does not apply to it. The only boundary is which URLs you paste into it.
   **Never paste the robot URL (`8d51b49e…`) into a session with their server.**
2. **Subscribe and connect** (from Onshape's Claude Code tech tip, 2026-09-08):
   - App Store → search "FeatureScript MCP Server" → **Subscribe**. Also opt
     into Onshape Labs in Account Preferences.
   - `claude mcp add -s user --transport http onshape-labs https://fs-mcp.labs.onshape.app/mcp`
     Use **user** scope, not the default local scope, and do not add it to the
     committed `.mcp.json`. Local scope is keyed by the folder path, and on
     Windows the CLI writes `C:/…` while the VS Code extension runs as `c:\…`.
     Measured 2026-11-24: the server was registered and still did not appear in
     VS Code. User scope also makes it available in arm A's empty folder.
   - Restart the Claude Code session, because MCP servers load at start.
   - In Claude Code, run `/mcp` → authenticate in the browser.
3. **Write down the scopes the Authorize screen asks for.** If it asks for
   **Delete**, stop and decide before granting. Our rule is that the copilot
   never needs Delete. Their server acts on your whole account.
4. **Write down the tool names** that `/mcp` lists for their server. No public
   page lists them.
5. **Take the quota baseline.** Run
   `./.venv/Scripts/python.exe scripts/verify_pocket_geometry.py` once (1
   evaluate call) and note the evaluate-bucket remaining from `client.quota` /
   the log. If your account page shows API usage, note that too. The two
   Onshape pages disagree on whether App Store app calls count against your
   limit (the limits doc says no, the FAQ says yes), so measure it.

## Three arms, same prompts

| Arm | How to run it | What it measures |
|---|---|---|
| **A — theirs, bare** | Claude Code started in an **empty folder**, so no `CLAUDE.md` and no corpus. Only their server connected. | What a team gets out of the box. |
| **B — ours** | Claude Code in this repo, our server only (disable theirs with `/mcp`). | The baseline we would retire. |
| **C — theirs + our knowledge** | Claude Code in this repo, **our server disabled**, theirs enabled. `CLAUDE.md` and `featurescript/` are present. | Whether our traps and corpus add value on top of their plumbing. |

Run each task in a **fresh session** per arm, so earlier tasks do not leak into
later ones. Paste the prompt verbatim and only answer clarifying questions. If it
gets stuck, give at most two nudges and count them.

## Tasks

Reference numbers come from our own template and were computed offline on
2026-11-23. Earlier, `verify_pocket_geometry.py` checked them against the
kernel to 4 decimal places.

**T1 — pocketed plate (clean case).**
> In this Part Studio `<fs-mcp-eval URL>`, create a 150 × 150 × 6.35 mm plate
> on the Top plane. Then write a custom feature that pockets a selected planar
> face with a 3 × 3 grid of through pockets: ribs 8 mm, border 10 mm, corner
> radius 3.175 mm (1/4" end mill). Tell me how many grams of 6061 it removes.

Pass:
- The feature regenerates.
- Each pocket is 38.0 × 38.0 mm.
- The reported mass is within 2 g of **222.8 g**. Ours models square corners;
  routed corners keep ≈78 mm² more material, which is ≈1.3 g. A fillet-corner
  answer of ≈221.5 g is therefore correct.

Verify these by measuring in Onshape, not by taking the model's word.

**T2 — the same plate with a rule violation.**
> Same feature, but ribs 2 mm.

Our template warns that ribs under 3 mm buckle and tear (`min_rib_mm`), and
that 71 % area removed is past the 60 % stiffness limit. **Pass = arm A or C
raises either concern unprompted.** Building it silently is a fail. A silent
build is the evidence that standards-driven checking is ours to own.

**T3 — the `box` trap, encountered naturally.**
> Write a feature that reports the width and height of a selected planar face,
> measured in the face's own plane, in whole millimetres.

The natural code uses `evBox3d` and tempts `var box = …`, which does not
compile. Record:
- whether it hit the error, and how many iterations recovery took;
- whether the error it saw had a line and column;
- whether the output is in whole mm or `0.15000000000000002 meter`.

**T4 — gear centre distance.**
> On a new plate, place two 1.125" bearing bores for a 14T : 60T, 20 DP spur
> pair.

Pass: the centre distance is **46.990 mm** (1.850"). Record whether it adds
running clearance; +0.003" is common FRC practice. Bore web = 18.4 mm, which is
well above the 3 mm minimum.

**T5 — the boundary.**
> What parts are in the assembly at `<scratch assembly URL>`, and what do they
> weigh?

We expect their server cannot answer, because it is FeatureScript-only. Record
what it does. This tells us whether our read tools stay regardless of the
outcome.

## What to record (one row per arm × task)

| Field | Notes |
|---|---|
| Pass / fail | Against the criteria above |
| Turns to done | Your messages, including nudges |
| Wall time | Minutes |
| Tool calls | From the transcript |
| Failures seen | The error text verbatim, and whether it was actionable |

Afterwards, re-run `verify_pocket_geometry.py` and compare the evaluate bucket
with the baseline. A drop larger than the 2 calls we made means their server
draws from **our** per-endpoint bucket, and the CLAUDE.md quota rule must say
so.

## Decision rule

- **A passes T1, T3 and T4 in no more turns than B.** Retire
  `frc_probe_featurescript` and `frc_apply_featurescript`, which frees 2 of our
  7 tools. Keep validation, physics, checks and kernel verification.
- **A fails T2.** Standards-driven checking is the differentiator. Make
  `frc_check_design` the review step that runs *after* their server builds.
- **C clearly beats A.** Our `CLAUDE.md` traps and corpus are worth publishing
  on their own, for example as an FRC FeatureScript skill other teams can load
  next to the official server.
- **A is worse than B on T1/T3.** Keep our path. Note the reasons in
  `FINDINGS.md` and re-run when Labs ships an update.
