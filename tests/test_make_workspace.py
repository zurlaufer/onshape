"""The team workspace: separate from the source, and complete without it.

Why it exists is in FINDINGS.md 2026-11-25: sessions run inside the repo read
and rewrote the copilot's source. These tests pin that the workspace carries
everything a session needs (the server, the traps, the corpus) and points
away from the repo.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "make_workspace", REPO / "scripts" / "make_workspace.py")
mw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mw)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    mw.build(tmp_path)
    return tmp_path


def test_server_is_launched_by_absolute_path_with_repo_importable(workspace):
    server = json.loads((workspace / ".mcp.json").read_text(encoding="utf-8"))
    entry = server["mcpServers"]["frc-cad-copilot"]
    assert Path(entry["command"]).is_absolute()
    assert Path(entry["command"]).exists()
    assert entry["args"] == ["-m", "src.mcp.server"]
    assert Path(entry["env"]["PYTHONPATH"]) == REPO


def test_traps_come_from_the_repo_claude_md_verbatim(workspace):
    claude = (workspace / "CLAUDE.md").read_text(encoding="utf-8")
    traps = mw.traps_section((REPO / "CLAUDE.md").read_text(encoding="utf-8"))
    assert traps in claude
    assert "`box` is a reserved word" in claude
    assert "{traps}" not in claude and "{repo}" not in claude


def test_developer_pointers_are_left_out(workspace):
    """build_corpus.py and test_corpus.py are for developers, not users."""
    claude = (workspace / "CLAUDE.md").read_text(encoding="utf-8")
    assert "build_corpus" not in claude
    assert "test_corpus" not in claude


def test_corpus_is_copied_for_grep(workspace):
    for example in (REPO / "featurescript").rglob("*.fs"):
        copy = workspace / example.relative_to(REPO)
        assert copy.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")


def _permissions(workspace: Path) -> dict:
    return json.loads((workspace / ".claude" / "settings.json")
                      .read_text(encoding="utf-8"))["permissions"]


def test_repo_is_out_of_reach(workspace):
    deny = _permissions(workspace)["deny"]
    assert any(r.startswith("Read(//") and r.endswith("/**)") for r in deny)
    # Bash was how evaluation sessions reached the source; a user session
    # has no need to run commands or write files.
    for tool in ("Bash", "Edit", "Write"):
        assert tool in deny


def test_only_the_writing_tools_ask(workspace):
    perms = _permissions(workspace)
    assert perms["ask"] == ["mcp__frc-cad-copilot__frc_apply_featurescript",
                            "mcp__frc-cad-copilot__frc_assemble"]
    assert not any("apply" in rule or "assemble" in rule for rule in perms["allow"])


def test_every_server_tool_is_either_allowed_or_asks(workspace):
    """A new tool left out of both lists would prompt with "don't ask again"
    on offer; a writing one left in `allow` would write unseen."""
    import re
    source = (REPO / "src" / "mcp" / "server.py").read_text(encoding="utf-8")
    tools = set(re.findall(r"@mcp\.tool\s+async def (\w+)", source))
    perms = _permissions(workspace)
    listed = {r.rsplit("__", 1)[-1] for r in perms["allow"] + perms["ask"]
              if r.startswith("mcp__frc-cad-copilot__")}
    assert tools == listed


def test_vendor_pages_are_readable_but_search_still_asks(workspace):
    allow = _permissions(workspace)["allow"]
    assert "WebFetch(domain:docs.wcproducts.com)" in allow
    assert "WebFetch(domain:reca.lc)" in allow
    assert not any(rule.startswith("WebSearch") for rule in allow)
    assert "WebFetch" not in allow, "an open WebFetch would allow every site"


def test_the_labs_server_is_off_here(workspace):
    """Registered user-wide, not bound by robot.toml, holds a delete scope."""
    assert "mcp__onshape-labs" in _permissions(workspace)["deny"]


def test_windows_paths_become_posix_rules():
    assert mw.posix_rule_path(Path("C:/Users/x/onshape")).startswith("//c/Users/x/onshape")


def test_refuses_to_build_inside_the_repo():
    with pytest.raises(SystemExit):
        mw.build(REPO / "inner-workspace")
    assert not (REPO / "inner-workspace" / ".mcp.json").exists()
