"""Tests for the FeatureScript skill: templates, local validation, diagnostics.

Offline. The Onshape-side layers are exercised through a stub client that
replays real response shapes captured from the live API on 2026-08-07.
"""

from __future__ import annotations

import json

import pytest

from src.skills.apply import (
    LIBRARY_VERSION,
    ScratchPolicy,
    WriteRefused,
    apply_module,
    probe_body,
)
from src.skills.templates import TEMPLATES, Severity
from src.skills.templates.tube_hole_pattern import TubeHolePattern
from src.skills.validate import validate_params, validate_source, worst_severity

GOOD = {
    "tube_width": 25.4,      # 1 inch tube
    "tube_length": 300.0,
    "hole_diameter": 6.35,   # 1/4 inch
    "hole_count": 5,
    "spacing": 25.4,         # the standard 1-inch FRC pattern
}


def errors(problems):
    return [p for p in problems if p.is_error]


class TestTemplateRegistry:
    def test_registry_is_small_and_named(self):
        assert "tube_hole_pattern" in TEMPLATES
        assert len(TEMPLATES) <= 8, "keep the library deliberate"

    def test_every_template_declares_its_parameters(self):
        for template in TEMPLATES.values():
            specs = template.parameters()
            assert specs
            assert all(s.description for s in specs), "each param needs a description"


class TestParameterValidation:
    """Layer 3: the semantic check that needs no geometry kernel."""

    def test_a_sane_pattern_passes(self):
        assert errors(validate_params("tube_hole_pattern", GOOD)) == []

    def test_hole_wider_than_the_tube_is_rejected(self):
        """The headline case: impossible on paper, caught for zero requests."""
        problems = validate_params(
            "tube_hole_pattern", {**GOOD, "hole_diameter": 30.0})
        assert errors(problems)
        message = errors(problems)[0].message
        assert "25.4" in message and "30" in message, "must quote the real numbers"

    def test_pattern_longer_than_the_tube_is_rejected(self):
        problems = validate_params(
            "tube_hole_pattern", {**GOOD, "hole_count": 40})
        assert any("spans" in p.message for p in errors(problems))

    def test_rejection_suggests_a_workable_value(self):
        problems = validate_params("tube_hole_pattern", {**GOOD, "hole_count": 40})
        spacing = next(p for p in errors(problems) if p.parameter == "spacing")
        assert spacing.suggestion, "a refusal must say what would work"

    def test_overlapping_holes_are_rejected(self):
        problems = validate_params(
            "tube_hole_pattern", {**GOOD, "spacing": 3.0})
        assert any("merge" in p.message for p in errors(problems))

    def test_thin_edge_distance_warns_but_does_not_block(self):
        problems = validate_params(
            "tube_hole_pattern", {**GOOD, "edge_distance": 1.0})
        assert errors(problems) == []
        assert any(p.severity is Severity.WARNING for p in problems)

    def test_heavy_material_removal_warns(self):
        """10 x 6.35 mm holes in 100 mm is 63% of the length removed."""
        problems = validate_params(
            "tube_hole_pattern",
            {**GOOD, "tube_length": 100.0, "hole_count": 10, "spacing": 8.0})
        assert errors(problems) == [], "this pattern fits; it is only weak"
        assert any("weakens" in p.message for p in problems)

    def test_missing_required_parameter_is_named(self):
        params = {k: v for k, v in GOOD.items() if k != "tube_width"}
        problems = validate_params("tube_hole_pattern", params)
        assert any(p.parameter == "tube_width" for p in errors(problems))

    def test_non_numeric_parameter_is_rejected(self):
        problems = validate_params("tube_hole_pattern", {**GOOD, "spacing": "wide"})
        assert any(p.parameter == "spacing" for p in errors(problems))

    def test_unknown_template_lists_the_real_ones(self):
        problems = validate_params("no_such_template", {})
        assert "tube_hole_pattern" in errors(problems)[0].suggestion

    def test_unknown_parameter_warns_only(self):
        problems = validate_params("tube_hole_pattern", {**GOOD, "colour": "red"})
        assert errors(problems) == []
        assert any(p.parameter == "colour" for p in problems)


