"""Tests for src/skills/assemble.py, the mate-free assembler.

Offline, against a stub that models what scripts/spike_assemble.py measured
on 2026-11-27: one whole-Part-Studio insert makes one instance per part, each
at identity and naming its source; a group is a `mateGroup` feature.

The rules under test are the ones that keep a wrong assembly from looking
right: nothing is added beside parts that were moved, nothing off its designed
position is locked into the group, and a re-run neither duplicates parts nor
stacks groups.
"""

from __future__ import annotations

import asyncio

import pytest

from src.skills.assemble import (
    GROUP_NAME,
    AssembleError,
    assemble,
    is_identity,
    part_studio_tabs,
    pick_studios,
)

DOC = "d" * 24
IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
SHIFTED = [1, 0, 0, 0.1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
FRAME = "f" * 24
INTAKE = "a" * 24


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class _FakeOnshape:
    def __init__(self, parts: dict[str, list[tuple[str, str]]], *, lands=IDENTITY):
        self.parts = parts                 # element id -> [(part id, bodyType)]
        self.lands = lands                 # where an inserted part ends up
        self.instances: list[dict] = []
        self.transforms: dict[str, list[float]] = {}
        self.features: list[dict] = []
        self.posts: list[str] = []

    def _add(self, element_id, part_id):
        iid = f"I{len(self.instances) + 1}"
        self.instances.append({"id": iid, "type": "Part", "documentId": DOC,
                               "elementId": element_id, "partId": part_id,
                               "name": f"{element_id[:1]}-{part_id}"})
        self.transforms[iid] = list(self.lands)

    async def get(self, path, **_):
        if path.startswith("/parts/"):
            eid = path.rsplit("/", 1)[-1]
            return _Response([{"partId": pid, "name": pid, "bodyType": kind}
                              for pid, kind in self.parts[eid]])
        if path.endswith("/features"):
            return _Response({"features": self.features})
        return _Response({"rootAssembly": {
            "instances": self.instances,
            "occurrences": [{"path": [i], "transform": t}
                            for i, t in self.transforms.items()]}})

    async def post(self, path, *, json_body=None, **_):
        self.posts.append(path)
        if path.endswith("/instances"):
            eid = json_body["elementId"]
            if json_body.get("isWholePartStudio"):
                for pid, kind in self.parts[eid]:
                    if kind == "solid":
                        self._add(eid, pid)
            else:
                self._add(eid, json_body["partId"])
            return _Response({})
        feature = dict(json_body["feature"])
        if "/featureid/" in path:
            for f in self.features:
                if f["featureId"] == feature["featureId"]:
                    f.update(feature)
        else:
            feature["featureId"] = f"G{len(self.features) + 1}"
            self.features.append(feature)
        return _Response({"feature": feature, "featureState": {"featureStatus": "OK"}})


def run(client, studios, **kwargs):
    return asyncio.run(assemble(client, document_id=DOC, workspace_id="w" * 24,
                                assembly_id="e" * 24, studios=studios, **kwargs))


def group_members(client) -> list[str]:
    (group,) = [f for f in client.features if f["name"] == GROUP_NAME]
    return [q["path"][0] for q in group["parameters"][0]["queries"]]


BOTH = [(FRAME, "Drivetrain"), (INTAKE, "Intake")]


@pytest.fixture
def robot():
    return _FakeOnshape({FRAME: [("A", "solid"), ("B", "solid"), ("S", "sheet")],
                         INTAKE: [("C", "solid")]})


class TestFirstRun:
    def test_one_insert_per_studio_and_one_group(self, robot):
        out = run(robot, BOTH)
        assert out["ok"] and out["inserted"] == 3
        assert sum(p.endswith("/instances") for p in robot.posts) == 2
        assert sorted(group_members(robot)) == ["I1", "I2", "I3"]
        assert out["group"] == {"name": GROUP_NAME, "members": 3, "status": "OK"}

    def test_sheet_bodies_are_not_parts(self, robot):
        run(robot, BOTH)
        assert "S" not in {i["partId"] for i in robot.instances}

    def test_preview_writes_nothing(self, robot):
        out = run(robot, BOTH, preview=True)
        assert out["changed"] is False and robot.posts == []
        assert out["studios"]["Drivetrain"]["to_insert"] == ["A", "B"]


class TestRerun:
    def test_nothing_new_means_no_insert_and_the_same_group(self, robot):
        run(robot, BOTH)
        robot.posts.clear()
        out = run(robot, BOTH)
        assert out["inserted"] == 0
        assert not any(p.endswith("/instances") for p in robot.posts)
        assert len(robot.features) == 1, "a re-run must update, not stack groups"
        assert any("/featureid/G1" in p for p in robot.posts)

    def test_a_part_added_later_is_inserted_alone(self, robot):
        run(robot, BOTH)
        robot.parts[FRAME].append(("N", "solid"))
        robot.posts.clear()
        out = run(robot, BOTH)
        assert out["inserted"] == 1
        assert [p for p in robot.posts if p.endswith("/instances")] == \
            [robot.posts[0]]
        assert len(group_members(robot)) == 4

    def test_naming_fewer_studios_drops_nobody_from_the_group(self, robot):
        run(robot, BOTH)
        run(robot, [(FRAME, "Drivetrain")])
        assert len(group_members(robot)) == 3

    def test_a_grouped_sub_assembly_stays_in_the_group(self, robot):
        """A placed swerve module is a sub-assembly instance, not a part."""
        run(robot, BOTH)
        robot.instances.append({"id": "MOD", "type": "Assembly", "documentId": DOC,
                                "elementId": "m" * 24, "name": "MK4i <1>"})
        robot.transforms["MOD"] = SHIFTED
        robot.features[0]["parameters"][0]["queries"].append(
            {"btType": "BTMIndividualOccurrenceQuery-626", "path": ["MOD"]})
        run(robot, BOTH)
        assert "MOD" in group_members(robot)


class TestWrongPositions:
    def test_refuses_to_add_beside_moved_parts(self, robot):
        run(robot, BOTH)
        robot.transforms["I1"] = SHIFTED
        robot.parts[INTAKE].append(("D", "solid"))
        robot.posts.clear()
        with pytest.raises(AssembleError, match="moved"):
            run(robot, BOTH)
        assert robot.posts == [], "the refusal comes before any write"

    def test_a_part_that_lands_off_position_is_reported_not_grouped(self):
        stray = _FakeOnshape({FRAME: [("A", "solid"), ("B", "solid")],
                              INTAKE: [("C", "solid")]}, lands=SHIFTED)
        out = run(stray, BOTH)
        assert out["ok"] is False
        assert len(out["not_in_place"]) == 3
        assert out["group"]["members"] == 0


class TestPicking:
    TABS = {FRAME: "Drivetrain", INTAKE: "Intake"}

    def test_tab_name_id_and_url(self):
        url = f"https://cad.onshape.com/documents/{DOC}/w/{'w' * 24}/e/{INTAKE}"
        assert pick_studios(["drivetrain", url], self.TABS, DOC) == BOTH
        assert pick_studios([FRAME], self.TABS, DOC) == [(FRAME, "Drivetrain")]

    def test_unknown_names_what_exists(self):
        with pytest.raises(AssembleError, match="Drivetrain"):
            pick_studios(["Shooter"], self.TABS, DOC)

    def test_another_document_is_refused(self):
        url = f"https://cad.onshape.com/documents/{'9' * 24}/w/{'w' * 24}/e/{FRAME}"
        with pytest.raises(AssembleError, match="another document"):
            pick_studios([url], self.TABS, DOC)

    def test_only_part_studios_are_tabs(self):
        elements = [{"id": FRAME, "name": "Drivetrain", "elementType": "PARTSTUDIO"},
                    {"id": "b" * 24, "name": "Robot", "elementType": "ASSEMBLY"}]
        assert part_studio_tabs(elements) == {FRAME: "Drivetrain"}


def test_identity_tolerates_float_noise_only():
    assert is_identity([v + 1e-12 for v in IDENTITY])
    assert not is_identity(SHIFTED)
    assert not is_identity([])
