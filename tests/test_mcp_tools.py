"""Tests for the MCP tool surface — the layer the model actually touches.

This file exists because a state assessment on 2026-08-09 found the layer
closest to the user was the least tested: 50 tests on the client, 59 on skill
validation, 37 on the context graph, and **none** on the tools themselves.

Everything here runs offline. That is not a compromise — the paths worth pinning
are the ones that must answer *without* spending a request: an unknown design, a
refused write, an impossible layout, and a response that outgrew its ceiling.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.api.errors import OnshapeConfigError, OnshapeTimeoutError
from src.api.quota import QuotaLedger
from src.context.queries import MAX_RESPONSE_BYTES
from src.context.workspace import Target, Workspace
from src.mcp import server
from src.skills import standards as standards_module
from src.skills.standards import Standards
from src.skills.templates import TEMPLATES

CITRUS = "8d51b49e4b833c57f7d33bf0"  # a real robot; never a write target
SCRATCH = "e424515893d4f1ab8b23e5c4"

POCKET = {
    "plate_width": 150.0, "plate_length": 150.0, "plate_thickness": 6.35,
    "columns": 3, "rows": 3, "rib_width": 8.0, "border_width": 12.0,
}


@pytest.fixture
def many_designs():
    """Stand in a grown library, then put the real one back.

    Three designs already fill most of the response ceiling, so "what happens at
    ten" is a question about next month, not a hypothetical.
    """
    real = dict(TEMPLATES)
    for i in range(10):
        TEMPLATES[f"clone_{i}"] = list(real.values())[i % len(real)]
    yield
    TEMPLATES.clear()
    TEMPLATES.update(real)


class TestListChecks:
    async def test_lists_every_design_with_its_parameters(self):
        payload = json.loads(await server.frc_list_checks())
        assert {c["name"] for c in payload["checks"]} == set(TEMPLATES)
        assert all("parameters" in c for c in payload["checks"])

    async def test_fits_the_ceiling_today(self):
        assert len(await server.frc_list_checks()) <= MAX_RESPONSE_BYTES

    async def test_a_grown_library_degrades_instead_of_failing(self, many_designs):
        """The defect this replaced: a single candidate meant an oversized
        listing fell through to 'too large' and told the model to narrow a call
        that took no arguments. Zero information, no way forward."""
        text = await server.frc_list_checks()
        payload = json.loads(text)
        assert len(text) <= MAX_RESPONSE_BYTES
        assert "error" not in payload
        assert len(payload["checks"]) == len(TEMPLATES), "every name survives"

    async def test_the_truncation_hint_names_a_call_that_exists(self, many_designs):
        """Advice the model cannot act on is worse than none."""
        payload = json.loads(await server.frc_list_checks())
        assert "frc_check_design" in payload.get("note", "")

    async def test_team_standards_are_silent_until_a_team_sets_one(self):
        """No standards.toml entries, no payload. A team that has decided
        nothing should not pay tokens to be told so on every call."""
        payload = json.loads(await server.frc_list_checks())
        assert "team_standards" not in payload

    async def test_team_standards_are_reported_when_set(self, monkeypatch):
        """The case that matters: a session designing to the generic 3 mm rib
        should find out the team decided 6 before it proposes anything."""
        monkeypatch.setattr(
            standards_module, "STANDARDS",
            Standards({"plate_pocket.min_rib_mm": 6.0}, source=Path("standards.toml")))

        payload = json.loads(await server.frc_list_checks())
        reported = payload["team_standards"]
        assert [s["standard"] for s in reported] == ["plate_pocket.min_rib_mm"]
        assert reported[0]["value"] == 6.0


class TestCheckDesign:
    """Free, instant, and the place the project's engineering value lives."""

    async def test_a_sound_design_returns_the_numbers_it_implies(self):
        payload = json.loads(await server.frc_check_design("plate_pocket_pattern", POCKET))
        assert payload["ok"] is True
        assert payload["api_requests_used"] == 0
        derived = payload["derived"]
        assert derived["pocket_mm"]["width"] == pytest.approx(36.6667, abs=1e-3)
        assert derived["mass_removed_g"] == pytest.approx(207.5, abs=1.0)

    async def test_gear_geometry_is_derived_too(self):
        payload = json.loads(await server.frc_check_design("gearbox_bore_pattern", {
            "driving_teeth": 14, "driven_teeth": 50, "bore_diameter": 12.7,
            "plate_width": 120.0, "plate_height": 90.0}))
        assert payload["derived"]["centre_distance_mm"] == pytest.approx(40.64)
        assert payload["derived"]["reduction"] == pytest.approx(50 / 14, abs=1e-3)

    async def test_it_hands_back_source_to_probe_and_apply(self):
        """The composition the first real session showed the model wants:
        check, probe, apply."""
        payload = json.loads(await server.frc_check_design("gearbox_bore_pattern", {
            "driving_teeth": 14, "driven_teeth": 50, "bore_diameter": 12.7,
            "plate_width": 120.0, "plate_height": 90.0}))
        assert payload["featurescript"]["probe_body"].startswith("function(")
        assert "defineFeature" in payload["featurescript"]["module"]

    async def test_over_the_ceiling_the_module_survives_the_probe(self):
        """The pocket template's source outgrew 8 KB on 2026-11-25. The module
        is the deliverable, and apply compiles it, so the probe goes first."""
        payload = json.loads(await server.frc_check_design("plate_pocket_pattern", POCKET))
        assert "defineFeature" in payload["featurescript"]["module"]
        assert "probe_body" not in payload["featurescript"]
        assert "frc_apply_featurescript" in payload["note"]

    async def test_an_impossible_layout_is_refused_for_nothing(self):
        """The number that justifies the whole local layer."""
        payload = json.loads(await server.frc_check_design(
            "plate_pocket_pattern", dict(POCKET, columns=20)))
        assert payload["ok"] is False
        assert payload["api_requests_used"] == 0
        assert payload["problems"]
        assert "featurescript" not in payload, "do not hand out source that cannot work"

    async def test_missing_parameters_are_named_not_just_counted(self):
        payload = json.loads(await server.frc_check_design(
            "plate_pocket_pattern", {"plate_width": 150.0}))
        assert "plate_thickness" in " ".join(p["message"] for p in payload["problems"])

    async def test_an_unknown_design_lists_the_real_ones(self):
        payload = json.loads(await server.frc_check_design("make_me_a_robot", {}))
        assert payload["available"] == sorted(TEMPLATES)

    async def test_derivations_never_turn_an_answer_into_an_error(self):
        """A bonus that raises must not cost the caller the real result."""
        class Awkward:
            name = "awkward"

            def centre_distance_mm(self, params):
                raise ZeroDivisionError("no pitch")

        assert server._derived_numbers(Awkward(), {}) == {}


