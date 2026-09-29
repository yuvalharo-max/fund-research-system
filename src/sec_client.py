"""SEC EDGAR access: full-text search, filing documents, and company metadata (all free, no API key)."""
from __future__ import annotations

import datetime as dt
import os
import re
import threading
import time
from functools import lru_cache

import requests
from bs4 import BeautifulSoup

FTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
SUBMISSIONS_URL = "https://data.sec.gov/submissions"
TIMEOUT = 30
MIN_INTERVAL = 0.12  # SEC fair-access limit is 10 requests/second across the whole tool


class SecError(Exception):
    pass


_rate_lock = threading.Lock()
_last_request = [0.0]


def _headers() -> dict:
    email = os.getenv("SEC_CONTACT_EMAIL", "").strip()
    if not email:
        raise SecError(
            "SEC_CONTACT_EMAIL is not set in .env — the SEC requires a contact email in every "
            "request (fair-access policy). Add SEC_CONTACT_EMAIL=you@example.com to .env and restart."
        )
    return {"User-Agent": f"fund-research-system {email}"}


def _get(url: str, params: dict | None = None) -> requests.Response:
    headers = _headers()
    for attempt in range(4):
        with _rate_lock:
            wait = MIN_INTERVAL - (time.monotonic() - _last_request[0])
            if wait > 0:
                time.sleep(wait)
            _last_request[0] = time.monotonic()
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            if attempt == 3:
                raise SecError(f"SEC request failed ({url}): {exc}") from exc
            time.sleep(2**attempt)
            continue
        if resp.status_code in (429, 503) and attempt < 3:
            time.sleep(2 ** (attempt + 1))
            continue
        if resp.status_code != 200:
            raise SecError(f"SEC request failed ({url}): HTTP {resp.status_code}")
        if "Undeclared Automated Tool" in resp.text[:2000]:
            raise SecError("SEC rejected the request as an undeclared automated tool — check SEC_CONTACT_EMAIL in .env.")
        return resp
    raise SecError(f"SEC request failed ({url}): too many retries")


def search_filings(query: str, start: dt.date, end: dt.date, forms: str = "8-K,6-K", progress_callback=None) -> list[dict]:
    """Every full-text-search hit (one per matching document) in the date range."""
    hits: list[dict] = []
    offset = 0
    while True:
        params = {
            "q": query,
            "forms": forms,
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
            "from": offset,
        }
        data = _get(FTS_URL, params).json()
        page = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        hits.extend(page)
        offset += len(page)
        if progress_callback:
            progress_callback(offset, max(total, 1), "Searching EDGAR filings")
        if not page or offset >= total or offset >= 10000:
            break
    return hits


def document_url(cik: str | int, accession: str, filename: str) -> str:
    return f"{ARCHIVES_URL}/{int(cik)}/{accession.replace('-', '')}/{filename}"


def filing_index_url(cik: str | int, accession: str) -> str:
    return f"{ARCHIVES_URL}/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"


def get_document_text(url: str) -> str:
    """Plain text of a filing document, without the hidden inline-XBRL header."""
    soup = BeautifulSoup(_get(url).text, "lxml")
    for tag in soup.find_all(re.compile(r"^ix:header$")):
        tag.decompose()
    for tag in soup.select('[style*="display:none"]'):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


@lru_cache(maxsize=2048)
def get_company(cik: str | int) -> dict:
    """{name, tickers, acceptance: {accession: acceptanceDateTime}} from SEC's own submissions record."""
    data = _get(f"{SUBMISSIONS_URL}/CIK{int(cik):010d}.json").json()
    recent = data.get("filings", {}).get("recent", {})
    acceptance = dict(zip(recent.get("accessionNumber", []), recent.get("acceptanceDateTime", [])))
    return {"name": data.get("name", ""), "tickers": data.get("tickers", []) or [], "acceptance": acceptance}
