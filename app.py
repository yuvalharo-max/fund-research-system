"""Market research tool — fund 13F analysis (Dataroma) and Magic Formula Investing."""
from collections import defaultdict

import streamlit as st
from dotenv import load_dotenv

from src import cache, dataroma_client, magicformula_analysis, magicformula_client, funds_analysis, status_store

load_dotenv()

st.set_page_config(page_title="Market Research Tool", layout="wide")
st.title("Market Research & Investment Idea Finder")

TAB_LABELS = {"funds": "Funds Analysis", "magicformula": "Magic Formula Analysis"}

STATUS_FILTER_OPTIONS = [
    "All (hide archived)",
    "Unread only",
    "⭐ Starred only",
    "🗑 Archived only",
    "Everything",
]


def _holdings_by_stock(progress_callback=None) -> dict[str, set[str]]:
    """Build a stock -> {fund names} index from Dataroma, used to cross-reference tab 2."""
    holdings_by_stock: dict[str, set[str]] = defaultdict(set)
    funds = dataroma_client.get_superinvestors()
    all_holdings, _ = dataroma_client.get_all_holdings(funds, progress_callback=progress_callback)
    for h in all_holdings:
        holdings_by_stock[h.stock_ticker].add(h.fund_name)
    return holdings_by_stock


def _row_summary_label(row, has_fund: bool) -> str:
    if has_fund:
        return (
            f"{row['Company']}   ·   {row['Fund']}   ·   "
            f"Drop {row['Price drop %']}%  /  Add {row['Position increase %']}%"
        )
    cap = row["Market cap ($M)"]
    cap_text = f"${cap:,.0f}M" if cap == cap else "n/a"  # NaN check
    return f"{row['Company']}   ·   Market cap {cap_text}"


def _render_results(df, csv_name: str, detail_cols: list[str], tab_key: str, has_fund: bool):
    """Filters + a clickable row list: click a row to expand its detail, or set its status inline."""
    if df.empty:
        st.info("No results to show.")
        return

    status = status_store.load_status(tab_key)

    def key_of(row) -> str:
        return status_store.row_key(row["Company"], row["Fund"] if has_fund else None)

    filter_cols = st.columns([2, 2, 3] if has_fund else [2, 2])
    with filter_cols[0]:
        status_filter = st.selectbox("Show", STATUS_FILTER_OPTIONS, key=f"{tab_key}_status_filter")
    with filter_cols[1]:
        search = st.text_input("Search company", key=f"{tab_key}_search")
    fund_filter: list[str] = []
    if has_fund:
        with filter_cols[2]:
            fund_filter = st.multiselect("Filter by fund", sorted(df["Fund"].unique()), key=f"{tab_key}_fund_filter")

    filtered = df.copy()
    if search:
        filtered = filtered[filtered["Company"].str.contains(search, case=False, na=False)]
    if fund_filter:
        filtered = filtered[filtered["Fund"].isin(fund_filter)]

    filtered["_key"] = filtered.apply(key_of, axis=1)
    filtered["_status"] = filtered["_key"].apply(lambda k: status_store.get_status(status, k))

    if status_filter == "All (hide archived)":
        filtered = filtered[filtered["_status"] != status_store.STATUS_ARCHIVED]
    elif status_filter == "Unread only":
        filtered = filtered[filtered["_status"] == status_store.STATUS_UNREAD]
    elif status_filter == "⭐ Starred only":
        filtered = filtered[filtered["_status"] == status_store.STATUS_STARRED]
    elif status_filter == "🗑 Archived only":
        filtered = filtered[filtered["_status"] == status_store.STATUS_ARCHIVED]

    st.caption(f"Showing {len(filtered)} of {len(df)} rows")
    st.download_button(
        "Export full detail to CSV", df.to_csv(index=False).encode("utf-8-sig"), csv_name, "text/csv"
    )

    if filtered.empty:
        st.info("No rows match the current filters.")
        return

    for _, row in filtered.iterrows():
        row_key = row["_key"]
        current_status = row["_status"]
        expand_flag = f"{tab_key}_expanded_{row_key}"
        if expand_flag not in st.session_state:
            st.session_state[expand_flag] = False

        col_status, col_main, col_link1, col_link2 = st.columns([1.6, 5, 0.7, 0.7])

        with col_status:
            new_status = st.selectbox(
                "Status",
                status_store.STATUS_OPTIONS,
                index=status_store.STATUS_OPTIONS.index(current_status),
                format_func=lambda s: status_store.STATUS_LABELS[s],
                key=f"{tab_key}_statussel_{row_key}",
                label_visibility="collapsed",
            )
            if new_status != current_status:
                status_store.set_status(tab_key, status, row_key, new_status)
                st.rerun()

        with col_main:
            if st.button(
                _row_summary_label(row, has_fund),
                key=f"{tab_key}_toggle_{row_key}",
                use_container_width=True,
            ):
                st.session_state[expand_flag] = not st.session_state[expand_flag]

        with col_link1:
            st.link_button("Open ↗", row["Yahoo Finance"], use_container_width=True)
        with col_link2:
            st.link_button("Search ↗", row["IR Search"], use_container_width=True)

        if st.session_state[expand_flag]:
            with st.container(border=True):
                for col in detail_cols:
                    if col in row and str(row[col]) not in ("", "nan", "-"):
                        st.markdown(f"**{col}:** {row[col]}")

        st.divider()


params = st.query_params
active_tab = params.get("tab", "funds")
if active_tab not in TAB_LABELS:
    active_tab = "funds"

selected_tab = st.segmented_control(
    "View", options=list(TAB_LABELS.keys()), format_func=lambda k: TAB_LABELS[k], default=active_tab
)
if selected_tab is None:
    selected_tab = active_tab
st.query_params["tab"] = selected_tab

st.divider()

if selected_tab == "funds":
    st.write(
        "Finds stocks where a fund on the list **increased its position** last quarter, "
        "while the reported price **dropped 10% or more** versus the prior quarter."
    )

    with st.expander("Quick test (optional)"):
        fund_limit = st.number_input(
            "Limit to first N funds (0 = all 83 funds)", min_value=0, value=0, step=5
        )

    detail_cols = ["Summary", "Hypothesis", "Similar companies (same fund)", "Other holders"]

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
            _render_results(df, "funds_analysis.csv", detail_cols, tab_key="funds", has_fund=True)

    if not ran_now:
        last_run = cache.load_latest_timestamp("funds")
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run("funds")
            if cached_df is not None:
                _render_results(cached_df, "funds_analysis.csv", detail_cols, tab_key="funds", has_fund=True)
        else:
            st.info("No run yet — click \"Run Funds Analysis\" above to get started.")

else:
    st.write("Shows companies from the Magic Formula Investing screener with market cap over $1B.")

    detail_cols = ["Summary", "Hypothesis", "Similar companies (screener)", "Funds holding it"]

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
            _render_results(df, "magic_formula_analysis.csv", detail_cols, tab_key="magicformula", has_fund=False)

    if not ran_now:
        last_run = cache.load_latest_timestamp("magicformula")
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run("magicformula")
            if cached_df is not None:
                _render_results(
                    cached_df, "magic_formula_analysis.csv", detail_cols, tab_key="magicformula", has_fund=False
                )
        else:
            st.info("No run yet — click \"Update Magic Formula Analysis\" above to get started.")