class TestWriteSafetyAtTheToolSurface:
    async def test_a_team_robot_is_refused_at_zero_cost(self):
        """FM2. Refused before anything is generated or spent — a copilot that
        can write into a robot the week before a competition is an incident
        waiting to happen."""
        payload = json.loads(await server.frc_apply_featurescript(
            where=f"{CITRUS}/b6722fcc07bf8d271ff14e47/5c9a297fdb631e7177684001",
            module="FeatureScript 3029;\nimport(path : \"onshape/std/geometry.fs\", version : \"3029.0\");\n",
            feature_name="nope"))
        assert payload["ok"] is False
        assert payload["stage"] == "write_safety"
        assert payload["api_requests_used"] == 0

    async def test_malformed_source_never_reaches_onshape(self):
        payload = json.loads(await server.frc_apply_featurescript(
            where=f"{SCRATCH}/65294e9343b8524f94ad5093/4fa46792842ff31b21179dad",
            module="this is not featurescript",
            feature_name="nope"))
        assert payload["ok"] is False
        assert payload["stage"] == "source"
        assert payload["api_requests_used"] == 0

    async def test_a_selection_for_an_undeclared_parameter_is_refused_free(self):
        """Onshape would ignore it silently, and the feature would regenerate
        as ERROR with an empty selection. Workspace trial, 2026-11-25."""
        from src.skills.templates import TEMPLATES
        module = TEMPLATES["plate_pocket_pattern"].render_module(POCKET)
        payload = json.loads(await server.frc_apply_featurescript(
            where=f"{SCRATCH}/65294e9343b8524f94ad5093/4fa46792842ff31b21179dad",
            module=module, feature_name="Pockets",
            selections={"pocketFace": "top"}))
        assert payload["ok"] is False
        assert payload["stage"] == "selections"
        assert payload["api_requests_used"] == 0
        assert "face" in payload["error"], "should list what IS declared"

    async def test_a_probe_lambda_with_an_import_is_refused_locally(self):
        """The evaluate endpoint rejects it; no reason to spend a request
        learning that again."""
        payload = json.loads(await server.frc_probe_featurescript(
            where=f"{SCRATCH}/65294e9343b8524f94ad5093/4fa46792842ff31b21179dad",
            body='import(path : "onshape/std/geometry.fs", version : "3029.0");'))
        assert payload["ok"] is False
        assert payload["api_requests_used"] == 0


