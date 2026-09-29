"""Tab 3 logic: share-buyback authorizations (new / increased) from SEC 8-K and 6-K filings.

Authorizations (permission to buy) and executed repurchases (shares already bought)
are separate event types and never merged. Every figure is extracted by rules from
the filing's own wording; nothing is summed or inferred across sentences, except
the increase implied by an explicit "from $A to $B".
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pandas as pd

from . import market_data, sec_client

EVENT_NEW = "New authorization"
EVENT_INCREASE = "Authorization increase"
EVENT_EXECUTED = "Repurchases executed"
EVENT_REVIEW = "Needs manual review"
EVENT_TYPES = [EVENT_NEW, EVENT_INCREASE, EVENT_EXECUTED, EVENT_REVIEW]
OUTPUT_COLUMNS = [
    "Filed (ET)", "Company", "Ticker", "Event type", "Amount", "Currency", "Total authorized after",
    "Remaining from prior program", "Expiration / duration", "Replaces prior program?", "Supporting quote",
    "Market cap ($M)", "Form", "Filing link", "Filing index",
]

SEARCH_QUERY = (
    '"repurchase program" OR "buyback program" OR "repurchase authorization" OR "repurchase plan" '
    'OR "repurchase programme" OR "buy-back programme" OR "buyback programme"'
)

EASTERN = ZoneInfo("America/New_York")
RECENT_DAYS = 21  # an 8-K is due within 4 business days of the event; older event dates = history
MIN_PROGRAM_VALUE = 10_000  # anything smaller is a per-share price, par value, or a clipped number

_MONTHS = {
    m[:3].lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July", "August",
         "September", "October", "November", "December"],
        start=1,
    )
}
_MONTH_PAT = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?"
)
_DATE_US = re.compile(rf"\b({_MONTH_PAT})\s+(\d{{1,2}}),?\s+(\d{{4}})")
_DATE_EU = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_PAT})\s+(\d{{4}})")
_DATE_MONTH_YEAR = re.compile(rf"\b({_MONTH_PAT})\s+(?:of\s+)?(\d{{4}})\b")
_ANY_DATE = rf"(?:{_MONTH_PAT}\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}\s+{_MONTH_PAT}\s+\d{{4}}|{_MONTH_PAT}\s+\d{{4}})"
_EVENT_DATE_PREFIX = re.compile(r"\b(on|in|dated|ended|effective(?: as of)?|from|since|during)\s*$", re.I)
_DATE_YEAR = re.compile(r"\b(?:in|during)\s+((?:19|20)\d{2})\b(?!\s*(?:Q|quarter))", re.I)
_DATE_QUARTER = re.compile(r"\b(?:([1-4])Q(\d{2})|Q([1-4])\s*[’']?(\d{2}|\d{4}))\b")

_CURRENCIES = [
    (r"US\$|U\.S\.\s?\$|USD\s?", "USD"),
    (r"C\$|CA\$|CAD\s?", "CAD"),
    (r"A\$|AUD\s?", "AUD"),
    (r"HK\$|HKD\s?", "HKD"),
    (r"NT\$|TWD\s?", "TWD"),
    (r"S\$|SGD\s?", "SGD"),
    (r"€|EUR\s?|Euro\s", "EUR"),
    (r"£|GBP\s?", "GBP"),
    (r"RMB\s?|CNY\s?", "CNY"),
    (r"¥|JPY\s?", "JPY"),
    (r"CHF\s?", "CHF"),
    (r"NIS\s?|₪", "ILS"),
    (r"\$", "USD"),
]
_CUR_PAT = "(?:" + "|".join(p for p, _ in _CURRENCIES) + ")"
MONEY = _CUR_PAT + r"\s?\d[\d,]*(?:\.\d+)?(?:\s*(?:billion|million|thousand|bn|mm|mn|m|b|k)\b)?"
_MONEY_RE = re.compile(
    r"(" + _CUR_PAT + r")\s?(\d[\d,]*(?:\.\d+)?)(?:\s*(billion|million|thousand|bn|mm|mn|m|b|k)\b)?", re.I
)
_MULT = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mm": 1e6, "mn": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}

_SHARES_RE = re.compile(
    r"(?:up to|repurchase|buy back) (?:an aggregate of |a total of |approximately |up to )?(\d[\d,]*(?:\.\d+)?)\s*(million)?\s+"
    r"(?:of (?:its|the Company[’']s) )?(?:outstanding )?(?:shares|ordinary shares|common shares|ADSs|units)",
    re.I,
)

_NOUN = (
    r"(?:re)?purchase (?:program|programme|plan|authori[sz]ation)|buy-?back|repurchase of up to|repurchase up to"
    r"|normal course issuer bid"
)
_BUYBACK_NOUN = re.compile(_NOUN, re.I)
_VERB = (
    r"(?:authori[sz]ed|authori[sz]es|approved|approves|adopted|adopts|renewed|renews|launch(?:ed|es)|"
    r"establish(?:ed|es)|implemented|increased|increases|expanded|expands|raised|raises|replenish\w*|upsiz\w*)"
)
# A decision ties the verb to the buyback itself: "Board authorized ... a share repurchase program",
# "the repurchase authorization was increased", "announces a new $X share repurchase program".
_DECISIONS = [
    re.compile(rf"\b{_VERB}\b[^.;]{{0,150}}?(?:{_NOUN})", re.I),
    re.compile(rf"(?:{_NOUN})[^.;]{{0,80}}?\b(?:was|were|has been|have been|is being)\s+{_VERB}\b", re.I),
    re.compile(
        r"\bannounc\w*\s+(?:that\s+)?(?:a|an|the|its)?\s*(?:new\s+|inaugural\s+|first\s+)?(?:" + MONEY + r"\s+)?"
        r"(?:share\s+|stock\s+|common stock\s+)?(?:re)?(?:purchase|buyback|buy-back)\s+(?:program|programme|plan|authori[sz]ation)",
        re.I,
    ),
]
_INCREASE_WORD = r"(?:increas\w*|rais(?:e|ed|es|ing)|expan\w*|upsiz\w*|augment\w*|replenish\w*|top[- ]up)"
_TARGET_WORD = r"(?:repurchase|buyback|buy-back|authori[sz]ation|program|programme|issuer bid)"
_INCREASE_PATTERNS = [
    re.compile(rf"\b{_INCREASE_WORD}\b[^.;]{{0,80}}?\b{_TARGET_WORD}\b", re.I),
    re.compile(rf"\b{_TARGET_WORD}\b[^.;]{{0,60}}?\b{_INCREASE_WORD}\b", re.I),
    re.compile(r"\badd?i?tional\s+(?:approximately\s+|up to\s+)?" + MONEY, re.I),
    re.compile(r"\bfrom\s+(?:approximately\s+)?" + MONEY + r"\s+to\s+(?:approximately\s+)?" + MONEY, re.I),
]
_NEW_PROGRAM = re.compile(r"\b(a|its) new\b[^.;]{0,60}?\b(re)?purchase (program|programme|plan|authori[sz]ation)", re.I)
_EXECUTED = re.compile(
    r"(?<!be )(?<!been )\b(repurchased|bought back|(?:has|have|had) purchased|purchased a total of"
    r"|executed (?:share )?repurchases|repurchases totall?ing)\b"
    r"|\bpurchased\s+\d[\d,.]*\s*(?:million\s+)?(?:of its own |own |ordinary |common )?shares"
    r"|\bpaid\s+" + MONEY + r"\s+for the repurchase"
    r"|\bentered into an? (accelerated share repurchase|ASR)",
    re.I,
)
# Mentions of an existing program ("announced on 30 October 2025", "previously approved",
# "authorized by the Board") name that program — they are not a decision made now.
_REFERENCES = [
    re.compile(rf"\b(?:previously\s+)?(?:announced|approved|authori[sz]ed|adopted)\s+(?:on|in|during|at|as of)\b[^.;]{{0,40}}?\d{{4}}", re.I),
    re.compile(r"\bpreviously\s+(?:announced|approved|authori[sz]ed|adopted|disclosed)\b", re.I),
    re.compile(r"\b(?:currently|already)\s+authori[sz]ed\b", re.I),
    re.compile(r"\b(?:approved|authori[sz]ed|adopted)\s+by\s+(?:the|its|our)\s+board", re.I),
    re.compile(r"\bauthori[sz]ed\s+(?:amount|but unissued|share capital)\b", re.I),
    # status ("the company is authorized to acquire ...") and adjectives ("its approved programs")
    re.compile(r"\b(?:is|was|are|were|remains?)\s+(?:currently\s+)?authori[sz]ed\s+to\b", re.I),
    re.compile(r"\b(?:its|the|our|their)\s+(?:approved|authori[sz]ed|adopted|existing)\s+(?:share\s+|stock\s+)?(?:re)?purchase", re.I),
]
SLIDE_PROSE_DENSITY = 2.5  # sentence ends per 1,000 chars; investor-deck text sits well below this
_NON_SHARE = re.compile(r"\b(notes|debentures|bonds|credit securities|convertible notes)\b", re.I)
_HIST_MARKERS = re.compile(
    r"\b(as previously (announced|disclosed|reported)|previously (announced|disclosed|approved|authori[sz]ed)"
    r"|had (authori[sz]ed|approved|renewed|adopted)|currently authori[sz]ed|remaining (under|on)"
    r"|since (the )?(inception|commencement))\b",
    re.I,
)
_ABBREVIATIONS = ["Inc.", "Corp.", "Co.", "Ltd.", "No.", "Nos.", "U.S.", "U.K.", "Mr.", "Ms.", "Dr.",
                  "N.V.", "S.A.", "L.P.", "plc.", "St.", "Jr.", "vs.", "approx.", "Jan.", "Feb.", "Mar.",
                  "Apr.", "Jun.", "Jul.", "Aug.", "Sep.", "Sept.", "Oct.", "Nov.", "Dec."]

_WORD_NUM = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|eighteen|twenty-four|thirty-six)"


def split_sentences(text: str) -> list[str]:
    for abbr in _ABBREVIATIONS:
        text = text.replace(abbr, abbr.replace(".", "\x00"))
    parts = re.split(r"(?<=[.;!?])\s+(?=[A-Z“\"(•])|\s•\s|\s◦\s", text)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


def _parse_money(match: re.Match) -> tuple[float, str]:
    prefix, number, unit = match.group(1), match.group(2), match.group(3)
    value = float(number.replace(",", ""))
    if unit:
        value *= _MULT[unit.lower()]
    token = prefix.strip()
    currency = next(
        (code for pat, code in _CURRENCIES if re.fullmatch(pat, token) or re.fullmatch(pat, token + " ")), "USD"
    )
    return value, currency


def _money_after(pattern: str, text: str) -> tuple[float, str] | None:
    """The first plausible program-sized amount right after `pattern`."""
    for m in re.finditer(pattern + r"(" + MONEY + r")", text, re.I):
        mm = _MONEY_RE.match(m.group(m.lastindex))
        if mm:
            value, currency = _parse_money(mm)
            if value >= MIN_PROGRAM_VALUE:
                return value, currency
    return None


def _amounts(text: str) -> list[tuple[float, str, int, int]]:
    """Program-size-like amounts: skips per-share prices, par values, and remaining balances."""
    found = []
    for m in _MONEY_RE.finditer(text):
        value, currency = _parse_money(m)
        tail = text[m.end(): m.end() + 45].lower()
        if value < MIN_PROGRAM_VALUE or re.match(r"\s*((per|a) (share|ads|unit)|par value)", tail):
            continue
        if re.match(r"\s*(of\s+)?(remaining|remains|remained|still available|available|unused|left)", tail):
            continue
        found.append((value, currency, m.start(), m.end()))
    return found


def _as_of_dates(text: str) -> list[dt.date]:
    """'As of <date>' marks a status snapshot (balances, counts), not when a decision was made."""
    dates = []
    for m in re.finditer(r"\bas of\s+(" + _ANY_DATE + ")", text, re.I):
        found = _event_dates("on " + m.group(1))
        dates.extend(found)
    return dates


def _event_dates(text: str) -> list[dt.date]:
    """Dates saying when something happened ('on', 'in', 'effective', 'since'),
    not horizons like 'through' / 'until' / 'expires on' or 'as of' snapshots."""
    dates = []

    def _is_event(start: int) -> bool:
        before = text[max(0, start - 30): start]
        if re.search(r"\b(expir\w*|through|until|ending|before|terminat\w*|matur\w*)\s+(on\s+|in\s+)?$", before, re.I):
            return False
        if re.search(r"\bas of\s*$", before, re.I):
            return False
        return bool(_EVENT_DATE_PREFIX.search(before))

    for m in _DATE_US.finditer(text):
        if _is_event(m.start()):
            try:
                dates.append(dt.date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2))))
            except ValueError:
                pass
    for m in _DATE_EU.finditer(text):
        if _is_event(m.start()):
            try:
                dates.append(dt.date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1))))
            except ValueError:
                pass
    for m in _DATE_MONTH_YEAR.finditer(text):
        if _is_event(m.start()):
            month, year = _MONTHS[m.group(1)[:3].lower()], int(m.group(2))
            next_month = dt.date(year + (month == 12), month % 12 + 1, 1)
            dates.append(next_month - dt.timedelta(days=1))  # latest possible day of that month
    for m in _DATE_YEAR.finditer(text):
        dates.append(dt.date(int(m.group(1)), 12, 31))
    for m in _DATE_QUARTER.finditer(text):
        quarter = int(m.group(1) or m.group(3))
        year = int(m.group(2) or m.group(4))
        year += 2000 if year < 100 else 0
        next_q = dt.date(year + (quarter == 4), (quarter * 3) % 12 + 1, 1)
        dates.append(next_q - dt.timedelta(days=1))
    return dates


def is_historical(text: str, filing_date: dt.date) -> bool:
    if re.search(r"\btoday\b", text, re.I):
        return False
    dates = _event_dates(text)
    cutoff = filing_date - dt.timedelta(days=RECENT_DAYS)
    if any(d >= cutoff for d in dates):
        return False
    if dates or any(d < cutoff for d in _as_of_dates(text)):
        return True
    return bool(_HIST_MARKERS.search(text))


def _strip_references(text: str) -> str:
    """Blank out references to existing programs, keeping every other character's position."""
    for pat in _REFERENCES:
        text = pat.sub(lambda m: " " * len(m.group()), text)
    return text


