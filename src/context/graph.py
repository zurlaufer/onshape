"""Navigable index over an Onshape assembly definition.

Pure: no I/O, no network, no MCP. Hand it a parsed assembly definition and it
builds a named tree you can navigate by name-path. This is the module that
carries the test coverage for the context layer.

WHY A TREE AND NOT THE OCCURRENCE LIST
--------------------------------------
Measured on a real FRC robot (Citrus Circuits `1678 2026`):

  full assembly definition   1,088,965 B
  named tree, depth 1              397 B      <- the default view
  named tree, depth 2           32,496 B
  named tree, unlimited         67,970 B

Two structural facts make this work, both measured rather than assumed:

1. **Names live on `instances`, never on `parts`.** A `parts[]` entry carries
   only `partId`/`documentId`/`elementId` — no name field exists. So every
   name-based operation walks the instance tree; the parts array cannot help.

2. **19 unique subassembly definitions stand behind 1,320 occurrences.** Four
   identical swerve modules are one definition placed four times. Answering
   structural questions about the 19 rather than the 1,320 is a 70x reduction
   that costs nothing and reads better besides ("4x Swerve Module").

Onshape already appends `<1>`, `<2>` to sibling names, so a slash-joined path of
names is a unique address and we need not invent an ID scheme.

NEVER touch `rootAssembly.occurrences` here. That array is the 553 KB that must
not reach the model; v1 answers navigation questions without it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

__all__ = ["AssemblyGraph", "Node", "PathNotFound", "MAX_DEPTH"]

# Guards against a malformed cache file sending recursion to the moon. Real
# assemblies nest ~7 deep; this is generous.
MAX_RECURSION = 64

# Depth ceiling exposed to callers. depth=2 is already 32 KB on a real robot,
# so anything beyond 3 is a mistake we should refuse rather than serve.
MAX_DEPTH = 3

PATH_SEP = "/"


class PathNotFound(KeyError):
    """A name-path did not resolve. Carries the valid alternatives."""

    def __init__(self, path: str, missing: str, available: list[str]) -> None:
        self.path = path
        self.missing = missing
        self.available = available
        shown = ", ".join(repr(a) for a in available[:12]) or "(no children)"
        more = f" (+{len(available) - 12} more)" if len(available) > 12 else ""
        super().__init__(
            f"No child named {missing!r} in {path or '<root>'}. Available: {shown}{more}"
        )


@dataclass
class Node:
    """One instance in the assembly tree.

    Attributes:
        name: Onshape's instance name, including its `<n>` sibling suffix.
        type: "Assembly" or "Part".
        path: Slash-joined name-path from the root, the address used by callers.
        definition: Identity of what this instance points at —
            `(documentId, elementId, configuration, partId)`. Two nodes sharing
            it are the same component. `partId` matters: one Part Studio holds
            many parts, so omitting it merges unrelated components.
        children: Populated for assemblies whose definition was resolvable.
        child_count: Number of direct children, known even when `children` was
            not expanded (lazy expansion keeps construction cheap).
    """

    name: str
    type: str
    path: str
    definition: tuple[str, str, str, str] | None = None
    children: list["Node"] = field(default_factory=list)
    child_count: int = 0

    @property
    def is_assembly(self) -> bool:
        return self.type == "Assembly"

    def walk(self) -> Iterator["Node"]:
        """Depth-first over this node and everything beneath it."""
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class Match:
    """A search hit."""

    name: str
    type: str
    path: str
    #: How many places this same definition appears in the assembly. A hit with
    #: instance_count=4 is one component used four times, not four components.
    instance_count: int


class AssemblyGraph:
    """A named, navigable view of an assembly definition."""

    def __init__(self, root: Node, definitions: dict[tuple[str, str, str, str], list[str]]) -> None:
        self.root = root
        #: definition key -> every name-path where it appears
        self.definitions = definitions

    # ---------------------------------------------------------------- build

    @classmethod
    def from_definition(cls, raw: dict[str, Any]) -> "AssemblyGraph":
        """Build the tree from a raw `GET /assemblies/.../` response.

        Subassembly instances are joined to their definitions by
        `(documentId, elementId, configuration)` — configuration matters because
        the same element under two configurations is genuinely two components.
        """
        root_assembly = raw.get("rootAssembly") or {}
        by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for sub in raw.get("subAssemblies") or []:
            by_key[cls._sub_key(sub)] = sub

        definitions: dict[tuple[str, str, str, str], list[str]] = {}
        root = Node(name="", type="Assembly", path="")
        cls._expand(root, root_assembly, by_key, definitions, depth=0)
        return cls(root, definitions)

    @staticmethod
    def _sub_key(d: dict[str, Any]) -> tuple[str, str, str]:
        """Key for joining a subassembly instance to its definition."""
        return (
            d.get("documentId", ""),
            d.get("elementId", ""),
            d.get("configuration") or "default",
        )

    @staticmethod
    def _definition_key(inst: dict[str, Any]) -> tuple[str, str, str, str]:
        """Identity of the *thing* an instance points at.

        `partId` is essential and easy to miss: one Part Studio holds many
        parts, so (documentId, elementId) alone is not an identity. On the real
        Citrus robot that key collapsed 483 distinct parts into 105 groups — one
        group merged 90 unrelated items (screws, shims, tread, shafts) into a
        single "component" reported as used 360 times. Onshape supplies partId
        on every Part instance; assemblies simply have none.
        """
        return (
            inst.get("documentId", ""),
            inst.get("elementId", ""),
            inst.get("configuration") or "default",
            inst.get("partId") or "",
        )

    @classmethod
    def _expand(
        cls,
        node: Node,
        source: dict[str, Any],
        by_key: dict[tuple[str, str, str], dict[str, Any]],
        definitions: dict[tuple[str, str, str, str], list[str]],
        depth: int,
    ) -> None:
        """Attach children of `source` to `node`, recursing into subassemblies."""
        if depth >= MAX_RECURSION:
            return

        instances = source.get("instances") or []
        node.child_count = len(instances)

        for inst in instances:
            name = inst.get("name") or "(unnamed)"
            child = Node(
                name=name,
                type=inst.get("type") or "Part",
                path=f"{node.path}{PATH_SEP}{name}" if node.path else name,
                definition=cls._definition_key(inst),
            )
            if child.definition:
                definitions.setdefault(child.definition, []).append(child.path)
            node.children.append(child)

            if child.is_assembly:
                sub = by_key.get(cls._sub_key(inst))
                if sub is not None:
                    cls._expand(child, sub, by_key, definitions, depth + 1)

    # ------------------------------------------------------------ navigate

    def resolve(self, path: str | None) -> Node:
        """Name-path to node. Raises PathNotFound naming valid siblings."""
        if not path:
            return self.root
        node = self.root
        walked: list[str] = []
        for part in [p for p in path.split(PATH_SEP) if p]:
            match = next((c for c in node.children if c.name == part), None)
            if match is None:
                raise PathNotFound(
                    PATH_SEP.join(walked), part, [c.name for c in node.children]
                )
            node = match
            walked.append(part)
        return node

    def instance_count(self, node: Node) -> int:
        """How many places this node's definition appears assembly-wide."""
        if node.definition is None:
            return 1
        return len(self.definitions.get(node.definition, [])) or 1

    def subtree(self, path: str | None = None, depth: int = 1) -> dict[str, Any]:
        """A depth-limited view below `path`, collapsing repeated definitions.

        Repeats collapse by design: four identical swerve modules render as one
        entry with `count: 4`. That is both smaller and closer to how a student
        thinks about the robot.
        """
        depth = max(0, min(int(depth), MAX_DEPTH))
        node = self.resolve(path)
        return {
            "path": node.path or "<root>",
            "type": node.type,
            "child_count": node.child_count,
            "children": self._render(node, depth),
        }

    def _render(self, node: Node, depth: int) -> list[dict[str, Any]]:
        if depth <= 0 or not node.children:
            return []

        rendered: list[dict[str, Any]] = []
        seen: dict[tuple[str, str, str, str] | None, dict[str, Any]] = {}

        for child in node.children:
            # Siblings sharing a definition are the same component repeated.
            key = child.definition
            if key is not None and key in seen:
                seen[key]["count"] += 1
                continue

            entry: dict[str, Any] = {"name": child.name, "type": child.type}
            if child.is_assembly:
                entry["child_count"] = child.child_count
            total = self.instance_count(child)
            if total > 1:
                # Used elsewhere in the robot too, not just among these siblings.
                entry["used_in_assembly"] = total
            entry["count"] = 1

            deeper = self._render(child, depth - 1)
            if deeper:
                entry["children"] = deeper

            seen[key] = entry
            rendered.append(entry)

        for entry in rendered:
            if entry["count"] == 1:
                del entry["count"]
        return rendered

    # -------------------------------------------------------------- search

    def search(self, query: str, limit: int = 25) -> list[Match]:
        """Case-insensitive substring search over names.

        Results are deduplicated by definition, so a component used four times
        returns once with `instance_count=4` rather than flooding the results.
        """
        needle = (query or "").strip().lower()
        if not needle:
            return []

        out: list[Match] = []
        seen: set[tuple[str, str, str, str] | None] = set()
        for node in self.root.walk():
            if node is self.root or needle not in node.name.lower():
                continue
            if node.definition is not None:
                if node.definition in seen:
                    continue
                seen.add(node.definition)
            out.append(
                Match(
                    name=node.name,
                    type=node.type,
                    path=node.path,
                    instance_count=self.instance_count(node),
                )
            )
            if len(out) >= limit:
                break
        return out

    # --------------------------------------------------------------- stats

    def summary(self) -> dict[str, Any]:
        """Counts worth leading with — the 19-vs-1320 story in one line."""
        assemblies = parts = 0
        for node in self.root.walk():
            if node is self.root:
                continue
            if node.is_assembly:
                assemblies += 1
            else:
                parts += 1
        unique = len({d for d in self.definitions})
        return {
            "total_instances": assemblies + parts,
            "assemblies": assemblies,
            "parts": parts,
            "unique_components": unique,
        }
