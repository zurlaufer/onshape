"""Can a Tube Converter *query* parameter be set through the API?

The answer, measured on 2026-08-11 against the scratch Part Studio: **yes,
through `deterministicIds`**. A control experiment settled the mechanism:

    A queryString-only (qCreatedBy(...))          -> stored as NOTHING, unresolved
    B deterministicIds=["<partId>"] only          -> resolved, canonicalized to
                                                     qCompressed naming the body
    C queryString + deterministicIds              -> resolved
    D deterministicIds=["ZZZZ"] (bad id)          -> silently dropped, NOTHING

So the query resolves through the ids of the target entities, which
`GET /partstudios/.../bodydetails` exposes (bodies[].id, faces[].id,
edges[].id). The queryString the UI writes is not the resolution key, and
an unresolvable id is silently dropped with no error — another instance of
"the write path reports no reason".

The flat plate in the scratch Part Studio still REGEN_ERRORs, and that is
the *expected* tube-profile rejection, not a query failure — the community
docs say the feature shells a tube-profile extrusion, and a
150 x 150 x 6.35 mm plate is not a tube. The clean run needs a canonical
1x1 block: extrude 1 x 1 x 12 in in the scratch Part Studio, then let this
spike convert it (it picks the tube-like body — longest axis >= 3x the
middle, short axes within 1.5x of each other — so the plate is skipped).

Costs about 8 requests plus one evaluate call when the status is not OK.
The inserted feature is removed again.

    ./.venv/Scripts/python.exe scripts/spike_tube_converter_query.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient  # noqa: E402
from src.api.write_policy import WritePolicy  # noqa: E402

# Scratch only. Never a team's robot.
DID = "e424515893d4f1ab8b23e5c4"
WID = "65294e9343b8524f94ad5093"
PART_STUDIO = "4fa46792842ff31b21179dad"

#: A public community library. Tube Converter and Gusset Generator share it.
LIB_DOCUMENT = "95c00401c440b44ad8799ef5"
LIB_ELEMENT = "7f62400703032fab9a7a3320"

FEATURE_NAME = "SPIKE query param"

#: Known-good values, copied from the community's "Block to tube" test part
#: studio (versioned read, 2026-08-11).
SCALARS = {
    "wall": ("0.125 in", None),
    "myHoleSize": ("0.196 in", None),
    "holeSpacing": ("0.5 in", None),
    "rowDist": ("0.5*in", None),
    "holeMargin": ("0.0*in", None),
    "patternOfs": ("0 in", None),
    "marginOffset": ("0 in", None),
    "do_holes": (True, None),
    "mike": (False, None),
    "is_custom_name": (False, None),
    "is_metric_name": (False, None),
    "OneInStyle": ("ONEROW", "OneInStyle"),
    "TwoInStyle": ("HIGHTIDE", "TwoInStyle"),
    "patternBase": ("CENTERED", "PatternBase"),
    "maxStyle": ("NORMAL", "MAXStyle"),
}


async def list_features(client, label: str) -> list[dict]:
    data = (await client.get(
        f"/partstudios/d/{DID}/w/{WID}/e/{PART_STUDIO}/features")).json()
    states = data.get("featureStates", {})
    rows = data.get("features", [])
    print(f"  [{label}] {len(rows)} feature(s)")
    for f in rows:
        status = states.get(f.get("featureId"), {}).get("featureStatus")
        print(f"      {f.get('name'):<26} {status}")
    return rows


def _bbox_span(b: dict) -> tuple[list[float], list[float]]:
    """Bounding-box min/max from a bodydetails body's vertices, in mm.

    The API reports points in meters; this converts to mm for display.
    """
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for v in b.get("vertices") or []:
        p = v.get("point") or {}
        for i, key in enumerate(("x", "y", "z")):
            val = p.get(key, 0.0) * 1000.0
            lo[i] = min(lo[i], val)
            hi[i] = max(hi[i], val)
    return lo, hi


async def find_tube_like_body(client) -> tuple[str, list[float], list[float]]:
    """Pick the body that looks like a tube: one long axis, two short axes.

    The scratch Part Studio holds a 150 x 150 x 6.35 mm plate; its two long
    axes are equal, so it is not tube-like and is skipped. Returns
    (body_id, lo_mm, hi_mm).
    """
    data = (await client.get(
        f"/partstudios/d/{DID}/w/{WID}/e/{PART_STUDIO}/bodydetails")).json()
    bodies = data.get("bodies", []) if isinstance(data, dict) else data
    print("  bodies in the Part Studio:")
    candidates = []
    for b in bodies:
        lo, hi = _bbox_span(b)
        dims = sorted(hi[i] - lo[i] for i in range(3))
        tube_like = dims[2] >= 3.0 * dims[1] and dims[1] <= 1.5 * dims[0]
        print(f"      id={b.get('id'):<6} {dims[0]:.1f} x {dims[1]:.1f} x "
              f"{dims[2]:.1f} mm  {'TUBE' if tube_like else ''}")
        if tube_like:
            candidates.append(b.get("id"))
    if not candidates:
        raise SystemExit("no tube-like body found - draw the 1x1 block "
                         "first (e.g. 25.4 x 25.4 x 304.8 mm)")
    return candidates[-1], lo, hi


async def main() -> int:
    policy = WritePolicy.from_manifest()
    if not policy.allows(DID):
        raise policy.refuse(DID)

    async with OnshapeApiClient.from_env() as client:
        versions = (await client.get(
            f"/documents/d/{LIB_DOCUMENT}/versions")).json()
        version = versions[-1]
        spec = (await client.get(
            f"/featurestudios/d/{LIB_DOCUMENT}/v/{version['id']}"
            f"/e/{LIB_ELEMENT}/featurespecs")).json()["featureSpecs"][0]
        namespace = f"d{LIB_DOCUMENT}::v{version['id']}::{spec['namespace']}"
        print(f"library version: {version.get('name')}")
        print(f"namespace      : {namespace}")

        body_id, lo, hi = await find_tube_like_body(client)
        print(f"target body    : {body_id}  ({hi[0]-lo[0]:.0f} x "
              f"{hi[1]-lo[1]:.0f} x {hi[2]-lo[2]:.0f} mm)")
        print(f"body query     : deterministicIds=[{body_id}]")
        print()

        await list_features(client, "before")

        parameters = [{
            "btType": "BTMParameterQueryList-148",
            "queries": [{
                "btType": "BTMIndividualQuery-138",
                "queryStatement": None,
                "queryString": "",
                "deterministicIds": [body_id],
            }],
            "filter": {"btType": "BTEntityTypeFilter-124",
                       "entityType": "BODY"},
            "nodeId": "SPIKEbody",
            "parameterId": "body",
            "parameterName": "",
        }]
        for pid, (value, enum_name) in SCALARS.items():
            if enum_name is None:
                if isinstance(value, bool):
                    parameters.append({"btType": "BTMParameterBoolean-144",
                                       "value": value,
                                       "nodeId": f"SPIKE{pid}",
                                       "parameterId": pid,
                                       "parameterName": ""})
                else:
                    parameters.append({
                        "btType": "BTMParameterQuantity-147",
                        "value": 0.0, "units": "",
                        "expression": value, "isInteger": False,
                        "nodeId": f"SPIKE{pid}", "parameterId": pid,
                        "parameterName": ""})
            else:
                parameters.append({
                    "btType": "BTMParameterEnum-145",
                    "namespace": namespace, "enumName": enum_name,
                    "value": value, "nodeId": f"SPIKE{pid}",
                    "parameterId": pid, "parameterName": ""})

        print("inserting with the body query set...")
        created = await client.post(
            f"/partstudios/d/{DID}/w/{WID}/e/{PART_STUDIO}/features",
            json_body={"feature": {
                "btType": "BTMFeature-134",
                "featureType": spec["featureType"],
                "name": FEATURE_NAME,
                "namespace": namespace,
                "parameters": parameters,
            }},
        )
        payload = created.json()
        feature_id = payload.get("feature", {}).get("featureId")
        status = payload.get("featureState", {}).get("featureStatus")
        print(f"  inserted - featureId {feature_id}, status {status}")
        print()

        # The resolution signal: a query that resolves comes back with its
        # deterministicIds echoed and the query canonicalized. The NOTHING
        # form also contains "qCompressed", so the echoed deterministicIds
        # are the signal, not the compressed string.
        stored_body = None
        for p in payload.get("feature", {}).get("parameters") or []:
            if p.get("parameterId") == "body":
                stored_body = p
        stored_q = (stored_body or {}).get("queries", [{}])[0]
        echoed_ids = stored_q.get("deterministicIds") or []
        resolved = body_id in echoed_ids
        qstring = stored_q.get("queryString", "")
        print(f"  stored query  : "
              f"{'CANONICALIZED (query resolved)' if resolved else qstring[:80]}")
        print(f"  deterministicIds: {echoed_ids}")
        print()

        result = 0
        if status == "OK":
            print("PASSED: the query resolved AND Tube Converter built a "
                  "converted tube from the target body.")
        elif resolved:
            print("Query resolved (canonicalized to qCompressed naming the "
                  "target body).")
            print("Feature status is", status, "- this is a *geometry* "
                  "rejection by the feature, not a query failure.")
            print("A canonical 1x1 tube-profile block is the clean input; "
                  "the flat plate is not one.")
        else:
            print("FAILED: the stored query was not canonicalized, so the "
                  "API-set query did not resolve.")
            result = 1

        if status != "OK":
            script = (
                "function(context is Context, queries) {\n"
                f"    return getFeatureStatus(context, [\"{feature_id}\"] "
                f"as Id);\n"
                "}"
            )
            fs = (await client.post(
                f"/partstudios/d/{DID}/w/{WID}/e/{PART_STUDIO}/featurescript",
                json_body={"script": script})).json()
            for n in fs.get("notices") or []:
                if n.get("level") in ("ERROR", "WARNING"):
                    print(f"  [{n.get('level')}] {n.get('message')}")
            fsv = fs.get("result") or {}
            print("  getFeatureStatus:",
                  json.dumps({(e.get("key") or {}).get("value"):
                              (e.get("value") or {}).get("value")
                              for e in fsv.get("value") or []}))
            print()

        print("removing the spike feature...")
        await client.request(
            "DELETE",
            f"/partstudios/d/{DID}/w/{WID}/e/{PART_STUDIO}"
            f"/features/featureid/{feature_id}")
        remaining = await list_features(client, "restored")
        if any(f.get("name") == FEATURE_NAME for f in remaining):
            print("\n  WARNING: cleanup failed. Remove it by hand.")
            return 1
        return result


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
