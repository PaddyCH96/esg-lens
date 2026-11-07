"""Score a stored document window and preserve the exact evidence used."""

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from esg_lens.nlp.pipeline import signal_version, source_tier
from esg_lens.scoring.aggregate import pillar_penalty, pillar_score
from esg_lens.scoring.composite import composite_score, confidence, contribution
from esg_lens.scoring.weights import evidence_weight, recency_weight, scoring_weight


@dataclass(frozen=True)
class WeightedSignal:
    w_src: float
    w_rec: float
    sentiment: float
    controversy: int
    w_ev: float
    w: float


def store_score(conn, ticker, documents, start, end, cfg, job_id, sources):
    company = conn.execute("SELECT * FROM companies WHERE ticker = ?", (ticker,)).fetchone()
    version = signal_version(cfg)
    evidence, members = [], {pillar: [] for pillar in "ESG"}
    counts = Counter(doc["source"] for doc in documents)
    for doc in documents:
        signal = conn.execute(
            "SELECT * FROM esg_signals WHERE document_id = ? AND model_version = ? AND included = 1",
            (doc["id"], version),
        ).fetchone()
        if not signal:
            continue
        published = datetime.fromisoformat(doc["published_at"])
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        recent = recency_weight(max(0, (end - published).total_seconds()) / 86400,
                                cfg.half_life[doc["doc_type"]])
        w_ev = evidence_weight(signal["weight_source"], recent, signal["relevance"])
        weighted = WeightedSignal(signal["weight_source"], recent, signal["sentiment_score"],
                                  signal["controversy_severity"], w_ev,
                                  scoring_weight(w_ev, signal["weight_category"]))
        members[signal["pillar"]].append((doc, signal, weighted))

    pillars = {}
    for pillar, group in members.items():
        signals = [weighted for _, _, weighted in group]
        score = pillar_score(signals, cfg)
        pillars[pillar] = {"score": score, "penalty": pillar_penalty(signals, cfg) if score is not None else 0,
                           "n_signals": len(signals),
                           "status": "ok" if score is not None else "insufficient_data"}
        total = sum(signal.w for signal in signals)
        for doc, signal, weighted in group:

            evidence.append({
                "signal_id": signal["id"], "pillar": pillar, "category": signal["category"],
                "title": doc["title"], "url": doc["url"], "domain": doc["domain"],
                "source_tier": None if doc["source"] == "edgar" else source_tier(doc["domain"], sources),
                "published_at": doc["published_at"], "sentiment": weighted.sentiment,
                "controversy_severity": weighted.controversy,
                "controversy_terms": json.loads(signal["controversy_terms"] or "[]"),
                "polarity": signal["polarity"], "weight_evidence": weighted.w_ev,
                "weight_source": weighted.w_src, "weight_recency": weighted.w_rec,
                "weight_category": signal["weight_category"], "relevance": signal["relevance"],
                "weight": weighted.w,
                "contribution": contribution(weighted.w, signal["polarity"], total)
                if score is not None else None,
            })

    scores = {pillar: item["score"] for pillar, item in pillars.items()}
    composite = composite_score(scores, company["sector"], cfg)
    total_evidence = sum(item["weight_evidence"] for item in evidence)
    coverage = sum(value is not None for value in scores.values())
    conf = confidence(len(evidence), total_evidence, coverage / len(pillars), cfg)
    base_weights = cfg.pillar_weights["overrides"].get(company["sector"], cfg.pillar_weights["default"])
    denominator = sum(base_weights[p] for p, value in scores.items() if value is not None)
    weights = {p: base_weights[p] / denominator if scores[p] is not None and denominator else 0
               for p in pillars}
    ranked = sorted((item for item in evidence if item["contribution"] is not None),
                    key=lambda item: (-abs(item["contribution"]), item["signal_id"]))
    percentile = None
    if company["sector"] and composite is not None:
        peers = [row[0] for row in conn.execute(
            "SELECT s.composite_score FROM esg_scores s JOIN companies c ON c.ticker=s.ticker "
            "WHERE c.sector=? AND c.ticker<>? AND s.id=(SELECT max(id) FROM esg_scores WHERE ticker=s.ticker) "
            "AND s.status='ok' AND s.config_hash=?",
            (company["sector"], ticker, cfg.config_hash),
        )]
        if len(peers) >= 5:
            percentile = 100 * sum((v < composite) + 0.5 * (v == composite) for v in peers) / len(peers)
    status = "ok" if composite is not None else "insufficient_data"
    snapshot = {
        "model_version": version, "pillars": pillars, "pillar_weights": weights,
        "sources": counts, "top_contributors": ranked[:20], "signals": evidence,
        "config": cfg.raw, "sector": company["sector"],
    }
    with conn:
        cursor = conn.execute(
            """INSERT INTO esg_scores
            (ticker, composite_score, e_score, s_score, g_score, e_penalty, s_penalty, g_penalty,
             confidence, sector_percentile, status, n_documents, n_signals, evidence_weight,
             window_start, window_end, methodology_version, config_hash, job_id, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, composite, *scores.values(), *(item["penalty"] for item in pillars.values()),
             conf, percentile, status, len(documents), len(evidence), total_evidence,
             start.isoformat(), end.isoformat(), cfg.version, cfg.config_hash, job_id, end.isoformat()),
        )
        score_id = cursor.lastrowid
        conn.execute("INSERT INTO score_snapshots (score_id, payload) VALUES (?, ?)",
                     (score_id, json.dumps(snapshot)))
        conn.executemany(
            "INSERT INTO score_contributions (score_id, signal_id, pillar, contribution, rank) VALUES (?, ?, ?, ?, ?)",
            [(score_id, item["signal_id"], item["pillar"], item["contribution"], rank)
             for rank, item in enumerate(ranked, 1)],
        )
    return score_id
