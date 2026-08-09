"""Step 4: signing regression, retry classification, quota, timeouts, cache TTL.

Entirely offline. No credentials, no network — `respx` intercepts at the
transport, matching the rule the rest of the suite follows.

The signing tests matter most. The algorithm has been correct and verified live
since 2026-08-02, and until now *nothing pinned it*. Every one of the five traps
documented in the README is a silent 401 if it regresses, and a silent 401 looks
exactly like a bad key — which is the most expensive kind of bug to chase.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import httpx
import pytest
import respx

from src.api.errors import (
    OnshapeAuthError,
    OnshapeClientError,
    OnshapeQuotaExhausted,
    OnshapeRateLimitError,
    OnshapeServerError,
    OnshapeTimeoutError,
)
from src.api.onshape_client import (
    DEFAULT_API_VERSION,
    OnshapeApiClient,
    OnshapeConfigError,
    build_auth_headers,
    generate_nonce,
)
from src.api.quota import QuotaLedger, bucket_key
from src.api.write_policy import WritePolicy, WriteRefused, document_id_in
from src.context.cache import AssemblyCache

ACCESS = "TESTACCESSKEY"
SECRET = "TESTSECRETKEY"
FROZEN_DATE = "Mon, 03 Aug 2026 12:00:00 GMT"
FROZEN_NONCE = "abcdefghijklmnopqrstuvwxy"


def make_client(**kwargs) -> OnshapeApiClient:
    kwargs.setdefault("max_retries", 2)
    # Transport tests POST to invented document ids, which the write policy
    # rightly refuses. Turn it off here and test it deliberately in
    # TestWritePolicy below, rather than letting every test depend on the
    # contents of robot.toml.
    kwargs.setdefault("write_policy", WritePolicy(enforce=False))
    return OnshapeApiClient(ACCESS, SECRET, **kwargs)


def signature_of(headers) -> str:
    """The base64 signature out of an Authorization header.

    Accepts either our own dict (original casing) or an httpx request's headers,
    which arrive lowercased off the wire.
    """
    value = headers.get("Authorization") or headers.get("authorization")
    return value.rsplit(":", 1)[-1]


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------


class TestSigning:
    def test_golden_vector(self):
        """Pin the exact signature for a frozen date and nonce.

        Re-derived here from the documented algorithm rather than copied from
        our own output, so this catches a regression in `build_auth_headers`
        instead of merely recording whatever it currently does.
        """
        headers = build_auth_headers(
            method="GET", path=f"/api/{DEFAULT_API_VERSION}/documents",
            query="limit=1", access_key=ACCESS, secret_key=SECRET,
            date=FROZEN_DATE, nonce=FROZEN_NONCE,
        )
        expected_string = (
            f"get\n{FROZEN_NONCE}\n{FROZEN_DATE}\napplication/json\n"
            f"/api/{DEFAULT_API_VERSION}/documents\nlimit=1\n"
        ).lower()
        expected = base64.b64encode(
            hmac.new(SECRET.encode(), expected_string.encode(),
                     hashlib.sha256).digest()
        ).decode()

        assert signature_of(headers) == expected
        assert headers["Authorization"].startswith(f"On {ACCESS}:HmacSHA256:")

    def test_lowercasing_applies_to_the_signature_only(self):
        """Trap 1. The Date on the wire keeps RFC 1123 casing."""
        headers = build_auth_headers(
            method="get", path="/api/v26/documents", access_key=ACCESS,
            secret_key=SECRET, date=FROZEN_DATE, nonce=FROZEN_NONCE,
        )
        assert headers["Date"] == FROZEN_DATE
        assert headers["Date"] != FROZEN_DATE.lower()
        assert headers["On-Nonce"] == FROZEN_NONCE

    def test_method_case_does_not_change_the_signature(self):
        lower = build_auth_headers(
            method="get", path="/api/v26/documents", access_key=ACCESS,
            secret_key=SECRET, date=FROZEN_DATE, nonce=FROZEN_NONCE)
        upper = build_auth_headers(
            method="GET", path="/api/v26/documents", access_key=ACCESS,
            secret_key=SECRET, date=FROZEN_DATE, nonce=FROZEN_NONCE)
        assert signature_of(lower) == signature_of(upper)

    def test_the_signed_path_matters(self):
        a = build_auth_headers(
            method="GET", path="/api/v26/documents", access_key=ACCESS,
            secret_key=SECRET, date=FROZEN_DATE, nonce=FROZEN_NONCE)
        b = build_auth_headers(
            method="GET", path="/api/v6/documents", access_key=ACCESS,
            secret_key=SECRET, date=FROZEN_DATE, nonce=FROZEN_NONCE)
        assert signature_of(a) != signature_of(b)

    def test_nonces_are_unique_and_long_enough(self):
        """Onshape requires at least 16 alphanumeric characters."""
        nonces = {generate_nonce() for _ in range(500)}
        assert len(nonces) == 500
        for nonce in list(nonces)[:20]:
            assert len(nonce) >= 16
            assert nonce.isalnum()

    def test_build_path_always_inserts_the_version(self):
        """Trap 4. Omitting it silently selects v0, the oldest API."""
        client = make_client()
        assert client.build_path("/documents") == f"/api/{DEFAULT_API_VERSION}/documents"
        assert client.build_path("documents") == f"/api/{DEFAULT_API_VERSION}/documents"

    def test_empty_api_version_is_refused(self, monkeypatch):
        monkeypatch.setenv("ONSHAPE_ACCESS_KEY", ACCESS)
        monkeypatch.setenv("ONSHAPE_SECRET_KEY", SECRET)
        monkeypatch.setenv("ONSHAPE_API_VERSION", "   ")
        with pytest.raises(OnshapeConfigError, match="v0"):
            OnshapeApiClient.from_env()


class TestSigningOverTheWire:
    @respx.mock
    async def test_query_is_encoded_once_and_matches_the_signature(self):
        """Trap 3. The signed query must be byte-identical to the sent one.

        A space is the tell: encoded once it is `name=a+b`; if httpx re-encoded
        it we would sign one string and send another, and get a 401.
        """
        route = respx.get("https://cad.onshape.com").mock(
            return_value=httpx.Response(200, json={}))
        async with make_client() as client:
            await client.get("/documents", query={"name": "a b", "limit": 1})

        request = route.calls[0].request
        sent_query = str(request.url).split("?", 1)[1]
        assert sent_query == "name=a+b&limit=1"

        expected = base64.b64encode(hmac.new(
            SECRET.encode(),
            (f"get\n{request.headers['On-Nonce']}\n{request.headers['Date']}\n"
             f"application/json\n{request.url.path}\n{sent_query}\n").lower().encode(),
            hashlib.sha256).digest()).decode()
        assert signature_of(dict(request.headers)) == expected

    @respx.mock
    async def test_every_retry_re_signs(self):
        """Trap 5. A reused nonce or a stale Date is a 401 on the retry.

        Signing must live *inside* the retry loop. This is the test that keeps
        it there.
        """
        respx.get("https://cad.onshape.com").mock(side_effect=[
            httpx.Response(500), httpx.Response(500), httpx.Response(200, json={})])
        async with make_client() as client:
            await client.get("/documents")

        calls = respx.calls
        assert len(calls) == 3
        nonces = {c.request.headers["On-Nonce"] for c in calls}
        signatures = {signature_of(dict(c.request.headers)) for c in calls}
        assert len(nonces) == 3, "each attempt needs a fresh nonce"
        assert len(signatures) == 3, "each attempt must be signed separately"


# --------------------------------------------------------------------------
# Retry classification
# --------------------------------------------------------------------------


class TestRetryPolicy:
    @respx.mock
    async def test_auth_failure_is_never_retried(self):
        route = respx.get("https://cad.onshape.com").mock(
            return_value=httpx.Response(401))
        async with make_client() as client:
            with pytest.raises(OnshapeAuthError):
                await client.get("/documents")
        assert route.call_count == 1

    @respx.mock
    async def test_other_4xx_is_never_retried(self):
        """A 404 does not become a 200. Retrying only spends quota."""
        route = respx.get("https://cad.onshape.com").mock(
            return_value=httpx.Response(404))
        async with make_client() as client:
            with pytest.raises(OnshapeClientError):
                await client.get("/documents/d/nope/w/nope/elements")
        assert route.call_count == 1

    @respx.mock
    async def test_5xx_on_a_get_is_retried_then_surfaces(self):
        route = respx.get("https://cad.onshape.com").mock(
            return_value=httpx.Response(503))
        async with make_client(max_retries=2) as client:
            with pytest.raises(OnshapeServerError):
                await client.get("/documents")
        assert route.call_count == 3  # initial + 2 retries

    @respx.mock
    async def test_429_is_retried_and_then_typed(self):
        route = respx.get("https://cad.onshape.com").mock(
            return_value=httpx.Response(429))
        async with make_client(max_retries=1) as client:
            with pytest.raises(OnshapeRateLimitError):
                await client.get("/documents")
        assert route.call_count == 2

    @respx.mock
    async def test_retry_after_header_is_honoured(self):
        respx.get("https://cad.onshape.com").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "7"}))
        async with make_client() as client:
            delay = client._retry_delay(0, httpx.Response(
                429, headers={"Retry-After": "7"}))
        assert delay == 7.0

    async def test_backoff_grows_and_is_jittered(self):
        """Full jitter: several clients retrying must not march in lockstep."""
        async with make_client() as client:
            early = [client._retry_delay(0, None) for _ in range(50)]
            late = [client._retry_delay(4, None) for _ in range(50)]
        assert len(set(early)) > 1, "a fixed delay would synchronise retries"
        assert max(early) <= 0.5
        assert max(late) <= 8.0
        assert max(late) > max(early)

    @respx.mock
    async def test_non_idempotent_post_is_not_retried_on_5xx(self):
        """The Feature Studio write carries a sourceMicroversion.

        After a partial success that value is stale, so a blind retry would
        conflict. It is retried by re-reading first, in apply.py — never here.
        """
        route = respx.post(
            "https://cad.onshape.com").mock(return_value=httpx.Response(500))
        async with make_client() as client:
            with pytest.raises(OnshapeServerError):
                await client.post(
                    "/featurestudios/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                    "/w/bbbbbbbbbbbbbbbbbbbbbbbb/e/cccccccccccccccccccccccc",
                    json_body={"contents": "x"})
        assert route.call_count == 1

    @respx.mock
    async def test_evaluate_post_is_retried_because_it_persists_nothing(self):
        route = respx.post("https://cad.onshape.com").mock(side_effect=[
            httpx.Response(500), httpx.Response(200, json={"notices": []})])
        async with make_client() as client:
            await client.post(
                "/partstudios/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                "/w/bbbbbbbbbbbbbbbbbbbbbbbb/e/cccccccccccccccccccccccc"
                "/featurescript", json_body={"script": "f"})
        assert route.call_count == 2

    @respx.mock
    async def test_read_timeout_is_not_retried(self):
        """Measured 2026-08-07: a timed-out request still spends its quota slot.

        The server received it and is very likely still computing, so a retry
        pays a second scarce request to duplicate work already in flight.
        """
        route = respx.get("https://cad.onshape.com").mock(
            side_effect=httpx.ReadTimeout("too slow"))
        async with make_client() as client:
            with pytest.raises(OnshapeTimeoutError):
                await client.get("/documents")
        assert route.call_count == 1

    @respx.mock
    async def test_connect_error_is_retried_because_it_costs_nothing(self):
        route = respx.get("https://cad.onshape.com").mock(side_effect=[
            httpx.ConnectError("refused"), httpx.Response(200, json={})])
        async with make_client() as client:
            await client.get("/documents")
        assert route.call_count == 2


# --------------------------------------------------------------------------
# Per-endpoint timeouts
# --------------------------------------------------------------------------


class TestTimeouts:
    def test_mass_properties_gets_the_long_timeout(self):
        """The real robot's assembly mass properties returns in 40.8 s.

        A flat 30 s is what made this endpoint look broken; the fix is a longer
        timeout, not a retry.
        """
        client = make_client()
        path = client.build_path(
            "/assemblies/d/aaaaaaaaaaaaaaaaaaaaaaaa/w/bbbbbbbbbbbbbbbbbbbbbbbb"
            "/e/cccccccccccccccccccccccc/massproperties")
        assert client.timeout_for("GET", path).read == 120.0

    def test_microversion_gets_a_short_timeout(self):
        client = make_client()
        path = client.build_path(
            "/documents/d/aaaaaaaaaaaaaaaaaaaaaaaa/w/bbbbbbbbbbbbbbbbbbbbbbbb"
            "/currentmicroversion")
        assert client.timeout_for("GET", path).read == 10.0

    def test_unlisted_endpoints_get_the_default(self):
        client = make_client(timeout=17.0)
        assert client.timeout_for("GET", client.build_path("/documents")).read == 17.0

    def test_connect_timeout_stays_short_even_when_read_is_long(self):
        """A slow endpoint is normal here. A slow handshake is not."""
        client = make_client()
        path = client.build_path(
            "/assemblies/d/aaaaaaaaaaaaaaaaaaaaaaaa/w/bbbbbbbbbbbbbbbbbbbbbbbb"
            "/e/cccccccccccccccccccccccc/massproperties")
        assert client.timeout_for("GET", path).connect == 10.0


# --------------------------------------------------------------------------
# Quota ledger
# --------------------------------------------------------------------------


class TestBucketKey:
    def test_ids_collapse_and_the_version_is_stripped(self):
        assert bucket_key(
            "GET", "/api/v26/assemblies/d/8d51b49e4b833c57f7d33bf0"
                   "/w/b6722fcc07bf8d271ff14e47/e/57419d060d54790668e1b5ec"
        ) == "GET /assemblies/d/{id}/w/{id}/e/{id}"

    def test_a_version_bump_does_not_orphan_a_bucket(self):
        a = bucket_key("GET", "/api/v26/documents")
        b = bucket_key("GET", "/api/v27/documents")
        assert a == b == "GET /documents"

    def test_distinct_routes_stay_distinct(self):
        """The whole point: these have ceilings of 400 and 1000."""
        micro = bucket_key(
            "GET", "/api/v26/documents/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                   "/w/bbbbbbbbbbbbbbbbbbbbbbbb/currentmicroversion")
        asm = bucket_key(
            "GET", "/api/v26/assemblies/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                   "/w/bbbbbbbbbbbbbbbbbbbbbbbb/e/cccccccccccccccccccccccc")
        assert micro != asm

    def test_method_is_part_of_the_key(self):
        path = "/api/v26/featurestudios/d/aaaaaaaaaaaaaaaaaaaaaaaa/w/b" * 1
        assert bucket_key("GET", path) != bucket_key("POST", path)

    def test_short_hex_words_are_not_mistaken_for_ids(self):
        """`features` and `elements` are routes, not identifiers."""
        assert "{id}" not in bucket_key("GET", "/api/v26/documents")
        key = bucket_key(
            "GET", "/api/v26/partstudios/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                   "/w/bbbbbbbbbbbbbbbbbbbbbbbb/e/cccccccccccccccccccccccc/features")
        assert key.endswith("/features")


class TestQuotaLedger:
    def test_ceiling_is_learned_from_the_highest_value_seen(self):
        ledger = QuotaLedger()
        for remaining in (400, 399, 398):
            ledger.record("GET", "/api/v26/documents",
                          {"x-rate-limit-remaining": str(remaining)})
        state = ledger.buckets["GET /documents"]
        assert state.ceiling == 400
        assert state.remaining == 398
        assert state.requests == 3

    def test_buckets_are_tracked_separately(self):
        ledger = QuotaLedger()
        ledger.record("GET", "/api/v26/documents",
                      {"x-rate-limit-remaining": "1000"})
        ledger.record("GET", "/api/v26/documents/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                             "/w/bbbbbbbbbbbbbbbbbbbbbbbb/currentmicroversion",
                      {"x-rate-limit-remaining": "400"})
        assert len(ledger.buckets) == 2
        assert ledger.requests_used == 2

    def test_refuses_below_the_floor(self):
        ledger = QuotaLedger(floor=5)
        ledger.record("GET", "/api/v26/documents", {"x-rate-limit-remaining": "3"})
        with pytest.raises(OnshapeQuotaExhausted, match="reserve"):
            ledger.check("GET", "/api/v26/documents")

    def test_allows_above_the_floor(self):
        ledger = QuotaLedger(floor=5)
        ledger.record("GET", "/api/v26/documents", {"x-rate-limit-remaining": "6"})
        ledger.check("GET", "/api/v26/documents")

    def test_an_unseen_bucket_is_always_allowed(self):
        """The header is the only way to learn anything, so the first request
        through a route must never be refused."""
        QuotaLedger(floor=500).check("GET", "/api/v26/documents")

    def test_an_absent_header_teaches_nothing_rather_than_full(self):
        ledger = QuotaLedger()
        ledger.record("GET", "/api/v26/documents", {})
        assert ledger.buckets == {}
        assert ledger.requests_used == 1, "the request still happened"

    def test_an_unparseable_header_is_ignored(self):
        ledger = QuotaLedger()
        ledger.record("GET", "/api/v26/documents", {"x-rate-limit-remaining": "lots"})
        assert ledger.buckets == {}

    def test_snapshot_puts_the_emptiest_bucket_first(self):
        ledger = QuotaLedger()
        ledger.record("GET", "/api/v26/documents", {"x-rate-limit-remaining": "1000"})
        ledger.record("POST", "/api/v26/partstudios/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                              "/w/bbbbbbbbbbbbbbbbbbbbbbbb"
                              "/e/cccccccccccccccccccccccc/featurescript",
                      {"x-rate-limit-remaining": "84"})
        ledger.record("POST", "/api/v26/partstudios/d/aaaaaaaaaaaaaaaaaaaaaaaa"
                              "/w/bbbbbbbbbbbbbbbbbbbbbbbb"
                              "/e/cccccccccccccccccccccccc/featurescript",
                      {"x-rate-limit-remaining": "8"})
        assert list(ledger.snapshot())[0].startswith("POST /partstudios")

    def test_warns_once_per_crossing_not_once_per_request(self, caplog):
        ledger = QuotaLedger(warn_fraction=0.2)
        ledger.record("GET", "/api/v26/documents", {"x-rate-limit-remaining": "100"})
        with caplog.at_level("WARNING"):
            for remaining in (10, 9, 8):
                ledger.record("GET", "/api/v26/documents",
                              {"x-rate-limit-remaining": str(remaining)})
        assert len([r for r in caplog.records if "quota low" in r.message]) == 1

    def test_floor_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("FRC_QUOTA_FLOOR", "42")
        assert QuotaLedger().floor == 42

    def test_a_bad_floor_falls_back_rather_than_crashing(self, monkeypatch):
        monkeypatch.setenv("FRC_QUOTA_FLOOR", "many")
        assert QuotaLedger().floor == 5


class TestQuotaOverTheWire:
    @respx.mock
    async def test_the_client_records_quota_from_responses(self):
        respx.get("https://cad.onshape.com").mock(return_value=httpx.Response(
            200, json={}, headers={"x-rate-limit-remaining": "997"}))
        async with make_client() as client:
            await client.get("/documents")
        assert client.quota.remaining("GET", "/api/v26/documents") == 997
        assert client.quota.requests_used == 1

    @respx.mock
    async def test_quota_is_recorded_from_error_responses_too(self):
        """Verified live: a 404 still carries the header. That is exactly when
        the number matters most."""
        respx.get("https://cad.onshape.com").mock(return_value=httpx.Response(
            404, headers={"x-rate-limit-remaining": "12"}))
        async with make_client() as client:
            with pytest.raises(OnshapeClientError):
                await client.get("/documents")
        assert client.quota.remaining("GET", "/api/v26/documents") == 12

    @respx.mock
    async def test_the_governor_refuses_before_spending(self):
        route = respx.get("https://cad.onshape.com").mock(
            return_value=httpx.Response(
                200, json={}, headers={"x-rate-limit-remaining": "2"}))
        async with make_client(quota=QuotaLedger(floor=5)) as client:
            await client.get("/documents")
            with pytest.raises(OnshapeQuotaExhausted):
                await client.get("/documents")
        assert route.call_count == 1, "the refused call must not reach the wire"

    @respx.mock
    async def test_retries_are_counted_individually(self):
        """Three attempts cost three requests. A tool reporting '1' would lie."""
        respx.get("https://cad.onshape.com").mock(side_effect=[
            httpx.Response(500, headers={"x-rate-limit-remaining": "50"}),
            httpx.Response(500, headers={"x-rate-limit-remaining": "49"}),
            httpx.Response(200, json={}, headers={"x-rate-limit-remaining": "48"})])
        async with make_client(max_retries=2) as client:
            await client.get("/documents")
        assert client.quota.requests_used == 3


# --------------------------------------------------------------------------
# Cache TTL — the 400-bucket fix
# --------------------------------------------------------------------------


class TestMicroversionTTL:
    @respx.mock
    async def test_repeated_questions_share_one_validator_request(self, tmp_path):
        """The reason this exists: currentmicroversion sits on a 400 bucket and
        used to be spent once per question."""
        micro = respx.get(
            url__regex=r".*currentmicroversion").mock(return_value=httpx.Response(
                200, json={"microversion": "m1"}))
        definition = respx.get(url__regex=r".*/assemblies/.*").mock(
            return_value=httpx.Response(200, json={"rootAssembly": {}}))

        cache = AssemblyCache(tmp_path, microversion_ttl=60.0)
        async with make_client() as client:
            for _ in range(4):
                await cache.get_definition(client, "d" * 24, "w" * 24, "e" * 24)

        assert micro.call_count == 1
        assert definition.call_count == 1
        assert cache.validator_requests == 1
        assert cache.validator_requests_saved == 3

    @respx.mock
    async def test_force_refresh_bypasses_the_ttl(self, tmp_path):
        micro = respx.get(url__regex=r".*currentmicroversion").mock(
            return_value=httpx.Response(200, json={"microversion": "m1"}))
        respx.get(url__regex=r".*/assemblies/.*").mock(
            return_value=httpx.Response(200, json={"rootAssembly": {}}))

        cache = AssemblyCache(tmp_path, microversion_ttl=60.0)
        async with make_client() as client:
            await cache.get_definition(client, "d" * 24, "w" * 24, "e" * 24)
            await cache.get_definition(
                client, "d" * 24, "w" * 24, "e" * 24, force_refresh=True)
        assert micro.call_count == 2

    @respx.mock
    async def test_a_zero_ttl_disables_reuse(self, tmp_path):
        micro = respx.get(url__regex=r".*currentmicroversion").mock(
            return_value=httpx.Response(200, json={"microversion": "m1"}))
        respx.get(url__regex=r".*/assemblies/.*").mock(
            return_value=httpx.Response(200, json={"rootAssembly": {}}))

        cache = AssemblyCache(tmp_path, microversion_ttl=0.0)
        async with make_client() as client:
            await cache.get_definition(client, "d" * 24, "w" * 24, "e" * 24)
            await cache.get_definition(client, "d" * 24, "w" * 24, "e" * 24)
        assert micro.call_count == 2

    @respx.mock
    async def test_different_documents_do_not_share_a_reading(self, tmp_path):
        micro = respx.get(url__regex=r".*currentmicroversion").mock(
            return_value=httpx.Response(200, json={"microversion": "m1"}))
        respx.get(url__regex=r".*/assemblies/.*").mock(
            return_value=httpx.Response(200, json={"rootAssembly": {}}))

        cache = AssemblyCache(tmp_path)
        async with make_client() as client:
            await cache.get_definition(client, "a" * 24, "w" * 24, "e" * 24)
            await cache.get_definition(client, "b" * 24, "w" * 24, "e" * 24)
        assert micro.call_count == 2


# --------------------------------------------------------------------------
# Error hierarchy
# --------------------------------------------------------------------------


class TestErrors:
    def test_every_error_carries_actionable_advice(self):
        """A model relaying `ReadTimeout` to a student tells them nothing."""
        from src.api import errors

        for name in errors.__all__:
            cls = getattr(errors, name)
            if cls is errors.OnshapeError:
                continue
            assert cls.advice, f"{name} needs an advice line"
            assert cls("boom").explain().startswith("boom")

    def test_existing_imports_still_resolve(self):
        """Other modules import these from onshape_client. Keep that working."""
        from src.api.onshape_client import OnshapeAuthError as A
        from src.api.onshape_client import OnshapeConfigError as C
        from src.api.errors import OnshapeError

        assert issubclass(A, OnshapeError) and issubclass(C, OnshapeError)

    def test_quota_exhausted_is_distinct_from_a_real_429(self):
        """One means we stopped ourselves with headroom left; the other means
        Onshape stopped us. Different advice, so different types."""
        assert not issubclass(OnshapeQuotaExhausted, OnshapeRateLimitError)
        assert not issubclass(OnshapeRateLimitError, OnshapeQuotaExhausted)


# --------------------------------------------------------------------------
# Write policy — enforced in the client, not in a caller
# --------------------------------------------------------------------------


SCRATCH = "aaaaaaaaaaaaaaaaaaaaaaaa"
ROBOT = "bbbbbbbbbbbbbbbbbbbbbbbb"


def guarded(**kwargs) -> OnshapeApiClient:
    kwargs.setdefault("write_policy", WritePolicy(allowed=frozenset({SCRATCH})))
    return OnshapeApiClient(ACCESS, SECRET, max_retries=0, **kwargs)


class TestDocumentIdInPath:
    def test_both_spellings_of_the_path(self):
        """Onshape writes /documents/{id} and /d/{id} in different places."""
        assert document_id_in(f"/api/v26/documents/{SCRATCH}") == SCRATCH
        assert document_id_in(
            f"/api/v26/partstudios/d/{SCRATCH}/w/cccccccccccccccccccccccc") == SCRATCH

    def test_a_path_with_no_document_yields_none(self):
        assert document_id_in("/api/v26/documents") is None


class TestWritePolicy:
    @respx.mock
    async def test_a_write_outside_the_allowlist_never_reaches_the_wire(self):
        """The whole point: refused before signing, before sending."""
        route = respx.post("https://cad.onshape.com").mock(
            return_value=httpx.Response(200, json={}))
        async with guarded() as client:
            with pytest.raises(WriteRefused, match="not marked writable"):
                await client.post(f"/partstudios/d/{ROBOT}/w/x/e/y/features",
                                  json_body={})
        assert route.call_count == 0

    @respx.mock
    async def test_DELETE_is_guarded_too(self):
        """The gap this class exists to close.

        In the first real session the model issued a raw DELETE through the
        client and got 200. It happened to check a policy first, in its own
        script, voluntarily. Courtesy is not enforcement.
        """
        route = respx.delete("https://cad.onshape.com").mock(
            return_value=httpx.Response(200, json={}))
        async with guarded() as client:
            with pytest.raises(WriteRefused):
                await client.request(
                    "DELETE", f"/partstudios/d/{ROBOT}/w/x/e/y/features/featureid/z")
        assert route.call_count == 0

    @respx.mock
    async def test_writes_to_an_allowed_document_go_through(self):
        route = respx.post("https://cad.onshape.com").mock(
            return_value=httpx.Response(200, json={}))
        async with guarded() as client:
            await client.post(f"/partstudios/d/{SCRATCH}/w/x/e/y/features",
                              json_body={})
        assert route.call_count == 1

    @respx.mock
    async def test_reads_are_never_blocked(self):
        """A read-only copilot on a team's robot is the normal case."""
        route = respx.get("https://cad.onshape.com").mock(
            return_value=httpx.Response(200, json={}))
        async with guarded() as client:
            await client.get(f"/assemblies/d/{ROBOT}/w/x/e/y")
        assert route.call_count == 1

    @respx.mock
    async def test_a_path_with_no_document_is_allowed(self):
        """It cannot modify a document, so there is nothing to guard."""
        route = respx.post("https://cad.onshape.com").mock(
            return_value=httpx.Response(200, json={}))
        async with guarded() as client:
            await client.post("/some/global/endpoint", json_body={})
        assert route.call_count == 1

    def test_the_refusal_names_what_is_writable(self):
        """A refusal that does not say what would work is a dead end."""
        policy = WritePolicy(allowed=frozenset({SCRATCH}))
        with pytest.raises(WriteRefused) as caught:
            policy.check("POST", f"/api/v26/partstudios/d/{ROBOT}/w/x")
        assert SCRATCH in str(caught.value)
        assert caught.value.advice

    def test_enforce_false_is_only_a_test_escape(self):
        WritePolicy(enforce=False).check("DELETE", f"/api/v26/documents/{ROBOT}")

    def test_env_override_adds_documents(self, monkeypatch):
        monkeypatch.setenv("FRC_SCRATCH_DOCUMENT_IDS", f"{ROBOT}, {SCRATCH}")
        policy = WritePolicy.from_manifest(workspace=None)
        assert ROBOT in policy.allowed and SCRATCH in policy.allowed