def _decisions(text: str) -> list[re.Match]:
    cleaned = _strip_references(text)
    return [m for pat in _DECISIONS for m in pat.finditer(cleaned)]


def _decision(text: str) -> re.Match | None:
    """The best-placed decision in a unit: a headline and the body sentence that
    restates it often share one unit, and the body is the better evidence."""
    found = _decisions(text)
    return max(found, key=lambda m: (_score(text, m.start()), -m.start())) if found else None


def _score(unit: str, pos: int = 0) -> int:
    """Prefer the plain-prose board decision over headlines, exhibit indexes and slide text.
    Judged on the text around the decision itself: a press release's headline and first
    paragraph often run together with no period between them."""
    sentence = unit[max(0, pos - 150): pos + 250]
    words = re.findall(r"[A-Za-z][A-Za-z’']+", sentence)
    long_words = [w for w in words if len(w) > 3]
    capitalized = sum(w[0].isupper() for w in long_words) / max(len(long_words), 1)
    score = 0
    score += 3 if re.search(r"\bboard\b", sentence, re.I) else 0
    score += 1 if re.search(r"\btoday\b|\bon \w+ \d{1,2}, \d{4}", sentence, re.I) else 0
    score += 1 if _amounts(sentence) else 0
    score -= 4 if capitalized > 0.6 else 0
    score -= 3 if re.search(r"exhibit|press release dated|entitled|titled", unit[max(0, pos - 80): pos + 40], re.I) else 0
    return score


