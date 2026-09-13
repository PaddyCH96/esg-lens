"""Measured facts about ``tests/fixtures/nlp_corpus.json``.

These constants exist so that 02-01 and 02-03 assert against ONE measurement instead of each
hard-coding a number from a plan document. Import them; never retype the values.

    from nlp_fixture_meta import MSFT_MULTI_DOC_CLUSTER_COUNT

WHY A CONSTANT AND NOT A LITERAL IN THE TEST
--------------------------------------------
Near-duplicate cluster counts and Jaccard values are TOKENIZER-DEPENDENT. Three independent
measurements of this same corpus during planning produced 7 clusters, 8 clusters and three
different Jaccard values for the same document pair, purely because each used a slightly
different tokenizer. The count below is therefore only meaningful together with the tokenizer
it was measured under, which is pinned in ``esg_lens.nlp.clean.content_tokens``:

    set(re.findall(r"[a-z0-9]+", title.lower()))

No stemming. No stopword removal. Applied to the RAW title, not to ``normalize_title`` output —
normalising first strips the exchange parenthetical that distinguishes two of the syndicated
re-posts and drops the measurement to 18 documents.

MEASURED 2026-09-14 against ``data/esg_lens.db`` (338 rows: AAPL 135 news + 12 sections,
MSFT 101 + 13, XOM 74 + 3), over the MSFT news subset of the exported fixture. The same
measurement over the FULL 101-row MSFT news corpus gives identical numbers, which is the
evidence that the fixture slice is representative rather than lucky.

If you change the tokenizer, the threshold, or the corpus, RE-MEASURE and update this file —
do not "fix" the failing test by editing the number in place.
"""

from __future__ import annotations

# Number of clusters containing two or more documents, over the fixture's MSFT news records.
MSFT_MULTI_DOC_CLUSTER_COUNT = 8

# Total documents covered by those clusters. This is the ROBUST invariant: all three
# independent planning measurements agreed on 19 even where they disagreed on the count.
MSFT_CLUSTERED_DOCUMENT_COUNT = 19

# Landmark names whose member documents MUST all land in the same cluster. Each pattern in
# scripts/export_nlp_fixtures.py is deliberately scoped so that its matches are exactly one
# cluster — a landmark spanning two clusters would make this assertion false and is a bug in
# the pattern, not in the clustering.
DUPLICATE_LANDMARK_GROUPS = (
    "msft_shareholder_alert_class_action_group",
    "msft_investors_lead_plain_group",
    "msft_investors_lead_schall_group",
    "msft_lost_money_join_group",
    "msft_deadline_alert_group",
    "msft_shareholders_lost_money_group",
    "msft_deadline_alert_shareholders_group",
    "msft_kahn_swick_foti_pair",
)

# Two genuinely distinct Microsoft lawsuits that must NOT merge. Their measured Jaccard varies
# 0.41-0.50 by tokenizer, so no test asserts a value — only that these two never share a cluster.
NON_MERGING_LANDMARK_PAIR = (
    "msft_wisconsin_data_center_suit",
    "msft_copilot_noise_class_actions",
)

# Title Case controversy headlines that MISS the lexicon as stored and HIT lowercased (Pitfall 1).
TITLE_CASE_CONTROVERSY_LANDMARKS = (
    "msft_shareholder_lawsuit_td_cowen",
    "msft_copilot_noise_class_actions",
    "aapl_icloud_uk_lawsuit",
    "aapl_ai_trade_secret_injunction",
    "aapl_att_uk_lawsuit",
)

# Controversy-trigger substring hit counts over the 75-record stride slice, measured 2026-09-14
# with the lex@2026-09b triggers. 02-03's differential regression test is calibrated here.
STRIDE_SLICE_SIZE = 75
STRIDE_TRIGGER_HITS_AS_STORED = 7
STRIDE_TRIGGER_HITS_LOWERCASED = 24

TICKERS = ("AAPL", "MSFT", "XOM")
NEWS_PER_TICKER = 25
