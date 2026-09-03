"""Collectors package — re-exports shared HTTP client and base types."""

from esg_lens.collectors.base import Collector, RawDocument, content_hash
from esg_lens.collectors.gdelt import GdeltCollector, build_gdelt_queries, filtered_aliases, normalize_domain, parse_seendate
from esg_lens.collectors.http import AsyncHttpClient, HttpClient, get_http_client
from esg_lens.collectors.newsapi import NewsApiCollector

__all__ = [
    "AsyncHttpClient",
    "Collector",
    "GdeltCollector",
    "HttpClient",
    "NewsApiCollector",
    "RawDocument",
    "build_gdelt_queries",
    "content_hash",
    "filtered_aliases",
    "get_http_client",
    "normalize_domain",
    "parse_seendate",
]