def _units(text: str) -> list[tuple[str, str]]:
    """(unit, date_context) pairs. Units are sentences, except that a long run-on
    'sentence' (tables, headline blocks) is cut into local clauses around each buyback
    mention so unrelated words can't leak in. date_context adds a little text before
    the clause so a date cut off at its start is still seen by is_historical."""
    units = []
    for s in split_sentences(text):
        if len(s) <= 450:
            units.append((s, s))
            continue
        windows: list[list[int]] = []
        for m in sorted(list(_BUYBACK_NOUN.finditer(s)) + list(_EXECUTED.finditer(s)), key=lambda m: m.start()):
            start = max(0, m.start() - 220)
            end = min(len(s), m.end() + 220)
            stop = re.search(r"[.;]\s", s[end: end + 200])
            end = end + stop.end() if stop else end
            if windows and start <= windows[-1][1]:
                windows[-1][1] = max(windows[-1][1], end)  # overlapping mentions: one merged clause
            else:
                windows.append([start, end])
        units.extend((s[start:end], s[max(0, start - 80):end]) for start, end in windows)
    return units


def _quote(sentence: str, anchor: int = 0, limit: int = 320) -> str:
    if len(sentence) <= limit:
        return sentence
    start = max(0, anchor - 120)
    end = min(len(sentence), start + limit)
    return ("…" if start > 0 else "") + sentence[start:end].strip() + ("…" if end < len(sentence) else "")


