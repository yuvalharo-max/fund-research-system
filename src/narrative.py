"""Fallback text for missing data (no LLM available for free-text summaries)."""
from __future__ import annotations


def business_summary_fallback(company_name: str) -> str:
    return f"No business summary available for {company_name}. Check the company's filings directly."
