"""The entity gate (NLP-04, D-2-03, 02-02 Task 2).

Every alias in these tests comes from `alias_seed.seed_from_edgar_cache` against the REAL cached
SEC file, so the gate is exercised against exactly the aliases production has — not against a
convenient invention. spaCy is the REAL en_core_web_sm for the same reason.

These tests never read a source file (G-4).
"""

from __future__ import annotations

import json

import pytest

from esg_lens.config import get_scoring_config
from esg_lens.nlp import alias_seed, entity, registry

# Real corpus landmarks (tests/fixtures/nlp_corpus.json). Referenced by landmark name so a
# corpus refresh that renumbers ids does not silently change what is under test.
CASED_NER_LANDMARK = "msft_shareholder_lawsuit_td_cowen"

# Synthetic-but-realistic sentences whose spaCy ORG spans were VERIFIED on 2026-09-14 with
# en_core_web_sm 3.7.x. Headline fragments are NOT used here: spaCy tags
# "Microsft Corp Faces Shareholder Suit" as one ORG span covering half the headline, which would
# make the ratio assertion a test of NER chunking rather than of the match rule.
TYPO_ORG_TITLE = "Shareholders sued Microsft Corp over data center emissions."  # ORG 'Microsft Corp'
NEAR_MISS_TITLE = "Investors sued Micron over a memory chip pricing scheme."  # ORG 'Micron'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def gated_conn(signals_conn):
    """The fixture corpus PLUS real EDGAR-seeded aliases."""
    alias_seed.seed_from_edgar_cache(signals_conn)
    return signals_conn


@pytest.fixture()
def msft_aliases(gated_conn):
    return entity.load_aliases(gated_conn, "MSFT")


@pytest.fixture()
def feeds_cfg():
    from esg_lens.config import load_yaml

    return load_yaml("rss_feeds")


def _doc(corpus, landmark=None, doc_id=None):
    for record in corpus:
        if landmark is not None and landmark in record["landmarks"]:
            return dict(record)
        if doc_id is not None and record["id"] == doc_id:
            return dict(record)
    pytest.fail(f"no corpus record for landmark={landmark!r} id={doc_id!r}")


def _first(corpus, **match):
    for record in corpus:
        raw = json.loads(record["raw_json"] or "{}")
        merged = {**record, "_rss_feed": raw.get("_rss_feed")}
        if all(merged.get(k) == v for k, v in match.items()):
            return dict(record)
    pytest.fail(f"no corpus record matching {match}")


def _news_doc(ticker, title, feed="google_news", doc_id=9001):
    return {
        "id": doc_id,
        "ticker": ticker,
        "source": "rss",
        "doc_type": "news",
        "title": title,
        "body": None,
        "raw_json": json.dumps({"_rss_feed": feed}) if feed else None,
    }


# ---------------------------------------------------------------------------
# load_aliases / usable_aliases
# ---------------------------------------------------------------------------
def test_load_aliases_includes_the_company_name_and_the_alias_rows(msft_aliases):
    lowered = {a.lower() for a in msft_aliases}
    assert "microsoft corp" in lowered  # the seeded SEC legal title (stored ALL-CAPS)
    assert "microsoft" in lowered
    assert "msft" in lowered
    assert "microsoft corporation" in lowered  # companies.name


def test_load_aliases_deduplicates_case_insensitively(gated_conn):
    gated_conn.execute(
        "INSERT OR IGNORE INTO company_aliases (ticker, alias, alias_type) VALUES (?, ?, ?)",
        ("MSFT", "microsoft", "common"),
    )
    gated_conn.commit()

    lowered = [a.lower() for a in entity.load_aliases(gated_conn, "MSFT")]

    assert lowered.count("microsoft") == 1


def test_the_bare_ticker_is_not_a_usable_alias(msft_aliases):
    """If the ticker counted, the fail-open check could never fire: every document collected
    under a ticker trivially carries it."""
    usable = {a.lower() for a in entity.usable_aliases(msft_aliases, "MSFT")}

    assert "msft" not in usable
    assert "microsoft" in usable


