"""Wrapper around yfinance for prices, market cap, sector and business summaries."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import yfinance as yf


class MarketDataError(Exception):
    pass


@lru_cache(maxsize=512)
def get_ticker_info(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).get_info()
    except Exception as exc:
        raise MarketDataError(f"לא ניתן לשלוף מידע עבור {ticker}: {exc}") from exc
    if not info:
        raise MarketDataError(f"לא נמצא מידע עבור {ticker}")
    return info


def get_sector(ticker: str) -> str:
    info = get_ticker_info(ticker)
    return info.get("sector") or "לא ידוע"


def get_market_cap(ticker: str) -> float | None:
    info = get_ticker_info(ticker)
    return info.get("marketCap")


def get_business_summary(ticker: str, max_sentences: int = 3) -> str:
    info = get_ticker_info(ticker)
    summary = info.get("longBusinessSummary") or ""
    if not summary:
        return "אין תקציר עסקי זמין."
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
        raise MarketDataError(f"לא ניתן לשלוף היסטוריית מחירים עבור {ticker}: {exc}") from exc
    if hist.empty:
        return None
    hist = hist[hist.index.date <= target_date]
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def ir_links(ticker: str, company_name: str) -> dict[str, str]:
    query = f"{company_name} investor relations".replace(" ", "+")
    return {
        "Yahoo Finance": f"https://finance.yahoo.com/quote/{ticker}",
        "חיפוש IR (Google)": f"https://www.google.com/search?q={query}",
    }
