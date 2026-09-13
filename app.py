"""Market research tool — fund 13F analysis (Dataroma) and Magic Formula Investing."""
from collections import defaultdict

import streamlit as st
from dotenv import load_dotenv

from src import cache, dataroma_client, magicformula_analysis, magicformula_client, funds_analysis

load_dotenv()

st.set_page_config(page_title="Market Research Tool", layout="wide")
st.title("Market Research & Investment Idea Finder")

tab1, tab2 = st.tabs(["Funds Analysis", "Magic Formula Analysis"])

LINK_COLUMN_CONFIG = {
    "Yahoo Finance": st.column_config.LinkColumn("Yahoo Finance", display_text="Open ↗"),
    "IR Search": st.column_config.LinkColumn("IR Search", display_text="Search ↗"),
}


def _holdings_by_stock(progress_callback=None) -> dict[str, set[str]]:
    """Build a stock -> {fund names} index from Dataroma, used to cross-reference tab 2."""
    holdings_by_stock: dict[str, set[str]] = defaultdict(set)
    funds = dataroma_client.get_superinvestors()
    all_holdings, _ = dataroma_client.get_all_holdings(funds, progress_callback=progress_callback)
    for h in all_holdings:
        holdings_by_stock[h.stock_ticker].add(h.fund_name)
    return holdings_by_stock


def _render_results(df, csv_name: str):
    if df.empty:
        st.info("No results to show.")
    else:
        st.dataframe(df, use_container_width=True, column_config=LINK_COLUMN_CONFIG)
        st.download_button("Export to CSV", df.to_csv(index=False).encode("utf-8-sig"), csv_name, "text/csv")


with tab1:
    st.write(
        "Finds stocks where a fund on the list **increased its position** last quarter, "
        "while the reported price **dropped 10% or more** versus the prior quarter."
    )

    with st.expander("Quick test (optional)"):
        fund_limit = st.number_input(
            "Limit to first N funds (0 = all 83 funds)", min_value=0, value=0, step=5
        )

    ran_now = False
    if st.button("Run Funds Analysis", type="primary"):
        ran_now = True
        progress = st.progress(0.0, text="Starting...")

        def _update(i: int, total: int, label: str):
            progress.progress(i / total, text=f"({i}/{total}) {label}")

        try:
            df = funds_analysis.run_funds_analysis(progress_callback=_update, fund_limit=fund_limit or None)
        except (dataroma_client.DataromaError,) as exc:
            st.error(f"Run failed: {exc}")
            ran_now = False
        else:
            progress.empty()
            timestamp = cache.save_run("funds", df)
            st.caption(f"Last updated: {timestamp}")
            _render_results(df, "funds_analysis.csv")

    if not ran_now:
        last_run = cache.load_latest_timestamp("funds")
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run("funds")
            if cached_df is not None:
                _render_results(cached_df, "funds_analysis.csv")
        else:
            st.info("No run yet — click \"Run Funds Analysis\" above to get started.")

with tab2:
    st.write("Shows companies from the Magic Formula Investing screener with market cap over $1B.")

    ran_now = False
    if st.button("Update Magic Formula Analysis", type="primary"):
        ran_now = True
        progress = st.progress(0.0, text="Starting...")

        def _scrape_update(i: int, total: int, name: str):
            progress.progress(i / total, text=f"({i}/{total}) Fetching fund list: {name}")

        def _update(i: int, total: int, name: str):
            progress.progress(i / total, text=f"({i}/{total}) Enriching data: {name}")

        try:
            holdings_index = _holdings_by_stock(progress_callback=_scrape_update)
            df = magicformula_analysis.run_magic_formula_analysis(
                holdings_by_stock=holdings_index, progress_callback=_update
            )
        except (magicformula_client.MagicFormulaError, dataroma_client.DataromaError) as exc:
            st.error(f"Run failed: {exc}")
            ran_now = False
        else:
            progress.empty()
            timestamp = cache.save_run("magicformula", df)
            st.caption(f"Last updated: {timestamp}")
            _render_results(df, "magic_formula_analysis.csv")

    if not ran_now:
        last_run = cache.load_latest_timestamp("magicformula")
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run("magicformula")
            if cached_df is not None:
                _render_results(cached_df, "magic_formula_analysis.csv")
        else:
            st.info("No run yet — click \"Update Magic Formula Analysis\" above to get started.")
