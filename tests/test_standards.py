"""Team standards: overrides only, and never silently ignored.

The two properties worth pinning, because both were design decisions rather
than accidents:

1. **No file, no change.** A fresh clone with no `standards.toml` behaves
   exactly as it did before the file existed. The other 350 tests are the real
   evidence for that; the tests here state it explicitly so nobody "fixes" it.
2. **A typo is refused.** A misspelled standard that silently does nothing is
   the worst failure a config file has: it looks configured, it is not
   enforced, and nobody finds out until a part comes off the router wrong.
   This project has paid for silent failures more than once.

Offline and free.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.skills import standards as standards_module
from src.skills.standards import (
    SPECS,
    Standards,
    StandardsError,
    reload_standards,
    standard,
    team_note,
)
from src.skills.templates import TEMPLATES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHIPPED = PROJECT_ROOT / "standards.toml"


@pytest.fixture
def with_standards(monkeypatch):
    """Install standards for one test, without touching the real file."""

    def install(overrides: dict[str, float], source: Path | None = None):
        installed = Standards(overrides, source=source or Path("standards.toml"))
        monkeypatch.setattr(standards_module, "STANDARDS", installed)
        return installed

    return install


# -- loading ---------------------------------------------------------------


def test_absent_file_is_empty_not_an_error(tmp_path: Path) -> None:
    loaded = Standards.load(tmp_path / "nothing.toml")
    assert loaded.source is None
    assert loaded.get("plate_pocket", "min_rib_mm") == 3.0


def test_override_takes_effect_and_is_attributed(tmp_path: Path) -> None:
    path = tmp_path / "standards.toml"
    path.write_text("[plate_pocket]\nmin_rib_mm = 6.0\n", encoding="utf-8")

    loaded = Standards.load(path)
    assert loaded.get("plate_pocket", "min_rib_mm") == 6.0
    assert loaded.is_team_set("plate_pocket", "min_rib_mm")
    # Untouched standards stay at their declared default.
    assert loaded.get("plate_pocket", "min_border_mm") == 5.0
    assert not loaded.is_team_set("plate_pocket", "min_border_mm")


def test_unknown_key_is_refused_with_the_valid_names(tmp_path: Path) -> None:
    path = tmp_path / "standards.toml"
    path.write_text("[plate_pocket]\nmin_rib = 6.0\n", encoding="utf-8")

    with pytest.raises(StandardsError) as exc:
        Standards.load(path)
    assert "min_rib" in str(exc.value)
    assert "plate_pocket.min_rib_mm" in str(exc.value), (
        "a refusal that does not name the correct spelling makes the user guess")


def test_unknown_section_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "standards.toml"
    path.write_text("[bellypan]\nmin_rib_mm = 6.0\n", encoding="utf-8")
    with pytest.raises(StandardsError):
        Standards.load(path)


@pytest.mark.parametrize("body", [
    "[plate_pocket]\nmin_rib_mm = 'three'\n",
    "[plate_pocket]\nmin_rib_mm = true\n",
    "[plate_pocket]\nmin_rib_mm = 0\n",
    "[plate_pocket]\nmin_rib_mm = -1.0\n",
    "plate_pocket = 3.0\n",
])
def test_malformed_values_are_refused(tmp_path: Path, body: str) -> None:
    """Including zero — a zero minimum disables a check while looking set."""
    path = tmp_path / "standards.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(StandardsError):
        Standards.load(path)


def test_asking_for_an_undeclared_standard_is_a_bug_not_a_default() -> None:
    with pytest.raises(KeyError):
        Standards().get("plate_pocket", "invented_mm")


# -- the shipped file ------------------------------------------------------


def test_shipped_file_loads_and_overrides_nothing() -> None:
    """It ships fully commented out: the mechanism without invented numbers."""
    loaded = Standards.load(SHIPPED)
    assert loaded.summary() == Standards().summary(), (
        "standards.toml has an uncommented value. That is fine for a real team "
        "— but then this test should be updated to say which decision it "
        "records and why.")


def test_shipped_file_documents_every_declared_standard() -> None:
    """A standard nobody can find is a standard nobody sets."""
    text = SHIPPED.read_text(encoding="utf-8")
    for spec in SPECS:
        assert f"[{spec.section}]" in text, f"{spec.section} section missing"
        assert spec.key in text, (
            f"{spec.path} is settable in code but absent from standards.toml, "
            f"so nobody would know it exists.")


# -- what the templates do with them ---------------------------------------


def test_template_enforces_the_team_number(with_standards) -> None:
    """The point of the whole exercise: a team rule becomes a real refusal."""
    with_standards({"plate_pocket.min_rib_mm": 12.0})

    problems = TEMPLATES["plate_pocket_pattern"].validate({
        "plate_width": 150.0, "plate_length": 150.0, "plate_thickness": 6.35,
        "columns": 3, "rows": 3, "rib_width": 8.0, "border_width": 12.0,
    })
    messages = [p.message for p in problems if p.parameter == "rib_width"]
    assert messages, "8 mm ribs should trip a 12 mm team minimum"
    assert "12" in messages[0]


def test_default_rib_width_passes_without_a_team_standard() -> None:
    """Same design, no override: no rib complaint. Proves 1. above."""
    problems = TEMPLATES["plate_pocket_pattern"].validate({
        "plate_width": 150.0, "plate_length": 150.0, "plate_thickness": 6.35,
        "columns": 3, "rows": 3, "rib_width": 8.0, "border_width": 12.0,
    })
    assert not [p for p in problems if p.parameter == "rib_width"]


def test_gearbox_web_refusal_quotes_the_team_number(with_standards) -> None:
    """An ERROR, not a warning — the bores really would run into each other."""
    with_standards({"gearbox_bore.min_web_mm": 20.0})

    problems = TEMPLATES["gearbox_bore_pattern"].validate({
        "driving_teeth": 14, "driven_teeth": 50, "diametral_pitch": 20,
        "bore_diameter": 28.575, "plate_width": 200.0, "plate_height": 120.0,
    })
    errors = [p for p in problems if p.is_error]
    assert errors, "a 20 mm web requirement should not fit this pair"
    assert any("20" in p.message for p in errors)


def test_team_note_appears_only_when_the_team_set_it(with_standards) -> None:
    """Silence for a default. A pointer costs tokens; say it when it means
    something, which is when a person made the decision."""
    assert team_note("plate_pocket", "min_rib_mm") == ""

    with_standards({"plate_pocket.min_rib_mm": 6.0}, source=Path("standards.toml"))
    assert "standards.toml" in team_note("plate_pocket", "min_rib_mm")
    assert team_note("plate_pocket", "min_border_mm") == ""


def test_reload_installs_the_new_values(tmp_path: Path) -> None:
    path = tmp_path / "standards.toml"
    path.write_text("[tube_hole]\nmin_wall_mm = 4.0\n", encoding="utf-8")
    try:
        reload_standards(path)
        assert standard("tube_hole", "min_wall_mm") == 4.0
    finally:
        # Leave the process as it was found; other tests read these.
        reload_standards(SHIPPED)
    assert standard("tube_hole", "min_wall_mm") == 2.0
