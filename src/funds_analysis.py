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


def _quarter_start_date(d: dt.date) -> dt.date:
    start_month = ((d.month - 1) // 3) * 3 + 1
    return dt.date(d.year, start_month, 1)


def _year_quarter(d: dt.date) -> tuple[int, int]:
    return d.year, ((d.month - 1) // 3) + 1


def _prev_year_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def _format_activity(direction: str | None, pct: float | None) -> str:
    if not direction:
        return "No change"
    return f"{direction} {pct:.1f}%" if pct is not None else direction


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

    holders_by_stock: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for h in all_holdings:
        holders_by_stock[h.stock_ticker].add((h.fund_ticker, h.fund_name))

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
        matches.append(h)

    if progress_callback:
        progress_callback(1, 1, f"Checking quarter/year price moves for {len(matches)} ideas...")

    today = dt.date.today()
    year_start = dt.date(today.year, 1, 1)
    quarter_start_pairs = list(
        {(h.stock_ticker, _quarter_start_date(portfolio_dates[h.fund_ticker])) for h in matches}
    )
    year_start_pairs = list({(h.stock_ticker, year_start) for h in matches})
    quarter_start_prices = market_data.get_prices_batch_after(quarter_start_pairs)
    year_start_prices = market_data.get_prices_batch_after(year_start_pairs)

    if progress_callback:
        progress_callback(1, 1, f"Fetching prior-quarter activity for {len(matches)} ideas...")

    history_pairs = [(h.fund_ticker, h.stock_ticker) for h in matches]

    def _history_progress(i, total, label):
        if progress_callback:
            progress_callback(i, total, f"Fetching prior-quarter activity: {label}")

    histories = dataroma_client.get_stock_histories_for_pairs(history_pairs, progress_callback=_history_progress)

    if progress_callback:
        progress_callback(1, 1, f"Enriching {len(matches)} ideas with company data...")

    market_data.prefetch_ticker_info([h.stock_ticker for h in matches])

    rows = []
    for h in matches:
        try:
            summary = market_data.get_business_summary(h.stock_ticker)
        except market_data.MarketDataError:
            summary = narrative.business_summary_fallback(h.stock_name)
        try:
            sector = market_data.get_sector(h.stock_ticker)
            industry = market_data.get_industry(h.stock_ticker)
        except market_data.MarketDataError:
            sector = industry = "Unknown"
        try:
            market_cap = market_data.get_market_cap(h.stock_ticker)
        except market_data.MarketDataError:
            market_cap = None

        other_holder_pairs = sorted(
            holders_by_stock[h.stock_ticker] - {(h.fund_ticker, h.fund_name)}, key=lambda p: p[1]
        )
        other_holders = [name for _, name in other_holder_pairs]
        other_holder_tickers = [ticker for ticker, _ in other_holder_pairs]

        portfolio_date = portfolio_dates[h.fund_ticker]
        q_start_price = quarter_start_prices.get((h.stock_ticker, _quarter_start_date(portfolio_date)))
        quarter_move_pct = (
            round((h.reported_price - q_start_price) / q_start_price * 100, 1)
            if q_start_price and q_start_price > 0
            else None
        )
        y_start_price = year_start_prices.get((h.stock_ticker, year_start))
        ytd_move_pct = (
            round((h.current_price - y_start_price) / y_start_price * 100, 1)
            if h.current_price and y_start_price and y_start_price > 0
            else None
        )

        prev_yq = _prev_year_quarter(*_year_quarter(portfolio_date))
        prev_record = next(
            (r for r in histories.get((h.fund_ticker, h.stock_ticker), []) if (r.year, r.quarter) == prev_yq),
            None,
        )
        prev_position_change = (
            _format_activity(prev_record.activity_direction, prev_record.activity_pct)
            if prev_record
            else "-"
        )

        rows.append(
            {
                "Company": f"{h.stock_ticker} - {h.stock_name}",
                "Fund": h.fund_name,
                "% of Portfolio": round(h.pct_of_portfolio, 2),
                "Quarter Price Move": (
                    f"{'🟢 +' if quarter_move_pct >= 0 else '🔴 '}{quarter_move_pct:.1f}%"
                    if quarter_move_pct is not None
                    else "-"
                ),
                "YTD Price Move": (
                    f"{'🟢 +' if ytd_move_pct >= 0 else '🔴 '}{ytd_move_pct:.1f}%"
                    if ytd_move_pct is not None
                    else "-"
                ),
                "Position increase %": round(h.activity_pct, 1),
                "Previous Position Change": prev_position_change,
                "Position Value ($)": h.value,
                "Company Market Cap ($M)": round(market_cap / 1_000_000, 1) if market_cap else None,
                "Sector": sector,
                "Industry": industry,
                "Summary": summary,
                "Other holders": ", ".join(other_holders) if other_holders else "-",
                "IR Search": market_data.ir_search_link(h.stock_name),
                "Quarter Price Move %": quarter_move_pct,
                "_ticker": h.stock_ticker,
                "_fund_ticker": h.fund_ticker,
                "_other_holder_names": "|".join(other_holders),
                "_other_holder_tickers": "|".join(other_holder_tickers),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        # Rank-based combined score so the top rows are strong across all three
        # dimensions (biggest portfolio weight, biggest quarter price drop, biggest add),
        # not just one. Quarter move can be None (no price data) — treat as "no drop"
        # (worst case, 0) so it doesn't win a rank slot it didn't earn.
        move_for_rank = df["Quarter Price Move %"].fillna(0)
        portfolio_rank = df["% of Portfolio"].rank(ascending=False, method="min")
        drop_rank = move_for_rank.rank(ascending=True, method="min")  # most negative move first
        add_rank = df["Position increase %"].rank(ascending=False, method="min")
        df["_combined_rank"] = portfolio_rank + drop_rank + add_rank
        df = df.sort_values("_combined_rank").drop(columns="_combined_rank").reset_index(drop=True)
    return df
