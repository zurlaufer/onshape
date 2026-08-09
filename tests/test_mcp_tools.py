"""Tests for the MCP tool surface — the layer the model actually touches.

This file exists because a state assessment on 2026-08-09 found the layer
closest to the user was the least tested: 50 tests on the client, 59 on skill
validation, 37 on the context graph, and **none** on the tools themselves.

Everything here runs offline. That is not a compromise — the paths worth pinning
are the ones that must answer *without* spending a request: an unknown template,
a refused write, an impossible design, and a response that outgrew its ceiling.
"""

from __future__ import annotations

import json

import pytest

from src.api.errors import OnshapeConfigError, OnshapeTimeoutError
from src.api.quota import QuotaLedger
from src.context.queries import MAX_RESPONSE_BYTES
from src.mcp import server
from src.skills.templates import TEMPLATES

CITRUS = "8d51b49e4b833c57f7d33bf0"  # a real robot; never a write target
SCRATCH = "e424515893d4f1ab8b23e5c4"


@pytest.fixture
def many_templates():
    """Stand in a grown library, then put the real one back.

    Three templates already fill ~70% of the response ceiling, so "what happens
    at ten" is a question about next month, not about a hypothetical.
    """
    real = dict(TEMPLATES)
    for i in range(10):
        TEMPLATES[f"clone_{i}"] = list(real.values())[i % len(real)]
    yield
    TEMPLATES.clear()
    TEMPLATES.update(real)


class TestListTemplates:
    async def test_lists_every_template_with_its_parameters(self):
        payload = json.loads(await server.frc_list_templates())
        names = {t["name"] for t in payload["templates"]}
        assert names == set(TEMPLATES)
        assert all("parameters" in t for t in payload["templates"])

    async def test_fits_the_ceiling_today(self):
        assert len(await server.frc_list_templates()) <= MAX_RESPONSE_BYTES

    async def test_one_template_by_name(self):
        payload = json.loads(
            await server.frc_list_templates("plate_pocket_pattern"))
        assert payload["template"]["name"] == "plate_pocket_pattern"
        assert payload["template"]["parameters"]

    async def test_unknown_name_lists_the_real_ones(self):
        """A dead end should hand back the way out."""
        payload = json.loads(await server.frc_list_templates("pocket"))
        assert "Unknown template" in payload["error"]
        assert payload["available"] == sorted(TEMPLATES)

    async def test_a_grown_library_degrades_instead_of_failing(self, many_templates):
        """The defect this replaced: a single candidate meant an oversized
        listing fell through to 'too large' and told the model to narrow a call
        that took no arguments. Zero template information, no way forward."""
        text = await server.frc_list_templates()
        payload = json.loads(text)

        assert len(text) <= MAX_RESPONSE_BYTES
        assert "error" not in payload
        assert payload["truncated"] is True
        assert len(payload["templates"]) == len(TEMPLATES), "every name survives"

    async def test_the_truncation_hint_names_a_call_that_exists(self, many_templates):
        """Advice the model cannot act on is worse than none."""
        payload = json.loads(await server.frc_list_templates())
        assert "frc_list_templates(name=" in payload["note"]

        # And it must actually work for a name from the degraded listing.
        first = payload["templates"][0]
        name = first if isinstance(first, str) else first["name"]
        detail = json.loads(await server.frc_list_templates(name))
        assert detail["template"]["parameters"]


class TestApplyTemplateFreePaths:
    """Everything that must be answered without spending a request."""

    PARAMS = {
        "plate_width": 150.0, "plate_length": 150.0, "plate_thickness": 6.35,
        "columns": 3, "rows": 3, "rib_width": 8.0, "border_width": 12.0,
    }

    async def test_unknown_template_is_refused_with_the_alternatives(self):
        payload = json.loads(await server.frc_apply_template(
            "make_me_a_robot", {}, SCRATCH, "W", "P"))
        assert payload["ok"] is False
        assert payload["available"] == sorted(TEMPLATES)

    async def test_a_team_robot_is_refused_at_zero_cost(self):
        """FM2. The check runs before anything is spent, and before any code is
        generated — a copilot that can write into a robot the week before a
        competition is an incident waiting to happen."""
        payload = json.loads(await server.frc_apply_template(
            "plate_pocket_pattern", self.PARAMS, CITRUS, "W", "P",
            feature_studio_id="F"))
        assert payload["ok"] is False
        assert payload["stage"] == "write_safety"
        assert payload["api_requests_used"] == 0

    async def test_an_impossible_design_never_reaches_onshape(self):
        """The number that justifies the whole local validation layer."""
        payload = json.loads(await server.frc_apply_template(
            "plate_pocket_pattern", dict(self.PARAMS, columns=20),
            SCRATCH, "W", "P"))
        assert payload["ok"] is False
        assert payload["stage"] == "parameters"
        assert payload["api_requests_used"] == 0
        assert payload["problems"]

    async def test_missing_parameters_are_named_not_just_counted(self):
        payload = json.loads(await server.frc_apply_template(
            "plate_pocket_pattern", {"plate_width": 150.0}, SCRATCH, "W", "P"))
        messages = " ".join(p["message"] for p in payload["problems"])
        assert "plate_thickness" in messages
        assert payload["api_requests_used"] == 0


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
        ledger.record("GET", "/api/v26/documents",
                      {"x-rate-limit-remaining": "900"})
        assert server._quota_note(_StubQuotaClient(ledger)) == {}

    def test_speaks_up_when_a_bucket_runs_low(self):
        ledger = QuotaLedger()
        ledger.record("POST", "/api/v26/partstudios/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                              "/w/bbbbbbbbbbbbbbbbbbbbbbbb"
                              "/e/cccccccccccccccccccccccc/featurescript",
                      {"x-rate-limit-remaining": "100"})
        ledger.record("POST", "/api/v26/partstudios/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                              "/w/bbbbbbbbbbbbbbbbbbbbbbbb"
                              "/e/cccccccccccccccccccccccc/featurescript",
                      {"x-rate-limit-remaining": "9"})
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
        assert "ONSHAPE_ACCESS_KEY" in text
        assert ".env" in text

    def test_an_unexpected_failure_still_names_its_type(self):
        """An unrecognised error is a bug in us; hiding it makes it harder to
        find, so the type stays visible."""
        assert "ValueError" in server._explain(ValueError("something odd"))
