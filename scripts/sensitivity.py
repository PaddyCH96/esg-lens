"""Report weight sensitivity and external ESG agreement for stored scores."""

import argparse
import json
from contextlib import closing
from copy import deepcopy
from statistics import correlation
from types import SimpleNamespace

from esg_lens.db.engine import get_connection
from esg_lens.scoring.composite import composite_score


def ranks(values):
    result = [0.0] * len(values)
    order = sorted(range(len(values)), key=values.__getitem__)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[i]] == values[order[j]]:
            j += 1
        for index in order[i:j]:
            result[index] = (i + j + 1) / 2
        i = j
    return result


def spearman(left, right):
    if len(left) < 2 or len(set(left)) < 2 or len(set(right)) < 2:
        return None
    return correlation(ranks(left), ranks(right))


def rescore(snapshot, sector, config):
    scores = {}
    for pillar, baseline in snapshot["pillars"].items():
        scores[pillar] = None
        if baseline["score"] is None:
            continue
        signals = [item for item in snapshot["signals"] if item["pillar"] == pillar]
        weights = [s["weight_evidence"] * config["category_weights"][s["category"]] for s in signals]
        base = 50 + 50 * sum(w * s["polarity"] for w, s in zip(weights, signals)) / sum(weights)
        scores[pillar] = min(100, max(0, base - baseline["penalty"]))
    return composite_score(scores, sector, SimpleNamespace(pillar_weights=config["pillar_weights"]))


def cases(config):
    categories = config["category_weights"]
    for key in categories:
        group = [c for c in categories if config["category_to_pillar"][c] == config["category_to_pillar"][key]]
        yield ("category_weights",), key, group
    pillars = config["pillar_weights"]
    for path, weights in [(("pillar_weights", "default"), pillars["default"]),
                          *((("pillar_weights", "overrides", sector), values)
                            for sector, values in pillars["overrides"].items())]:
        for key in weights:
            yield path, key, list(weights)


def perturb(config, path, key, group, factor):
    config = deepcopy(config)
    weights = config
    for part in path:
        weights = weights[part]
    weights[key] *= factor
    total = sum(weights[name] for name in group)
    for name in group:
        weights[name] /= total
    return config


def report(conn):
    rows = conn.execute(
        """SELECT s.*, snap.payload, c.sector, c.external_esg_score, c.external_esg_provider
           FROM esg_scores s JOIN score_snapshots snap ON snap.score_id=s.id JOIN companies c USING(ticker)
           WHERE s.status='ok' AND s.id=(SELECT max(id) FROM esg_scores WHERE ticker=s.ticker)
           ORDER BY s.computed_at DESC LIMIT 50"""
    ).fetchall()
    if not rows:
        return {"sample_size": 0, "benchmark_sample_size": 0, "spearman": None, "sensitivity": []}
    # Compare only results from the same methodology and retain the newest cohort.
    newest_hash = rows[0]["config_hash"]
    rows = [row for row in rows if row["config_hash"] == newest_hash]
    snapshots = [json.loads(row["payload"]) for row in rows]
    config = snapshots[0]["config"]
    baseline = [row["composite_score"] for row in rows]
    changes = []
    for path, key, group in cases(config):
        for factor in (.75, 1.25):
            updated = perturb(config, path, key, group, factor)
            scores = [rescore(snap, snap["sector"], updated) for snap in snapshots]
            changes.append({
                "weight": ".".join((*path, key)), "factor": factor,
                "spearman": spearman(baseline, scores),
                "max_score_change": max(abs(a - b) for a, b in zip(baseline, scores)),
            })
    benchmark = [row for row in rows if row["external_esg_score"] is not None]
    return {
        "sample_size": len(rows), "config_hash": newest_hash,
        "benchmark_sample_size": len(benchmark),
        "benchmark_providers": sorted({row["external_esg_provider"] or "unknown" for row in benchmark}),
        "spearman": spearman([row["composite_score"] for row in benchmark],
                             [row["external_esg_score"] for row in benchmark]),
        "sensitivity": changes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path")
    args = parser.parse_args()
    with closing(get_connection(args.db_path)) as conn:
        print(json.dumps(report(conn), indent=2))


if __name__ == "__main__":
    main()
