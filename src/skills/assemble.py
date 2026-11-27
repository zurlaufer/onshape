"""Assemble a robot that was designed in robot coordinates, without mates.

WHY NO MATES. A workspace session on 2026-11-27 built a 2023 robot top-down:
one layout feature, each subsystem in its own Part Studio, every part already
placed in robot coordinates. Such parts need no mate to be in the right place.
`scripts/spike_assemble.py` measured it: a whole Part Studio inserted through
the API lands at identity, and the assembly's bounding box equals the Part
Studio's to 0.0 mm. A mate is the thing that can be silently wrong (STATUS.md,
"not worth doing"), so this builds an assembly with none, and holds the parts
together with one Group.

WHAT IT REFUSES, and why:

  * Part Studios from another document. Inserting across documents needs a
    released version, and a robot that silently pins to an old version is the
    failure we would then have to explain.
  * Adding parts when the ones already there were moved. New parts land at
    their designed position; moved old ones are not there any more, so the
    two would disagree with no error. The person moves them back first.
  * Grouping an instance that did not land at identity. The spike says this
    does not happen; if it ever does, locking it in a group would hide it.

Re-running is safe: an instance names its source Part Studio and part, so what
is already in the assembly is skipped, and the group is updated in place.

No `print()` here: this runs inside the MCP server, where stdout is protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

__all__ = [
    "GROUP_NAME", "DEFAULT_ASSEMBLY_NAME", "AssembleError", "StudioPlan",
    "assemble", "part_studio_tabs", "pick_studios", "is_identity",
]

#: Matched by name on a re-run, so the copilot updates its own group and
#: leaves any group a person made alone.
GROUP_NAME = "Robot (copilot)"
#: The tab created when `where` names a document rather than an assembly.
DEFAULT_ASSEMBLY_NAME = "Robot"

_IDENTITY = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
_ELEMENT_IN_URL = re.compile(r"/e/([0-9a-f]{24})", re.IGNORECASE)
_DOCUMENT_IN_URL = re.compile(r"/documents/([0-9a-f]{24})", re.IGNORECASE)


class AssembleError(ValueError):
    """A request this module will not act on; the message says what would work."""


def is_identity(transform: list[float], tol: float = 1e-9) -> bool:
    """Onshape's 4x4 occurrence transform, row-major, translation in metres."""
    return len(transform) == 16 and all(
        abs(a - b) <= tol for a, b in zip(transform, _IDENTITY))


def part_studio_tabs(elements: list[dict]) -> dict[str, str]:
    """Element id -> tab name, for the Part Studios of one document."""
    return {e["id"]: e.get("name", "") for e in elements
            if e.get("elementType") == "PARTSTUDIO"}


def pick_studios(requested: list[str], tabs: dict[str, str],
                 document_id: str) -> list[tuple[str, str]]:
    """Each request as (element id, tab name): a tab name, an id, or a URL.

    Tab names are what a person sees, so they come first. Raises on anything
    that is not a Part Studio of this document, naming the ones that are.
    """
    by_name = {name.strip().lower(): eid for eid, name in tabs.items()}
    picked: list[tuple[str, str]] = []
    unknown: list[str] = []
    for item in requested:
        text = item.strip()
        other_doc = _DOCUMENT_IN_URL.search(text)
        if other_doc and other_doc.group(1).lower() != document_id.lower():
            raise AssembleError(
                f"{item!r} is in another document. Only Part Studios of the "
                f"assembly's own document can be assembled here.")
        in_url = _ELEMENT_IN_URL.search(text)
        eid = (by_name.get(text.lower())
               or (text if text in tabs else None)
               or (in_url.group(1) if in_url and in_url.group(1) in tabs else None))
        if eid is None:
            unknown.append(item)
        elif eid not in (p[0] for p in picked):
            picked.append((eid, tabs[eid]))
    if unknown:
        raise AssembleError(
            f"Not a Part Studio of this document: {unknown}. "
            f"Part Studios here: {sorted(tabs.values())}.")
    if not picked:
        raise AssembleError(
            f"Name the Part Studios to assemble. Part Studios here: "
            f"{sorted(tabs.values())}.")
    return picked


@dataclass
class StudioPlan:
    """One Part Studio: its solid parts and which are already in the assembly."""

    element_id: str
    name: str
    parts: dict[str, str]                      # part id -> part name
    present: dict[str, dict] = field(default_factory=dict)  # part id -> instance

    @property
    def missing(self) -> list[str]:
        return [pid for pid in self.parts if pid not in self.present]

    @property
    def moved(self) -> list[str]:
        return [inst.get("name", pid) for pid, inst in self.present.items()
                if not inst.get("_at_identity", True)]


def _instances_by_source(definition: dict, document_id: str) -> dict[tuple[str, str], dict]:
    """Top-level part instances keyed by (source element, part id), each
    marked with whether its occurrence sits at identity."""
    root = definition.get("rootAssembly", {})
    placed = {o["path"][0]: is_identity(o.get("transform", []))
              for o in root.get("occurrences", []) if len(o.get("path", [])) == 1}
    out: dict[tuple[str, str], dict] = {}
    for inst in root.get("instances", []):
        if inst.get("type") != "Part" or inst.get("documentId") != document_id:
            continue
        out[(inst.get("elementId"), inst.get("partId"))] = {
            **inst, "_at_identity": placed.get(inst["id"], True)}
    return out


