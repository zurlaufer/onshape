"""Tests for `where` resolution and the manifest.

Offline. These cover the layer that replaced three opaque hex parameters with
one name — and the write boundary that now lives underneath it.
"""

from __future__ import annotations

import pytest

from src.context.workspace import (
    Target,
    UnknownTarget,
    Workspace,
    WorkspaceError,
    parse_onshape_url,
)

DOC = "8d51b49e4b833c57f7d33bf0"
WS = "b6722fcc07bf8d271ff14e47"
EL = "57419d060d54790668e1b5ec"


class TestUrlParsing:
    def test_a_plain_workspace_url(self):
        t = parse_onshape_url(f"https://cad.onshape.com/documents/{DOC}/w/{WS}/e/{EL}")
        assert (t.document_id, t.wvm, t.wvm_id, t.element_id) == (DOC, "w", WS, EL)

    def test_a_query_string_does_not_break_it(self):
        """Onshape appends ?renderMode=... constantly."""
        t = parse_onshape_url(
            f"https://cad.onshape.com/documents/{DOC}/w/{WS}/e/{EL}?renderMode=0&uiState=x")
        assert t.element_id == EL

    def test_versions_and_microversions(self):
        assert parse_onshape_url(f"/documents/{DOC}/v/{WS}/e/{EL}").wvm == "v"
        assert parse_onshape_url(f"/documents/{DOC}/m/{WS}/e/{EL}").wvm == "m"

    def test_the_api_style_d_spelling(self):
        """Onshape writes /documents/{id} in the UI and /d/{id} in API paths."""
        assert parse_onshape_url(f"/documents/d/{DOC}/w/{WS}").document_id == DOC

    def test_a_document_only_url(self):
        t = parse_onshape_url(f"https://cad.onshape.com/documents/{DOC}")
        assert t.document_id == DOC and t.element_id is None

    def test_an_enterprise_host(self):
        """Enterprise domains vary, so only the path shape is checked."""
        t = parse_onshape_url(f"https://citrus.onshape.com/documents/{DOC}/w/{WS}/e/{EL}")
        assert t.document_id == DOC

    def test_rubbish_is_none_not_an_exception(self):
        assert parse_onshape_url("the intake plate") is None
        assert parse_onshape_url("") is None


@pytest.fixture
def manifest(tmp_path):
    path = tmp_path / "robot.toml"
    path.write_text(f"""
[[target]]
name = "robot"
url = "https://cad.onshape.com/documents/{DOC}/w/{WS}/e/{EL}"
kind = "assembly"
writable = false

[[target]]
name = "Intake"
url = "https://cad.onshape.com/documents/aaaaaaaaaaaaaaaaaaaaaaaa/w/bbbbbbbbbbbbbbbbbbbbbbbb/e/cccccccccccccccccccccccc"
kind = "partstudio"
writable = true
""", encoding="utf-8")
    return Workspace.load(path)


class TestResolution:
    def test_by_name(self, manifest):
        assert manifest.resolve("Intake").document_id == "a" * 24

    def test_names_are_case_insensitive(self, manifest):
        """Students type what they remember, not what was typed into the file."""
        assert manifest.resolve("intake").name == "Intake"
        assert manifest.resolve("  INTAKE  ").name == "Intake"

    def test_by_url(self, manifest):
        assert manifest.resolve(
            f"https://cad.onshape.com/documents/{DOC}/w/{WS}/e/{EL}"
        ).document_id == DOC

    def test_by_raw_triple(self, manifest):
        t = manifest.resolve(f"{DOC}/{WS}/{EL}")
        assert (t.document_id, t.element_id) == (DOC, EL)

    def test_a_pasted_url_cannot_smuggle_write_access(self, manifest):
        """Writability comes from the manifest, never from how you asked."""
        assert manifest.resolve(
            f"https://cad.onshape.com/documents/{DOC}/w/{WS}/e/{EL}").writable is False
        assert manifest.resolve(
            "https://cad.onshape.com/documents/aaaaaaaaaaaaaaaaaaaaaaaa"
            "/w/bbbbbbbbbbbbbbbbbbbbbbbb/e/cccccccccccccccccccccccc").writable is True

    def test_an_unknown_name_lists_the_real_ones(self, manifest):
        with pytest.raises(UnknownTarget) as caught:
            manifest.resolve("Elevator")
        assert "Intake" in str(caught.value) and "robot" in str(caught.value)

    def test_an_empty_where_is_refused(self, manifest):
        with pytest.raises(UnknownTarget):
            manifest.resolve("   ")


class TestElementTypeGuard:
    """The session's only 400: a Part Studio id handed to an assembly tool."""

    def test_the_wrong_kind_is_caught_before_any_request(self, manifest):
        with pytest.raises(UnknownTarget, match="needs an assembly"):
            manifest.resolve("Intake", expect="assembly")

    def test_the_right_kind_passes(self, manifest):
        assert manifest.resolve("robot", expect="assembly").kind == "assembly"

    def test_a_url_has_no_kind_so_it_is_not_second_guessed(self, manifest):
        """We only know a tab's type if the manifest says so."""
        manifest.resolve(f"{DOC}/{WS}/{EL}", expect="assembly")


class TestManifestLoading:
    def test_a_missing_manifest_is_empty_not_fatal(self, tmp_path):
        """A fresh clone must still start; only names go missing."""
        w = Workspace.load(tmp_path / "absent.toml")
        assert w.targets == [] and w.writable_documents == frozenset()

    def test_contradictory_writability_is_refused_at_load(self, tmp_path):
        """A boundary that depends on which entry was read is not a boundary."""
        path = tmp_path / "robot.toml"
        path.write_text(f"""
[[target]]
name = "a"
url = "https://cad.onshape.com/documents/{DOC}/w/{WS}/e/{EL}"
writable = true

[[target]]
name = "b"
url = "https://cad.onshape.com/documents/{DOC}/w/{WS}/e/aaaaaaaaaaaaaaaaaaaaaaaa"
writable = false
""", encoding="utf-8")
        with pytest.raises(WorkspaceError, match="both writable and read-only"):
            Workspace.load(path)

    def test_a_target_without_a_usable_url_is_refused(self, tmp_path):
        path = tmp_path / "robot.toml"
        path.write_text('[[target]]\nname = "x"\nurl = "not a url"\n', encoding="utf-8")
        with pytest.raises(WorkspaceError, match="address bar"):
            Workspace.load(path)

    def test_writable_documents_are_collected(self, manifest):
        assert manifest.writable_documents == frozenset({"a" * 24})

    def test_summary_is_small_enough_to_return_from_a_tool(self, manifest):
        rows = manifest.summary()
        assert {r["name"] for r in rows} == {"robot", "Intake"}
        assert all(set(r) == {"name", "kind", "writable"} for r in rows)


class TestTarget:
    def test_path_builds_the_onshape_fragment(self):
        t = Target(document_id=DOC, wvm_id=WS, element_id=EL)
        assert t.path == f"/d/{DOC}/w/{WS}/e/{EL}"

    def test_a_version_target_uses_v(self):
        assert Target(document_id=DOC, wvm_id=WS, wvm="v").path == f"/d/{DOC}/v/{WS}"

    def test_describe_flags_read_only(self):
        assert "[read-only]" in Target(DOC, WS, name="robot").describe()
        assert "[read-only]" not in Target(DOC, WS, name="x", writable=True).describe()
