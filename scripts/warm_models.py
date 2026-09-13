#!/usr/bin/env python
"""Pre-download the Hugging Face models so the first API request does not pay for it.

architecture.md §4 lists this script; it did not exist until Phase 2 setup.

First run downloads ~500MB and takes ~3 minutes on a warm connection. Subsequent loads are
~2 seconds from the local cache. Run this once after install, and in CI before any NLP test that
is not stubbed.
"""

from __future__ import annotations

import os
import time

# Keep model weights inside the project, next to the DB and HTTP cache (all gitignored).
os.environ.setdefault("HF_HOME", "data/models")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

MODELS = [
    ("yiyanghkust/finbert-esg-9-categories", "ESG relevance gate + 9-category classification"),
    ("ProsusAI/finbert", "financial sentiment — one feature, NOT ESG polarity"),
]


def main() -> int:
    import spacy
    from transformers import pipeline

    t0 = time.time()
    try:
        spacy.load("en_core_web_sm")
        print("  ok   en_core_web_sm")
    except OSError:
        print("  FAIL en_core_web_sm — run: python -m spacy download en_core_web_sm")
        return 1

    for name, purpose in MODELS:
        t = time.time()
        try:
            pipeline("text-classification", model=name, device=-1)
        except Exception as e:  # pragma: no cover - network dependent
            print(f"  FAIL {name}: {e}")
            return 1
        print(f"  ok   {name:42} {time.time() - t:5.1f}s  ({purpose})")

    print(f"\nAll models cached under {os.environ['HF_HOME']} in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
