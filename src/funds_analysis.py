"""Tab 1 logic: cross-quarter price-drop + conviction-add signal across Dataroma superinvestors."""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

import pandas as pd

from . import dataroma_client, market_data, narrative

DROP_THRESHOLD_PCT = 10.0


def _parse_portfolio_date(date_str: str | None) -> dt.date | None:
    if not date_str:
        return None
    try:
        return dt.datetime.strptime(date_str, "%d %b %Y").date()
    except ValueError:
        return None


def _prev_quarter_date(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=91)


def run_funds_analysis(progress_callback=None, fund_limit: int | None = None) -> pd.DataFrame:
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

    holdings_by_fund: dict[str, list[dataroma_client.Holding]] = defaultdict(list)
    for h in all_holdings:
        holdings_by_fund[h.fund_ticker].append(h)

    add_candidates = [
        h
        for h in all_holdings
        if h.activity_direction == "Add" and h.activity_pct is not None and h.reported_price is not None
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
        if drop_pct < DROP_THRESHOLD_PCT:
            continue
        matches.append((h, drop_pct))

    if progress_callback:
        progress_callback(1, 1, f"Enriching {len(matches)} ideas with company data...")

    all_relevant_tickers = {h.stock_ticker for h, _ in matches}
    for h, _ in matches:
        all_relevant_tickers.update(o.stock_ticker for o in holdings_by_fund[h.fund_ticker])
    market_data.prefetch_ticker_info(list(all_relevant_tickers))

    rows = []
    for h, drop_pct in matches:
        portfolio_date = portfolio_dates.get(h.fund_ticker)
        try:
            sector = market_data.get_sector(h.stock_ticker)
        except market_data.MarketDataError:
            sector = "Unknown"
        try:
            summary = market_data.get_business_summary(h.stock_ticker)
        except market_data.MarketDataError:
            summary = narrative.business_summary_fallback(h.stock_name)

        similar = [
            other.stock_name
            for other in holdings_by_fund[h.fund_ticker]
            if other.stock_ticker != h.stock_ticker and _same_sector(other.stock_ticker, sector)
        ]
        other_holders = sorted(holders_by_stock[h.stock_ticker] - {h.fund_name})

        quarter_label = f"{portfolio_date.year} Q{(portfolio_date.month - 1)//3 + 1}" if portfolio_date else "Unknown"

        links = market_data.ir_links(h.stock_ticker, h.stock_name)

        rows.append(
            {
                "Company": f"{h.stock_ticker} - {h.stock_name}",
                "Fund": h.fund_name,
                "Summary": summary,
                "Yahoo Finance": links.get("Yahoo Finance"),
                "IR Search": links.get("IR Search"),
                "Hypothesis": narrative.fund_hypothesis(
                    h.fund_name, h.stock_name, h.activity_pct, drop_pct, quarter_label
                ),
                "Similar companies (same fund)": ", ".join(similar[:5]) if similar else "-",
                "Other holders": ", ".join(other_holders) if other_holders else "-",
                "Price drop %": round(drop_pct, 1),
                "Position increase %": round(h.activity_pct, 1),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Price drop %", ascending=False).reset_index(drop=True)
    return df


def _same_sector(ticker: str, sector: str) -> bool:
    try:
        return market_data.get_sector(ticker) == sector
    except market_data.MarketDataError:
        return False
