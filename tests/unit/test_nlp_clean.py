"""nlp/clean.py against the real corpus (02-01 Task 3).

Every duplicate assertion here runs over `tests/fixtures/nlp_corpus.json`, which is exported
from `data/esg_lens.db` — real syndicated press releases, real lawsuits, real EDGAR sections.
No invented fixtures: the failure mode this module exists to prevent (two genuinely distinct
Microsoft lawsuits collapsing into one) is only visible on text nobody wrote for a test.

Cluster counts come from `nlp_fixture_meta`, not from literals, because they are
tokenizer-dependent — see that module's docstring.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from esg_lens.config import get_scoring_config
from esg_lens.nlp import clean, registry
from nlp_fixture_meta import (
    DUPLICATE_LANDMARK_GROUPS,
    MSFT_CLUSTERED_DOCUMENT_COUNT,
    MSFT_MULTI_DOC_CLUSTER_COUNT,
    NON_MERGING_LANDMARK_PAIR,
    TITLE_CASE_CONTROVERSY_LANDMARKS,
)

THRESHOLD = float(get_scoring_config().nlp["near_duplicate_jaccard"])


@pytest.fixture()
def msft_news(nlp_corpus):
    return [r for r in nlp_corpus if r["doc_type"] == "news" and r["ticker"] == "MSFT"]


@pytest.fixture()
def by_landmark(nlp_corpus):
    index = defaultdict(list)
    for record in nlp_corpus:
        for name in record["landmarks"]:
            index[name].append(record)
    return index


@pytest.fixture()
def msft_clusters(msft_news):
    assignment = clean.cluster_near_duplicates(msft_news)
    groups = defaultdict(list)
    for doc_id, root in assignment.items():
        groups[root].append(doc_id)
    return assignment, [sorted(v) for v in groups.values() if len(v) > 1]


@pytest.fixture()
def long_filing_section(nlp_corpus):
    sections = [
        r
        for r in nlp_corpus
        if r["doc_type"] == "filing_section" and "Table of Contents" in (r["body"] or "")
    ]
    assert sections, "fixture lost its EDGAR page-furniture landmark"
    return max(sections, key=lambda r: len(r["body"]))


# ---------------------------------------------------------------------------
# Normalisation and the case contract
# ---------------------------------------------------------------------------
def test_normalize_title_strips_exchange_parenthetical_publisher_and_whitespace():
    assert clean.normalize_title("Microsoft (NASDAQ: MSFT) Faces   Suit - Reuters") == (
        "microsoft faces suit"
    )


def test_normalize_title_tolerates_empty_input():
    assert clean.normalize_title("") == ""
    assert clean.normalize_title(None) == ""


def test_text_for_matching_differs_from_display_text_on_title_case(by_landmark):
    """The contract 02-03 depends on. If these ever agree, Pitfall 1 is back."""
    for name in TITLE_CASE_CONTROVERSY_LANDMARKS:
        for record in by_landmark[name]:
            title = record["title"]
            assert clean.display_text(title) == title
            assert clean.text_for_matching(title) == title.lower()
            assert clean.text_for_matching(title) != clean.display_text(title), name


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------
def test_syndicated_reposts_score_at_or_above_the_configured_threshold(by_landmark):
    for name in DUPLICATE_LANDMARK_GROUPS:
        members = by_landmark[name]
        assert len(members) >= 2, f"{name} lost members from the corpus"
        # Connectivity, not every pair: clustering is transitive by design. In the real
        # "MSFT Shareholder Alert" group, 42~43 and 42~53 both clear 0.70 while 43~53 scores
        # 0.625 — three syndications of one press release, correctly one cluster. Asserting
        # every pair would demand a stricter property than union-find provides and than the
        # feature needs.
        for member in members:
            best = max(
                clean.jaccard(
                    clean.content_tokens(member["title"]), clean.content_tokens(other["title"])
                )
                for other in members
                if other["id"] != member["id"]
            )
            assert best >= THRESHOLD, f"{name}: {member['id']} is isolated at {best:.3f}"


def test_the_two_distinct_microsoft_lawsuits_score_below_the_threshold(by_landmark):
    """Asserts only that they stay apart — never a Jaccard VALUE.

    The measured value for this pair varies 0.41-0.50 depending on tokenizer details, which is
    precisely why a value assertion here would be a fragile test dressed up as a strong one.
    """
    left, right = (by_landmark[n][0] for n in NON_MERGING_LANDMARK_PAIR)
    score = clean.jaccard(clean.content_tokens(left["title"]), clean.content_tokens(right["title"]))
    assert score < THRESHOLD


def test_jaccard_of_an_empty_token_set_is_zero():
    assert clean.jaccard(frozenset(), clean.content_tokens("anything at all")) == 0.0


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------
def test_clustering_reproduces_the_measured_coverage_on_the_real_corpus(msft_clusters):
    _, multi = msft_clusters
    assert sum(len(c) for c in multi) == MSFT_CLUSTERED_DOCUMENT_COUNT
    assert len(multi) == MSFT_MULTI_DOC_CLUSTER_COUNT


def test_every_syndicated_landmark_group_lands_in_one_cluster(msft_clusters, by_landmark):
    assignment, _ = msft_clusters
    for name in DUPLICATE_LANDMARK_GROUPS:
        roots = {assignment[r["id"]] for r in by_landmark[name]}
        assert len(roots) == 1, f"{name} was split across {len(roots)} clusters"


def test_the_distinct_lawsuits_never_share_a_cluster(msft_clusters, by_landmark):
    assignment, _ = msft_clusters
    left, right = (by_landmark[n][0] for n in NON_MERGING_LANDMARK_PAIR)
    assert assignment[left["id"]] != assignment[right["id"]]


def test_a_filing_section_never_joins_a_news_cluster_even_at_an_identical_title():
    title = "Microsoft Corporation Securities Class Action Lawsuit Deadline Alert"
    docs = [
        {"id": 1, "ticker": "MSFT", "doc_type": "news", "title": title},
        {"id": 2, "ticker": "MSFT", "doc_type": "filing_section", "title": title},
    ]
    assignment = clean.cluster_near_duplicates(docs)
    assert assignment[1] != assignment[2]


def test_clustering_is_blocked_per_ticker():
    title = "Q3 Earnings Beat Expectations As Margins Expand Across The Portfolio"
    docs = [
        {"id": 1, "ticker": "MSFT", "doc_type": "news", "title": title},
        {"id": 2, "ticker": "AAPL", "doc_type": "news", "title": title},
    ]
    assignment = clean.cluster_near_duplicates(docs)
    assert assignment[1] != assignment[2]


def test_collapses_are_logged_with_both_titles_and_the_score(msft_news, capture_logs):
    clean.cluster_near_duplicates(msft_news)
    collapses = [r for r in capture_logs if r["event"] == "near_duplicate_collapsed"]
    assert collapses, "no collapse was logged on a corpus known to contain duplicates"
    first = collapses[0]
    assert first["title_a"] and first["title_b"]
    assert first["jaccard"] >= THRESHOLD


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
def test_source_tier_orders_wire_above_reputable_above_promotional():
    assert clean.source_tier("reuters.com") < clean.source_tier("cnbc.com")
    assert clean.source_tier("cnbc.com") < clean.source_tier("prnewswire.com")
    # Anything unlisted falls to the documented catch-all, not to the most credible tier.
    assert clean.source_tier("cnbc.com") < clean.source_tier("some-unknown-blog.example")


def test_pick_survivor_prefers_the_more_credible_source():
    docs = [
        {"id": 10, "domain": "prnewswire.com", "source": "rss", "published_at": "2026-01-01"},
        {"id": 11, "domain": "cnbc.com", "source": "rss", "published_at": "2026-01-02"},
    ]
    assert clean.pick_survivor(docs) == 11


def test_pick_survivor_breaks_ties_by_earliest_then_lowest_id():
    docs = [
        {"id": 30, "domain": "cnbc.com", "source": "rss", "published_at": "2026-01-02"},
        {"id": 20, "domain": "cnbc.com", "source": "rss", "published_at": "2026-01-01"},
        {"id": 19, "domain": "cnbc.com", "source": "rss", "published_at": "2026-01-01"},
    ]
    assert clean.pick_survivor(docs) == 19


def test_pick_survivor_is_deterministic_under_input_reordering():
    """Without the id tiebreak, two runs over one cluster can retain different documents and
    Phase 3's scores move for no reason."""
    docs = [
        {"id": i, "domain": "prnewswire.com", "source": "rss", "published_at": "2026-01-01"}
        for i in (7, 3, 9, 1, 5)
    ]
    expected = clean.pick_survivor(docs)
    for shift in range(len(docs)):
        rotated = docs[shift:] + docs[:shift]
        assert clean.pick_survivor(rotated) == expected
    assert clean.pick_survivor(list(reversed(docs))) == expected


