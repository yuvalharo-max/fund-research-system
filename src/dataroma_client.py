"""Scraper for dataroma.com — fully public, no login required."""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.dataroma.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (fund-research-system personal research tool)"}
TIMEOUT = 20


class DataromaError(Exception):
    pass


@dataclass
class Holding:
    fund_ticker: str
    fund_name: str
    stock_ticker: str
    stock_name: str
    pct_of_portfolio: float
    activity_direction: str | None  # "Add" | "Reduce" | None
    activity_pct: float | None
    shares: int | None
    reported_price: float | None
    current_price: float | None


def _get(url: str) -> BeautifulSoup:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise DataromaError(f"נכשלה קריאה ל-Dataroma ({url}): {exc}") from exc
    return BeautifulSoup(resp.text, "lxml")


def get_superinvestors() -> list[dict]:
    """Return list of {ticker, name} for every tracked superinvestor."""
    soup = _get(f"{BASE_URL}/m/home.php")
    funds = []
    seen = set()
    for a in soup.select("a[href*='holdings.php?m=']"):
        href = a.get("href", "")
        match = re.search(r"m=([A-Za-z0-9.]+)", href)
        if not match:
            continue
        ticker = match.group(1)
        name = a.get_text(strip=True)
        name = re.sub(r"Updated\s+\d{1,2}\s+\w+\s+\d{4}\s*$", "", name).strip()
        if not name or ticker in seen:
            continue
        seen.add(ticker)
        funds.append({"ticker": ticker, "name": name})
    if not funds:
        raise DataromaError("לא נמצאה רשימת קרנות בדף הבית של Dataroma — ייתכן שמבנה האתר השתנה.")
    return funds


def _parse_money(text: str) -> float | None:
    text = text.replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_activity(text: str) -> tuple[str | None, float | None]:
    text = text.strip()
    if not text:
        return None, None
    match = re.match(r"(Add|Reduce|Buy|Sell)\s*([\d.]+)?%?", text)
    if not match:
        return None, None
    direction, pct = match.group(1), match.group(2)
    return direction, (float(pct) if pct else None)


def get_holdings(fund_ticker: str, fund_name: str) -> tuple[list[Holding], str | None]:
    """Return (holdings, portfolio_date_iso) for one fund's current 13F snapshot."""
    soup = _get(f"{BASE_URL}/m/holdings.php?m={fund_ticker}")

    portfolio_date = None
    date_label = soup.find(string=re.compile("Portfolio date"))
    if date_label:
        match = re.search(r"Portfolio date:\s*([\d]{1,2}\s+\w+\s+\d{4})", date_label.parent.get_text())
        if match:
            portfolio_date = match.group(1)

    table = soup.find("table", id="grid")
    if table is None:
        raise DataromaError(f"לא נמצאה טבלת אחזקות עבור {fund_ticker} — ייתכן שמבנה האתר השתנה.")

    holdings: list[Holding] = []
    for tr in table.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 11:
            continue
        stock_cell = cells[1]
        stock_match = re.match(r"([A-Za-z0-9.]+)\s*-\s*(.+)", stock_cell)
        if not stock_match:
            continue
        stock_ticker, stock_name = stock_match.group(1), stock_match.group(2)
        try:
            pct_portfolio = float(cells[2]) if cells[2] else 0.0
        except ValueError:
            pct_portfolio = 0.0
        direction, activity_pct = _parse_activity(cells[3])
        shares = None
        try:
            shares = int(cells[4].replace(",", "")) if cells[4] else None
        except ValueError:
            pass
        reported_price = _parse_money(cells[5])
        current_price = _parse_money(cells[8]) if len(cells) > 8 else None

        holdings.append(
            Holding(
                fund_ticker=fund_ticker,
                fund_name=fund_name,
                stock_ticker=stock_ticker,
                stock_name=stock_name,
                pct_of_portfolio=pct_portfolio,
                activity_direction=direction,
                activity_pct=activity_pct,
                shares=shares,
                reported_price=reported_price,
                current_price=current_price,
            )
        )

    if not holdings:
        raise DataromaError(f"לא נמצאו אחזקות עבור {fund_ticker} — ייתכן שמבנה האתר השתנה.")

    return holdings, portfolio_date
