"""Document search (TF-IDF) and the keyword categorizer."""
from __future__ import annotations

import pytest

from orbit_copilot.categorizer import auto_categorize
from orbit_copilot.llm_utils import clean_json_response
from orbit_copilot.rag import query_documents

from .conftest import ALICE, BOB


@pytest.mark.asyncio
async def test_search_ranks_the_matching_statement_first():
    # Regression: with two documents the old IDF gave unique terms weight 0,
    # so this query could rank either statement first.
    results = await query_documents(ALICE, "airline flight")
    assert results and results[0]["filename"] == "amex_march.pdf"
    assert "Delta airlines" in results[0]["snippet"]

    results = await query_documents(ALICE, "rent landlord")
    assert results[0]["filename"] == "chase_march.pdf"


@pytest.mark.asyncio
async def test_search_is_scoped_to_the_user():
    assert await query_documents(BOB, "statement") == []


@pytest.mark.parametrize("description, category", [
    ("Uber to the airport", "Transport"),
    ("Chipotle burrito", "Food"),
    ("Comcast internet bill", "Utilities"),
    ("Netflix monthly", "Entertainment"),
])
def test_keyword_categories(description, category):
    out = auto_categorize(description, fallback_to_ai=False)
    assert out["category"] == category and out["method"] == "keyword"


def test_unknown_description_falls_back_without_network():
    out = auto_categorize("zzqx unknown merchant", fallback_to_ai=False)
    assert out["method"] == "default"


@pytest.mark.parametrize("raw, expected", [
    ('{"a": 1}', '{"a": 1}'),
    ('```json\n{"a": 1}\n```', '{"a": 1}'),
    ('```\n{"a": 1}```', '{"a": 1}'),
])
def test_clean_json_response(raw, expected):
    assert clean_json_response(raw) == expected
