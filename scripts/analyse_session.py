"""What actually happened in a Claude Code session, read from the transcript.

Written after a transcript read by hand reshaped the entire tool surface. The
session had used the copilot for **4 of 79 tool calls** and never called the
main tool at all — a fact no amount of asking would have produced, because a
model at the end of a session is a poor witness to its own behaviour. It has the
failures in context and will agree with whatever you suggest.

    ./.venv/Scripts/python.exe scripts/analyse_session.py            # newest
    ./.venv/Scripts/python.exe scripts/analyse_session.py 7e93b1fc   # by prefix

Offline and free — it reads the JSONL that Claude Code already writes.

**THIS COUNTS. IT DOES NOT DECIDE.** None of the conclusions that reshaped this
project came from counting: they came from reading the transcript, from web
research, and from the user correcting the project's purpose. What the script
removes is the need to remember and the temptation to trust a self-report.

And the obvious trap, stated so nobody falls into it: a number that is easy to
compute becomes the thing you optimise. **"MCP usage %" is not a target.** If the
model got further by writing its own script, that is information about the
tools, not a failure by the model.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
TRANSCRIPTS = (Path.home() / ".claude" / "projects"
               / "c--Users-zurla-Desktop-onshape")

#: Imports of our own code, which is how the model reaches past the tools.
_OUR_IMPORT = re.compile(r"(?:from|import)\s+(src\.[a-z_.]+)")


def find_transcript(prefix: str | None) -> Path | None:
    if not TRANSCRIPTS.is_dir():
        return None
    files = sorted(TRANSCRIPTS.glob("*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if prefix:
        files = [f for f in files if f.name.startswith(prefix)]
    return files[0] if files else None


def blocks(path: Path):
    """Every content block, in order, with the role that produced it."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            yield message.get("role"), {"type": "text", "text": content}
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    yield message.get("role"), block


def analyse(path: Path) -> dict:
    tools: Counter[str] = Counter()
    imports: Counter[str] = Counter()
    prompts: list[str] = []
    failures: list[str] = []
    own_code_chars = 0
    pending: list[str] = []

    for role, block in blocks(path):
        kind = block.get("type")

        if kind == "text" and role == "user":
            text = (block.get("text") or "").strip()
            if text and not text.startswith("<"):
                prompts.append(text)

        elif kind == "tool_use":
            name = block.get("name", "?")
            tools[name] += 1
            pending.append(name)
            payload = json.dumps(block.get("input") or {})
            if name in ("Write", "Edit"):
                own_code_chars += len(payload)
            for module in _OUR_IMPORT.findall(payload):
                imports[module.split(".")[1] if module.count(".") >= 1 else module] += 1

        elif kind == "tool_result":
            name = pending.pop(0) if pending else "?"
            content = block.get("content")
            text = content if isinstance(content, str) else " ".join(
                b.get("text", "") for b in content or [] if isinstance(b, dict))
            # Only real signals. A first cut flagged anything containing the
            # substring "error" and duly reported a payload whose status was
            # OK, plus this project's own test source. A detector that is
            # usually wrong trains you to skip the time it is right.
            if block.get("is_error") or '"ok": false' in text[:600].lower():
                failures.append(f"{name}: {text[:110].strip()}")

    ours = {n: c for n, c in tools.items() if n.startswith("mcp__")}
    return {
        "path": path,
        "tools": tools,
        "our_calls": sum(ours.values()),
        "total_calls": sum(tools.values()),
        "our_tools": ours,
        "imports": imports,
        "prompts": prompts,
        "failures": failures,
        "own_code_chars": own_code_chars,
    }


def report(data: dict) -> None:
    total = data["total_calls"] or 1
    ours = data["our_calls"]

    print(f"transcript : {data['path'].name}")
    print(f"user turns : {len(data['prompts'])}")
    print()
    print(f"tool calls : {data['total_calls']}")
    print(f"  ours     : {ours}  ({ours / total:.0%})")
    print(f"  other    : {data['total_calls'] - ours}")
    print()

    print("-- our tools, by use --")
    if data["our_tools"]:
        for name, count in sorted(data["our_tools"].items(),
                                  key=lambda kv: -kv[1]):
            print(f"  {count:>4}  {name.split('__')[-1]}")
    else:
        print("  none were called")
    print()

    print("-- everything else --")
    for name, count in data["tools"].most_common(8):
        if not name.startswith("mcp__"):
            print(f"  {count:>4}  {name}")
    print()

    print("-- our modules imported directly, bypassing the tools --")
    if data["imports"]:
        for module, count in data["imports"].most_common():
            print(f"  {count:>4}  src.{module}")
        print("  (a module used this way is one the tools do not expose well "
              "enough,\n   or do not need to)")
    else:
        print("  none")
    print()

    if data["own_code_chars"]:
        chars = data["own_code_chars"]
        print(f"-- code the model wrote itself: ~{chars:,} chars "
              f"(~{chars // 4:,} tokens) --")
        print()

    if data["failures"]:
        print(f"-- tool results that reported a problem ({len(data['failures'])}) --")
        for line in data["failures"][:10]:
            print(f"  {line}")
        if len(data["failures"]) > 10:
            print(f"  ... and {len(data['failures']) - 10} more")
        print()

    print("-- what the person asked for --")
    for prompt in data["prompts"][:6]:
        first = prompt.splitlines()[0]
        print(f"  * {first[:96]}")
    print()
    print("Now read the transcript around anything surprising. The counts say "
          "where to look;\nthey do not say what it means. Write what you "
          "conclude into FINDINGS.md.")


def main(argv: list[str]) -> int:
    prefix = argv[1] if len(argv) > 1 else None
    path = find_transcript(prefix)
    if path is None:
        print(f"No transcript found in {TRANSCRIPTS}"
              + (f" starting with {prefix!r}" if prefix else ""))
        return 1
    report(analyse(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
