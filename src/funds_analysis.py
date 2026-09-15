"""Tab 1 logic: cross-quarter price-drop + conviction-add signal across Dataroma superinvestors."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import pandas as pd

from . import dataroma_client, market_data, narrative


def _parse_portfolio_date(date_str: str | None) -> dt.date | None:
    if not date_str:
        return None
    try:
        return dt.datetime.strptime(date_str, "%d %b %Y").date()
    except ValueError:
        return None


def _prev_quarter_date(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=91)


def run_funds_analysis(
    progress_callback=None,
    fund_limit: int | None = None,
    min_drop_pct: float = 10.0,
    min_add_pct: float = 0.0,
) -> pd.DataFrame:
    funds = dataroma_client.get_superinvestors()
    if fund_limit:
        funds = funds[:fund_limit]

    def _scrape_progress(i, total, name):
        if progress_callback:
            progress_callback(i, total, f"Fetching data: {name}")

    all_holdings, raw_dates = dataroma_client.get_all_holdings(funds, progress_callback=_scrape_progress)
    portfolio_dates = {ticker: _parse_portfolio_date(d) for ticker, d in raw_dates.items()}

    holders_by_stock: dict[str, set[str]] = defaultdict(set)
    for h in all_holdings:
        holders_by_stock[h.stock_ticker].add(h.fund_name)

    add_candidates = [
        h
        for h in all_holdings
        if h.activity_direction == "Add"
        and h.activity_pct is not None
        and h.activity_pct >= min_add_pct
        and h.reported_price is not None
        and portfolio_dates.get(h.fund_ticker)
    ]

    if progress_callback:
        progress_callback(1, 1, f"Checking historical prices for {len(add_candidates)} holdings...")

    price_pairs = list(
        {(h.stock_ticker, _prev_quarter_date(portfolio_dates[h.fund_ticker])) for h in add_candidates}
    )
    prev_prices = market_data.get_prices_batch(price_pairs)

    matches = []
    for h in add_candidates:
        prev_price = prev_prices.get((h.stock_ticker, _prev_quarter_date(portfolio_dates[h.fund_ticker])))
        if prev_price is None or prev_price <= 0:
            continue
        drop_pct = (prev_price - h.reported_price) / prev_price * 100
        if drop_pct < min_drop_pct:
            continue
        matches.append((h, drop_pct))

    if progress_callback:
        progress_callback(1, 1, f"Enriching {len(matches)} ideas with company data...")

    market_data.prefetch_ticker_info([h.stock_ticker for h, _ in matches])

    rows = []
    for h, drop_pct in matches:
        try:
            summary = market_data.get_business_summary(h.stock_ticker)
        except market_data.MarketDataError:
            summary = narrative.business_summary_fallback(h.stock_name)
        try:
            sector = market_data.get_sector(h.stock_ticker)
            industry = market_data.get_industry(h.stock_ticker)
        except market_data.MarketDataError:
            sector = industry = "Unknown"

        other_holders = sorted(holders_by_stock[h.stock_ticker] - {h.fund_name})

        rows.append(
            {
                "Company": f"{h.stock_ticker} - {h.stock_name}",
                "Fund": h.fund_name,
                "% of Portfolio": round(h.pct_of_portfolio, 2),
                "Price drop %": round(drop_pct, 1),
                "Position increase %": round(h.activity_pct, 1),
                "Sector": sector,
                "Industry": industry,
                "Summary": summary,
                "Other holders": ", ".join(other_holders) if other_holders else "-",
                "IR Search": market_data.ir_search_link(h.stock_name),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        # Rank-based combined score so the top rows are strong across all three
        # dimensions (biggest portfolio weight, biggest price drop, biggest add),
        # not just one.
        portfolio_rank = df["% of Portfolio"].rank(ascending=False, method="min")
        drop_rank = df["Price drop %"].rank(ascending=False, method="min")
        add_rank = df["Position increase %"].rank(ascending=False, method="min")
        df["_combined_rank"] = portfolio_rank + drop_rank + add_rank
        df = df.sort_values("_combined_rank").drop(columns="_combined_rank").reset_index(drop=True)
    return df