# ---------------------------------------------------------------------------
# D-2-03: per-source trust
# ---------------------------------------------------------------------------
def test_edgar_documents_bypass_the_gate_without_loading_spacy(
    gated_conn, msft_aliases, feeds_cfg, nlp_corpus, monkeypatch
):
    """Identity is definitional for a filing: the company filed it."""
    monkeypatch.setattr(
        registry, "get_spacy", lambda: pytest.fail("the gate must not load spaCy for EDGAR")
    )
    doc = _first(nlp_corpus, source="edgar")

    result = entity.gate_decision(doc, msft_aliases, feeds_cfg)

    assert result.admit is True
    assert result.reason is None
    assert result.gate_skipped == "edgar"


def test_a_trusted_feed_bypasses_the_gate_without_loading_spacy(
    msft_aliases, feeds_cfg, nlp_corpus, monkeypatch
):
    """Real case from the corpus: a yahoo_ticker document filed under MSFT whose headline is
    'Nvidia vs. AMD: Elon Musk Picked a Side on the SpaceX Earnings Call'. It names no Microsoft
    alias whatsoever, and rejecting it would discard signal Yahoo has already tied to the ticker.
    """
    doc = _first(nlp_corpus, _rss_feed="yahoo_ticker", ticker="MSFT")
    monkeypatch.setattr(
        registry, "get_spacy", lambda: pytest.fail("the gate must not load spaCy for a trusted feed")
    )

    result = entity.gate_decision(doc, msft_aliases, feeds_cfg)

    assert result.admit is True
    assert result.reason is None
    assert result.gate_skipped == "trusted_feed"


def test_trust_is_read_from_config_not_hard_coded(msft_aliases, feeds_cfg, nlp_corpus):
    """Flipping google_news to `skip` in the config must flip the gate's behaviour. If this
    passes while a feed name is hard-coded in entity.py, the hard-coded name is dead code that
    disagrees with config — which is how the two silently diverge."""
    doc = _news_doc("MSFT", "Investors sued Micron over a memory chip pricing scheme.")
    flipped = json.loads(json.dumps(feeds_cfg))
    flipped["feeds"]["google_news"]["entity_gate"] = "skip"

    assert entity.gate_decision(doc, msft_aliases, feeds_cfg).admit is False
    assert entity.gate_decision(doc, msft_aliases, flipped).gate_skipped == "trusted_feed"


@pytest.mark.parametrize("raw_json", [None, "{}", '{"_rss_feed": "some_new_feed"}', "{not json"])
def test_an_unknown_or_missing_feed_defaults_to_applying_the_gate(msft_aliases, feeds_cfg, raw_json):
    """The asymmetry is deliberate (T-02-10): an unknown feed must not inherit `skip`."""
    doc = _news_doc("MSFT", NEAR_MISS_TITLE)
    doc["raw_json"] = raw_json

    result = entity.gate_decision(doc, msft_aliases, feeds_cfg)

    assert result.gate_skipped is None
    assert result.admit is False
    assert result.reason == "entity_mismatch"


# ---------------------------------------------------------------------------
# The match rule: containment OR SequenceMatcher ratio, best alias
# ---------------------------------------------------------------------------
def test_a_google_news_headline_naming_the_company_is_admitted(
    msft_aliases, feeds_cfg, nlp_corpus
):
    doc = _doc(nlp_corpus, landmark=CASED_NER_LANDMARK)

    result = entity.gate_decision(doc, msft_aliases, feeds_cfg)

    assert result.admit is True
    assert result.reason is None
    assert result.entities


def test_containment_alone_admits_without_needing_an_org_entity(msft_aliases, feeds_cfg):
    """Clause (i) is independent of NER. Lowercased, unpunctuated text that spaCy will not tag
    as an ORG still contains the alias, and that is enough."""
    doc = _news_doc("MSFT", "shares of microsoft slid after the emissions report")

    result = entity.gate_decision(doc, msft_aliases, feeds_cfg)

    assert result.admit is True
    assert result.matched_alias is not None
    assert result.match_kind == "containment"


