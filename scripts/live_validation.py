"""Exercise a running ESG Lens API and report only observed live results."""

import argparse
import json
import time
from pathlib import Path

import httpx

from esg_lens.db.engine import get_connection
from scripts.sensitivity import report

PORTFOLIO = "MSFT GOOGL AMZN META NVDA JPM BAC WMT COST KO PG JNJ UNH CVX COP".split()


def wait_for(client, path, predicate, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            response = client.get(path)
            if response.is_success and predicate(response.json()):
                return response.json()
        except httpx.ConnectError:
            pass
        time.sleep(3)
    raise RuntimeError(f"Timed out waiting for {path}")


def analyze(client, tickers, sources):
    start = time.monotonic()
    response = client.post("/api/v1/portfolio/analyze", json={
        "tickers": tickers, "sources": sources, "lookback_days": 90,
    })
    response.raise_for_status()
    job = wait_for(client, response.json()["links"]["status"],
                   lambda data: data["status"] in ("done", "partial", "failed", "cancelled"),
                   timeout=1200)
    results = {}
    for ticker in tickers:
        score = client.get(f"/api/v1/company/{ticker}/score")
        if score.is_success:
            data = score.json()
            evidence = data["evidence"]
            results[ticker] = {
                "status": data["score"]["status"], "score": data["score"]["composite"],
                "sources": evidence["sources"], "coverage": evidence["source_coverage"],
                "example_links": [row["url"] for row in evidence.get("top_contributors", [])
                                  if row["url"]],
            }
    return {"elapsed_seconds": round(time.monotonic() - start, 1),
            "job_status": job["status"], "items": job["items"], "scores": results}


def counts():
    with get_connection() as conn:
        return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("raw_documents", "esg_signals")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("validation.json"))
    parser.add_argument("--edgar", action="store_true", help="Enable SEC sources with a real contact configured")
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    sources = ["google_rss", "yfinance"]
    if args.edgar:
        sources += ["edgar", "sec_litigation"]
    result = {"sources_requested": sources}
    try:
        with httpx.Client(base_url=args.base_url) as client:
            result["health"] = wait_for(client, "/api/v1/healthz", lambda data: data["models_loaded"], 600)
            result["initial"] = analyze(client, ["AAPL", "XOM"], sources)
            result["counts_before_repeat"] = counts()
            result["repeat"] = analyze(client, ["AAPL", "XOM"], sources)
            result["counts_after_repeat"] = counts()
            if args.benchmark:
                result["cohort"] = analyze(client, PORTFOLIO, sources)
                with get_connection() as conn:
                    result["benchmark"] = report(conn)
            observed = result["initial"]["scores"]
            if not all(ticker in observed and sum(observed[ticker]["sources"].values()) > 0
                       for ticker in ("AAPL", "XOM")):
                raise RuntimeError("AAPL and XOM did not both produce stored live evidence")
    finally:
        # Keep aggregate evidence readable in the workflow log.
        summary = dict(result)
        for name in ("initial", "repeat", "cohort"):
            if name in result:
                summary[name] = {**result[name], "scores": {
                    ticker: {k: v for k, v in score.items() if k != "example_links"}
                    for ticker, score in result[name]["scores"].items()
                }}
        benchmark = summary.get("benchmark")
        if benchmark:
            changes = benchmark["sensitivity"]
            ranks = [row["spearman"] for row in changes if row["spearman"] is not None]
            summary["benchmark"] = {
                **{k: v for k, v in benchmark.items() if k != "sensitivity"},
                "sensitivity_cases": len(changes),
                "min_rank_correlation": min(ranks, default=None),
                "max_score_change": max((row["max_score_change"] for row in changes), default=None),
            }
        print("LIVE_VALIDATION_SUMMARY " + json.dumps(summary), flush=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
