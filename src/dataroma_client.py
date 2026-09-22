"""Scraper for dataroma.com — fully public, no login required."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    value: float | None


@dataclass
class QuarterRecord:
    year: int
    quarter: int
    shares: int | None
    pct_of_portfolio: float | None
    activity_direction: str | None  # "Add" | "Reduce" | "Buy" | "Sell" | None
    activity_pct: float | None
    reported_price: float | None


def _get(url: str) -> BeautifulSoup:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise DataromaError(f"Dataroma request failed ({url}): {exc}") from exc
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
        raise DataromaError("No fund list found on the Dataroma home page — the site structure may have changed.")
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

    declared_count = None
    count_label = soup.find(string=re.compile("No\\. of stocks"))
    if count_label:
        count_match = re.search(r"No\.\s*of\s*stocks:\s*(\d+)", count_label.parent.get_text())
        if count_match:
            declared_count = int(count_match.group(1))

    table = soup.find("table", id="grid")
    if table is None:
        raise DataromaError(f"No holdings table found for {fund_ticker} — the site structure may have changed.")

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
        value = _parse_money(cells[6]) if len(cells) > 6 else None
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
                value=value,
            )
        )

    if not holdings and declared_count != 0:
        raise DataromaError(f"No holdings found for {fund_ticker} — the site structure may have changed.")

    return holdings, portfolio_date


def get_all_holdings(
    funds: list[dict], progress_callback=None, max_workers: int = 15
) -> tuple[list[Holding], dict[str, str | None]]:
    """Fetch every fund's holdings concurrently (independent HTTP GETs)."""
    all_holdings: list[Holding] = []
    portfolio_dates: dict[str, str | None] = {}
    done = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(get_holdings, fund["ticker"], fund["name"]): fund for fund in funds
        }
        for future in as_completed(futures):
            fund = futures[future]
            holdings, date_str = future.result()
            all_holdings.extend(holdings)
            portfolio_dates[fund["ticker"]] = date_str
            done += 1
            if progress_callback:
                progress_callback(done, len(funds), fund["name"])

    return all_holdings, portfolio_dates


def get_stock_history(fund_ticker: str, stock_ticker: str) -> list[QuarterRecord]:
    """Full quarter-by-quarter holding/activity history for one (fund, stock) pair."""
    soup = _get(f"{BASE_URL}/m/hist/hist.php?f={fund_ticker}&s={stock_ticker}")

    table = soup.find("table", id="grid")
    if table is None:
        raise DataromaError(
            f"No history table found for {fund_ticker}/{stock_ticker} — the site structure may have changed."
        )

    records: list[QuarterRecord] = []
    for tr in table.find_all("tr")[1:]:  # skip header row
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 6:
            continue
        period_match = re.match(r"(\d{4})\s*Q(\d)", cells[0])
        if not period_match:
            continue
        year, quarter = int(period_match.group(1)), int(period_match.group(2))
        shares = None
        try:
            shares = int(cells[1].replace(",", "")) if cells[1] else None
        except ValueError:
            pass
        try:
            pct_portfolio = float(cells[2]) if cells[2] else None
        except ValueError:
            pct_portfolio = None
        direction, activity_pct = _parse_activity(cells[3])
        reported_price = _parse_money(cells[5])

        records.append(
            QuarterRecord(
                year=year,
                quarter=quarter,
                shares=shares,
                pct_of_portfolio=pct_portfolio,
                activity_direction=direction,
                activity_pct=activity_pct,
                reported_price=reported_price,
            )
        )

    return records


def get_stock_histories(
    fund_tickers: list[str], stock_ticker: str, max_workers: int = 8
) -> dict[str, list[QuarterRecord]]:
    """get_stock_history for several funds (holding the same stock) concurrently."""
    results: dict[str, list[QuarterRecord]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(get_stock_history, fund_ticker, stock_ticker): fund_ticker
            for fund_ticker in fund_tickers
        }
        for future in as_completed(futures):
            fund_ticker = futures[future]
            try:
                results[fund_ticker] = future.result()
            except DataromaError:
                results[fund_ticker] = []
    return results


def get_stock_histories_for_pairs(
    pairs: list[tuple[str, str]], progress_callback=None, max_workers: int = 15
) -> dict[tuple[str, str], list[QuarterRecord]]:
    """get_stock_history for many (fund_ticker, stock_ticker) pairs concurrently."""
    results: dict[tuple[str, str], list[QuarterRecord]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(get_stock_history, fund_ticker, stock_ticker): (fund_ticker, stock_ticker)
            for fund_ticker, stock_ticker in pairs
        }
        for future in as_completed(futures):
            pair = futures[future]
            try:
                results[pair] = future.result()
            except DataromaError:
                results[pair] = []
            done += 1
            if progress_callback:
                progress_callback(done, len(pairs), f"{pair[1]} @ {pair[0]}")
    return results
