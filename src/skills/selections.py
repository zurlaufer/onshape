"""Choosing a face for a feature's selection, and handing it to Onshape.

WHY THIS EXISTS. A feature with a required `Query` parameter ("the face to
pocket") regenerates as ERROR when the selection is empty, even if its body
does nothing (measured 2026-11-25). `frc_apply_featurescript` used to insert
features with no parameters at all, so every pick-a-face feature was rolled
back, and the first session from the team workspace lost its pocket grid to it.

HOW A SELECTION IS SET. Through `deterministicIds`, which Onshape resolves and
canonicalises. The `queryString` the UI writes is not the resolution key. An
unknown id is silently DROPPED, with no error; see
`scripts/spike_tube_converter_query.py`, measured 2026-08-11. So every id here
is chosen from the Part Studio's own `bodydetails`, never invented.

WHICH FACE. A model cannot see the screen, so faces are named by geometry:

    "top"             planar face square to Z, highest   (the usual plate face)
    "bottom"          planar face square to Z, lowest
    "largest_planar"  biggest planar face; ties go to the highest
    "<face id>"       an id from bodydetails, passed through if it exists

Only solid bodies are considered. A pocket in a surface removes nothing
(learned the hard way; see plate_pocket_pattern). Ties are broken by height,
then by id, so the same Part Studio always gives the same answer.

`bodydetails` reports a face's PLANE normal, not its outward normal. The top
and bottom of a plate both read +Z, so up and down are told apart by where
the face is, not by which way the normal points.

Pure: no I/O. Units from Onshape are metres; descriptions are in mm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "SELECTORS",
    "ChosenFace",
    "SelectionError",
    "declared_query_parameters",
    "resolve_face",
    "query_parameter",
]

SELECTORS = ("top", "bottom", "largest_planar")

#: A plane counts as "square to Z" when its normal is within this of +-Z.
_AXIS_TOLERANCE = 1e-6

_QUERY_DECLARATION = re.compile(r"definition\.(\w+)\s+is\s+Query\b")


class SelectionError(ValueError):
    """A selection that cannot be resolved. The message says what exists."""


@dataclass(frozen=True)
class ChosenFace:
    face_id: str
    body_id: str
    area_mm2: float
    z_mm: float

    def describe(self) -> str:
        return (f"face {self.face_id} on body {self.body_id}: "
                f"{self.area_mm2:,.0f} mm2 at z = {self.z_mm:.2f} mm")


def declared_query_parameters(module: str) -> set[str]:
    """Parameter ids the module declares as `definition.<id> is Query`."""
    return set(_QUERY_DECLARATION.findall(module))


def _planar_faces(bodydetails: dict[str, Any]) -> list[tuple[dict, str]]:
    bodies = bodydetails.get("bodies", []) if isinstance(bodydetails, dict) else []
    out = []
    for body in bodies:
        if str(body.get("type", "")).upper() != "SOLID":
            continue
        for face in body.get("faces") or []:
            if (face.get("surface") or {}).get("type") == "PLANE":
                out.append((face, str(body.get("id"))))
    return out


def _height(face: dict) -> float:
    box = face.get("box") or {}
    return float((box.get("maxCorner") or {}).get("z", 0.0))


def _square_to_z(face: dict) -> bool:
    normal = (face.get("surface") or {}).get("normal") or {}
    return abs(abs(float(normal.get("z", 0.0))) - 1.0) < _AXIS_TOLERANCE


def _chosen(face: dict, body_id: str) -> ChosenFace:
    return ChosenFace(face_id=str(face["id"]), body_id=body_id,
                      area_mm2=float(face.get("area", 0.0)) * 1e6,
                      z_mm=_height(face) * 1000.0)


def resolve_face(bodydetails: dict[str, Any], selector: str) -> ChosenFace:
    """The one face a selector names, or a SelectionError saying why not."""
    faces = _planar_faces(bodydetails)
    if not faces:
        raise SelectionError(
            "The Part Studio has no planar face on a solid body, so there is "
            "nothing to select. Create the solid first.")

    if selector not in SELECTORS:
        for face, body_id in faces:
            if face.get("id") == selector:
                return _chosen(face, body_id)
        raise SelectionError(
            f"No planar face with id {selector!r}. Use one of {list(SELECTORS)}, "
            f"or an id from: {sorted(str(f['id']) for f, _ in faces)}.")

    if selector in ("top", "bottom"):
        candidates = [(f, b) for f, b in faces if _square_to_z(f)]
        if not candidates:
            raise SelectionError(
                f"No planar face is square to Z, so {selector!r} is ambiguous. "
                f"Use 'largest_planar' or a face id.")
        sign = 1.0 if selector == "top" else -1.0
        face, body_id = max(candidates, key=lambda fb: (sign * _height(fb[0]),
                                                        str(fb[0]["id"])))
        return _chosen(face, body_id)

    face, body_id = max(faces, key=lambda fb: (round(float(fb[0].get("area", 0.0)), 12),
                                               _height(fb[0]), str(fb[0]["id"])))
    return _chosen(face, body_id)


def query_parameter(parameter_id: str, face_ids: list[str]) -> dict[str, Any]:
    """The feature parameter that sets a face selection by deterministic id.

    Same shape as the spike that proved it, with a FACE filter in place of
    BODY.
    """
    return {
        "btType": "BTMParameterQueryList-148",
        "queries": [{
            "btType": "BTMIndividualQuery-138",
            "queryStatement": None,
            "queryString": "",
            "deterministicIds": list(face_ids),
        }],
        "filter": {"btType": "BTEntityTypeFilter-124", "entityType": "FACE"},
        "parameterId": parameter_id,
        "parameterName": "",
    }
