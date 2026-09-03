"""Phase 0 acceptance: config loader, version, hash, weight sums, no magic numbers."""

from pathlib import Path


def test_scoring_config_version_and_hash():
    from esg_lens.config import ScoringConfig, CONFIG_DIR

    cfg = ScoringConfig(config_dir=CONFIG_DIR)
    assert cfg.version == "0.1.0"
    assert len(cfg.config_hash) == 64  # sha256 hex
    # stable
    cfg2 = ScoringConfig(config_dir=CONFIG_DIR)
    assert cfg.config_hash == cfg2.config_hash


def test_category_weights_sum_to_one_per_pillar():
    from esg_lens.config import get_scoring_config

    cfg = get_scoring_config()
    cat_w = cfg.raw["category_weights"]
    pillar_map = cfg.raw["category_to_pillar"]
    per_pillar: dict[str, float] = {}
    for cat, w in cat_w.items():
        pillar = pillar_map[cat]
        per_pillar[pillar] = per_pillar.get(pillar, 0.0) + float(w)
    for pillar, total in per_pillar.items():
        assert abs(total - 1.0) < 1e-6, f"{pillar} sums to {total}"


def test_pillar_weights_sum_to_one():
    from esg_lens.config import get_scoring_config

    cfg = get_scoring_config()
    default = cfg.raw["pillar_weights"]["default"]
    assert abs(sum(default.values()) - 1.0) < 1e-6
    for sector, w in cfg.raw["pillar_weights"]["overrides"].items():
        assert abs(sum(w.values()) - 1.0) < 1e-6, f"{sector} sums to {sum(w.values())}"


def test_no_hardcoded_weights_in_src():
    """SCAF-05: weights must not be hardcoded in src/."""
    import re

    src = Path(__file__).resolve().parents[2] / "src"
    # Known allowed numbers: version strings, port numbers, etc. We check for
    # weight-like literals that should come from YAML instead.
    # This is a lightweight guard — the real guarantee is that ScoringConfig is used.
    forbidden = re.compile(r"weight\s*=\s*0\.(34|33|45|25|30|40|35|55)")
    hits = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if forbidden.search(text):
            hits.append(str(py))
    assert not hits, f"Hardcoded weights found in: {hits}"
