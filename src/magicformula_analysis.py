"""Tab 2 logic: Magic Formula screener results enriched with sector/fund cross-referencing."""
from __future__ import annotations

from collections import defaultdict

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

    sector_by_ticker: dict[str, str] = {}
    for r in results:
        try:
            sector_by_ticker[r.ticker] = market_data.get_sector(r.ticker)
        except market_data.MarketDataError:
            sector_by_ticker[r.ticker] = "לא ידוע"

    by_sector: dict[str, list[str]] = defaultdict(list)
    for r in results:
        by_sector[sector_by_ticker[r.ticker]].append(r.name)

    rows = []
    for i, r in enumerate(results):
        if progress_callback:
            progress_callback(i + 1, len(results), r.name)

        try:
            summary = market_data.get_business_summary(r.ticker)
        except market_data.MarketDataError:
            summary = narrative.business_summary_fallback(r.name)

        sector = sector_by_ticker[r.ticker]
        similar = [name for name in by_sector[sector] if name != r.name]
        fund_holders = sorted(holdings_by_stock.get(r.ticker, set()))
        links = market_data.ir_links(r.ticker, r.name)

        rows.append(
            {
                "חברה": f"{r.ticker} - {r.name}",
                "סיכום": summary,
                "מקורות": "; ".join(f"{k}: {v}" for k, v in links.items()),
                "השערה": narrative.magic_formula_hypothesis(r.name, None, None),
                "חברות דומות (מאגר)": ", ".join(similar[:5]) if similar else "-",
                "קרנות מהרשימה שמחזיקות": ", ".join(fund_holders) if fund_holders else "-",
                "שווי שוק ($M)": r.market_cap_million,
            }
        )

    return pd.DataFrame(rows)
