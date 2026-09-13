"""כלי מחקר שוק — ניתוח קרנות (Dataroma) ו-Magic Formula Investing."""
from collections import defaultdict

import streamlit as st
from dotenv import load_dotenv

from src import cache, dataroma_client, magicformula_analysis, magicformula_client, funds_analysis

load_dotenv()

st.set_page_config(page_title="כלי מחקר שוק", layout="wide")
st.title("כלי מחקר שוק ואיתור רעיונות השקעה")

tab1, tab2 = st.tabs(["ניתוח קרנות (Funds Analysis)", "ניתוח מאגר Magic Formula"])


def _holdings_by_stock() -> dict[str, set[str]]:
    """Build a stock -> {fund names} index from Dataroma, used to cross-reference tab 2."""
    holdings_by_stock: dict[str, set[str]] = defaultdict(set)
    funds = dataroma_client.get_superinvestors()
    for fund in funds:
        holdings, _ = dataroma_client.get_holdings(fund["ticker"], fund["name"])
        for h in holdings:
            holdings_by_stock[h.stock_ticker].add(h.fund_name)
    return holdings_by_stock


with tab1:
    st.write(
        "מזהה מניות בהן קרן מהרשימה **הגדילה אחזקה** ברבעון האחרון, "
        "בזמן שהמחיר המדווח **צנח ב-10% ומעלה** לעומת הרבעון הקודם."
    )
    last_run = cache.load_latest_timestamp("funds")
    if last_run:
        st.caption(f"עודכן לאחרונה: {last_run}")

    if st.button("הרץ ניתוח קרנות", type="primary"):
        progress = st.progress(0.0, text="מתחיל...")
        status = st.empty()

        def _update(i: int, total: int, name: str):
            progress.progress(i / total, text=f"({i}/{total}) שולף נתונים: {name}")

        try:
            df = funds_analysis.run_funds_analysis(progress_callback=_update)
        except (dataroma_client.DataromaError,) as exc:
            st.error(f"הריצה נכשלה: {exc}")
        else:
            progress.empty()
            if df.empty:
                st.info("לא נמצאו מניות שעונות על שני התנאים בריצה הזו.")
            else:
                st.dataframe(df, use_container_width=True)
                st.download_button(
                    "ייצוא ל-CSV", df.to_csv(index=False).encode("utf-8-sig"), "funds_analysis.csv", "text/csv"
                )
            timestamp = cache.save_run("funds", df)
            st.caption(f"עודכן לאחרונה: {timestamp}")

with tab2:
    st.write("מציג חברות ממאגר Magic Formula Investing עם שווי שוק מעל מיליארד דולר.")
    last_run = cache.load_latest_timestamp("magicformula")
    if last_run:
        st.caption(f"עודכן לאחרונה: {last_run}")

    if st.button("עדכן ניתוח Magic Formula", type="primary"):
        progress = st.progress(0.0, text="מתחיל...")

        def _update(i: int, total: int, name: str):
            progress.progress(i / total, text=f"({i}/{total}) מעשיר מידע: {name}")

        try:
            holdings_index = _holdings_by_stock()
            df = magicformula_analysis.run_magic_formula_analysis(
                holdings_by_stock=holdings_index, progress_callback=_update
            )
        except (magicformula_client.MagicFormulaError, dataroma_client.DataromaError) as exc:
            st.error(f"הריצה נכשלה: {exc}")
        else:
            progress.empty()
            if df.empty:
                st.info("לא נמצאו תוצאות בריצה הזו.")
            else:
                st.dataframe(df, use_container_width=True)
                st.download_button(
                    "ייצוא ל-CSV",
                    df.to_csv(index=False).encode("utf-8-sig"),
                    "magic_formula_analysis.csv",
                    "text/csv",
                )
            timestamp = cache.save_run("magicformula", df)
            st.caption(f"עודכן לאחרונה: {timestamp}")
