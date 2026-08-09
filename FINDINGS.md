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