def _expiration(text: str) -> str:
    if re.search(
        r"\b(?:no|does not have an?|without an?|has no|not subject to an?|no set|no fixed)\s+"
        r"(?:fixed |set |specified |stated )?(?:expiration|termination|end) date",
        text, re.I,
    ):
        return "No expiration date"
    m = re.search(rf"\bextend\w*\b[^.;]{{0,60}}?\b(?:to|through|until)\s+({_ANY_DATE})", text, re.I)
    if m:
        return f"Extended to {m.group(1)}"
    date_pat = rf"(?:{_ANY_DATE}|(?<=in )\d{{4}}|(?<=end of )\d{{4}})"
    m = re.search(rf"\b(?:expire[sd]?|expiring|expiration)\b[^.;]{{0,40}}?({date_pat})", text, re.I)
    if m:
        return f"Expires {m.group(1)}"
    m = re.search(rf"\b(through|until|ending(?: on)?|on or before|no later than)\s+(the end of \d{{4}}|{_ANY_DATE})", text, re.I)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    m = re.search(rf"\b({_WORD_NUM})[- ](month|year)s?\b(?:[- ]period| term)?", text, re.I)
    if m and re.search(r"period|over|term|within|duration", text[max(0, m.start() - 30): m.end() + 15], re.I):
        return f"{m.group(1)} {m.group(2)}{'s' if m.group(1) not in ('1', 'one') else ''}"
    return ""


