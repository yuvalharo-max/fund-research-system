"""Tab 2 logic: Magic Formula screener results enriched with fund cross-referencing."""
from __future__ import annotations

import pandas as pd

from . import magicformula_client, market_data, narrative


def run_magic_formula_analysis(
    holdings_by_stock: dict[str, set[str]] | None = None,
    min_market_cap_million: float = 1000,
    number_of_stocks: int = 50,
    progress_callback=None,
) -> pd.DataFrame:
    results = magicformula_client.get_screener_results(
        min_market_cap_million=min_market_cap_million, number_of_stocks=number_of_stocks
    )
    holdings_by_stock = holdings_by_stock or {}

    market_data.prefetch_ticker_info([r.ticker for r in results])

    rows = []
    for i, r in enumerate(results):
        if progress_callback:
            progress_callback(i + 1, len(results), r.name)

        try:
            summary = market_data.get_business_summary(r.ticker)
        except market_data.MarketDataError:
            summary = narrative.business_summary_fallback(r.name)

        fund_holders = sorted(holdings_by_stock.get(r.ticker, set()))
        links = market_data.ir_links(r.ticker, r.name)

        rows.append(
            {
                "Company": f"{r.ticker} - {r.name}",
                "Summary": summary,
                "Funds holding it": ", ".join(fund_holders) if fund_holders else "-",
                "Market cap ($M)": r.market_cap_million,
                "Yahoo Finance": links.get("Yahoo Finance"),
                "IR Search": links.get("IR Search"),
                "_fund_holder_count": len(fund_holders),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        # Surface companies your own funds already hold first (strongest cross-signal),
        # then bigger/safer companies within that.
        df = df.sort_values(
            ["_fund_holder_count", "Market cap ($M)"], ascending=[False, False]
        ).drop(columns="_fund_holder_count").reset_index(drop=True)
    return df