# ---------------------------------------------------------------------------
# Boilerplate
# ---------------------------------------------------------------------------
def test_strip_boilerplate_removes_edgar_page_furniture_from_a_real_filing(long_filing_section):
    body = long_filing_section["body"]
    cleaned = clean.strip_boilerplate(body)
    assert "Table of Contents" in body
    assert "Table of Contents" not in cleaned
    # The substantive text either side survives intact.
    assert "DEF 14A" in cleaned
    assert len(cleaned) > len(body) * 0.5


def test_strip_boilerplate_is_a_fixed_point(long_filing_section):
    once = clean.strip_boilerplate(long_filing_section["body"])
    assert clean.strip_boilerplate(once) == once


def test_strip_boilerplate_drops_rss_tails_but_keeps_the_article():
    text = (
        "Microsoft faces a class action over data center noise in Wisconsin.\n"
        "Residents say the turbines run through the night.\n"
        "Continue reading...\n"
        "Read more at example.com\n"
        "Copyright 2026 Example Media\n"
        "All rights reserved\n"
    )
    cleaned = clean.strip_boilerplate(text)
    assert "class action over data center noise" in cleaned
    assert "Residents say the turbines" in cleaned
    for tail in ("Continue reading", "Read more at", "Copyright", "All rights reserved"):
        assert tail not in cleaned


