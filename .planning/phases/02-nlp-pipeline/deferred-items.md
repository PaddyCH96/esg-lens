# Deferred items — discovered during 02-02, out of scope

- `tests/integration/test_backfill.py::test_backfill_file_contains_required_patterns` is the exact
  Phase 1 G-4 anti-pattern this phase forbids: it `read_text()`s `scripts/backfill.py` and asserts
  substrings appear in the source. It proves nothing about behaviour. It predates Phase 2 and the
  file is not owned by 02-01/02-02/02-03, so it was left alone. Delete or replace it with a
  behavioural test in a cleanup pass.
- `tests/integration/test_edgar.py` restores `settings.CACHE_DIR` by bare assignment at the end of
  the function body, which does not run if an assertion fails earlier. Same class of leak as the
  one fixed in `test_backfill.py`; harmless while those tests pass. Convert to `monkeypatch`.
- `src/esg_lens/collectors/yfinance_meta.py` returns 404 for all three tickers. `alias_seed.py`
  works around the symptom; the collector itself is a Phase 1 defect, explicitly out of scope here.
