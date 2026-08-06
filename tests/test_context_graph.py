"""Tests for the assembly context layer.

Offline: no network, no credentials. `graph.py` is pure by design, so this is
where the real coverage sits.

The cases that matter are the ones guarding measured facts — definition dedup
and the response ceiling. Those are what stop the 1.09 MB problem returning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.context.cache import AssemblyCache
from src.context.graph import MAX_DEPTH, AssemblyGraph, PathNotFound
from src.context.queries import (
    MAX_RESPONSE_BYTES,
    assembly_overview,
    find_parts,
    fit_response,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mini_assembly.json"

ROBOT = "Robot <1>"
DRIVETRAIN = f"{ROBOT}/1678-2026-E-0700 Drivetrain <1>"
SWERVE = f"{DRIVETRAIN}/Swerve Module <1>"
KRAKEN = f"{SWERVE}/Motor Assembly <1>/Kraken X60 <1>"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def graph(raw) -> AssemblyGraph:
    return AssemblyGraph.from_definition(raw)


class TestTreeConstruction:
    def test_root_has_one_child(self, graph):
        assert [c.name for c in graph.root.children] == [ROBOT]

    def test_subassemblies_joined_by_document_and_element(self, graph):
        """The join is (documentId, elementId, configuration), not by name."""
        drivetrain = graph.resolve(DRIVETRAIN)
        assert drivetrain.child_count == 5
        assert "Swerve Module <1>" in [c.name for c in drivetrain.children]

    def test_nesting_reaches_five_levels(self, graph):
        kraken = graph.resolve(KRAKEN)
        assert kraken.type == "Part"
        assert len(kraken.path.split("/")) == 5

    def test_parts_have_no_names_in_source_data(self, raw):
        """Guards the fact that forced this whole design.

        Onshape's parts[] entries carry no name field, so nothing may try to
        read names from there. If Onshape ever adds one this test fails, which
        is the correct prompt to revisit graph.py.
        """
        assert raw["parts"], "fixture should contain parts"
        for part in raw["parts"]:
            assert "name" not in part


class TestDefinitionDedup:
    """19 unique definitions behind 1,320 occurrences on the real robot."""

    def test_four_instances_share_one_definition(self, graph):
        swerves = [
            c
            for c in graph.resolve(DRIVETRAIN).children
            if c.name.startswith("Swerve Module")
        ]
        assert len(swerves) == 4
        assert len({s.definition for s in swerves}) == 1

    def test_repeats_collapse_to_one_entry_with_a_count(self, graph):
        children = graph.subtree(DRIVETRAIN, depth=1)["children"]
        swerve = [c for c in children if c["name"].startswith("Swerve Module")]
        assert len(swerve) == 1, "four identical modules must render as one entry"
        assert swerve[0]["count"] == 4

    def test_unique_components_below_total_instances(self, graph):
        s = graph.summary()
        assert s["unique_components"] < s["total_instances"]

    def test_singletons_carry_no_count_key(self, graph):
        """Only repeats get a count, so the common case stays terse."""
        children = graph.subtree(DRIVETRAIN, depth=1)["children"]
        belly = next(c for c in children if c["name"] == "Belly Pan <1>")
        assert "count" not in belly

    def test_different_parts_in_one_part_studio_stay_distinct(self, graph):
        """REGRESSION: partId belongs in the definition key.

        Bracket and Standoff live in the same Part Studio (D11/E11) and differ
        only by partId. Keying on (documentId, elementId) alone merged them.
        On the real Citrus robot that bug collapsed 483 distinct parts into 105
        groups — one group merged 90 unrelated items (screws, shims, tread,
        shafts) and reported a single shaft as being used 360 times.
        """
        intake = graph.subtree(f"{ROBOT}/Intake <1>", depth=1)["children"]
        names = [c["name"] for c in intake]
        assert "Bracket <1>" in names
        assert "Standoff <1>" in names, "distinct partIds must not be merged"

        bracket = next(c for c in intake if c["name"] == "Bracket <1>")
        standoff = next(c for c in intake if c["name"] == "Standoff <1>")
        assert bracket["count"] == 2, "two real Brackets share a partId"
        assert "count" not in standoff, "the Standoff is a single distinct part"

    def test_instance_count_is_not_inflated_by_studio_siblings(self, graph):
        """A part's reuse count must reflect that part, not its Part Studio."""
        assert graph.search("standoff")[0].instance_count == 1
        assert graph.search("bracket")[0].instance_count == 2


class TestNavigation:
    def test_depth_one_returns_only_direct_children(self, graph):
        children = graph.subtree(ROBOT, depth=1)["children"]
        assert all("children" not in c for c in children)

    def test_depth_two_returns_grandchildren(self, graph):
        children = graph.subtree(ROBOT, depth=2)["children"]
        assert any("children" in c for c in children)

    def test_depth_is_clamped(self, graph):
        assert graph.subtree(ROBOT, depth=99) == graph.subtree(ROBOT, depth=MAX_DEPTH)

    def test_depth_zero_returns_no_children(self, graph):
        assert graph.subtree(ROBOT, depth=0)["children"] == []

    def test_bad_path_names_the_valid_siblings(self, graph):
        with pytest.raises(PathNotFound) as exc:
            graph.resolve(f"{ROBOT}/Nonexistent")
        message = str(exc.value)
        assert "Nonexistent" in message
        assert "Intake <1>" in message, "must list what the caller could have meant"


