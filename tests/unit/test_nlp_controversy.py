"""NLP-06: deterministic controversy severity that actually fires on real headlines.

READ THE COMMENT ON `test_title_case_corpus_headlines_score_as_stored` BEFORE DELETING ANYTHING
HERE. There are two superficially similar tests in this file and they pin different things:

  * the FIVE LANDMARK headlines are the Pitfall 1 regression guard of record. They go through
    `severity()` on the title AS STORED, so deleting `clean.text_for_matching()` from `severity()`
    makes them fail.
  * the STORED-vs-LOWERCASED DIFFERENTIAL is a calibration guard. It lowercases its own inputs and
    calls `_severity_on_text()`, so it is insensitive to `severity()`'s own lowercasing and does
    NOT catch a Pitfall 1 regression. What it catches is a corpus refresh or a lexicon edit that
    erodes the headroom the fix depends on.

A future session that believes the differential covers Pitfall 1 will delete the landmarks and
ship a broken feature with a green suite. It does not.
"""

from __future__ import annotations

import json

import pytest

from esg_lens.nlp import controversy

# The five Title Case controversy headlines from the real corpus. Exported alongside their
# fixture landmark keys in tests/nlp_fixture_meta.py; matched here by substring so the assertion
# names the actual headline when the corpus changes under it.
#
# "Lawsuit Against Apple To Remain In State Court" is DELIBERATELY ABSENT. It was quoted in
# 02-CONTEXT.md D-2-01 from a corpus a smoke test destroyed and matches zero rows today.
LANDMARK_HEADLINES = (
    "Apple (AAPL) Faces £3 Billion UK iCloud Lawsuit As Class Action Moves Ahead",
    "Apple (AAPL) Seeks Injunction In AI Trade Secret Lawsuit",
    "Apple (AAPL) Draws New £2 Billion UK ATT Lawsuit With Wider Europe Stakes",
    "Microsoft (MSFT) Faces Shareholder Lawsuit; TD Cowen Maintains Buy Rating",
    "Microsoft (MSFT) Faces Two New Class Actions Over Copilot And Data Center Noise",
)

# Carries BOTH `lawsuit` (tier 2) and `class action` (tier 3) — one headline exercising two tiers
# and the max-wins rule.
TWO_TIER_HEADLINE = LANDMARK_HEADLINES[0]

STOCK_DROP_HEADLINE = (
    "MSFT Stock Drop: Microsoft Copilot Functionality Issues Lead to 10% Stock Drop "
    "and Securities Fraud Class Action for Investors"
)

# Floors for the differential guard. See test_lowercasing_finds_multiples_more_controversies.
MIN_DIFFERENTIAL_RATIO = 2.0
MIN_LOWERCASED_HIT_RATE = 0.25


@pytest.fixture(autouse=True)
def _fresh_matchers():
    """Every test starts from a cold matcher cache and leaves one behind."""
    controversy.reset_matchers()
    yield
    controversy.reset_matchers()


def _stored_title(nlp_corpus, needle: str) -> str:
    """The corpus title containing `needle`, AS STORED. Fails loudly if it has gone."""
    for record in nlp_corpus:
        title = record.get("title") or ""
        if needle in title:
            return title
    pytest.fail(
        f"Landmark headline absent from tests/fixtures/nlp_corpus.json: {needle!r}. "
        "Re-measure before editing this test — a missing landmark is a corpus problem, "
        "not a test problem."
    )


# ---------------------------------------------------------------------------
# Pitfall 1 — the regression guard of record
# ---------------------------------------------------------------------------
def test_title_case_corpus_headlines_score_as_stored(nlp_corpus):
    """THE Pitfall 1 regression guard.

    `PhraseMatcher(attr="LEMMA")` is case-sensitive: spaCy tags capitalised nouns PROPN and the
    lemmatizer returns the capitalised surface form, so the trigger `lawsuit` never matches
    `Lawsuit`. Measured on the live corpus: 6/101 MSFT headlines hit as stored vs 39/101
    lowercased. `severity()` lowercases internally via `clean.text_for_matching`; remove that one
    call and every assertion below fails.
    """
    for needle in LANDMARK_HEADLINES:
        title = _stored_title(nlp_corpus, needle)
        score, terms = controversy.severity(title)
        assert score >= 2, f"Title Case headline scored {score}, expected >= 2: {title!r}"
        assert terms, f"a non-zero severity must carry its matched terms: {title!r}"


def test_two_tier_headline_takes_the_maximum_tier(nlp_corpus):
    """`lawsuit` is tier 2 and `class action` is tier 3 in the same headline. Max wins."""
    title = _stored_title(nlp_corpus, TWO_TIER_HEADLINE)
    score, terms = controversy.severity(title)
    assert score == 3
    assert any("lawsuit" in t for t in terms)
    assert any("class action" in t for t in terms)


