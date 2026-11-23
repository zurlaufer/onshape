"""Can a Tube Converter *parameter* be set through the API?

spike_community_featurescript.py proved a foreign namespace resolves — the
feature inserts with no parameters and the error status is the *expected*
one (nothing selected). What it did not prove, and what item 3 in STATUS.md
is really about, is whether a **query** parameter can be driven remotely.
Tube Converter takes a sketch as its tube; if the query parameter needs a
human picking geometry in the UI, the capability is "scalars only" and we
record that and do not build a tool.

This spike only READS: it fetches the feature spec through the published
version and prints each parameter's name, requirement and the value shape
the API expects, so the insert spike can be written against facts instead
of the 30 KB spec. No writes, no evaluate budget.

    ./.venv/Scripts/python.exe scripts/spike_tube_converter_params.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.onshape_client import OnshapeApiClient  # noqa: E402

#: A public community library. Tube Converter and Gusset Generator share it.
LIB_DOCUMENT = "95c00401c440b44ad8799ef5"
LIB_ELEMENT = "7f62400703032fab9a7a3320"


def describe(p: dict) -> str:
    """The value shape of a parameter, read from the BTParameterSpec."""
    bt = p.get("btType")
    if bt == "BTParameterSpecQuery-174":
        f = p.get("filter") or {}
        if f.get("btType") == "BTEntityTypeFilter-124":
            return f"QUERY {f.get('entityType')}"
        return f"QUERY {f.get('btType', '?')}"
    return bt.removeprefix("BTParameterSpec") if bt else "?"


async def main() -> int:
    async with OnshapeApiClient.from_env() as client:
        versions = (await client.get(
            f"/documents/d/{LIB_DOCUMENT}/versions")).json()
        if not versions:
            print("FAILED: the library publishes no versions.")
            return 1
        version = versions[-1]

        spec = (await client.get(
            f"/featurestudios/d/{LIB_DOCUMENT}/v/{version['id']}"
            f"/e/{LIB_ELEMENT}/featurespecs")).json()["featureSpecs"][0]

        print(f"library version: {version.get('name')} ({version['id']})")
        print(f"feature        : {spec['featureTypeName']} "
              f"({spec['featureType']})")
        print(f"namespace      : {spec['namespace']}")
        print()
        print(f"{'parameter':<22} {'name':<30} shape")
        print("-" * 70)
        for p in spec.get("parameters") or []:
            print(f"{p.get('parameterId', '?'):<22} "
                  f"{p.get('parameterName', ''):<30} {describe(p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
