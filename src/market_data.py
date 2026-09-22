"""Wrapper around yfinance for historical prices and business summaries."""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import yfinance as yf


class MarketDataError(Exception):
    pass


@lru_cache(maxsize=512)
def get_ticker_info(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).get_info()
    except Exception as exc:
        raise MarketDataError(f"Could not fetch info for {ticker}: {exc}") from exc
    if not info:
        raise MarketDataError(f"No info found for {ticker}")
    return info


def get_market_cap(ticker: str) -> float | None:
    info = get_ticker_info(ticker)
    return info.get("marketCap")


def get_sector(ticker: str) -> str:
    info = get_ticker_info(ticker)
    return info.get("sector") or "Unknown"


def get_industry(ticker: str) -> str:
    info = get_ticker_info(ticker)
    return info.get("industry") or "Unknown"


def get_business_summary(ticker: str, max_sentences: int = 3) -> str:
    info = get_ticker_info(ticker)
    summary = info.get("longBusinessSummary") or ""
    if not summary:
        return "No business summary available."
    sentences = [s.strip() for s in summary.split(". ") if s.strip()]
    short = ". ".join(sentences[:max_sentences])
    return short + ("." if not short.endswith(".") else "")


def get_price_on_or_before(ticker: str, target_date: dt.date) -> float | None:
    """Closing price on target_date, or the nearest earlier trading day."""
    start = target_date - dt.timedelta(days=10)
    end = target_date + dt.timedelta(days=1)
    try:
        hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
    except Exception as exc:
        raise MarketDataError(f"Could not fetch price history for {ticker}: {exc}") from exc
    if hist.empty:
        return None
    hist = hist[hist.index.date <= target_date]
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def get_price_on_or_after(ticker: str, target_date: dt.date) -> float | None:
    """Closing price on target_date, or the nearest later trading day (e.g. a quarter/year start)."""
    start = target_date
    end = target_date + dt.timedelta(days=10)
    try:
        hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
    except Exception as exc:
        raise MarketDataError(f"Could not fetch price history for {ticker}: {exc}") from exc
    if hist.empty:
        return None
    hist = hist[hist.index.date >= target_date]
    if hist.empty:
        return None
    return float(hist["Close"].iloc[0])


def _batch(pairs, lookup_fn, max_workers: int = 4) -> dict[tuple[str, dt.date], float | None]:
    results: dict[tuple[str, dt.date], float | None] = {}

    def _one(pair: tuple[str, dt.date]):
        ticker, target_date = pair
        try:
            return pair, lookup_fn(ticker, target_date)
        except MarketDataError:
            return pair, None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for pair, price in executor.map(_one, pairs):
            results[pair] = price
    return results


def get_prices_batch(
    pairs: list[tuple[str, dt.date]], max_workers: int = 4
) -> dict[tuple[str, dt.date], float | None]:
    """Resolve many (ticker, date) -> price-on-or-before lookups concurrently."""
    return _batch(pairs, get_price_on_or_before, max_workers)


def get_prices_batch_after(
    pairs: list[tuple[str, dt.date]], max_workers: int = 4
) -> dict[tuple[str, dt.date], float | None]:
    """Resolve many (ticker, date) -> price-on-or-after lookups concurrently."""
    return _batch(pairs, get_price_on_or_after, max_workers)


def get_price_history(ticker: str, period: str = "5y"):
    """Daily close price history as a DataFrame (columns include 'Close'), indexed by date."""
    try:
        hist = yf.Ticker(ticker).history(period=period)
    except Exception as exc:
        raise MarketDataError(f"Could not fetch price history for {ticker}: {exc}") from exc
    if hist.empty:
        raise MarketDataError(f"No price history available for {ticker}")
    return hist


def prefetch_ticker_info(tickers: list[str], max_workers: int = 4) -> None:
    """Warm the get_ticker_info cache concurrently so later sequential calls are instant."""
    unique = [t for t in dict.fromkeys(tickers)]

    def _one(ticker: str) -> None:
        try:
            get_ticker_info(ticker)
        except MarketDataError:
            pass

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_one, unique))


def ir_search_link(company_name: str) -> str:
    query = f"{company_name} investor relations".replace(" ", "+")
    return f"https://www.google.com/search?q={query}"