class TestSourceValidation:
    """Layers 1 and 2 — structural sanity and symbol warnings."""

    def test_a_rendered_module_is_clean(self):
        module = TubeHolePattern().render_module(GOOD)
        assert errors(validate_source(module, is_module=True)) == []

    def test_a_rendered_probe_is_clean(self):
        probe = TubeHolePattern().render_probe(GOOD)
        assert errors(validate_source(probe, is_module=False)) == []

    def test_truncated_output_is_caught(self):
        module = TubeHolePattern().render_module(GOOD)
        problems = validate_source(module[: len(module) // 2], is_module=True)
        assert any("Unbalanced" in p.message for p in errors(problems))

    def test_module_without_header_is_rejected(self):
        problems = validate_source("import(path : \"x\", version : \"1\");\n{}",
                                   is_module=True)
        assert any("header" in p.message for p in errors(problems))

    def test_probe_containing_an_import_is_rejected(self):
        """Measured: the evaluate endpoint PARSE-errors on import."""
        probe = ('import(path : "onshape/std/geometry.fs", version : "3029.0");\n'
                 "function(context is Context, queries) { return 1; }")
        assert any("import" in p.message for p in errors(validate_source(probe, is_module=False)))

    def test_probe_containing_export_is_rejected(self):
        probe = "export const x = function(context is Context, queries) { return 1; };"
        assert any("export" in p.message for p in errors(validate_source(probe, is_module=False)))

    def test_empty_source_is_rejected(self):
        assert errors(validate_source("   ", is_module=True))

    def test_hallucinated_function_warns_but_never_blocks(self):
        """False positives cost more than false negatives, so: warning only."""
        probe = ("function(context is Context, queries) "
                 "{ return totallyMadeUpFunction(context); }")
        problems = validate_source(probe, is_module=False)
        assert errors(problems) == []
        assert any("totallyMadeUpFunction" in p.message for p in problems)

    def test_real_std_symbols_do_not_warn(self):
        probe = ("function(context is Context, queries) "
                 "{ return evBox3d(context, { \"topology\" : qEverything(EntityType.BODY) }); }")
        assert validate_source(probe, is_module=False) == []

    def test_comments_and_strings_do_not_confuse_the_checks(self):
        probe = ('function(context is Context, queries) {\n'
                 '  // a stray brace { in a comment\n'
                 '  var s = "and ( one in a string";\n'
                 '  return 1;\n}')
        assert errors(validate_source(probe, is_module=False)) == []

    def test_worst_severity_ranks_correctly(self):
        assert worst_severity([]) is None
        clean = validate_source(TubeHolePattern().render_probe(GOOD), is_module=False)
        assert worst_severity(clean) is None


class TestRendering:
    def test_hole_positions_follow_spacing(self):
        probe = TubeHolePattern().render_probe({**GOOD, "edge_distance": 10.0})
        for expected in (10, 35.4, 60.8):
            assert f"{expected:g} * millimeter" in probe

    def test_module_carries_its_parameters_as_a_comment(self):
        module = TubeHolePattern().render_module(GOOD)
        assert "hole_diameter=6.35" in module

    def test_module_and_probe_agree_on_positions(self):
        module = TubeHolePattern().render_module(GOOD)
        probe = TubeHolePattern().render_probe(GOOD)
        positions = TubeHolePattern()._positions(GOOD)
        for p in positions:
            assert f"{p:g} * millimeter" in module
            assert f"{p:g} * millimeter" in probe


# --------------------------------------------------------------------------
# Onshape-side layers, against captured response shapes.
# --------------------------------------------------------------------------

CLEAN = {"btType": "BTFeatureScriptEvalResponse-1859", "notices": [],
         "result": {"btType": "...BTFSValueNumber", "value": 5.0}}

PARSE_ERROR = {
    "result": None,
    "notices": [{
        "btType": "BTNotice-227", "type": "PARSE", "level": "ERROR",
        "message": "no viable alternative at input '+ ;'",
        "expressionErrorInfo": None,
        "stackTrace": [{"line": 1, "column": 52, "endLine": 1, "endColumn": 53}],
    }],
}

UNKNOWN_FUNCTION = {
    "result": None,
    "notices": [{
        "btType": "BTNotice-227", "type": "SEMANTIC", "level": "ERROR",
        "message": "",
        "expressionErrorInfo": {
            "errorMessageIdentifier": "PARAMETER_EXPRESSION_UNKNOWN_FUNCTION",
            "messageArguments": [
                {"use": "STRING", "value": {"value": "thisFunctionDoesNotExist"}},
                {"use": "INTEGER", "value": {"value": 1.0}},
            ],
        },
        "stackTrace": [{"line": 1, "column": 40}],
    }],
}

QUERY_MATCHED_NOTHING = {
    "result": None,
    "notices": [{
        "btType": "BTNotice-227", "type": "OTHER", "level": "ERROR",
        "message": "throw query matched nothing",
        "expressionErrorInfo": None,
        "stackTrace": [{"line": 0, "column": 0}],
    }],
}


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _StubClient:
    def __init__(self, eval_payload=None, spec_count=1):
        self.eval_payload = eval_payload or CLEAN
        self.spec_count = spec_count
        self.posts: list[tuple[str, dict]] = []
        self.contents = "FeatureScript 3029;\n// original\n"

    async def get(self, path, **_):
        if path.endswith("/featurespecs"):
            return _Response({"featureSpecs": [{"featureTypeName": "T"}] * self.spec_count})
        return _Response({"contents": self.contents,
                          "serializationVersion": "1.2.21",
                          "sourceMicroversion": "MV1"})

    async def post(self, path, *, json_body=None, **_):
        self.posts.append((path, json_body or {}))
        if path.endswith("/featurescript"):
            return _Response(self.eval_payload)
        self.contents = (json_body or {}).get("contents", self.contents)
        return _Response({"contents": self.contents})


class TestProbeDiagnostics:
    """Layer 5 — the upgrade Step 0 bought us."""

    @pytest.mark.asyncio
    async def test_clean_probe_reports_success_and_value(self):
        client = _StubClient(CLEAN)
        result = await probe_body(client, "function(context is Context, queries) {}",
                                  document_id="D", workspace_id="W", part_studio_id="P")
        assert result.ok
        assert result.value == 5.0

    @pytest.mark.asyncio
    async def test_parse_error_carries_line_and_column(self):
        client = _StubClient(PARSE_ERROR)
        result = await probe_body(client, "bad", document_id="D",
                                  workspace_id="W", part_studio_id="P")
        assert not result.ok
        problem = result.problems[0]
        assert problem.line == 1 and problem.column == 52
        assert "no viable alternative" in problem.message

    @pytest.mark.asyncio
    async def test_unknown_function_name_is_reassembled_into_readable_text(self):
        """Onshape puts this in messageArguments rather than `message`."""
        client = _StubClient(UNKNOWN_FUNCTION)
        result = await probe_body(client, "bad", document_id="D",
                                  workspace_id="W", part_studio_id="P")
        assert "thisFunctionDoesNotExist" in result.problems[0].message

    @pytest.mark.asyncio
    async def test_query_matching_nothing_surfaces_the_thrown_message(self):
        """The FM1 error class. Spike 3b said this was unavailable; it is not."""
        client = _StubClient(QUERY_MATCHED_NOTHING)
        result = await probe_body(client, "bad", document_id="D",
                                  workspace_id="W", part_studio_id="P")
        assert not result.ok
        assert "query matched nothing" in result.problems[0].message

    @pytest.mark.asyncio
    async def test_failure_is_read_from_notices_not_the_status_code(self):
        """Onshape returns HTTP 200 even when the script failed."""
        client = _StubClient(PARSE_ERROR)
        result = await probe_body(client, "bad", document_id="D",
                                  workspace_id="W", part_studio_id="P")
        assert not result.ok

    @pytest.mark.asyncio
    async def test_probe_sends_the_expected_library_version(self):
        client = _StubClient(CLEAN)
        await probe_body(client, "x", document_id="D", workspace_id="W", part_studio_id="P")
        assert client.posts[0][1]["libraryVersion"] == LIBRARY_VERSION


class TestWriteSafety:
    """FM2 — writes never reach a team's robot."""

    @pytest.mark.asyncio
    async def test_unlisted_document_is_refused(self):
        with pytest.raises(WriteRefused):
            await apply_module(_StubClient(), "FeatureScript 3029;",
                               document_id="8d51b49e4b833c57f7d33bf0",  # Citrus
                               workspace_id="W", feature_studio_id="F")

    @pytest.mark.asyncio
    async def test_refusal_happens_before_any_request(self):
        client = _StubClient()
        with pytest.raises(WriteRefused):
            await apply_module(client, "x", document_id="nope",
                               workspace_id="W", feature_studio_id="F")
        assert client.posts == []

    @pytest.mark.asyncio
    async def test_allowlisted_document_is_accepted(self):
        client = _StubClient()
        policy = ScratchPolicy(allowed=frozenset({"SCRATCH"}))
        ok, problems = await apply_module(client, "FeatureScript 3029;",
                                          document_id="SCRATCH", workspace_id="W",
                                          feature_studio_id="F", policy=policy)
        assert ok and problems == []


class TestApplyModule:
    @pytest.mark.asyncio
    async def test_zero_specs_means_compile_failure(self):
        client = _StubClient(spec_count=0)
        ok, problems = await apply_module(
            client, "FeatureScript 3029;", document_id="SCRATCH", workspace_id="W",
            feature_studio_id="F", policy=ScratchPolicy(allowed=frozenset({"SCRATCH"})))
        assert not ok
        assert "did not compile" in problems[0].message
        assert "probe" in problems[0].suggestion, "must point at the layer that explains why"

    @pytest.mark.asyncio
    async def test_original_source_is_restored(self):
        client = _StubClient()
        original = client.contents
        await apply_module(client, "FeatureScript 3029;\n// new\n",
                           document_id="SCRATCH", workspace_id="W",
                           feature_studio_id="F",
                           policy=ScratchPolicy(allowed=frozenset({"SCRATCH"})))
        assert client.contents == original

    @pytest.mark.asyncio
    async def test_restore_happens_even_when_compilation_fails(self):
        client = _StubClient(spec_count=0)
        original = client.contents
        await apply_module(client, "broken", document_id="SCRATCH", workspace_id="W",
                           feature_studio_id="F",
                           policy=ScratchPolicy(allowed=frozenset({"SCRATCH"})))
        assert client.contents == original
