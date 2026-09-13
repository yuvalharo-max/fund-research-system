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
            "MAGICFORMULA_EMAIL / MAGICFORMULA_PASSWORD לא מוגדרים ב-.env. "
            "העתק את .env.example ל-.env ומלא את הפרטים שלך."
        )

    session = requests.Session()
    try:
        login_page = session.get(f"{BASE_URL}/Account/LogOn", headers=HEADERS, timeout=TIMEOUT)
        login_page.raise_for_status()
    except requests.RequestException as exc:
        raise MagicFormulaError(f"נכשלה טעינת עמוד ההתחברות: {exc}") from exc

    soup = BeautifulSoup(login_page.text, "lxml")
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if token_input is None:
        raise MagicFormulaError("לא נמצא __RequestVerificationToken בעמוד ההתחברות — ייתכן שמבנה האתר השתנה.")
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
        raise MagicFormulaError(f"נכשלה בקשת ההתחברות: {exc}") from exc

    if "logon" in resp.url.lower() or "log on" in resp.text.lower()[:2000]:
        raise MagicFormulaError(
            "ההתחברות ל-Magic Formula Investing נכשלה — בדוק MAGICFORMULA_EMAIL/PASSWORD ב-.env."
        )
    return session


def get_screener_results(min_market_cap_million: float = 1000, number_of_stocks: int = 50) -> list[ScreenerResult]:
    session = _session_login()

    try:
        page = session.get(f"{BASE_URL}/Screening/StockScreening", headers=HEADERS, timeout=TIMEOUT)
        page.raise_for_status()
    except requests.RequestException as exc:
        raise MagicFormulaError(f"נכשלה טעינת עמוד הסקרינר: {exc}") from exc

    soup = BeautifulSoup(page.text, "lxml")
    form = None
    for candidate in soup.find_all("form"):
        if candidate.find("input", {"name": re.compile("marketcap", re.I)}) or candidate.find(
            "input", {"name": re.compile("radiobutton", re.I)}
        ):
            form = candidate
            break
    if form is None:
        raise MagicFormulaError(
            "לא נמצא טופס הסקרינר בעמוד המחובר — ייתכן שמבנה האתר השתנה או שההתחברות לא הצליחה."
        )

    action = form.get("action") or "/Screening/StockScreening"
    action_url = action if action.startswith("http") else f"{BASE_URL}{action}"

    payload: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        if inp.get("type") == "checkbox" and not inp.get("checked"):
            continue
        payload[name] = inp.get("value", "")

    market_cap_field = next((n for n in payload if re.search("marketcap", n, re.I) and "txt" not in n.lower()), None)
    if market_cap_field is None:
        market_cap_field = next((n for n in payload if re.search("marketcap", n, re.I)), None)
    if market_cap_field:
        payload[market_cap_field] = str(min_market_cap_million)

    stocks_field = next((n for n in payload if re.search("radiobutton|numberofstocks|numstocks", n, re.I)), None)
    if stocks_field:
        payload[stocks_field] = str(number_of_stocks)

    try:
        result_resp = session.post(action_url, data=payload, headers=HEADERS, timeout=TIMEOUT)
        result_resp.raise_for_status()
    except requests.RequestException as exc:
        raise MagicFormulaError(f"נכשלה בקשת הסקרינר: {exc}") from exc

    return _parse_results(result_resp.text)


def _parse_results(html: str) -> list[ScreenerResult]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id=re.compile("grid|result|screen", re.I)) or soup.find("table")
    if table is None:
        raise MagicFormulaError("לא נמצאה טבלת תוצאות בעמוד הסקרינר — ייתכן שמבנה האתר השתנה.")

    results: list[ScreenerResult] = []
    for tr in table.select("tbody tr") or table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 2:
            continue
        ticker_match = re.match(r"([A-Za-z0-9.]+)\s*-?\s*(.*)", cells[1] if len(cells) > 2 else cells[0])
        if not ticker_match:
            continue
        ticker, name = ticker_match.group(1), (ticker_match.group(2) or cells[0])
        rank = None
        try:
            rank = int(cells[0])
        except (ValueError, IndexError):
            pass
        market_cap = None
        for cell in cells:
            cap_match = re.match(r"\$?([\d,]+\.?\d*)$", cell.replace(",", ""))
            if cap_match:
                try:
                    market_cap = float(cap_match.group(1))
                except ValueError:
                    pass
        results.append(ScreenerResult(ticker=ticker, name=name.strip(), market_cap_million=market_cap, rank=rank))

    if not results:
        raise MagicFormulaError(
            "לא נמצאו תוצאות בטבלת הסקרינר — ייתכן שמבנה האתר השתנה. יש לבדוק ידנית את /Screening/StockScreening."
        )
    return results
