"""Tests for `persist_feature` — leaving a working feature behind.

Offline, against a stub that models the two things Onshape actually does here:
a feature has a regeneration status, and rewriting a Feature Studio's source
moves the microversion any inserted feature is pinned to.

Every rule below was learned by breaking a real document. A dead `Boom 1` sat in
a scratch Part Studio because the source was restored out from under it.
"""

from __future__ import annotations

import pytest

from src.skills.apply import PREVIEW_STUDIO_NAME, persist_feature, studio_name_for


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class _FakeOnshape:
    """A Part Studio, a Feature Studio, and the microversion linking them."""

    def __init__(self, *, spec_count=1, regen_status="OK", existing=None,
                 delete_fails=False):
        self.spec_count = spec_count
        self.regen_status = regen_status
        self.delete_fails = delete_fails
        self.contents = "FeatureScript 3029;\n// original\n"
        self.microversion = 1
        self.features: list[dict] = list(existing or [])
        self.deleted: list[str] = []

    # -- reads -----------------------------------------------------------

    async def get(self, path, **_):
        if path.endswith("/featurespecs"):
            specs = [{"featureType": "myFeature",
                      "namespace": f"eSTUDIO::m{self.microversion}"}] * self.spec_count
            return _Response({"featureSpecs": specs})
        if path.endswith("/features"):
            return _Response({
                "features": self.features,
                "featureStates": {
                    f["featureId"]: {"featureStatus": self.regen_status}
                    for f in self.features
                },
            })
        return _Response({"contents": self.contents,
                          "serializationVersion": "1.2.21",
                          "sourceMicroversion": f"m{self.microversion}"})

    # -- writes ----------------------------------------------------------

    async def post(self, path, *, json_body=None, **_):
        body = json_body or {}
        if "/features/featureid/" in path:
            fid = path.rsplit("/", 1)[-1]
            for feature in self.features:
                if feature["featureId"] == fid:
                    feature.update(body["feature"])
            return _Response({"feature": body["feature"]})
        if path.endswith("/features"):
            feature = dict(body["feature"])
            feature["featureId"] = f"F{len(self.features) + 1}"
            self.features.append(feature)
            return _Response({"feature": feature})
        # Writing the Feature Studio MOVES the microversion. That is the whole
        # reason an already-inserted feature has to be repointed afterwards.
        self.contents = body.get("contents", self.contents)
        self.microversion += 1
        return _Response({"contents": self.contents})

    async def request(self, method, path, **_):
        if method == "DELETE":
            if self.delete_fails:
                raise RuntimeError("Onshape said no")
            fid = path.rsplit("/", 1)[-1]
            self.deleted.append(fid)
            self.features = [f for f in self.features if f["featureId"] != fid]
        return _Response({})


async def persist(client, **kwargs):
    return await persist_feature(
        client, "FeatureScript 3029;\n// new\n",
        document_id="SCRATCH", workspace_id="W", feature_studio_id="FS",
        part_studio_id="PS", feature_name="Climber Plate", **kwargs)


class TestTheSourceSurvives:
    """The `Boom 1` lesson, as an assertion."""

    async def test_the_feature_studio_is_not_restored(self):
        client = _FakeOnshape()
        original = client.contents
        result = await persist(client)
        assert result.ok
        assert client.contents != original, (
            "restoring strands the inserted feature at a microversion that no "
            "longer defines it")
        assert "// new" in client.contents

    async def test_the_feature_points_at_the_source_just_written(self):
        client = _FakeOnshape()
        result = await persist(client)
        assert result.namespace == f"eSTUDIO::m{client.microversion}"
        assert client.features[0]["namespace"] == result.namespace


class TestIdempotence:
    async def test_a_second_run_updates_rather_than_duplicating(self):
        client = _FakeOnshape()
        first = await persist(client)
        second = await persist(client)
        assert len(client.features) == 1, "running twice must not stack features"
        assert first.updated is False and second.updated is True
        assert second.feature_id == first.feature_id

    async def test_the_update_repoints_the_namespace(self):
        """Rewriting the source moves the microversion; skipping this step is
        exactly how a feature gets stranded."""
        client = _FakeOnshape()
        await persist(client)
        stale = client.features[0]["namespace"]
        await persist(client)
        assert client.features[0]["namespace"] != stale


