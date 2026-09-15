"""Tab 2 logic: Magic Formula screener results enriched with fund cross-referencing.

Ranking insight: the screener returns the top `number_of_stocks` by combined
Magic Formula rank among companies at or above a chosen minimum market cap.
Lowering that minimum lets more (small-cap) competitors into the running.
A company that keeps its spot in the top N even as smaller and smaller
competitors are let in has a stronger underlying rank than one that only
appears once small caps are excluded — i.e. a deeper price/value gap. So we
re-run the screener at a ladder of shrinking thresholds and rank companies by
the lowest threshold at which they still survive.
"""
from __future__ import annotations

import pandas as pd

from . import magicformula_client, market_data, narrative

LADDER_FRACTIONS = (1.0, 0.75, 0.5, 0.35, 0.25, 0.15, 0.10, 0.05)


def run_magic_formula_analysis(
    holdings_by_stock: dict[str, set[str]] | None = None,
    min_market_cap_million: float = 1000,
    number_of_stocks: int = 50,
    progress_callback=None,
) -> pd.DataFrame:
    thresholds = sorted({round(min_market_cap_million * f) for f in LADDER_FRACTIONS}, reverse=True)

    def _fetch_progress(i, total, label):
        if progress_callback:
            progress_callback(i, total, f"Checking rank stability at {label}")

    results_by_threshold = magicformula_client.get_multi_threshold_results(
        thresholds, number_of_stocks=number_of_stocks, progress_callback=_fetch_progress
    )
    baseline = results_by_threshold[thresholds[0]]  # thresholds[0] == min_market_cap_million (largest)

    survival_threshold: dict[str, float] = {}
    for t in sorted(thresholds):  # ascending: smallest threshold first
        appearing = {r.ticker for r in results_by_threshold[t]}
        for r in baseline:
            if r.ticker not in survival_threshold and r.ticker in appearing:
                survival_threshold[r.ticker] = t

    holdings_by_stock = holdings_by_stock or {}
    market_data.prefetch_ticker_info([r.ticker for r in baseline])

    rows = []
    for i, r in enumerate(baseline):
        if progress_callback:
            progress_callback(i + 1, len(baseline), f"Enriching: {r.name}")

        try:
            summary = market_data.get_business_summary(r.ticker)
        except market_data.MarketDataError:
            summary = narrative.business_summary_fallback(r.name)
        try:
            sector = market_data.get_sector(r.ticker)
            industry = market_data.get_industry(r.ticker)
        except market_data.MarketDataError:
            sector = industry = "Unknown"

        fund_holders = sorted(holdings_by_stock.get(r.ticker, set()))

        rows.append(
            {
                "Company": f"{r.ticker} - {r.name}",
                "Sector": sector,
                "Industry": industry,
                "Summary": summary,
                "Funds holding it": ", ".join(fund_holders) if fund_holders else "-",
                "Market cap ($M)": r.market_cap_million,
                "IR Search": market_data.ir_search_link(r.name),
                "_survival_threshold": survival_threshold.get(r.ticker, thresholds[0]),
                "_fund_holder_count": len(fund_holders),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        # Deepest price/value gap first (lowest survival threshold), then your
        # own funds' conviction, then bigger/safer companies as a tiebreaker.
        df = df.sort_values(
            ["_survival_threshold", "_fund_holder_count", "Market cap ($M)"],
            ascending=[True, False, False],
        ).drop(columns=["_survival_threshold", "_fund_holder_count"]).reset_index(drop=True)
    return df