def test_a_misspelled_org_admits_via_the_configured_ratio(msft_aliases, feeds_cfg):
    """Clause (ii), independent of clause (i): 'Microsft' contains no alias as a substring."""
    doc = _news_doc("MSFT", TYPO_ORG_TITLE)

    result = entity.gate_decision(doc, msft_aliases, feeds_cfg)

    assert result.admit is True
    assert result.match_kind == "ratio"
    assert result.match_ratio >= get_scoring_config().nlp["entity_match_ratio"]


def test_the_ratio_is_taken_against_the_best_alias_not_the_first(msft_aliases):
    """'Microsft Corp' scores 0.963 against the seeded legal alias but only 0.727 against the
    short 'Microsoft'. A first-match-wins implementation rejects it; `max` admits it.

    The values are asserted so that a config change able to flip this outcome is visible in the
    diff rather than discovered in production."""
    best = entity.best_alias_match("Microsft Corp", msft_aliases)
    short_only = entity.best_alias_match("Microsft Corp", ["Microsoft"])

    assert round(best.ratio, 3) == 0.963
    assert best.alias.lower() == "microsoft corp"
    assert round(short_only.ratio, 3) == 0.727
    assert short_only.ratio < get_scoring_config().nlp["entity_match_ratio"] <= best.ratio


def test_a_near_miss_company_is_rejected(msft_aliases, feeds_cfg):
    """Micron and Microsoft are genuinely different companies. A gate that cannot tell them
    apart is not a gate. 'micron' scores 0.667 against 'microsoft' — the highest of any MSFT
    alias — and is a substring of none of them."""
    doc = _news_doc("MSFT", NEAR_MISS_TITLE)

    result = entity.gate_decision(doc, msft_aliases, feeds_cfg)
    best = entity.best_alias_match("Micron", msft_aliases)

    assert result.admit is False
    assert result.reason == "entity_mismatch"
    assert round(best.ratio, 3) == 0.667
    assert best.ratio < get_scoring_config().nlp["entity_match_ratio"]


def test_a_real_headline_about_another_company_is_rejected(msft_aliases, feeds_cfg, nlp_corpus):
    """A genuine AAPL google_news headline, filed under MSFT. This is the spoofing case T-02-09
    exists for."""
    doc = _doc(nlp_corpus, doc_id=115)
    doc["ticker"] = "MSFT"

    result = entity.gate_decision(doc, msft_aliases, feeds_cfg)

    assert result.admit is False
    assert result.reason == "entity_mismatch"


def test_the_match_ratio_floor_comes_from_config_not_from_a_literal(
    msft_aliases, feeds_cfg, monkeypatch
):
    """Lowering the configured floor below 0.667 must admit Micron. If it does not, the
    threshold in entity.py is not the configured one."""
    doc = _news_doc("MSFT", NEAR_MISS_TITLE)
    cfg = get_scoring_config()
    monkeypatch.setitem(cfg.nlp, "entity_match_ratio", 0.5)

    assert entity.gate_decision(doc, msft_aliases, feeds_cfg).admit is True


# ---------------------------------------------------------------------------
# Fail open, LOUDLY
# ---------------------------------------------------------------------------
def test_a_ticker_with_no_usable_aliases_is_admitted_marked_and_warned(feeds_cfg, capture_logs):
    """Failing CLOSED here would reject 100% of google_news for that ticker and produce a
    pipeline that runs clean and finds nothing — the Phase 1 failure mode. Admitting is only
    defensible because the marker and the warning make the blind spot auditable (A5/T-02-09).
    A version that admits silently must fail this test."""
    doc = _news_doc("MSFT", NEAR_MISS_TITLE, doc_id=4242)

    result = entity.gate_decision(doc, ["MSFT"], feeds_cfg)

    assert result.admit is True
    assert result.reason is None
    assert result.gate_skipped == "no_aliases"

    marker = json.loads(result.entities_json)
    assert marker["gate_skipped_no_aliases"] is True

    warnings = [r for r in capture_logs if r["event"] == "entity_gate_skipped_no_aliases"]
    assert warnings, "the gate admitted with no aliases and said nothing"
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["ticker"] == "MSFT"
    assert warnings[0]["document_id"] == 4242