def _replaces(text: str) -> str:
    if re.search(r"\b(replac\w*|supersed\w*|in (place|lieu) of|terminat\w*|cancel\w*)\b[^.;]{0,120}"
                 r"\b(prior|previous|existing|former|earlier|all other|outstanding)\b", text, re.I):
        return "Yes"
    if re.search(r"following the expiration of[^.;]{0,60}\b(prior|previous|existing|former)\b", text, re.I):
        return "Yes (prior program expired)"
    if re.search(r"\bin addition to\b[^.;]{0,80}\b(prior|previous|existing|current|remaining)\b", text, re.I):
        return "No (in addition to existing)"
    return "Not stated"


def _total_after(text: str) -> tuple[float, str] | None:
    patterns = [
        r"\b(?:total|aggregate|overall|cumulative)\s+(?:available\s+|remaining\s+)?(?:share\s+|stock\s+)?(?:repurchase\s+)?"
        r"(?:authori[sz]ation|amount authori[sz]ed|capacity|program size|program)\s+(?:of|is|to|at|will be|now stands at|of up to|up to)\s+(?:approximately\s+|about\s+)?",
        r"\bbring(?:s|ing)?\s+(?:the\s+)?(?:total|aggregate)[^.;$€£]{0,80}?\bto\s+(?:approximately\s+|about\s+)?",
        r"\bto a total of\s+(?:approximately\s+)?",
        r"\btotal amount authori[sz]ed\b[^.;$€£]{0,80}?\b(?:is|of|to)\s+(?:approximately\s+)?",
        r"\bfrom\s+(?:approximately\s+)?" + MONEY + r"\s+to\s+(?:approximately\s+)?",
        r"\bincreas\w*[^.;$€£]{0,120}?\bto\s+(?:approximately\s+|up to\s+)?",
    ]
    for pat in patterns:
        found = _money_after(pat, text)
        if found:
            return found
    return None


def _remaining(text: str) -> tuple[float, str] | None:
    for m in re.finditer(r"(" + MONEY + r")\s+(?:of\s+)?(?:remaining|remains|remained|still available|available|unused|left)", text, re.I):
        mm = _MONEY_RE.match(m.group(1))
        if mm and _parse_money(mm)[0] >= MIN_PROGRAM_VALUE:
            return _parse_money(mm)
    for pat in [r"\bremaining (?:authori[sz]ation|availability|capacity|amount|balance)\s+of\s+(?:approximately\s+)?",
                r"\bof which\s+(?:approximately\s+)?"]:
        found = _money_after(pat, text)
        if found:
            return found
    return None


