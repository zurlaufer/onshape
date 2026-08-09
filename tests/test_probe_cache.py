"""Tests for the probe cache, the worked-example corpus, and learned symbols.

All three exist to protect one number: the FeatureScript evaluate bucket, at
roughly **100 requests a day shared across everyone on one key**. Making the
probe a first-class tool spends that faster, so these are what pay for it.
"""

from __future__ import annotations

import pytest

from src.skills.apply import probe_body
from src.skills.probe_cache import ProbeCache, body_digest
from src.skills import symbols

TARGET = "DOC/WS/PS"


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _CountingClient:
    """Counts probes, because the whole point is not making them."""

    def __init__(self, payload=None):
        self.payload = payload or {"notices": [], "result": {"value": 7.0}}
        self.probes = 0

    async def post(self, path, *, json_body=None, **_):
        self.probes += 1
        return _Response(self.payload)


UNKNOWN_FUNCTION = {"notices": [{
    "type": "SEMANTIC",
    "expressionErrorInfo": {
        "errorMessageIdentifier": "PARAMETER_EXPRESSION_UNKNOWN_FUNCTION",
        "messageArguments": [{"value": {"value": "qMadeUpQuery"}}],
    },
}]}


class TestDigest:
    def test_the_same_body_and_target_key_the_same(self):
        a = body_digest("f()", target=TARGET, library_version=3029)
        b = body_digest("f()", target=TARGET, library_version=3029)
        assert a == b

    def test_a_library_bump_invalidates_everything(self):
        """Serving a 3029 answer for a 3030 probe would be answering a question
        about a different language."""
        assert (body_digest("f()", target=TARGET, library_version=3029)
                != body_digest("f()", target=TARGET, library_version=3030))

    def test_a_different_part_studio_is_a_different_question(self):
        """Geometry is the one thing the cache cannot see: the same body against
        different bodies can legitimately give different answers."""
        assert (body_digest("f()", target="DOC/WS/A", library_version=3029)
                != body_digest("f()", target="DOC/WS/B", library_version=3029))

    def test_whitespace_is_not_normalised(self):
        """Onshape reports errors by line and column, so two bodies differing
        only in layout have genuinely different diagnostics."""
        assert (body_digest("f()", target=TARGET, library_version=3029)
                != body_digest("\nf()", target=TARGET, library_version=3029))


