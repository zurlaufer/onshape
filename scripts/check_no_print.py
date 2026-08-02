"""Fail if anything under src/ calls print().

Under MCP stdio transport stdout carries the JSON-RPC stream. A stray print()
injects a raw line into it and the in-flight tool reply is lost — the call dies
with no error surfaced to the client. Measured on fastmcp 3.4.5; see the README.

There is no runtime safety net (repointing sys.stdout takes the protocol down
with it), so the rule is enforced here instead of merely documented.

Uses the AST rather than a text search, so docstrings and comments that merely
mention print() are not flagged. To permit a deliberate call, put `allow-print`
in a comment on the same line.

    python scripts/check_no_print.py        # exit 1 on any violation
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
ESCAPE = "allow-print"


def violations(path: Path) -> list[tuple[int, str]]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    found = []
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "print":
            line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            if ESCAPE in line:
                continue
            found.append((node.lineno, line.strip()))
    return found


def main() -> int:
    if not SRC.is_dir():
        sys.stderr.write(f"no src/ directory at {SRC}\n")
        return 2

    total = 0
    for path in sorted(SRC.rglob("*.py")):
        for lineno, text in violations(path):
            rel = path.relative_to(SRC.parent)
            # ASCII only: this prints to a Windows console under cp1252.
            sys.stderr.write(f"{rel}:{lineno}: print() call - use `logger` instead\n")
            sys.stderr.write(f"    {text}\n")
            total += 1

    if total:
        sys.stderr.write(
            f"\n{total} print() call(s) under src/. stdout is the MCP protocol "
            f"channel; writing to it drops tool replies silently.\n"
            f"Use the module logger (bound to stderr), or add an `{ESCAPE}` "
            f"comment if the call is genuinely intentional.\n"
        )
        return 1

    sys.stderr.write("ok: no print() calls under src/\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
