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


def run_funds_analysis(progress_callback=None) -> pd.DataFrame:
    funds = dataroma_client.get_superinvestors()

    all_holdings: list[dataroma_client.Holding] = []
    portfolio_dates: dict[str, dt.date | None] = {}

    for i, fund in enumerate(funds):
        if progress_callback:
            progress_callback(i + 1, len(funds), fund["name"])
        holdings, date_str = dataroma_client.get_holdings(fund["ticker"], fund["name"])
        all_holdings.extend(holdings)
        portfolio_dates[fund["ticker"]] = _parse_portfolio_date(date_str)

    holders_by_stock: dict[str, set[str]] = defaultdict(set)
    for h in all_holdings:
        holders_by_stock[h.stock_ticker].add(h.fund_name)

    holdings_by_fund: dict[str, list[dataroma_client.Holding]] = defaultdict(list)
    for h in all_holdings:
        holdings_by_fund[h.fund_ticker].append(h)

    rows = []
    for h in all_holdings:
        if h.activity_direction != "Add" or h.activity_pct is None or h.reported_price is None:
            continue

        portfolio_date = portfolio_dates.get(h.fund_ticker)
        prev_price = None
        if portfolio_date:
            try:
                prev_price = market_data.get_price_on_or_before(
                    h.stock_ticker, _prev_quarter_date(portfolio_date)
                )
            except market_data.MarketDataError:
                prev_price = None

        if prev_price is None or prev_price <= 0:
            continue

        drop_pct = (prev_price - h.reported_price) / prev_price * 100
        if drop_pct < DROP_THRESHOLD_PCT:
            continue

        try:
            sector = market_data.get_sector(h.stock_ticker)
        except market_data.MarketDataError:
            sector = "לא ידוע"
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

        quarter_label = f"{portfolio_date.year} Q{(portfolio_date.month - 1)//3 + 1}" if portfolio_date else "לא ידוע"

        links = market_data.ir_links(h.stock_ticker, h.stock_name)

        rows.append(
            {
                "חברה": f"{h.stock_ticker} - {h.stock_name}",
                "קרן": h.fund_name,
                "סיכום": summary,
                "מקורות": "; ".join(f"{k}: {v}" for k, v in links.items()),
                "השערה": narrative.fund_hypothesis(
                    h.fund_name, h.stock_name, h.activity_pct, drop_pct, quarter_label
                ),
                "חברות דומות (אותה קרן)": ", ".join(similar[:5]) if similar else "-",
                "קרנות נוספות שמחזיקות": ", ".join(other_holders) if other_holders else "-",
                "% ירידת מחיר": round(drop_pct, 1),
                "% הגדלת אחזקה": round(h.activity_pct, 1),
            }
        )

    return pd.DataFrame(rows)


def _same_sector(ticker: str, sector: str) -> bool:
    try:
        return market_data.get_sector(ticker) == sector
    except market_data.MarketDataError:
        return False