class TestOneStudioPerFeature:
    """The workspace trial, 2026-11-25: a second feature, written into the same
    studio, erased the first one's source and its plate vanished."""

    def test_each_feature_gets_its_own_studio(self):
        plate, pockets = studio_name_for("Plate", "PS1"), studio_name_for("Pocket Grid", "PS1")
        assert plate != pockets
        assert studio_name_for("Plate", "PS1") == plate, "re-applying must find it again"

    def test_the_same_name_in_another_part_studio_gets_another_studio(self):
        """2026-11-25: "Pocket Grid 3x3" applied in one tab silently replaced
        the module behind the same-named feature in another tab, which then
        reported OK while cutting nothing."""
        assert studio_name_for("Pocket Grid 3x3", "1bc8d6ddce99f930") !=             studio_name_for("Pocket Grid 3x3", "0f73aed1fd2d0035")

    def test_previews_never_share_a_studio_with_a_feature(self):
        """A preview restores its studio, and restoring strands features."""
        assert PREVIEW_STUDIO_NAME not in {studio_name_for(n, "PS1") for n in
                                           ("Plate", "Pocket Grid", "Preview plate")}

    def test_the_preview_name_is_reserved(self):
        with pytest.raises(ValueError):
            studio_name_for("preview", "PS1")

    async def test_a_same_type_feature_from_another_studio_is_not_ours(self):
        """Two studios can both define `myFeature`. Updating the other one would
        repoint it at code it was never written from."""
        foreign = {"featureId": "F9", "featureType": "myFeature",
                   "namespace": "eOTHER::m7", "name": "Someone else's"}
        client = _FakeOnshape(existing=[foreign])
        result = await persist(client)
        assert result.ok and result.updated is False
        assert foreign["namespace"] == "eOTHER::m7", "the foreign feature was touched"
        assert len(client.features) == 2


class TestSelectionsOnUpdate:
    """Re-resolving "top" on update picked the face the feature itself had cut."""

    @staticmethod
    def _query(pid, ids):
        return {"btType": "BTMParameterQueryList-148", "parameterId": pid,
                "queries": [{"btType": "BTMIndividualQuery-138",
                             "deterministicIds": ids}]}

    async def test_a_named_selection_keeps_the_original_face(self):
        client = _FakeOnshape()
        await persist(client, parameters=[self._query("face", ["TOP_BEFORE"])])
        result = await persist(client, parameters=[self._query("face", ["TOP_AFTER"])],
                               keep_existing_queries={"face"})
        assert result.updated and result.kept_selections == ["face"]
        ids = client.features[0]["parameters"][0]["queries"][0]["deterministicIds"]
        assert ids == ["TOP_BEFORE"]

    async def test_an_explicit_face_replaces_it(self):
        client = _FakeOnshape()
        await persist(client, parameters=[self._query("face", ["TOP_BEFORE"])])
        result = await persist(client, parameters=[self._query("face", ["SIDE"])])
        assert result.kept_selections == []
        ids = client.features[0]["parameters"][0]["queries"][0]["deterministicIds"]
        assert ids == ["SIDE"]


class TestRollback:
    async def test_a_failed_insert_is_removed_again(self):
        """We created it. Leaving something broken behind is the failure this
        function exists to prevent."""
        client = _FakeOnshape(regen_status="ERROR")
        result = await persist(client)
        assert result.ok is False
        assert result.rolled_back is True
        assert client.features == [], "nothing broken may be left in the tree"

    async def test_a_failed_update_is_left_alone(self):
        """Deliberate asymmetry: it is somebody else's feature. Deleting a
        student's work to tidy up an error is worse than the error."""
        client = _FakeOnshape(regen_status="ERROR", existing=[
            {"featureId": "F1", "featureType": "myFeature",
             "namespace": "eSTUDIO::m0", "name": "Climber Plate"}])
        result = await persist(client)
        assert result.ok is False
        assert result.rolled_back is False
        assert client.deleted == []
        assert any("left in place" in p.message for p in result.problems)

    async def test_a_failed_rollback_says_so_loudly(self):
        client = _FakeOnshape(regen_status="ERROR", delete_fails=True)
        result = await persist(client)
        assert result.ok is False and result.rolled_back is False
        assert any("could not be" in p.message for p in result.problems)
        assert any("Delete it in Onshape" in (p.suggestion or "")
                   for p in result.problems)


class TestCompileFailure:
    async def test_nothing_is_inserted_when_the_module_does_not_compile(self):
        client = _FakeOnshape(spec_count=0)
        result = await persist(client)
        assert result.ok is False
        assert client.features == []
        assert any("did not compile" in p.message for p in result.problems)

    async def test_the_failure_points_at_the_probe(self):
        result = await persist(_FakeOnshape(spec_count=0))
        assert any("probe" in (p.suggestion or "") for p in result.problems)


class TestResultShape:
    async def test_success_reports_what_it_did(self):
        result = await persist(_FakeOnshape())
        payload = result.to_dict()
        assert payload["ok"] is True
        assert payload["feature_id"] and payload["feature_type"] == "myFeature"
        assert "rolled_back" not in payload, "quiet when there is nothing to say"

    async def test_rollback_is_visible_in_the_result(self):
        result = await persist(_FakeOnshape(regen_status="ERROR"))
        assert result.to_dict()["rolled_back"] is True
