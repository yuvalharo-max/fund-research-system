"""Market research tool — fund 13F analysis (Dataroma) and Magic Formula Investing."""
from collections import defaultdict

import streamlit as st
from dotenv import load_dotenv

from src import cache, dataroma_client, magicformula_analysis, magicformula_client, funds_analysis, status_store

load_dotenv()

st.set_page_config(page_title="Market Research Tool", layout="wide")
st.title("Market Research & Investment Idea Finder")

LINK_COLUMN_CONFIG = {
    "Yahoo Finance": st.column_config.LinkColumn("Yahoo Finance", display_text="Open ↗", width="small"),
    "IR Search": st.column_config.LinkColumn("IR Search", display_text="Search ↗", width="small"),
}

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


def _status_icon(item: dict) -> str:
    icon = "✓ " if item.get("read") else ""
    if item.get("priority") == status_store.PRIORITY_STARRED:
        icon += "⭐"
    elif item.get("priority") == status_store.PRIORITY_ARCHIVED:
        icon += "🗑"
    return icon.strip() or "—"


def _render_results(df, csv_name: str, compact_cols: list[str], detail_cols: list[str], key: str, has_fund: bool):
    """Filters + a compact, scannable table; clicking a row reveals full detail and mark-as actions."""
    if df.empty:
        st.info("No results to show.")
        return

    status = status_store.load_status(key)

    def key_of(row) -> str:
        return status_store.row_key(row["Company"], row["Fund"] if has_fund else None)

    filter_cols = st.columns([2, 2, 3] if not has_fund else [2, 2, 2, 3])
    with filter_cols[0]:
        status_filter = st.selectbox("Show", STATUS_FILTER_OPTIONS, key=f"{key}_status_filter")
    with filter_cols[1]:
        search = st.text_input("Search company", key=f"{key}_search")
    fund_filter: list[str] = []
    if has_fund:
        with filter_cols[2]:
            fund_filter = st.multiselect("Filter by fund", sorted(df["Fund"].unique()), key=f"{key}_fund_filter")

    filtered = df.copy()
    if search:
        filtered = filtered[filtered["Company"].str.contains(search, case=False, na=False)]
    if fund_filter:
        filtered = filtered[filtered["Fund"].isin(fund_filter)]

    filtered["_key"] = filtered.apply(key_of, axis=1)
    filtered["_item"] = filtered["_key"].apply(lambda k: status_store.get_item(status, k))
    filtered["Status"] = filtered["_item"].apply(_status_icon)

    if status_filter == "All (hide archived)":
        filtered = filtered[filtered["_item"].apply(lambda i: i.get("priority") != status_store.PRIORITY_ARCHIVED)]
    elif status_filter == "Unread only":
        filtered = filtered[
            filtered["_item"].apply(lambda i: not i.get("read") and i.get("priority") != status_store.PRIORITY_ARCHIVED)
        ]
    elif status_filter == "⭐ Starred only":
        filtered = filtered[filtered["_item"].apply(lambda i: i.get("priority") == status_store.PRIORITY_STARRED)]
    elif status_filter == "🗑 Archived only":
        filtered = filtered[filtered["_item"].apply(lambda i: i.get("priority") == status_store.PRIORITY_ARCHIVED)]

    st.caption(f"Showing {len(filtered)} of {len(df)} rows")

    if filtered.empty:
        st.info("No rows match the current filters.")
        return

    event = st.dataframe(
        filtered[["Status"] + compact_cols],
        width="stretch",
        column_config=LINK_COLUMN_CONFIG,
        on_select="rerun",
        selection_mode="single-row",
        key=f"{key}_grid",
    )
    st.download_button(
        "Export full detail to CSV", df.to_csv(index=False).encode("utf-8-sig"), csv_name, "text/csv"
    )

    selected_rows = event.selection.rows if event and event.selection else []
    if not selected_rows:
        st.caption("Click a row above to see its full summary/hypothesis and to mark it read, starred, or archived.")
        return

    row = filtered.iloc[selected_rows[0]]
    row_key = row["_key"]
    item = row["_item"]

    with st.container(border=True):
        st.markdown(f"#### {row['Company']}")
        for col in detail_cols:
            if col in row and str(row[col]) not in ("", "nan", "-"):
                st.markdown(f"**{col}:** {row[col]}")

        action_cols = st.columns(4)
        with action_cols[0]:
            new_read = st.checkbox("Read", value=item.get("read", False), key=f"{key}_read_{row_key}")
            if new_read != item.get("read", False):
                status_store.set_item(key, status, row_key, read=new_read)
                st.rerun()
        with action_cols[1]:
            if st.button("⭐ Follow up later", key=f"{key}_star_{row_key}"):
                status_store.set_item(key, status, row_key, priority=status_store.PRIORITY_STARRED)
                st.rerun()
        with action_cols[2]:
            if st.button("🗑 Not relevant", key=f"{key}_arch_{row_key}"):
                status_store.set_item(key, status, row_key, priority=status_store.PRIORITY_ARCHIVED)
                st.rerun()
        with action_cols[3]:
            if item.get("priority") and st.button("Clear flag", key=f"{key}_clear_{row_key}"):
                status_store.set_item(key, status, row_key, priority=None)
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
        "while the reported price **dropped 10% or more** versus the prior quarter."
    )

    with st.expander("Quick test (optional)"):
        fund_limit = st.number_input(
            "Limit to first N funds (0 = all 83 funds)", min_value=0, value=0, step=5
        )

    compact_cols = ["Company", "Fund", "Price drop %", "Position increase %", "Yahoo Finance", "IR Search"]
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
            _render_results(df, "funds_analysis.csv", compact_cols, detail_cols, key="funds", has_fund=True)

    if not ran_now:
        last_run = cache.load_latest_timestamp("funds")
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run("funds")
            if cached_df is not None:
                _render_results(cached_df, "funds_analysis.csv", compact_cols, detail_cols, key="funds", has_fund=True)
        else:
            st.info("No run yet — click \"Run Funds Analysis\" above to get started.")

else:
    st.write("Shows companies from the Magic Formula Investing screener with market cap over $1B.")

    compact_cols = ["Company", "Market cap ($M)", "Yahoo Finance", "IR Search"]
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
            _render_results(
                df, "magic_formula_analysis.csv", compact_cols, detail_cols, key="magicformula", has_fund=False
            )

    if not ran_now:
        last_run = cache.load_latest_timestamp("magicformula")
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run("magicformula")
            if cached_df is not None:
                _render_results(
                    cached_df,
                    "magic_formula_analysis.csv",
                    compact_cols,
                    detail_cols,
                    key="magicformula",
                    has_fund=False,
                )
        else:
            st.info("No run yet — click \"Update Magic Formula Analysis\" above to get started.")
