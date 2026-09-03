---
phase: 1
slug: collectors
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-09-03
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | pyproject.toml |
| **Quick run command** | `PYTHONPATH=src pytest -q` |
| **Full suite command** | `PYTHONPATH=src pytest -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `PYTHONPATH=src pytest -q`
- **After every plan wave:** Run `PYTHONPATH=src pytest -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01-01 | 1 | COLL-01 | — | UA contains @, rate limits enforced, hishel 24h cached | unit | `pytest tests/unit/test_http_client.py -q` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01-01 | 1 | COLL-02 | — | never-raise, collection_runs row on failure | unit | `pytest tests/unit/test_collector_base.py -q` | ❌ W0 | ⬜ pending |
| 01-02-01 | 01-02 | 1 | COLL-03 | — | GDELT alias OR + ESG bundle, domain, content_hash | integration | `pytest tests/integration/test_gdelt.py -q` | ❌ W0 | ⬜ pending |
| 01-02-02 | 01-02 | 1 | COLL-06 | — | NewsAPI disabled by default | unit | `pytest tests/unit/test_newsapi_flag.py -q` | ❌ W0 | ⬜ pending |
| 01-03-01 | 01-03 | 2 | COLL-04 | — | EDGAR CIK cache, submissions, Item 1/1A split | integration | `pytest tests/integration/test_edgar.py -q` | ❌ W0 | ⬜ pending |
| 01-03-02 | 01-03 | 2 | COLL-05, COLL-07 | — | yfinance aliases + sustainability isolation, dedup | integration | `pytest tests/integration/test_yfinance_and_backfill.py -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/conftest.py` — extend db_conn, add hishel temp dir fixture, respx fixtures
- [ ] `tests/fixtures/gdelt_sample.json` + `tests/fixtures/edgar/*.json` — recorded API payloads
- [ ] `tests/unit/test_http_client.py` — UA, rate limit, hishel forced caching, force_refresh bypass
- [ ] `tests/unit/test_collector_base.py` — never-raise contract, collection_runs logging
- [ ] `tests/integration/test_gdelt.py` + `test_edgar.py` + `test_yfinance_and_backfill.py` stubs
- [ ] `beautifulsoup4` + `hishel` added to pyproject.toml dev deps

*If none: "Existing infrastructure covers all phase requirements."*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| SEC EDGAR blocks on missing contact email | COLL-01 | Requires live SEC IP block observation | Verify User-Agent header contains @ via unit test + manual curl to sec.gov with bad UA |

*If none: "All phase behaviors have automated verification."*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
