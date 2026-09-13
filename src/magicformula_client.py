"""Client for magicformulainvesting.com — requires a registered account.

The screener widget is disabled/JS-driven when logged out, so instead of
hardcoding its field names (which could be wrong), we log in, fetch the
authenticated screener page, and introspect the real form fields at runtime
before submitting. If the site's markup changes and we can't find the
expected fields, we raise a clear error rather than guessing.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.magicformulainvesting.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (fund-research-system personal research tool)"}
TIMEOUT = 20


class MagicFormulaError(Exception):
    pass


@dataclass
class ScreenerResult:
    ticker: str
    name: str
    market_cap_million: float | None
    rank: int | None


def _session_login() -> requests.Session:
    email = os.environ.get("MAGICFORMULA_EMAIL")
    password = os.environ.get("MAGICFORMULA_PASSWORD")
    if not email or not password:
        raise MagicFormulaError(
            "MAGICFORMULA_EMAIL / MAGICFORMULA_PASSWORD are not set in .env. "
            "Copy .env.example to .env and fill in your details."
        )

    session = requests.Session()
    try:
        login_page = session.get(f"{BASE_URL}/Account/LogOn", headers=HEADERS, timeout=TIMEOUT)
        login_page.raise_for_status()
    except requests.RequestException as exc:
        raise MagicFormulaError(f"Failed to load the login page: {exc}") from exc

    soup = BeautifulSoup(login_page.text, "lxml")
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if token_input is None:
        raise MagicFormulaError("__RequestVerificationToken not found on the login page — the site structure may have changed.")
    token = token_input.get("value", "")

    try:
        resp = session.post(
            f"{BASE_URL}/Account/Logon",
            data={"Email": email, "Password": password, "__RequestVerificationToken": token},
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise MagicFormulaError(f"Login request failed: {exc}") from exc

    if "logon" in resp.url.lower() or "log on" in resp.text.lower()[:2000]:
        raise MagicFormulaError(
            "Login to Magic Formula Investing failed — check MAGICFORMULA_EMAIL/PASSWORD in .env."
        )
    return session


def get_screener_results(min_market_cap_million: float = 1000, number_of_stocks: int = 50) -> list[ScreenerResult]:
    session = _session_login()
    return _submit_screener(session, min_market_cap_million, number_of_stocks)


def get_multi_threshold_results(
    thresholds_million: list[float], number_of_stocks: int = 50, progress_callback=None
) -> dict[float, list[ScreenerResult]]:
    """Run the screener once per market-cap threshold, reusing one login session."""
    session = _session_login()
    results_by_threshold: dict[float, list[ScreenerResult]] = {}
    for i, threshold in enumerate(thresholds_million):
        if progress_callback:
            progress_callback(i + 1, len(thresholds_million), f"${threshold:,.0f}M minimum market cap")
        results_by_threshold[threshold] = _submit_screener(session, threshold, number_of_stocks)
    return results_by_threshold


def _submit_screener(
    session: requests.Session, min_market_cap_million: float, number_of_stocks: int
) -> list[ScreenerResult]:
    try:
        page = session.get(f"{BASE_URL}/Screening/StockScreening", headers=HEADERS, timeout=TIMEOUT)
        page.raise_for_status()
    except requests.RequestException as exc:
        raise MagicFormulaError(f"Failed to load the screener page: {exc}") from exc

    soup = BeautifulSoup(page.text, "lxml")
    form = None
    for candidate in soup.find_all("form"):
        if candidate.find("input", {"name": re.compile("marketcap", re.I)}):
            form = candidate
            break
    if form is None:
        raise MagicFormulaError(
            "Screener form not found on the authenticated page — the site structure may have changed, or login failed."
        )

    action = form.get("action") or "/Screening/StockScreening"
    action_url = action if action.startswith("http") else f"{BASE_URL}{action}"

    payload: dict[str, str] = {}
    radio_groups: dict[str, list] = defaultdict(list)
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        input_type = inp.get("type")
        if input_type == "checkbox" and not inp.has_attr("checked"):
            continue
        if input_type == "radio":
            radio_groups[name].append(inp)
            continue
        payload[name] = inp.get("value", "")

    for name, options in radio_groups.items():
        checked = next((o for o in options if o.has_attr("checked")), options[0] if options else None)
        if checked is not None:
            payload[name] = checked.get("value", "")

    market_cap_field = next((n for n in payload if re.search("marketcap", n, re.I) and "txt" not in n.lower()), None)
    if market_cap_field is None:
        market_cap_field = next((n for n in payload if re.search("marketcap", n, re.I)), None)
    if market_cap_field:
        payload[market_cap_field] = str(min_market_cap_million)

    # The 30/50-stocks control is a true/false radio group (e.g. "Select30"):
    # true selects the 30-stock view, false selects 50.
    for name, options in radio_groups.items():
        values = {o.get("value", "").lower() for o in options}
        if values == {"true", "false"} and name != market_cap_field:
            payload[name] = "true" if number_of_stocks <= 30 else "false"

    try:
        result_resp = session.post(action_url, data=payload, headers=HEADERS, timeout=TIMEOUT)
        result_resp.raise_for_status()
    except requests.RequestException as exc:
        raise MagicFormulaError(f"Screener request failed: {exc}") from exc

    return _parse_results(result_resp.text)


def _parse_results(html: str) -> list[ScreenerResult]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_=re.compile("screeningdata", re.I))
    if table is None:
        # Fall back to any table whose header row mentions "Ticker".
        table = next(
            (t for t in soup.find_all("table") if "ticker" in t.get_text()[:200].lower()), None
        )
    if table is None:
        raise MagicFormulaError("No results table found on the screener page — the site structure may have changed.")

    rows = table.find_all("tr")
    results: list[ScreenerResult] = []
    for rank, tr in enumerate(rows[1:], start=1):  # skip the header row
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 3:
            continue
        name, ticker, market_cap_raw = cells[0], cells[1], cells[2]
        if not ticker:
            continue
        market_cap = None
        try:
            market_cap = float(market_cap_raw.replace(",", "").replace("$", ""))
        except ValueError:
            pass
        results.append(ScreenerResult(ticker=ticker, name=name, market_cap_million=market_cap, rank=rank))

    if not results:
        raise MagicFormulaError(
            "No results found in the screener table — the site structure may have changed. "
            "Check /Screening/StockScreening manually."
        )
    return results