class TestSearch:
    def test_finds_a_part_nested_five_levels_down(self, graph):
        matches = graph.search("kraken")
        assert len(matches) == 1
        assert matches[0].path == KRAKEN

    def test_search_is_case_insensitive(self, graph):
        assert graph.search("KRAKEN")[0].path == graph.search("kraken")[0].path

    def test_instance_count_reflects_reuse(self, graph):
        """The Kraken sits inside a module used 4x, so it exists 4x."""
        assert graph.search("kraken")[0].instance_count == 4

    def test_results_dedupe_by_definition(self, graph):
        """Two Brackets share a definition -> one hit, count 2."""
        matches = graph.search("bracket")
        assert len(matches) == 1
        assert matches[0].instance_count == 2

    def test_empty_query_returns_nothing(self, graph):
        assert graph.search("   ") == []


class TestResponseCeiling:
    """The guardrail that stops a size assumption becoming a 1.09 MB surprise."""

    def test_overview_stays_under_the_ceiling(self, graph):
        for depth in range(0, MAX_DEPTH + 1):
            out = assembly_overview(graph, depth=depth)
            assert len(out) <= MAX_RESPONSE_BYTES

    def test_output_is_always_valid_json(self, graph):
        for depth in range(0, MAX_DEPTH + 1):
            json.loads(assembly_overview(graph, depth=depth))

    def test_oversized_payload_degrades_and_explains(self):
        big = {"blob": "x" * (MAX_RESPONSE_BYTES * 2)}
        small = {"blob": "ok"}
        out = json.loads(
            fit_response([lambda: big, lambda: small], narrow_hint="Narrow the query.")
        )
        assert out["truncated"] is True
        assert "Narrow the query." in out["note"]

    def test_ceiling_holds_even_when_nothing_fits(self):
        huge = {"blob": "x" * (MAX_RESPONSE_BYTES * 2)}
        out = fit_response([lambda: huge], narrow_hint="Try harder.")
        assert len(out) <= MAX_RESPONSE_BYTES
        assert json.loads(out)["truncated"] is True

    def test_search_results_cap(self, graph):
        assert len(find_parts(graph, "e")) <= MAX_RESPONSE_BYTES


class TestQueryResults:
    def test_root_overview_includes_a_summary(self, graph):
        out = json.loads(assembly_overview(graph))
        assert out["summary"]["unique_components"] > 0

    def test_subtree_overview_omits_the_summary(self, graph):
        """The summary is a whole-robot statistic; it would mislead on a subtree."""
        assert "summary" not in json.loads(assembly_overview(graph, path=ROBOT))

    def test_bad_path_returns_an_error_not_an_exception(self, graph):
        out = json.loads(assembly_overview(graph, path="No/Such/Thing"))
        assert "error" in out

    def test_empty_search_query_is_rejected(self, graph):
        assert "error" in json.loads(find_parts(graph, ""))

    def test_no_match_suggests_a_shorter_fragment(self, graph):
        out = json.loads(find_parts(graph, "zzzznope"))
        assert out["matches"] == []
        assert "note" in out

    def test_occurrences_never_leak_into_a_result(self, graph):
        """The 553 KB array must not reach the model by any path."""
        blobs = [
            assembly_overview(graph, depth=MAX_DEPTH),
            assembly_overview(graph, path=ROBOT, depth=MAX_DEPTH),
            find_parts(graph, "a"),
        ]
        for blob in blobs:
            assert "transform" not in blob
            assert "occurrence" not in blob.lower()


class _StubResponse:
    def __init__(self, payload: dict, text: str | None = None) -> None:
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _StubClient:
    """Counts requests so the cache's whole purpose can be asserted."""

    def __init__(self, definition: dict, microversion: str = "MV1") -> None:
        self.definition = definition
        self.microversion = microversion
        self.calls: list[str] = []

    async def get(self, path: str, **_: object) -> _StubResponse:
        self.calls.append(path)
        if path.endswith("currentmicroversion"):
            return _StubResponse({"microversion": self.microversion})
        return _StubResponse(self.definition)


class TestCache:
    @pytest.mark.asyncio
    async def test_cold_fetch_then_warm_hit(self, raw, tmp_path):
        client = _StubClient(raw)
        cache = AssemblyCache(tmp_path)

        first = await cache.get_definition(client, "D", "W", "E")
        assert first["rootAssembly"]["instances"][0]["name"] == ROBOT
        assert cache.definition_requests == 1

        second = await cache.get_definition(client, "D", "W", "E")
        assert second["rootAssembly"] == first["rootAssembly"]
        # The point of the whole module: the 1.09 MB fetch did not repeat.
        assert cache.definition_requests == 1
        assert cache.validator_requests == 2

    @pytest.mark.asyncio
    async def test_changed_microversion_invalidates(self, raw, tmp_path):
        client = _StubClient(raw)
        cache = AssemblyCache(tmp_path)
        await cache.get_definition(client, "D", "W", "E")

        client.microversion = "MV2"
        await cache.get_definition(client, "D", "W", "E")
        assert cache.definition_requests == 2

    @pytest.mark.asyncio
    async def test_stale_entries_are_pruned(self, raw, tmp_path):
        client = _StubClient(raw)
        cache = AssemblyCache(tmp_path)
        await cache.get_definition(client, "D", "W", "E")
        client.microversion = "MV2"
        await cache.get_definition(client, "D", "W", "E")

        assert len(list(tmp_path.glob("D_E_*.json"))) == 1

    @pytest.mark.asyncio
    async def test_corrupt_entry_refetches_instead_of_raising(self, raw, tmp_path):
        client = _StubClient(raw)
        cache = AssemblyCache(tmp_path)
        await cache.get_definition(client, "D", "W", "E")

        for path in tmp_path.glob("*.json"):
            path.write_text("{ this is not json", encoding="utf-8")

        result = await cache.get_definition(client, "D", "W", "E")
        assert result["rootAssembly"]["instances"][0]["name"] == ROBOT
        assert cache.definition_requests == 2
