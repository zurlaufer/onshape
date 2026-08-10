"""Regenerate `featurescript/`, the worked-example corpus.

    ./.venv/Scripts/python.exe scripts/build_corpus.py

Offline and free. Run it after changing any renderer the corpus draws from —
`tests/test_corpus.py` fails if what is committed has drifted from what the
sources now produce, so this is the fix for that failure.

What the corpus is for: `CLAUDE.md` tells every session to grep here before
writing FeatureScript. It is **grepped, never loaded whole** — twenty examples
would not fit the token budget, four do. See `src/skills/corpus.py` for what
earns a place.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.skills.corpus import EXAMPLES, build_corpus  # noqa: E402


def main() -> int:
    written = build_corpus()

    for example, path in zip(EXAMPLES, written):
        mark = "verified" if example.ok else "FAILURE "
        print(f"  {mark}  {path}  ({len(example.body()):,} chars)")

    print(f"\n{len(written)} example(s) written. Try:")
    print("  grep -rn evBox3d featurescript/")
    print("  grep -rn box featurescript/failures/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
