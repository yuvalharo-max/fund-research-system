"""Rule-based (no-LLM) summary/hypothesis text generation."""
from __future__ import annotations


def fund_hypothesis(fund_name: str, company_name: str, add_pct: float, drop_pct: float, quarter: str) -> str:
    return (
        f"{fund_name} הגדילה את האחזקה ב{company_name} בכ-{add_pct:.1f}% ברבעון {quarter}, "
        f"בזמן שהמחיר המדווח צנח בכ-{drop_pct:.1f}% לעומת הרבעון הקודם. "
        f"השילוב בין הגדלת אחזקה לירידת מחיר עשוי לרמז שהקרן מעריכה פער בין מחיר לשווי פנימי — "
        f"זו השערה בלבד המבוססת על נתוני האחזקה, ולא ייעוץ השקעה."
    )


def magic_formula_hypothesis(company_name: str, earnings_yield: float | None, roic: float | None) -> str:
    parts = [f"{company_name} מופיעה בדירוג Magic Formula"]
    if earnings_yield is not None:
        parts.append(f"עם תשואת רווח (Earnings Yield) של כ-{earnings_yield:.1f}%")
    if roic is not None:
        parts.append(f"ותשואה על ההון המושקע (ROIC) של כ-{roic:.1f}%")
    parts.append(
        "— שילוב שעל פי מתודולוגיית Magic Formula עשוי להצביע על תמחור-חסר יחסי לסקטור; "
        "זו השערה שדורשת בדיקה נוספת, ולא ייעוץ השקעה."
    )
    return " ".join(parts)


def business_summary_fallback(company_name: str) -> str:
    return f"אין תקציר עסקי זמין עבור {company_name}. מומלץ לבדוק את דוחות החברה ישירות."
