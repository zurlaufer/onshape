"""Build (or refresh) a team workspace: where people actually use the copilot.

    ./.venv/Scripts/python.exe scripts/make_workspace.py            # ../frc-copilot
    ./.venv/Scripts/python.exe scripts/make_workspace.py <folder>

WHY A SEPARATE FOLDER. The FS MCP evaluation (FINDINGS.md, 2026-11-25) ran
sessions inside this repo, and they behaved like developers, not users. They
read the source, wrote 36 ad-hoc scripts against `src/` in place of calling
the tools, and in one case added a module, tests and a CLAUDE.md rule from
a one-line prompt. A user's session must see the tools and nothing else.

What the workspace holds:

  .mcp.json              the server, by absolute path, with the repo on PYTHONPATH
                         (the server pins its own cwd to the repo; see server.py)
  CLAUDE.md              user-facing instructions; the FeatureScript traps are
                         copied from this repo's CLAUDE.md, so there is one source
  featurescript/         a copy of the worked-example corpus, for grep
  .claude/settings.json  deny Read/Edit/Write on the repo. A guardrail, not a
                         sandbox: Bash can still reach it, which is why
                         CLAUDE.md says so in words too.

Re-run after changing CLAUDE.md or the corpus. It overwrites only the files above.
Offline and free.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "workspace_template" / "CLAUDE.md.template"
DEFAULT_TARGET = REPO.parent / "frc-copilot"


def python_executable() -> Path:
    """The repo's venv interpreter, Windows or POSIX layout."""
    for candidate in (REPO / ".venv" / "Scripts" / "python.exe",
                      REPO / ".venv" / "bin" / "python"):
        if candidate.exists():
            return candidate
    raise SystemExit(f"no virtualenv found under {REPO / '.venv'}")


def traps_section(claude_md: str) -> str:
    """The bullet list under '## FeatureScript traps', verbatim.

    Only the bullets: the paragraph after them points developers at build
    scripts and tests, which a user's session has no business opening.
    """
    match = re.search(r"^## FeatureScript traps[^\n]*\n(.*?)(?=^## )", claude_md,
                      re.MULTILINE | re.DOTALL)
    if not match:
        raise SystemExit("CLAUDE.md has no '## FeatureScript traps' section")
    lines, keep = [], False
    for line in match.group(1).splitlines():
        if line.startswith("- "):
            keep = True
        elif line and not line.startswith(" "):
            keep = False
        if keep:
            lines.append(line)
    return "\n".join(lines).rstrip()


def posix_rule_path(path: Path) -> str:
    """Claude Code permission rules match POSIX paths. An absolute path starts
    with `//`, and on Windows C:\\x becomes //c/x."""
    text = path.resolve().as_posix()
    drive = re.match(r"^([A-Za-z]):/(.*)$", text)
    return f"//{drive.group(1).lower()}/{drive.group(2)}" if drive else f"/{text}"


def build(target: Path) -> list[Path]:
    if target.resolve() == REPO or REPO in target.resolve().parents:
        raise SystemExit("the workspace must be outside the repo")
    target.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    mcp = {"mcpServers": {"frc-cad-copilot": {
        "type": "stdio",
        "command": str(python_executable()),
        "args": ["-m", "src.mcp.server"],
        "env": {"PYTHONPATH": str(REPO)},
    }}}
    (target / ".mcp.json").write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
    written.append(target / ".mcp.json")

    claude = TEMPLATE.read_text(encoding="utf-8").format(
        repo=REPO, traps=traps_section((REPO / "CLAUDE.md").read_text(encoding="utf-8")))
    (target / "CLAUDE.md").write_text(claude, encoding="utf-8")
    written.append(target / "CLAUDE.md")

    corpus = target / "featurescript"
    if corpus.exists():
        shutil.rmtree(corpus)
    shutil.copytree(REPO / "featurescript", corpus)
    written.append(corpus)

    repo_rule = posix_rule_path(REPO) + "/**"
    # No auto-approval of the server: Claude Code ignores
    # `enableAllProjectMcpServers` in a project file (checked 2026-11-25, the
    # server still showed "Pending approval"), and rightly, since a folder
    # should not approve its own code. The user approves it once.
    settings = {
        "permissions": {"deny": [f"Read({repo_rule})", f"Edit({repo_rule})",
                                 f"Write({repo_rule})"]},
    }
    (target / ".claude").mkdir(exist_ok=True)
    (target / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    written.append(target / ".claude" / "settings.json")
    return written


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    for path in build(target):
        print(f"wrote {path}")
    print(f"\nOpen {target} in VS Code (or run `claude` there). The copilot's tools "
          "appear under /mcp; the source is out of reach on purpose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