def _increase_amount(sentence: str) -> tuple[float, str] | None:
    m = re.search(r"\bfrom\s+(?:approximately\s+)?(" + MONEY + r")\s+to\s+(?:approximately\s+)?(" + MONEY + r")", sentence, re.I)
    if m:
        (va, ca), (vb, cb) = _parse_money(_MONEY_RE.match(m.group(1))), _parse_money(_MONEY_RE.match(m.group(2)))
        if ca == cb and vb > va:
            return vb - va, cb
    for pat in [r"\bby\s+(?:an additional\s+|approximately\s+|up to\s+)?",
                r"\bincreas\w*\s+(?:of|in the amount of)\s+(?:up to\s+)?(?:an additional\s+)?",
                r"\badd?i?tional\s+(?:approximately\s+|up to\s+)?"]:
        found = _money_after(pat, sentence)
        if found:
            return found
    for m in re.finditer(r"(" + MONEY + r")\s+(?:increase|expansion|addition|top[- ]up)", sentence, re.I):
        value, currency = _parse_money(_MONEY_RE.match(m.group(1)))
        if value >= MIN_PROGRAM_VALUE:
            return value, currency
    return None


def _program_amount(first: str, from_pos: int, following: list[str]) -> tuple[float | None, str]:
    """Program size: first amount after the decision verb, then in the next sentences,
    and only then anything before the verb (e.g. a headline figure)."""
    for text in [first[from_pos:]] + following + [first]:
        amounts = _amounts(text)
        if amounts:
            return amounts[0][0], amounts[0][1]
        m = _SHARES_RE.search(text)
        if m:
            return float(m.group(1).replace(",", "")) * (1e6 if m.group(2) else 1), "shares"
    return None, ""


_SUFFIX_MONEY_RE = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s*(million|billion)?\s*(EUR|USD|GBP|CHF|euros?)\b", re.I)
_SUFFIX_CODES = {"eur": "EUR", "euro": "EUR", "euros": "EUR", "usd": "USD", "gbp": "GBP", "chf": "CHF"}


def _executed_amount(sentence: str, verb_pos: int = 0) -> tuple[float | None, str]:
    """Money actually spent: the first amount after the 'repurchased' verb that isn't
    the program's own size ('under the €250 million program')."""
    def _spent(amounts):
        for value, currency, start, end in amounts:
            if not re.match(r"\s*(share\s+|stock\s+)?(buy-?back|repurchase)\s+(program|programme|plan|authori)",
                            sentence[end: end + 40], re.I):
                return value, currency
        return None

    amounts = _amounts(sentence)
    found = _spent([a for a in amounts if a[2] >= verb_pos]) or _spent(amounts)
    if found:
        return found
    for m in _SUFFIX_MONEY_RE.finditer(sentence):
        value = float(m.group(1).replace(",", "")) * _MULT.get((m.group(2) or "").lower(), 1)
        if value >= MIN_PROGRAM_VALUE:
            return value, _SUFFIX_CODES[m.group(3).lower()]
    m = re.search(r"\b(?:repurchased|bought back|purchased)\s+(?:a total of\s+|approximately\s+|an aggregate of\s+)?"
                  r"(\d[\d,]*(?:\.\d+)?)\s*(million)?\s+(?:of (?:its|our) )?(?:own )?(?:shares|ordinary shares|ADSs|common shares)",
                  sentence, re.I)
    if m:
        return float(m.group(1).replace(",", "")) * (1e6 if m.group(2) else 1), "shares"
    return None, ""


def _event(kind: str, url: str, quote: str, amount=None, currency="", total=None, remaining=None,
           expiration="", replaces="") -> dict:
    return {"type": kind, "amount": amount, "currency": currency, "total_after": total,
            "remaining_prior": remaining, "expiration": expiration, "replaces": replaces,
            "quote": quote, "doc_url": url}


