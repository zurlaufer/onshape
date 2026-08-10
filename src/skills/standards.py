"""The team's shop rules, as numbers the checks actually use.

WHY THIS IS NOT A DOCUMENT
--------------------------
The obvious way to capture "how we draft in this team" is a markdown file. This
project has measured what that is worth: the `box` reserved-word trap was written
down in `STATUS.md`, a later session read `STATUS.md` **twice**, and hit the trap
anyway — because it was fact #23 in an 11,000-token file. A long document read
once does not change behaviour.

So a standard here is a number that produces a **refusal or a warning with the
number in it**, at zero API cost, at the moment the design is checked. That is
the only form of "our team keeps 5 mm of border" that survives contact with work.

WHAT BELONGS HERE, AND WHAT DOES NOT
------------------------------------
Only things a team can legitimately decide. Three kinds of number get confused:

  * **Physical facts** — 6061 density, the circumradius of 1/2 in hex stock, a
    flanged bearing's OD. A team cannot vote on these, and making them editable
    would invite someone to "tune" a density until the mass came out right. They
    stay as constants in the module that uses them.
  * **Shop policy** — minimum rib, minimum border, end mill radius, minimum web.
    These genuinely differ: a team with a waterjet has different minima from one
    with a hand router. **These are what this file is for.**
  * **Guidelines with a theoretical basis** — 14 teeth before undercut (18 is the
    theory, 14 is what shops accept). Borderline, and included, because the
    number a team enforces is a shop-acceptance decision.

OVERRIDES ONLY, NEVER A SOURCE OF TRUTH
---------------------------------------
Every standard is declared below with its default and the reason for it. The TOML
file only *overrides*. Consequences, all deliberate:

  * a missing `standards.toml` changes nothing — a fresh clone behaves exactly as
    before, and the 350 existing tests do not depend on a config file;
  * an unknown section or key is **refused at load**, because a typo'd key that
    silently does nothing is the worst failure a config file has. This project
    has paid for silent failures more than once;
  * `STANDARDS.cite()` says whether a number came from the team or from the
    default, so a refusal can point at where to go argue with it.

Do not invent numbers to fill this file. A standard nobody stated is worse than
no standard: it will be enforced, and it will be wrong.

No `print()` — stdout is the MCP protocol channel (see src/mcp/server.py).
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "Standards",
    "StandardsError",
    "StandardSpec",
    "SPECS",
    "STANDARDS",
    "standard",
    "team_note",
    "reload_standards",
    "DEFAULT_STANDARDS_FILE",
]

DEFAULT_STANDARDS_FILE = Path("standards.toml")


class StandardsError(RuntimeError):
    """`standards.toml` is malformed, or names a standard that does not exist."""


@dataclass(frozen=True)
class StandardSpec:
    """One overridable number: what it is, what it defaults to, and why."""

    section: str
    key: str
    default: float
    unit: str
    why: str

    @property
    def path(self) -> str:
        return f"{self.section}.{self.key}"


#: Every standard a team may set. Adding one here is what makes it settable;
#: anything not listed is refused at load with the list of valid names.
#:
#: The defaults are the values that were hardcoded in the templates before this
#: file existed, so behaviour is unchanged until a team edits something.
SPECS: tuple[StandardSpec, ...] = (
    # -- plate pocketing ---------------------------------------------------
    StandardSpec(
        "plate_pocket", "min_rib_mm", 3.0, "mm",
        "Material left between adjacent pockets. Ribs thinner than this buckle "
        "under load rather than carry it, and tear while routing."),
    StandardSpec(
        "plate_pocket", "min_border_mm", 5.0, "mm",
        "Material left around the outside. Pockets running to the edge remove "
        "the flange that bolts and gussets need."),
    StandardSpec(
        "plate_pocket", "min_floor_mm", 1.0, "mm",
        "Thinnest pocket floor that survives handling. Below it, cut through."),
    StandardSpec(
        "plate_pocket", "max_removed_fraction", 0.6, "fraction",
        "Past this share of the face area the plate is more hole than plate and "
        "its bending stiffness falls off sharply."),
    StandardSpec(
        "plate_pocket", "end_mill_radius_mm", 3.175, "mm",
        "Radius of the smallest end mill the shop actually owns; a 1/4 in "
        "cutter is the FRC default. Sets the smallest internal corner, and so "
        "the smallest pocket that can be cut at all."),

    # -- tube hole patterns ------------------------------------------------
    StandardSpec(
        "tube_hole", "min_wall_mm", 2.0, "mm",
        "Aluminium left between a hole and the tube edge. Below this it tears "
        "out under load."),
    StandardSpec(
        "tube_hole", "edge_distance_factor", 1.0, "diameters",
        "Hole centre at least this many hole-diameters from the tube end."),
    StandardSpec(
        "tube_hole", "max_material_removed", 0.5, "fraction",
        "Share of the tube length taken as holes before it is materially "
        "weakened in bending."),

    # -- gearbox plates ----------------------------------------------------
    StandardSpec(
        "gearbox_bore", "min_teeth_no_undercut", 14, "teeth",
        "Smallest 20-degree spur pinion the shop will accept. 18 is the "
        "theoretical undercut limit; 14 is common FRC practice."),
    StandardSpec(
        "gearbox_bore", "min_web_mm", 3.0, "mm",
        "Aluminium left between two bores before the web tears out."),
    StandardSpec(
        "gearbox_bore", "min_edge_mm", 4.0, "mm",
        "Material between a bearing bore and the plate edge."),
)

_BY_PATH: dict[str, StandardSpec] = {spec.path: spec for spec in SPECS}


class Standards:
    """Team overrides on top of the declared defaults."""

    def __init__(
        self,
        overrides: dict[str, float] | None = None,
        source: Path | None = None,
    ) -> None:
        self._overrides = dict(overrides or {})
        self.source = source

    # -- reading ---------------------------------------------------------

    def get(self, section: str, key: str) -> float:
        """The value in force, team override or declared default.

        Raises:
            KeyError: the standard is not declared. A caller asking for one that
                does not exist is a bug in us, not a configuration problem, so
                it fails loudly rather than returning a plausible number.
        """
        path = f"{section}.{key}"
        spec = _BY_PATH.get(path)
        if spec is None:
            raise KeyError(
                f"No standard named {path!r}. Declared: "
                f"{', '.join(sorted(_BY_PATH))}")
        return self._overrides.get(path, spec.default)

    def is_team_set(self, section: str, key: str) -> bool:
        return f"{section}.{key}" in self._overrides

    def cite(self, section: str, key: str) -> str:
        """Where this number came from, for the end of a message.

        A student who disagrees with a refusal needs to know whether to argue
        with the team or with us.
        """
        if self.is_team_set(section, key):
            name = self.source.name if self.source else str(DEFAULT_STANDARDS_FILE)
            return f"your team standard, {name}"
        return "the default; set it in standards.toml to change it"

    def summary(self) -> list[dict[str, object]]:
        """Every standard in force. Small enough to return from a tool."""
        return [
            {
                "standard": spec.path,
                "value": self.get(spec.section, spec.key),
                "unit": spec.unit,
                "team_set": self.is_team_set(spec.section, spec.key),
            }
            for spec in SPECS
        ]

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Standards":
        """Read the overrides. An absent file is empty, not an error."""
        location = Path(path or os.environ.get("FRC_STANDARDS_FILE")
                        or DEFAULT_STANDARDS_FILE)
        if not location.is_file():
            logger.info("no standards at %s; using declared defaults", location)
            return cls()

        try:
            raw = tomllib.loads(location.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise StandardsError(f"Could not read {location}: {exc}") from exc

        overrides: dict[str, float] = {}
        for section, entries in raw.items():
            if not isinstance(entries, dict):
                raise StandardsError(
                    f"{location}: {section!r} must be a section like "
                    f"[plate_pocket], not a bare value.")
            for key, value in entries.items():
                path_key = f"{section}.{key}"
                spec = _BY_PATH.get(path_key)
                if spec is None:
                    # Silence here would mean a typo'd standard is simply not
                    # enforced, and nobody finds out until a part is cut wrong.
                    raise StandardsError(
                        f"{location}: no standard named {path_key!r}. "
                        f"Valid names: {', '.join(sorted(_BY_PATH))}")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise StandardsError(
                        f"{location}: {path_key} must be a number "
                        f"({spec.unit}), got {value!r}.")
                if value <= 0:
                    raise StandardsError(
                        f"{location}: {path_key} must be positive, got {value!r}. "
                        f"A zero minimum disables the check silently — delete the "
                        f"line instead if that is what you want.")
                overrides[path_key] = float(value)

        logger.info("standards: %d override(s) from %s", len(overrides), location)
        return cls(overrides, source=location)


#: Loaded once at import, like `Workspace` in the MCP server. Tests and the
#: reload path replace it through `reload_standards()`.
STANDARDS = Standards.load()


def reload_standards(path: Path | str | None = None) -> Standards:
    """Re-read the file and install the result. Returns what is now in force."""
    global STANDARDS
    STANDARDS = Standards.load(path)
    return STANDARDS


def standard(section: str, key: str) -> float:
    """The value in force for one standard.

    A function rather than a constant so that callers pick up a reload — and so
    that a test can install different standards without re-importing every
    template that reads them.
    """
    return STANDARDS.get(section, key)


def team_note(section: str, key: str) -> str:
    """A sentence to append to a message, but only when the team set the number.

    Empty for a default, deliberately. A student reading *"ribs under 3 mm
    buckle"* is reading engineering; a student reading *"ribs under 6 mm"* needs
    to know that is a decision somebody on their team made and can revisit.
    Adding the pointer unconditionally would spend tokens on every check to say
    nothing, and would make every existing message longer for teams that have no
    standards file at all.
    """
    if not STANDARDS.is_team_set(section, key):
        return ""
    name = STANDARDS.source.name if STANDARDS.source else str(DEFAULT_STANDARDS_FILE)
    return f" This is your team's standard, from {name}."