class TestCache:
    def test_a_miss_then_a_hit(self, tmp_path):
        cache = ProbeCache(tmp_path)
        assert cache.get("abc") is None
        cache.put("abc", {"ok": True, "value": 1})
        assert cache.get("abc") == {"ok": True, "value": 1}
        assert cache.hits == 1 and cache.misses == 1

    def test_expired_entries_are_re_probed(self, tmp_path):
        """A stale 'this compiles' is a worse answer than spending a request."""
        cache = ProbeCache(tmp_path, ttl_seconds=0.0)
        cache.put("abc", {"ok": True})
        assert cache.get("abc") is None

    def test_a_corrupt_entry_is_a_miss_not_a_crash(self, tmp_path):
        cache = ProbeCache(tmp_path)
        cache.put("abc", {"ok": True})
        next(tmp_path.glob("*.json")).write_text("{ broken", encoding="utf-8")
        assert cache.get("abc") is None

    def test_a_cache_that_cannot_write_is_slow_not_broken(self, tmp_path, monkeypatch):
        """Losing the cache must cost a request, never the answer."""
        from pathlib import Path

        def refuse(*_args, **_kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(Path, "write_text", refuse)
        cache = ProbeCache(tmp_path)
        cache.put("abc", {"ok": True})  # must not raise
        monkeypatch.undo()
        assert cache.get("abc") is None


class TestCacheInTheProbe:
    async def test_an_identical_body_costs_no_request(self):
        """The saving that justifies all of this."""
        client = _CountingClient()
        cache = ProbeCache.__new__(ProbeCache)
        ProbeCache.__init__(cache, ".cache/probe-test", ttl_seconds=60)
        store: dict = {}
        cache.get = lambda d: store.get(d)          # type: ignore[method-assign]
        cache.put = lambda d, r: store.__setitem__(d, r)  # type: ignore[method-assign]

        for _ in range(3):
            result = await probe_body(client, "function(context is Context, queries) {}",
                                      document_id="D", workspace_id="W",
                                      part_studio_id="P", cache=cache)
            assert result.ok
        assert client.probes == 1, "two of the three should have been free"

    async def test_a_changed_body_is_probed_again(self):
        client = _CountingClient()
        store: dict = {}
        cache = ProbeCache(".cache/probe-test")
        cache.get = lambda d: store.get(d)          # type: ignore[method-assign]
        cache.put = lambda d, r: store.__setitem__(d, r)  # type: ignore[method-assign]

        await probe_body(client, "f1()", document_id="D", workspace_id="W",
                         part_studio_id="P", cache=cache)
        await probe_body(client, "f2()", document_id="D", workspace_id="W",
                         part_studio_id="P", cache=cache)
        assert client.probes == 2

    async def test_failures_are_cached_too(self):
        """Re-asking Onshape the same broken question spends the same scarce
        request for the same answer."""
        client = _CountingClient(UNKNOWN_FUNCTION)
        store: dict = {}
        cache = ProbeCache(".cache/probe-test")
        cache.get = lambda d: store.get(d)          # type: ignore[method-assign]
        cache.put = lambda d, r: store.__setitem__(d, r)  # type: ignore[method-assign]

        first = await probe_body(client, "qMadeUpQuery()", document_id="D",
                                 workspace_id="W", part_studio_id="P", cache=cache)
        second = await probe_body(client, "qMadeUpQuery()", document_id="D",
                                  workspace_id="W", part_studio_id="P", cache=cache)
        assert first.ok is False and second.ok is False
        assert client.probes == 1
        assert second.problems and second.problems[0].message

    async def test_no_cache_still_works(self):
        client = _CountingClient()
        result = await probe_body(client, "f()", document_id="D", workspace_id="W",
                                  part_studio_id="P")
        assert result.ok and client.probes == 1


class TestLearnedSymbols:
    """Grown from observation rather than extracted.

    The extractor `symbols.py` once promised was never written, so the list has
    always been ~130 hand-typed names against a standard library of thousands.
    Learning from the probe costs nothing and cannot go stale.
    """

    def setup_method(self):
        symbols._OBSERVED.clear()
        symbols._REJECTED.clear()
        symbols.known_symbols.cache_clear()

    teardown_method = setup_method

    async def test_a_body_that_compiles_teaches_its_names(self):
        assert not symbols.is_known_symbol("qSomethingNovel")
        await probe_body(_CountingClient(), "qSomethingNovel(context)",
                         document_id="D", workspace_id="W", part_studio_id="P")
        assert symbols.is_known_symbol("qSomethingNovel"), (
            "Onshape accepted the body, so the name exists")

    async def test_a_rejected_name_is_remembered_as_bad(self):
        await probe_body(_CountingClient(UNKNOWN_FUNCTION), "qMadeUpQuery()",
                         document_id="D", workspace_id="W", part_studio_id="P")
        assert symbols.is_rejected("qMadeUpQuery")

    async def test_a_failing_body_teaches_no_names(self):
        """Its names are not vouched for by a run that did not succeed."""
        await probe_body(_CountingClient(UNKNOWN_FUNCTION), "qMadeUpQuery()",
                         document_id="D", workspace_id="W", part_studio_id="P")
        assert not symbols.is_known_symbol("qMadeUpQuery")

    def test_the_curated_list_still_stands(self):
        for name in ("evBox3d", "planeToCSys", "qOwnerBody", "qSketchRegion"):
            assert symbols.is_known_symbol(name)


class TestCorpus:
    """Worked examples, grepped and never loaded whole — one is ~500 tokens."""

    def test_a_verified_body_is_saved_readably(self, tmp_path):
        path = ProbeCache.promote(
            "pocket grid", "function(context is Context, queries) {}",
            note="Lays a pocket grid on a selected face.", ok=True,
            corpus_dir=tmp_path)
        text = path.read_text(encoding="utf-8")
        assert path.suffix == ".fs", "grep should land on FeatureScript"
        assert "// Lays a pocket grid" in text
        assert "Verified against Onshape" in text
        assert path.parent.name == "verified"

    def test_a_failure_is_kept_with_its_diagnostic(self, tmp_path):
        """`box` was hit twice — by this project and by a later session that had
        read the fact and not retained it. One example prevents the second."""
        path = ProbeCache.promote(
            "reserved word box", "var box = evBox3d(context, {});",
            note="`box` is a reserved word.", ok=False,
            diagnostics="mismatched input 'box' expecting ID (line 1)",
            corpus_dir=tmp_path)
        text = path.read_text(encoding="utf-8")
        assert path.parent.name == "failures"
        assert "Do not copy this shape" in text
        assert "mismatched input" in text

    def test_names_with_awkward_characters_are_safe(self, tmp_path):
        path = ProbeCache.promote("a/b: c*d", "f()", note="x", ok=True,
                                  corpus_dir=tmp_path)
        assert path.is_file()
