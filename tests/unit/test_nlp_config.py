"""Phase 2 config surface — tau as a per-doc-type mapping, the nlp block, and the lexicon bump.

These tests assert on LOADED VALUES, never on the text of a YAML or .py file. The Phase 1 gap
G-4 was a suite of tests that grepped source files and stayed green while the code was broken.
"""

from __future__ import annotations

import copy

import pytest

from esg_lens.config import ScoringConfig, get_scoring_config, load_yaml


@pytest.fixture()
def cfg():
    return get_scoring_config()


# ---------------------------------------------------------------------------
# tau — D-2-01
# ---------------------------------------------------------------------------


def test_tau_is_a_per_doc_type_mapping(cfg):
    """A uniform tau=0.60 dropped genuine ESG lawsuits scoring 0.45/0.48, because FinBERT-ESG was
    trained on report sentences and headlines score systematically lower."""
    tau = cfg.tau
    assert isinstance(tau, dict)
    assert set(tau) >= {"news", "filing_section", "press_release"}
    assert tau["news"] == 0.45
    assert tau["filing_section"] == 0.60


def test_nlp_block_carries_every_phase_2_threshold(cfg):
    nlp = cfg.nlp
    for key in (
        "batch_size",
        "near_duplicate_jaccard",
        "max_sentences_per_section",
        "min_sentence_chars",
        "sentiment_label_band",
        "min_aliases_for_entity_gate",
        "max_controversy_terms",
        "entity_match_ratio",
    ):
        assert key in nlp, f"nlp.{key} missing from scoring.yaml"
    assert nlp["near_duplicate_jaccard"] == 0.70
    assert nlp["entity_match_ratio"] == 0.85
    assert nlp["batch_size"] == 16


def test_model_version_carries_the_bumped_lexicon(cfg):
    """NLP-07's no-op contract keys on model_version, so a lexicon change that does not bump it
    leaves every existing signal row stale and unreprocessed."""
    assert "lex@2026-09b" in cfg.scoring["model_version"]


# ---------------------------------------------------------------------------
# Validation — the guard that stops D-2-01 being silently reverted
# ---------------------------------------------------------------------------


def test_validate_rejects_the_old_scalar_tau(cfg):
    raw = copy.deepcopy(cfg.raw)
    raw["tau"] = 0.60
    with pytest.raises(ValueError, match="tau"):
        ScoringConfig._validate(raw)


def test_validate_rejects_tau_missing_a_doc_type(cfg):
    raw = copy.deepcopy(cfg.raw)
    del raw["tau"]["filing_section"]
    with pytest.raises(ValueError, match="filing_section"):
        ScoringConfig._validate(raw)


@pytest.mark.parametrize("key", ["batch_size", "near_duplicate_jaccard", "entity_match_ratio"])
def test_validate_names_the_missing_nlp_key(cfg, key):
    """Failing loudly WITH THE KEY NAME is the point: a KeyError three layers into an inference
    loop is not a usable diagnostic."""
    raw = copy.deepcopy(cfg.raw)
    del raw["nlp"][key]
    with pytest.raises(ValueError, match=key):
        ScoringConfig._validate(raw)


def test_validate_rejects_a_missing_nlp_block(cfg):
    raw = copy.deepcopy(cfg.raw)
    del raw["nlp"]
    with pytest.raises(ValueError, match="nlp"):
        ScoringConfig._validate(raw)


# ---------------------------------------------------------------------------
# Lexicon — Pitfall 2
# ---------------------------------------------------------------------------


def test_lexicon_version_is_bumped():
    assert load_yaml("controversy_lexicon")["version"] == "lex@2026-09b"


def test_bare_dropped_is_gone_but_constrained_forms_remain():
    """Bare `dropped` lemmatises to `drop` and fired on "MSFT Stock Drop: ... Class Action",
    demoting a tier-3 controversy on a price-movement headline."""
    phrases = load_yaml("controversy_lexicon")["negation_guard"]["phrases"]
    assert "dropped" not in phrases
    assert "charges dropped" in phrases
    assert "lawsuit dropped" in phrases


def test_fine_stays_in_tier_2():
    """02-03 disambiguates `fine` with a POS filter. Removing it here would lose real penalties."""
    assert "fine" in load_yaml("controversy_lexicon")["tiers"][2]["triggers"]


# ---------------------------------------------------------------------------
# Per-feed entity-gate trust — D-2-03
# ---------------------------------------------------------------------------


def test_entity_gate_trust_is_declared_per_feed():
    feeds = load_yaml("rss_feeds")["feeds"]
    assert feeds["google_news"]["entity_gate"] == "apply"
    assert feeds["yahoo_ticker"]["entity_gate"] == "skip"
