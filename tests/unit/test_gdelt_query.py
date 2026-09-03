"""Unit tests for GDELT query construction D-01..D-03 and normalization — no network."""

import pytest
import structlog


from esg_lens.collectors.gdelt import (
    build_gdelt_queries,
    filtered_aliases,
    normalize_domain,
    parse_seendate,
    _load_esg_terms,
)


def test_filtered_aliases_drops_short_and_stoplist():
    # len <=4 should be dropped
    assert filtered_aliases(["AB", "ABC", "ABCD"]) == []
    assert filtered_aliases(["A", "Inc"]) == []
    # stoplist inc/corp should be dropped even if longer? inc is 3 so already dropped, test Holdings (8 chars but stoplist)
    assert "Holdings" not in filtered_aliases(["Holdings", "Apple Inc"])
    assert "holdings" not in [x.lower().strip('"').lower() for x in filtered_aliases(["holdings", "Apple"])]
    # inc and corp variants
    result = filtered_aliases(["Inc", "Corp", "Apple", "Apple Inc"])
    # Inc and Corp dropped, Apple kept (len 5), Apple Inc quoted kept
    assert '"Apple Inc"' in result
    assert "Apple" in result
    assert "Inc" not in result
    assert "Corp" not in result
    # exact stoplist words with punctuation
    assert filtered_aliases(["Inc.", "holdings"]) == []


def test_filtered_aliases_quoting_multiword():
    result = filtered_aliases(["Apple Inc", "Exxon Mobil", "Microsoft"])
    assert '"Apple Inc"' in result
    assert '"Exxon Mobil"' in result
    assert "Microsoft" in result
    # single word not quoted
    assert '"Microsoft"' not in result
    # ensure multi-word wrapped in double quotes
    for r in result:
        if " " in r.strip('"'):
            assert r.startswith('"') and r.endswith('"')


def test_build_gdelt_queries_single_when_under_400():
    aliases = ["Apple Inc"]
    esg_terms = ["Climate Change", "oil spill"]
    queries = build_gdelt_queries(aliases, esg_terms, max_chars=400)
    assert len(queries) == 1
    q = queries[0]
    # alias OR-group plus ESG bundle inside parentheses
    assert '"Apple Inc"' in q
    assert "Climate Change" in q or '"Climate Change"' in q
    assert q.startswith("(") and q.endswith(")")
    # raw length under 400
    assert len(q) <= 400


def test_build_gdelt_queries_chunk_at_400_produces_two_and_warning(caplog=None):
    # Use long bundle to exceed 400
    aliases = ["Apple Inc", "Microsoft Corporation"]
    esg_terms = [f"term{i} phrase" for i in range(30)]  # each ~14 chars + OR => >400
    # Capture structlog warning via structlog testing if available, else just check length logic
    import structlog
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        queries = build_gdelt_queries(aliases, esg_terms, max_chars=400)
    assert len(queries) == 2
    # each query should contain alias_group
    for q in queries:
        assert '"Apple Inc"' in q or "Apple Inc" in q
        # quoted multi-word terms
        assert '"' in q
    # warning logged with gdelt_query_chunked
    assert any("gdelt_query_chunked" in str(log.get("event", "")) or "gdelt_query_chunked" in str(log) for log in logs)
    # also verify raw_length/chars and query count present
    warn_logs = [l for l in logs if l.get("event") == "gdelt_query_chunked"]
    assert len(warn_logs) >= 1
    assert warn_logs[0].get("queries") == 2 or warn_logs[0].get("query_count") == 2
    assert warn_logs[0].get("chars") is not None or warn_logs[0].get("raw_length") is not None


def test_bundle_contains_controversy_lexicon_terms():
    terms = _load_esg_terms()
    # Must contain categories
    assert "Climate Change" in terms
    assert "Corporate Governance" in terms
    # Must contain controversy lexicon triggers from tiers 1-3
    for expected in ["oil spill", "bribery", "fraud", "fine", "lawsuit", "criticized", "alleged", "scrutiny", "pollution"]:
        assert any(expected.lower() in t.lower() for t in terms), f"missing {expected}"
    # Lexicon tiers: severe/material/minor all represented
    assert any("child labor" in t.lower() for t in terms)
    assert any("data breach" in t.lower() for t in terms)
    assert any("downgrade" in t.lower() for t in terms)


def test_domain_normalization_strips_www_and_lowercases():
    assert normalize_domain("https://www.reuters.com/business/apple") == "reuters.com"
    assert normalize_domain("https://WWW.BLOOMBERG.COM/news") == "bloomberg.com"
    assert normalize_domain("https://www.theguardian.com/business") == "theguardian.com"
    assert normalize_domain("https://reuters.com") == "reuters.com"
    assert normalize_domain("http://BBC.COM/path") == "bbc.com"
    assert normalize_domain(None) is None
    assert normalize_domain("") is None


def test_parse_seendate_to_iso():
    # GDELT seendate format %Y%m%dT%H%M%SZ
    assert parse_seendate("20260903T091200Z") == "2026-09-03T09:12:00Z"
    assert parse_seendate("20260902T143000Z") == "2026-09-02T14:30:00Z"
    # ISO variant
    assert parse_seendate("2026-09-03T09:12:00Z") == "2026-09-03T09:12:00Z"
    assert parse_seendate(None) is None
    # published_at should end with Z
    assert parse_seendate("20260901T080000Z").endswith("Z")


def test_quoted_bundle_terms():
    # multi-word bundle terms should be quoted
    aliases = ["Apple"]
    esg_terms = ["oil spill", "Climate Change", "fine"]
    queries = build_gdelt_queries(aliases, esg_terms, max_chars=400)
    q = queries[0]
    assert '"oil spill"' in q
    assert '"Climate Change"' in q
    # single word not quoted
    assert "fine" in q
    # ensure not double-quoted single
    assert '"fine"' not in q


def test_filtered_aliases_contains_len_and_stoplist_logic():
    import pathlib

    src = pathlib.Path("src/esg_lens/collectors/gdelt.py").read_text()
    assert "filtered_aliases" in src
    assert "build_gdelt_queries" in src
    # D-02 filtering len <=4
    assert "len" in src and "<=4" in src or "<= 4" in src
    assert "stoplist" in src.lower() or "STOPLIST" in src
    # D-03 400 char chunk
    assert "400" in src
    assert "gdelt_query_chunked" in src


def test_gdelt_uses_get_http_client_and_collector():
    import pathlib

    src = pathlib.Path("src/esg_lens/collectors/gdelt.py").read_text()
    assert "get_http_client" in src
    assert "class GdeltCollector" in src
    assert "Collector" in src
    # must not create own httpx client
    assert "httpx.AsyncClient" not in src
    assert "httpx.Client" not in src
    # query construction checks
    assert "mode" in src and "artlist" in src
    assert "format" in src and "json" in src
    assert "maxrecords" in src and "250" in src
    # domain normalization via urlparse hostname lower and www.
    assert "urlparse" in src
    assert "hostname" in src
    assert "www." in src
    # seendate parsing format
    assert "%Y%m%dT%H%M%SZ" in src
    # content_hash import
    assert "content_hash" in src