def _group_feature(instance_ids: list[str], feature_id: str | None) -> dict:
    """The one shape that regenerates OK, measured 2026-11-27.

    The parameter id is `occurrencesQuery` (from GET .../featurespecs). A
    top-level "occurrences" field is accepted, silently dropped, and the
    group regenerates as ERROR with no members.
    """
    feature: dict = {
        "btType": "BTMMateGroup-65",
        "featureType": "mateGroup",
        "name": GROUP_NAME,
        "parameters": [{
            "btType": "BTMParameterQueryWithOccurrenceList-67",
            "parameterId": "occurrencesQuery",
            "queries": [{"btType": "BTMIndividualOccurrenceQuery-626", "path": [i]}
                        for i in instance_ids],
        }],
    }
    if feature_id:
        feature["featureId"] = feature_id
    return {"feature": feature}


def _group_members(feature: dict) -> list[str]:
    ids: list[str] = []
    for param in feature.get("parameters", []):
        if param.get("parameterId") == "occurrencesQuery":
            for query in param.get("queries", []):
                path = query.get("path") or []
                if len(path) == 1:
                    ids.append(path[0])
    return ids


async def _read(client, base: str, assembly_id: str, document_id: str):
    definition = (await client.get(f"/assemblies/{base}/e/{assembly_id}")).json()
    return definition, _instances_by_source(definition, document_id)


async def assemble(client, *, document_id: str, workspace_id: str, assembly_id: str,
                   studios: list[tuple[str, str]], preview: bool = False) -> dict:
    """Insert what is missing from each Part Studio, then group all of it.

    Returns a JSON-ready dict. `ok` is False only when something is wrong in
    Onshape afterwards; a refusal raises AssembleError before any write.
    """
    base = f"d/{document_id}/w/{workspace_id}"
    _, existing = await _read(client, base, assembly_id, document_id)

    plans: list[StudioPlan] = []
    for element_id, name in studios:
        parts = (await client.get(f"/parts/{base}/e/{element_id}")).json()
        solids = {p["partId"]: p.get("name", p["partId"]) for p in parts
                  if p.get("bodyType") == "solid"}
        present = {pid: existing[(element_id, pid)] for pid in solids
                   if (element_id, pid) in existing}
        plans.append(StudioPlan(element_id, name, solids, present))

    moved = {p.name: p.moved for p in plans if p.moved}
    if moved and any(p.missing for p in plans):
        raise AssembleError(
            f"These parts were moved away from their designed position: {moved}. "
            f"New parts would land where the design says and disagree with them. "
            f"Move them back (or start a new assembly tab), then run this again.")

    out: dict = {
        "studios": {p.name: {"parts": len(p.parts), "already_there": len(p.present),
                             "to_insert": [p.parts[i] for i in p.missing]}
                    for p in plans},
    }
    empty = [p.name for p in plans if not p.parts]
    if empty:
        out["no_solid_parts"] = empty
    if preview:
        out.update(ok=True, stage="preview", changed=False)
        return out

    for plan in plans:
        missing = plan.missing
        if not missing:
            continue
        if not plan.present:
            # One request for the whole studio: the case the spike measured.
            await client.post(f"/assemblies/{base}/e/{assembly_id}/instances", json_body={
                "documentId": document_id, "elementId": plan.element_id,
                "isWholePartStudio": True, "includePartTypes": ["PARTS"]})
        else:
            for part_id in missing:
                await client.post(f"/assemblies/{base}/e/{assembly_id}/instances", json_body={
                    "documentId": document_id, "elementId": plan.element_id,
                    "partId": part_id})

    definition, after = await _read(client, base, assembly_id, document_id)
    ours = {(p.element_id, pid) for p in plans for pid in p.parts}
    members = [inst for key, inst in after.items() if key in ours]
    not_in_place = [inst.get("name") for inst in members if not inst["_at_identity"]
                    and (inst["elementId"], inst["partId"]) not in existing]
    out["inserted"] = sum(len(p.missing) for p in plans)
    if not_in_place:
        out["not_in_place"] = not_in_place

    # The group: every in-place part from these studios, plus whatever the
    # group already held, so naming fewer studios on a re-run drops nothing.
    features = (await client.get(f"/assemblies/{base}/e/{assembly_id}/features")).json()
    group = next((f for f in features.get("features", [])
                  if f.get("featureType") == "mateGroup" and f.get("name") == GROUP_NAME),
                 None)
    keep = set(_group_members(group)) if group else set()
    # Every top-level instance still there, sub-assemblies included: a placed
    # swerve module is a sub-assembly, and dropping it from the group on a
    # re-run would leave it free to be dragged off its corner.
    live = {inst["id"] for inst in definition.get("rootAssembly", {}).get("instances", [])}
    ids = sorted({inst["id"] for inst in members if inst["_at_identity"]}
                 | (keep & live))

    status = None
    if len(ids) >= 2:
        path = f"/assemblies/{base}/e/{assembly_id}/features"
        if group:
            path += f"/featureid/{quote(group['featureId'], safe='')}"
        reply = (await client.post(path, json_body=_group_feature(
            ids, group["featureId"] if group else None))).json()
        status = (reply.get("featureState") or {}).get("featureStatus")
    out["group"] = {"name": GROUP_NAME, "members": len(ids),
                    "status": status or ("not needed" if len(ids) < 2 else "unknown")}

    out["ok"] = not not_in_place and (len(ids) < 2 or status == "OK")
    out["stage"] = "assembled"
    return out