class TestWhereResolution:
    """One `where` replaced three hex ids on every tool."""

    async def test_an_unknown_name_explains_itself(self):
        answer = await server.onshape_assembly_overview(where="Elevator")
        assert "Elevator" in answer
        assert "scratch" in answer or "robot" in answer, "must list what exists"

    async def test_the_wrong_tab_type_is_caught_before_a_request(self):
        """The first real session's only 400: a Part Studio id passed to an
        assembly tool. Now it never leaves the process."""
        answer = await server.onshape_assembly_overview(where="scratch")
        assert "partstudio" in answer and "assembly" in answer

    def test_a_document_without_a_tab_is_refused_with_advice(self):
        empty = Workspace([Target(document_id=CITRUS, wvm_id="w" * 24,
                                  name="robot", kind=None)])
        original, server._workspace = server._workspace, empty
        try:
            with pytest.raises(Exception, match="names a document but not a tab"):
                server._resolve("robot", expect="assembly")
        finally:
            server._workspace = original


class _StubQuotaClient:
    def __init__(self, ledger: QuotaLedger) -> None:
        self.quota = ledger


class TestQuotaNote:
    """Surfaced only when it is worth a student's attention.

    Onshape meters per endpoint and the FeatureScript bucket is ~100 a day, so
    the warning earns its bytes when it appears — and reporting healthy numbers
    on every call would not.
    """

    def test_silent_when_there_is_headroom(self):
        ledger = QuotaLedger()
        ledger.record("GET", "/api/v26/documents", {"x-rate-limit-remaining": "900"})
        assert server._quota_note(_StubQuotaClient(ledger)) == {}

    def test_speaks_up_when_a_bucket_runs_low(self):
        ledger = QuotaLedger()
        route = ("/api/v26/partstudios/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                 "/w/bbbbbbbbbbbbbbbbbbbbbbbb/e/cccccccccccccccccccccccc/featurescript")
        ledger.record("POST", route, {"x-rate-limit-remaining": "100"})
        ledger.record("POST", route, {"x-rate-limit-remaining": "9"})
        note = server._quota_note(_StubQuotaClient(ledger))
        assert note["quota_warning"]["low_endpoints"]
        assert "each endpoint separately" in note["quota_warning"]["note"]


class TestErrorExplanations:
    """A model relaying a failure to a student needs a sentence, not a class."""

    def test_a_typed_error_carries_its_advice(self):
        text = server._explain(OnshapeTimeoutError("GET /x exceeded 30s."))
        assert "exceeded 30s" in text
        assert "longer timeout" in text, "the advice must survive"

    def test_config_errors_point_at_the_file_to_fix(self):
        text = server._explain(OnshapeConfigError("Missing ONSHAPE_ACCESS_KEY."))
        assert "ONSHAPE_ACCESS_KEY" in text and ".env" in text

    def test_an_unexpected_failure_still_names_its_type(self):
        """An unrecognised error is a bug in us; hiding it makes it harder to
        find, so the type stays visible."""
        assert "ValueError" in server._explain(ValueError("something odd"))
