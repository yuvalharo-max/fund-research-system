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

STATUS_OPTION_LABELS = list(status_store.STATUS_LABELS.values())
LABEL_TO_STATUS = {v: k for k, v in status_store.STATUS_LABELS.items()}

LINK_COLUMN_CONFIG = {
    "Yahoo Finance": st.column_config.LinkColumn("Yahoo Finance", display_text="Open ↗", width="small"),
    "IR Search": st.column_config.LinkColumn("IR Search", display_text="Search ↗", width="small"),
    "Status": st.column_config.SelectboxColumn("Status", options=STATUS_OPTION_LABELS, width="small"),
}


def _holdings_by_stock(progress_callback=None) -> dict[str, set[str]]:
    """Build a stock -> {fund names} index from Dataroma, used to cross-reference tab 2."""
    holdings_by_stock: dict[str, set[str]] = defaultdict(set)
    funds = dataroma_client.get_superinvestors()
    all_holdings, _ = dataroma_client.get_all_holdings(funds, progress_callback=progress_callback)
    for h in all_holdings:
        holdings_by_stock[h.stock_ticker].add(h.fund_name)
    return holdings_by_stock


def _render_results(df, csv_name: str, display_cols: list[str], tab_key: str, has_fund: bool):
    """Filters + an editable grid: change the Status cell inline to mark read/starred/archived."""
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

    filtered["Status"] = filtered["_status"].apply(lambda s: status_store.STATUS_LABELS[s])
    row_keys = filtered["_key"].reset_index(drop=True)
    grid_df = filtered[["Status"] + display_cols].reset_index(drop=True)

    edited = st.data_editor(
        grid_df,
        width="stretch",
        column_config=LINK_COLUMN_CONFIG,
        disabled=display_cols,
        hide_index=True,
        key=f"{tab_key}_editor",
    )

    changed = False
    for i in range(len(edited)):
        new_status = LABEL_TO_STATUS[edited.loc[i, "Status"]]
        rk = row_keys.iloc[i]
        if status_store.get_status(status, rk) != new_status:
            status_store.set_status(tab_key, status, rk, new_status)
            changed = True
    if changed:
        st.rerun()


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
        "while the reported price **dropped** versus the prior quarter."
    )
    st.caption("Data source: [Dataroma](https://www.dataroma.com)")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        min_drop_pct = st.number_input(
            "Minimum price drop % (vs prior quarter)", min_value=0.0, value=10.0, step=1.0
        )
    with col_b:
        min_add_pct = st.number_input("Minimum position increase %", min_value=0.0, value=0.0, step=1.0)
    with col_c:
        fund_limit = st.number_input(
            "Quick test: limit to first N funds (0 = all 83)", min_value=0, value=0, step=5
        )

    display_cols = [
        "Company", "Fund", "% of Portfolio", "Price drop %", "Position increase %",
        "Summary", "Other holders", "Yahoo Finance", "IR Search",
    ]

    ran_now = False
    if st.button("Run Funds Analysis", type="primary"):
        ran_now = True
        progress = st.progress(0.0, text="Starting...")

        def _update(i: int, total: int, label: str):
            progress.progress(i / total, text=f"({i}/{total}) {label}")

        try:
            df = funds_analysis.run_funds_analysis(
                progress_callback=_update,
                fund_limit=fund_limit or None,
                min_drop_pct=min_drop_pct,
                min_add_pct=min_add_pct,
            )
        except (dataroma_client.DataromaError,) as exc:
            st.error(f"Run failed: {exc}")
            ran_now = False
        else:
            progress.empty()
            timestamp = cache.save_run("funds", df)
            st.caption(f"Last updated: {timestamp}")
            _render_results(df, "funds_analysis.csv", display_cols, tab_key="funds", has_fund=True)

    if not ran_now:
        last_run = cache.load_latest_timestamp("funds")
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run("funds")
            if cached_df is not None:
                _render_results(cached_df, "funds_analysis.csv", display_cols, tab_key="funds", has_fund=True)
        else:
            st.info("No run yet — click \"Run Funds Analysis\" above to get started.")

else:
    st.write("Shows companies from the Magic Formula Investing screener with market cap over $1B.")
    st.caption("Data source: [Magic Formula Investing](https://www.magicformulainvesting.com)")

    display_cols = ["Company", "Summary", "Funds holding it", "Market cap ($M)", "Yahoo Finance", "IR Search"]

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
            _render_results(df, "magic_formula_analysis.csv", display_cols, tab_key="magicformula", has_fund=False)

    if not ran_now:
        last_run = cache.load_latest_timestamp("magicformula")
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run("magicformula")
            if cached_df is not None:
                _render_results(
                    cached_df, "magic_formula_analysis.csv", display_cols, tab_key="magicformula", has_fund=False
                )
        else:
            st.info("No run yet — click \"Update Magic Formula Analysis\" above to get started.")
