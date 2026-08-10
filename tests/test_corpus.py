"""The committed corpus must match what the renderers currently produce.

WHY THIS TEST EXISTS. `CLAUDE.md` is loaded on every request and tells a session
to grep `featurescript/` before writing FeatureScript. Two failure modes, both
silent without a test:

1. **The directory is empty.** That was the state until 2026-08-10 — `promote()`
   was written, documented, and never called. A session spends a tool call to
   discover there is nothing there.
2. **The examples drift.** Two entries are generated from
   `plate_pocket_pattern.py` and `climber_plate.py`. Change a renderer and the
   committed `.fs` quietly becomes a worked example of code that no longer
   exists — which is worse than an empty directory, because it looks current.

So this regenerates into a tmp dir and compares. When it fails, the fix is
`./.venv/Scripts/python.exe scripts/build_corpus.py`.

Offline and free, like every other test here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.skills.corpus import EXAMPLES, build_corpus
from src.skills.probe_cache import CORPUS_DIR

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMMITTED = PROJECT_ROOT / CORPUS_DIR

REBUILD = "Run: ./.venv/Scripts/python.exe scripts/build_corpus.py"


def _committed_path(example) -> Path:
    return COMMITTED / ("verified" if example.ok else "failures") / f"{example.name}.fs"


def test_corpus_directory_exists() -> None:
    """CLAUDE.md points every session here. It has to be there."""
    assert COMMITTED.is_dir(), (
        f"{CORPUS_DIR} does not exist, but CLAUDE.md tells every session to grep "
        f"it. {REBUILD}")


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e.name)
def test_committed_example_matches_its_source(example, tmp_path: Path) -> None:
    """Regenerate into a tmp dir; the committed file must be identical."""
    build_corpus(tmp_path)

    fresh = tmp_path / ("verified" if example.ok else "failures") / f"{example.name}.fs"
    committed = _committed_path(example)

    assert committed.is_file(), f"{committed} is missing. {REBUILD}"
    assert committed.read_text(encoding="utf-8") == fresh.read_text(encoding="utf-8"), (
        f"{committed.name} has drifted from what its source now renders. {REBUILD}")


def test_failures_are_labelled_as_failures() -> None:
    """A failure copied by mistake is the one thing this corpus must not enable.

    `promote()` writes the banner; this pins that a reader who lands mid-file
    from a grep still cannot mistake a failure for something to copy.
    """
    for example in EXAMPLES:
        if example.ok:
            continue
        text = _committed_path(example).read_text(encoding="utf-8")
        assert "KEPT AS A FAILURE" in text
        assert "Onshape said:" in text, "a failure without its diagnostic teaches nothing"


def test_verified_examples_are_bare_lambdas() -> None:
    """The evaluate endpoint rejects `import`, `annotation` and `export`.

    Every verified entry is a probe body, so it must be sendable as-is. An
    example that cannot be pasted into `frc_probe_featurescript` is a trap.
    """
    for example in EXAMPLES:
        if not example.ok:
            continue
        body = example.body()
        assert body.lstrip().startswith("function(context is Context"), example.name
        for rejected in ("import(", "annotation {", "export const"):
            assert rejected not in body, (
                f"{example.name} contains {rejected!r}, which the evaluate "
                f"endpoint rejects — this example cannot be probed as written.")


def test_corpus_stays_small_enough_to_grep() -> None:
    """It is grepped, never loaded whole — but a session may still read a hit.

    One example is roughly 500-800 tokens. The ceiling here is deliberately
    loose: it exists to catch the corpus growing into something that would blow
    a context window if someone cat'ed it, not to police individual examples.
    """
    total = sum(len(example.body()) for example in EXAMPLES)
    assert total < 40_000, (
        f"the corpus is {total:,} chars (~{total // 4:,} tokens). Promote fewer, "
        f"more general examples rather than one per design.")
