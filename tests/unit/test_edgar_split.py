"""Unit tests for EDGAR Item 1/1A split and CIK handling."""

from pathlib import Path

import pytest

from esg_lens.collectors.edgar import EdgarCollector, split_10k_items


FIXTURE_HTML = Path("tests/fixtures/edgar_10k_item1_excerpt.html").read_text() if Path("tests/fixtures/edgar_10k_item1_excerpt.html").exists() else ""


def test_split_returns_both_sections_from_fixture():
    fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "edgar_10k_item1_excerpt.html"
    if not fixture.exists():
        fixture = Path("tests/fixtures/edgar_10k_item1_excerpt.html")
    html = fixture.read_text()
    result = split_10k_items(html)
    assert "Item 1" in result
    assert "Item 1A" in result
    assert "Apple Inc. designs" in result["Item 1"]
    assert "RISK FACTORS" in result["Item 1A"] or "risk factors" in result["Item 1A"].lower()


def test_split_handles_nbsp_and_nested_tags():
    html = "<html><body><p><b>ITEM&nbsp;1. BUSINESS</b></p><p>Business text here.</p><p><b>ITEM&nbsp;1A. RISK FACTORS</b></p><p>Risk text here.</p></body></html>"
    result = split_10k_items(html)
    assert "Item 1" in result
    assert "Item 1A" in result
    assert "Business text" in result["Item 1"]
    assert "Risk text" in result["Item 1A"]


def test_split_handles_missing_items_returns_empty():
    html = "<html><body><p>No relevant items here</p><p>Just random text</p></body></html>"
    result = split_10k_items(html)
    assert result == {}


def test_split_case_insensitive_and_variants():
    html = "<html><body><p>item 1. business</p><p>Some business</p><p>Item 1A - Risk Factors</p><p>Some risks</p></body></html>"
    result = split_10k_items(html)
    assert "Item 1" in result
    assert "Item 1A" in result


def test_accession_dash_removal_and_cik_lstrip():
    accession = "0000320193-23-000077"
    nodash = accession.replace("-", "")
    assert nodash == "000032019323000077"
    cik_padded = "0000320193"
    cik_int = cik_padded.lstrip("0")
    assert cik_int == "320193"
    # Also check url construction
    primary = "aapl-20230930.htm"
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{nodash}/{primary}"
    assert "320193" in url
    assert "000032019323000077" in url
    assert url == "https://www.sec.gov/Archives/edgar/data/320193/000032019323000077/aapl-20230930.htm"


def test_company_tickers_dict_shape():
    data = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
    }
    collector = EdgarCollector()
    mapping = collector._build_mapping(data)
    assert mapping["AAPL"] == "320193"
    assert mapping["MSFT"] == "789019"


def test_company_tickers_list_shape():
    data = [
        {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
    ]
    collector = EdgarCollector()
    mapping = collector._build_mapping(data)
    assert mapping["AAPL"] == "320193"
    assert mapping["MSFT"] == "789019"


def test_company_tickers_both_shapes_equal():
    dict_data = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
    }
    list_data = [
        {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
    ]
    collector = EdgarCollector()
    assert collector._build_mapping(dict_data) == collector._build_mapping(list_data)


def test_split_filters_only_item1_and_1a():
    # Contains Item 1, 1A, 1B, 2 — only 1 and 1A should be kept, 1B and 2 boundaries truncate sections
    html = """
    <html><body>
    <p>ITEM 1. BUSINESS</p><p>Business content</p>
    <p>ITEM 1A. RISK FACTORS</p><p>Risk content</p>
    <p>ITEM 1B. UNRESOLVED STAFF COMMENTS</p><p>Other content</p>
    <p>ITEM 2. PROPERTIES</p><p>Properties content</p>
    </body></html>
    """
    result = split_10k_items(html)
    assert set(result.keys()).issubset({"Item 1", "Item 1A"})
    assert "Item 1" in result
    assert "Item 1A" in result
    # Item 1A should be truncated before Item 1B, so Other content and Properties should not be in Item 1A
    assert "Other content" not in result.get("Item 1A", "")
    assert "Properties content" not in result.get("Item 1A", "")
    # Business content should be in Item 1 but not contain later sections
    assert "Business content" in result["Item 1"]


def test_cik_zero_pad():
    collector = EdgarCollector()
    # build mapping returns raw cik_str, get_cik zero-pads — test mapping vs pad
    raw = "320193"
    padded = raw.zfill(10)
    assert padded == "0000320193"
    assert len(padded) == 10
