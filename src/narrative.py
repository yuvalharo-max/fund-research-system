"""Rule-based (no-LLM) summary/hypothesis text generation."""
from __future__ import annotations


def fund_hypothesis(fund_name: str, company_name: str, add_pct: float, drop_pct: float, quarter: str) -> str:
    return (
        f"{fund_name} increased its position in {company_name} by about {add_pct:.1f}% in {quarter}, "
        f"while the reported price dropped by about {drop_pct:.1f}% versus the prior quarter. "
        f"The combination of adding to the position while the price fell may suggest the fund sees a gap "
        f"between price and intrinsic value — this is only a hypothesis based on holdings data, not investment advice."
    )


def magic_formula_hypothesis(company_name: str, earnings_yield: float | None, roic: float | None) -> str:
    parts = [f"{company_name} appears in the Magic Formula ranking"]
    if earnings_yield is not None:
        parts.append(f"with an earnings yield of about {earnings_yield:.1f}%")
    if roic is not None:
        parts.append(f"and a return on invested capital (ROIC) of about {roic:.1f}%")
    parts.append(
        "— a combination that, per Magic Formula methodology, may indicate relative undervaluation; "
        "this is a hypothesis that requires further research, not investment advice."
    )
    return " ".join(parts)


def business_summary_fallback(company_name: str) -> str:
    return f"No business summary available for {company_name}. Check the company's filings directly."