def test_an_empty_alias_list_also_fails_open_loudly(feeds_cfg, capture_logs):
    result = entity.gate_decision(_news_doc("MSFT", NEAR_MISS_TITLE), [], feeds_cfg)

    assert result.admit is True
    assert result.gate_skipped == "no_aliases"
    assert any(r["event"] == "entity_gate_skipped_no_aliases" for r in capture_logs)


def test_the_no_alias_check_is_driven_by_config(msft_aliases, feeds_cfg, monkeypatch):
    """Raising min_aliases_for_entity_gate above the real alias count must fail the gate open."""
    cfg = get_scoring_config()
    monkeypatch.setitem(cfg.nlp, "min_aliases_for_entity_gate", len(msft_aliases) + 10)

    result = entity.gate_decision(_news_doc("MSFT", NEAR_MISS_TITLE), msft_aliases, feeds_cfg)

    assert result.gate_skipped == "no_aliases"


def test_a_normal_decision_carries_no_no_alias_marker(msft_aliases, feeds_cfg, nlp_corpus):
    doc = _doc(nlp_corpus, landmark=CASED_NER_LANDMARK)

    marker = json.loads(entity.gate_decision(doc, msft_aliases, feeds_cfg).entities_json)

    assert "gate_skipped_no_aliases" not in marker


# ---------------------------------------------------------------------------
# The gate reads CASED text — the regression that must fail if someone "optimises"
# ---------------------------------------------------------------------------
def test_ner_on_cased_text_finds_more_orgs_than_on_lowercased_text(nlp_corpus, spacy_nlp):
    """en_core_web_sm's NER depends heavily on capitalisation. 02-03 runs a SECOND, LOWERCASED
    spaCy pass for lemma-based controversy matching; merging the two passes to 'save' ~3ms would
    silently degrade this one. Measured on a real corpus headline: 3 ORG spans cased, 1 lowered.
    """
    title = _doc(nlp_corpus, landmark=CASED_NER_LANDMARK)["title"]

    cased = [e for e in spacy_nlp(title).ents if e.label_ == "ORG"]
    lowered = [e for e in spacy_nlp(title.lower()).ents if e.label_ == "ORG"]

    assert len(cased) > len(lowered)


def test_the_gate_extracts_the_cased_org_spans(nlp_corpus, msft_aliases, feeds_cfg):
    """Proves the gate itself uses the cased text, not just that spaCy can."""
    doc = _doc(nlp_corpus, landmark=CASED_NER_LANDMARK)

    entities = entity.gate_decision(doc, msft_aliases, feeds_cfg).entities

    assert any(e != e.lower() for e in entities), f"every ORG span was lowercased: {entities}"


# ---------------------------------------------------------------------------
# Never-raise, no writes
# ---------------------------------------------------------------------------
def test_a_document_with_no_title_does_not_raise(msft_aliases, feeds_cfg):
    doc = _news_doc("MSFT", None)

    result = entity.gate_decision(doc, msft_aliases, feeds_cfg)

    assert result.admit is False
    assert result.reason == "entity_mismatch"


def test_the_gate_writes_nothing(gated_conn, msft_aliases, feeds_cfg, nlp_corpus):
    """Persistence belongs to 02-03's pipeline.py. The gate returns decisions."""
    before = gated_conn.execute("SELECT COUNT(*) FROM esg_signals").fetchone()[0]

    entity.gate_decision(_doc(nlp_corpus, landmark=CASED_NER_LANDMARK), msft_aliases, feeds_cfg)

    assert gated_conn.execute("SELECT COUNT(*) FROM esg_signals").fetchone()[0] == before