def test_lowercasing_finds_multiples_more_controversies(nlp_corpus):
    """CALIBRATION guard, NOT the Pitfall 1 regression guard.

    Both rates below are computed by this test on text it controls, through
    `_severity_on_text()`, which does not lowercase. Removing the lowercasing from `severity()`
    therefore leaves both numbers unchanged — the five landmark assertions above are what catch
    that. This test catches a corpus refresh or lexicon edit that erodes the headroom.

    A single absolute floor is NOT sufficient and an earlier draft of the plan got this wrong.
    Per-ticker stored hit rates on the live corpus are AAPL 27%, MSFT 6%, XOM 3%, so a `>= 25%`
    absolute floor alone PASSES on an AAPL-heavy slice with the lowercasing deleted and FAILS on
    an XOM-heavy slice with the fix correctly in place. The DIFFERENTIAL is what detects erosion;
    the absolute floor only stops the differential being satisfied by two near-zero numbers.
    02-01 Task 2b pins the slice at 25 records per ticker by deterministic stride precisely so
    both floors are reproducible.
    """
    news = [r for r in nlp_corpus if r["doc_type"] == "news" and r["is_stride"]]
    assert news, "the stride slice is empty — regenerate tests/fixtures/nlp_corpus.json"

    def has_hit(text: str) -> bool:
        return controversy._severity_on_text(text)[0] > 0

    stored_rate = sum(has_hit(r["title"]) for r in news) / len(news)
    lower_rate = sum(has_hit((r["title"] or "").lower()) for r in news) / len(news)

    assert lower_rate >= MIN_DIFFERENTIAL_RATIO * stored_rate, (
        f"lowercased {lower_rate:.3f} vs stored {stored_rate:.3f} — the Pitfall 1 headroom has "
        "eroded; re-measure the corpus and the lexicon before touching this floor"
    )
    assert lower_rate >= MIN_LOWERCASED_HIT_RATE, (
        f"lowercased hit rate {lower_rate:.3f} is below the measured 0.32 floor"
    )


# ---------------------------------------------------------------------------
# The negation guard
# ---------------------------------------------------------------------------
def test_negator_before_the_trigger_demotes_exactly_one_tier():
    score, terms = controversy.severity("Apple was cleared of the fraud allegations")
    assert score == 2  # `fraud` is tier 3, demoted once
    assert terms


def test_negator_after_the_trigger_demotes_exactly_one_tier():
    """THE symmetric-window test. "the lawsuit was dismissed" is the commonest real construction
    and a before-only window would return 3 here."""
    score, _ = controversy.severity("The securities fraud lawsuit was dismissed")
    assert score == 2


def test_unnegated_tier_three_stays_at_three():
    score, _ = controversy.severity("Microsoft faces securities fraud class action lawsuit")
    assert score == 3


def test_stock_price_language_never_negates_a_real_controversy():
    """Pitfall 2. Before the lex@2026-09b lexicon fix, bare `dropped` lemmatised to `drop` and
    fired on "Stock Drop", demoting both `fraud` and `class action` from 3 to 2."""
    score, _ = controversy.severity(STOCK_DROP_HEADLINE)
    assert score == 3


# ---------------------------------------------------------------------------
# Pitfall 3 — the `fine` ambiguity
# ---------------------------------------------------------------------------
def test_adjectival_fine_is_not_a_controversy():
    assert controversy.severity("The weather was fine") == (0, [])


def test_verbal_fined_still_matches():
    """`fined` tags VERB and lemmatises to `fine`, so dropping `fine` from the lexicon would not
    remove the ambiguity — the POS filter is the only fix that works."""
    score, terms = controversy.severity("Company fined for pollution violations")
    assert score == 2
    assert terms


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------
def test_matched_terms_are_a_json_array_never_a_concatenated_string():
    _, terms = controversy.severity("Microsoft faces securities fraud class action lawsuit")
    assert isinstance(terms, list)
    assert json.loads(json.dumps(terms)) == terms


def test_zero_severity_returns_an_empty_term_list():
    assert controversy.severity("Microsoft released a new keyboard") == (0, [])


# ---------------------------------------------------------------------------
# Section aggregation — MAX, never mean (scoring_methodology.md §6.1)
# ---------------------------------------------------------------------------
def test_section_takes_the_maximum_severity_and_the_union_of_terms():
    severity_, terms = controversy.section_controversy(
        [
            "The company faces a securities fraud class action.",
            "A separate lawsuit alleges pollution at the refinery.",
            "Revenue grew in the third quarter.",
        ]
    )
    assert severity_ == 3
    joined = " ".join(terms)
    assert "fraud" in joined  # from the severity-3 sentence
    assert "pollution" in joined  # from the severity-2 sentence — the UNION, not just the max


def test_section_aggregates_post_negation_severity():
    """A negated 3 must not outrank a genuine 2, so the max is taken over EFFECTIVE severity."""
    severity_, _ = controversy.section_controversy(
        [
            "The securities fraud charges were dismissed.",
            "A lawsuit alleges pollution at the refinery.",
        ]
    )
    assert severity_ == 2


def test_section_terms_are_capped_by_config():
    from esg_lens.config import get_scoring_config

    cap = int(get_scoring_config().nlp["max_controversy_terms"])
    sentences = [
        "The company faces a lawsuit, a fine, a penalty, a recall, a strike, an investigation, "
        "a data breach, layoffs, a sanction, a violation, discrimination, harassment, "
        "pollution, contamination, bribery, fraud, corruption, embezzlement, a criminal probe, "
        "a class action, an oil spill, a fatality and human trafficking."
    ] * 3
    _, terms = controversy.section_controversy(sentences)
    assert len(terms) <= cap
    assert len(terms) == len(set(terms)), "terms are deduplicated"


def test_matchers_are_built_once_across_a_fifty_sentence_call(monkeypatch):
    calls = []
    real = controversy._build_matchers

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(controversy, "_build_matchers", counting)
    controversy.reset_matchers()
    controversy.section_controversy([f"Sentence number {i} mentions a lawsuit." for i in range(50)])
    assert calls == [1]