def extract_events(docs: list[tuple[str, str]], filing_date: dt.date) -> list[dict]:
    """Classify one filing (its matching documents as (url, text) pairs) into buyback events."""
    units: list[tuple[str, str, str]] = []  # (doc_url, unit, date_context)
    slide_docs = set()
    for url, text in docs:
        units.extend((url, u, ctx) for u, ctx in _units(text))
        if len(text) > 8000 and text.count(". ") / len(text) * 1000 < SLIDE_PROSE_DENSITY:
            slide_docs.add(url)

    candidates: dict[str, list[tuple[int, int, int]]] = defaultdict(list)  # type -> [(score, idx, verb_pos)]
    executed: list[int] = []
    review: list[int] = []
    for i, (url, s, ctx) in enumerate(units):
        noun = _BUYBACK_NOUN.search(s)
        if not noun and not _EXECUTED.search(s):
            continue
        if _NON_SHARE.search(s) and not re.search(r"\b(shares|stock|ADSs|equity)\b", s, re.I):
            continue
        decision = _decision(s)
        if not decision:
            if _EXECUTED.search(s) and (_amounts(s) or re.search(r"\d[\d,]*\s*(million\s+)?(shares|ADSs)", s)) \
                    and not is_historical(ctx, filing_date):
                executed.append(i)
            continue
        if is_historical(ctx, filing_date):
            continue
        following = [u for _, u, _ in units[i + 1: i + 3]]
        has_size = bool(_amounts(s) or _SHARES_RE.search(s) or any(_amounts(f) or _SHARES_RE.search(f) for f in following))
        score = _score(s, decision.start()) if url not in slide_docs else -1
        cleaned = _strip_references(s)
        if not _NEW_PROGRAM.search(s) and any(p.search(cleaned) for p in _INCREASE_PATTERNS):
            kind = EVENT_INCREASE
        elif has_size:
            kind = EVENT_NEW
        else:
            review.append(i)
            continue
        if score < 0:  # only headline / slide / exhibit-index evidence: flag it, don't assert it
            review.append(i)
            continue
        candidates[kind].append((score, i, decision.start()))

    events = []
    best = {k: max(v, key=lambda c: (c[0], -c[1])) for k, v in candidates.items() if v}
    kind = None
    if EVENT_INCREASE in best and (EVENT_NEW not in best or best[EVENT_INCREASE][0] >= best[EVENT_NEW][0]):
        kind = EVENT_INCREASE
    elif EVENT_NEW in best:
        kind = EVENT_NEW

    if kind:
        _, idx, verb_pos = best[kind]
        url, s, _ = units[idx]
        following = [u for _, u, _ in units[idx + 1: idx + 4]]
        window = " ".join([s] + following)
        context = " ".join(u for _, u, _ in units[max(0, idx - 1): idx + 6])
        if kind == EVENT_INCREASE:
            amount, currency = _increase_amount(s) or _increase_amount(window) or (None, "")
        else:
            amount, currency = _program_amount(s, verb_pos, following)
        total = _total_after(window)
        if kind == EVENT_NEW and total and amount and total[0] == amount:
            total = None  # just the new program's own size restated
        remaining = _remaining(context) or _remaining(" ".join(u for _, u, _ in units if _BUYBACK_NOUN.search(u)))
        currency = currency or (total or remaining or (None, ""))[1]
        events.append(_event(
            kind, url, _quote(s, verb_pos), amount, currency,
            total[0] if total and total[1] == currency else None,
            remaining[0] if remaining and remaining[1] == currency else None,
            _expiration(window) or _expiration(context),
            _replaces(context),
        ))
    elif review:
        def _review_rank(i):
            d = _decision(units[i][1])
            return _score(units[i][1], d.start() if d else 0), -i

        idx = max(review, key=_review_rank)
        url, s, _ = units[idx]
        decision = _decision(s)
        events.append(_event(EVENT_REVIEW, url, _quote(s, decision.start() if decision else 0), replaces="Not stated"))

    if executed:
        idx = max(executed, key=lambda i: (bool(_amounts(units[i][1])), -i))
        url, s, _ = units[idx]
        m = _EXECUTED.search(s)
        amount, currency = _executed_amount(s, m.start() if m else 0)
        events.append(_event(EVENT_EXECUTED, url, _quote(s, m.start() if m else 0), amount, currency))
    return events


