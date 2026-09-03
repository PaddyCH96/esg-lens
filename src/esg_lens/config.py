"""Central configuration — pydantic-settings + YAML scoring config.

Ground rule (handoff §0.4): every weight, threshold and half-life reads from
config/scoring.yaml. A magic number in a .py file is a bug.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # src/esg_lens/config.py -> project root
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR_DEFAULT = DATA_DIR / "cache"
DB_PATH_DEFAULT = DATA_DIR / "esg_lens.db"


# ---------------------------------------------------------------------------
# Settings (env + .env)
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    """Runtime settings — env vars and .env, never weights."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # EDGAR mandates a contact email in the User-Agent (handoff trap)
    USER_AGENT: str = Field(
        default="ESG-Lens/0.1.0 (contact: esg-lens@example.com)",
        description="HTTP User-Agent — must contain a contact email for SEC EDGAR",
    )
    CONTACT_EMAIL: str = Field(default="esg-lens@example.com")

    # Paths
    DB_PATH: str = Field(default=str(DB_PATH_DEFAULT))
    CACHE_DIR: str = Field(default=str(CACHE_DIR_DEFAULT))

    # Runtime tuning
    TORCH_THREADS: int = Field(default=4, ge=1, le=32)
    LOG_LEVEL: str = Field(default="INFO")
    RETENTION_DAYS: int = Field(default=30, ge=1)
    MAX_CONCURRENT_JOBS: int = Field(default=1, ge=1)
    MAX_TICKERS_PER_JOB: int = Field(default=25, ge=1, le=100)

    # Feature flags
    NEWSAPI_ENABLED: bool = Field(default=False)

    @field_validator("USER_AGENT")
    @classmethod
    def _ua_must_contain_email(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("USER_AGENT must contain a contact email (SEC EDGAR requirement)")
        return v


# Singleton — import as `from esg_lens.config import settings`
settings = Settings()


# ---------------------------------------------------------------------------
# ScoringConfig — loads config/scoring.yaml + friends, exposes version + hash
# ---------------------------------------------------------------------------
class ScoringConfig:
    """Loads YAML configs, validates sums to 1.0, exposes version + config_hash."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or CONFIG_DIR
        self._raw: dict[str, Any] = {}
        self._hash: str = ""
        self._load()

    def _load(self) -> None:
        scoring_path = self.config_dir / "scoring.yaml"
        if not scoring_path.exists():
            raise FileNotFoundError(f"Missing {scoring_path}")

        with open(scoring_path) as f:
            raw = yaml.safe_load(f) or {}

        # Validate weight sums (handoff §0.4 sanity)
        self._validate(raw)
        self._raw = raw
        # Stable hash: sorted keys, compact JSON
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
        self._hash = hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _validate(raw: dict[str, Any]) -> None:
        # category weights per pillar sum to 1.0
        cat_w = raw.get("category_weights", {})
        pillar_map = raw.get("category_to_pillar", {})
        per_pillar: dict[str, float] = {}
        for cat, w in cat_w.items():
            pillar = pillar_map.get(cat)
            if pillar:
                per_pillar[pillar] = per_pillar.get(pillar, 0.0) + float(w)
        for pillar, total in per_pillar.items():
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"category_weights for pillar {pillar} sum to {total}, expected 1.0")

        # pillar_weights default sums to 1.0
        pw = raw.get("pillar_weights", {})
        default = pw.get("default", {})
        if default:
            total = sum(float(v) for v in default.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"pillar_weights.default sums to {total}, expected 1.0")
        for sector, weights in pw.get("overrides", {}).items():
            total = sum(float(v) for v in weights.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"pillar_weights.overrides[{sector}] sums to {total}, expected 1.0")

    # ---- public surface (handoff §0.4) ----
    @property
    def version(self) -> str:
        return str(self._raw.get("version", "unknown"))

    @property
    def config_hash(self) -> str:
        return self._hash

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw

    # Convenience accessors — so callers do `scoring_config.tau` not `raw["tau"]`
    def __getattr__(self, name: str) -> Any:
        if name in self._raw:
            return self._raw[name]
        raise AttributeError(f"ScoringConfig has no key '{name}'")

    def get(self, key: str, default: Any = None) -> Any:
        return self._raw.get(key, default)


@lru_cache(maxsize=1)
def get_scoring_config() -> ScoringConfig:
    return ScoringConfig()


# Eager singleton for convenience (tests may override via get_scoring_config.cache_clear())
scoring_config = get_scoring_config()


# ---------------------------------------------------------------------------
# Helpers to load the other YAMLs
# ---------------------------------------------------------------------------
def load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}