def test_strip_boilerplate_tolerates_empty_input():
    assert clean.strip_boilerplate("") == ""
    assert clean.strip_boilerplate(None) == ""


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------
def test_split_sentences_returns_multiple_sentences_from_a_real_edgar_section(
    spacy_nlp, long_filing_section
):
    sentences = clean.split_sentences(long_filing_section["body"], long_filing_section["id"])
    assert len(sentences) > 1
    cfg = get_scoring_config().nlp
    assert all(len(s) >= int(cfg["min_sentence_chars"]) for s in sentences)
    assert len(sentences) <= int(cfg["max_sentences_per_section"])


def test_split_sentences_caps_a_pathological_section_and_logs_the_original_count(
    spacy_nlp, capture_logs
):
    cap = int(get_scoring_config().nlp["max_sentences_per_section"])
    synthetic = " ".join(
        f"The company disclosed a material governance finding numbered {i} during the period."
        for i in range(500)
    )
    sentences = clean.split_sentences(synthetic, document_id=4242)

    assert len(sentences) == cap
    events = [r for r in capture_logs if r["event"] == "filing_section_truncated"]
    assert events, "truncation must be logged — a silent cap is an invisible data loss"
    assert events[0]["sentences_found"] == 500
    assert events[0]["sentences_kept"] == cap
    assert events[0]["document_id"] == 4242


def test_split_sentences_takes_the_first_n_not_a_sample(spacy_nlp):
    """NLP-07's contract is that re-running at the same model_version is a no-op."""
    cap = int(get_scoring_config().nlp["max_sentences_per_section"])
    synthetic = " ".join(
        f"The company disclosed a material governance finding numbered {i} during the period."
        for i in range(cap + 50)
    )
    first = clean.split_sentences(synthetic)
    assert first == clean.split_sentences(synthetic)
    assert "numbered 0 " in first[0]


def test_split_sentences_drops_degenerate_table_fragments(spacy_nlp):
    cfg = get_scoring_config().nlp
    text = (
        "| 1,234 | 5,678 | 9,012 |\n"
        "12.4\n"
        "The Company recorded a material weakness in internal control over financial reporting.\n"
    )
    sentences = clean.split_sentences(text)
    assert any("material weakness" in s for s in sentences)
    assert not any(s.startswith("|") for s in sentences)
    assert all(len(s) >= int(cfg["min_sentence_chars"]) for s in sentences)


def test_split_sentences_raises_rather_than_degrading_when_spacy_is_unavailable(monkeypatch):
    """No `text.split(". ")` fallback. A silent degrade makes a green suite meaningless."""

    def _explode():
        raise OSError("en_core_web_sm not found")

    monkeypatch.setattr(registry, "get_spacy", _explode)
    with pytest.raises(OSError):
        clean.split_sentences("The Company disclosed a material governance finding this period.")


def test_split_sentences_returns_empty_for_empty_input():
    assert clean.split_sentences("") == []