def _accepted_et(raw: str | None, fallback: str) -> str:
    if not raw:
        return f"{fallback} (time n/a)"
    stamp = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return stamp.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M ET")


def run_buyback_scan(days_back: int = 14, progress_callback=None) -> pd.DataFrame:
    def _progress(i, total, label):
        if progress_callback:
            progress_callback(i, total, label)

    end = dt.date.today()
    start = end - dt.timedelta(days=days_back)
    hits = sec_client.search_filings(SEARCH_QUERY, start, end, progress_callback=_progress)

    filings: dict[str, dict] = {}
    for h in hits:
        accession, filename = h["_id"].split(":", 1)
        if filename.lower().endswith(".pdf"):
            continue
        src = h["_source"]
        f = filings.setdefault(accession, {
            "cik": src["ciks"][0],
            "form": (src.get("root_forms") or [src.get("form", "")])[0],
            "file_date": src["file_date"],
            "display_name": (src.get("display_names") or [""])[0],
            "docs": [],
        })
        f["docs"].append((src.get("file_type", ""), filename))

    # The main 8-K / 6-K body first, then its exhibits (usually the press release).
    jobs = [
        (acc, f, fname)
        for acc, f in filings.items()
        for _, fname in sorted(f["docs"], key=lambda d: not d[0].startswith(("8-K", "6-K")))
    ]
    texts: dict[str, list[tuple[str, str]]] = defaultdict(list)
    done = [0]

    def _fetch(job):
        acc, f, fname = job
        url = sec_client.document_url(f["cik"], acc, fname)
        try:
            text = sec_client.get_document_text(url)
        except sec_client.SecError:
            text = ""
        done[0] += 1
        _progress(done[0], len(jobs), f"Reading filings: {f['display_name'][:40]}")
        return acc, url, text

    with ThreadPoolExecutor(max_workers=6) as pool:
        for acc, url, text in pool.map(_fetch, jobs):
            if text:
                texts[acc].append((url, text))

    raw_events = []
    for acc, f in filings.items():
        filing_date = dt.date.fromisoformat(f["file_date"])
        for ev in extract_events(texts.get(acc, []), filing_date):
            raw_events.append((acc, f, ev))

    ciks = list(dict.fromkeys(f["cik"] for _, f, _ in raw_events))
    companies: dict[str, dict] = {}
    for i, cik in enumerate(ciks, start=1):
        try:
            companies[cik] = sec_client.get_company(cik)
        except sec_client.SecError:
            companies[cik] = {"name": "", "tickers": [], "acceptance": {}}
        _progress(i, len(ciks), "Looking up company details")

    tickers = [c["tickers"][0] for c in companies.values() if c["tickers"]]
    _progress(1, 1, f"Fetching market caps for {len(set(tickers))} companies...")
    market_data.prefetch_ticker_info(tickers)

    rows = []
    for acc, f, ev in raw_events:
        company = companies[f["cik"]]
        ticker = company["tickers"][0] if company["tickers"] else ""
        market_cap = None
        if ticker:
            try:
                market_cap = market_data.get_market_cap(ticker)
            except market_data.MarketDataError:
                pass
        rows.append({
            "Filed (ET)": _accepted_et(company["acceptance"].get(acc), f["file_date"]),
            "Company": company["name"] or re.sub(r"\s*\(.*$", "", f["display_name"]),
            "Ticker": ticker,
            "Event type": ev["type"],
            "Amount": ev["amount"],
            "Currency": ev["currency"],
            "Total authorized after": ev["total_after"],
            "Remaining from prior program": ev["remaining_prior"],
            "Expiration / duration": ev["expiration"],
            "Replaces prior program?": ev["replaces"],
            "Supporting quote": ev["quote"],
            "Market cap ($M)": round(market_cap / 1e6, 1) if market_cap else None,
            "Form": f["form"],
            "Filing link": ev["doc_url"],
            "Filing index": sec_client.filing_index_url(f["cik"], acc),
        })

    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    if not df.empty:
        # One announcement is often filed twice (e.g. an 8-K plus a separate press-release filing).
        day = df["Filed (ET)"].str[:10]
        df = df[~df.assign(_day=day).duplicated(subset=["Company", "Event type", "Amount", "Total authorized after", "_day"])]
        df = df.sort_values("Filed (ET)", ascending=False).reset_index(drop=True)
    return df
