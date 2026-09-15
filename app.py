"""Market research tool — fund 13F analysis (Dataroma) and Magic Formula Investing."""
import os
import subprocess
from collections import defaultdict
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from src import cache, dataroma_client, magicformula_analysis, magicformula_client, funds_analysis, status_store

load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent

st.set_page_config(page_title="Market Research Tool", layout="wide")
st.title("Market Research & Investment Idea Finder")

with st.expander("⚙️ App maintenance"):
    if st.button("🔄 Update Tool (pull latest changes + restart)"):
        with st.spinner("Checking for updates..."):
            result = subprocess.run(
                ["git", "pull", "--ff-only"], cwd=PROJECT_DIR, capture_output=True, text=True
            )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            st.error(f"Update failed:\n\n{output}")
        elif "Already up to date" in result.stdout:
            st.info("Already up to date — nothing to restart.")
        else:
            st.success(
                "Pulled new changes — restarting now. This page will show a brief "
                "connection error and reconnect automatically in a few seconds."
            )
            st.code(output)
            subprocess.Popen(
                ["bash", str(PROJECT_DIR / "update_restart.sh"), str(os.getpid())],
                cwd=PROJECT_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

TAB_LABELS = {"funds": "Funds Analysis", "magicformula": "Magic Formula Analysis"}

STATUS_FILTER_OPTIONS = [
    "All (hide archived)",
    "Unread only",
    "⭐ Starred only",
    "🗑 Archived only",
    "Everything",
]

COLUMN_CONFIG = {
    "IR Search": st.column_config.LinkColumn("IR Search", display_text="Search ↗", width="small"),
}


def _holdings_by_stock(progress_callback=None) -> dict[str, set[str]]:
    """Build a stock -> {fund names} index from Dataroma, used to cross-reference tab 2."""
    holdings_by_stock: dict[str, set[str]] = defaultdict(set)
    funds = dataroma_client.get_superinvestors()
    all_holdings, _ = dataroma_client.get_all_holdings(funds, progress_callback=progress_callback)
    for h in all_holdings:
        holdings_by_stock[h.stock_ticker].add(h.fund_name)
    return holdings_by_stock


def _render_results(
    df, csv_name: str, compact_cols: list[str], detail_cols: list[str], tab_key: str, has_fund: bool
):
    """Filters + an editable grid (change Status inline) + a separate detail viewer below."""
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
    filtered = filtered.reset_index(drop=True)

    event = st.dataframe(
        filtered[["Status"] + compact_cols],
        width="stretch",
        column_config=COLUMN_CONFIG,
        on_select="rerun",
        selection_mode="single-row",
        key=f"{tab_key}_grid",
    )

    # Remember the selected row by its stable key in session_state, not just the
    # grid's own reported selection — changing Status updates that column's text,
    # which resets the grid's selection on the next rerun, closing the panel right
    # when a status button is clicked. Session state survives that.
    sel_state_key = f"{tab_key}_selected_key"
    selected_rows = event.selection.rows if event and event.selection else []
    if selected_rows:
        st.session_state[sel_state_key] = filtered.iloc[selected_rows[0]]["_key"]

    selected_key = st.session_state.get(sel_state_key)
    matches = filtered[filtered["_key"] == selected_key] if selected_key else filtered.iloc[0:0]
    if matches.empty:
        st.session_state[sel_state_key] = None
        st.caption(
            "Click the checkbox on the left of a row above to see its full detail "
            "and mark it read, starred, or archived."
        )
        return

    row = matches.iloc[0]
    row_key = row["_key"]
    current_status = row["_status"]

    with st.container(border=True):
        st.markdown(f"#### {row['Company']}")
        for col in detail_cols:
            if col in row and str(row[col]) not in ("", "nan", "-"):
                st.markdown(f"**{col}:** {row[col]}")

        action_cols = st.columns(4)
        with action_cols[0]:
            new_read = st.checkbox(
                "Read", value=current_status == status_store.STATUS_READ, key=f"{tab_key}_read_{row_key}"
            )
            if new_read and current_status == status_store.STATUS_UNREAD:
                status_store.set_status(tab_key, status, row_key, status_store.STATUS_READ)
                st.rerun()
            elif not new_read and current_status == status_store.STATUS_READ:
                status_store.set_status(tab_key, status, row_key, status_store.STATUS_UNREAD)
                st.rerun()
        with action_cols[1]:
            if st.button("⭐ Follow up later", key=f"{tab_key}_star_{row_key}"):
                status_store.set_status(tab_key, status, row_key, status_store.STATUS_STARRED)
                st.rerun()
        with action_cols[2]:
            if st.button("🗑 Not relevant", key=f"{tab_key}_arch_{row_key}"):
                status_store.set_status(tab_key, status, row_key, status_store.STATUS_ARCHIVED)
                st.rerun()
        with action_cols[3]:
            if current_status != status_store.STATUS_UNREAD and st.button(
                "Clear flag", key=f"{tab_key}_clear_{row_key}"
            ):
                status_store.set_status(tab_key, status, row_key, status_store.STATUS_UNREAD)
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

    compact_cols = ["Company", "Fund", "% of Portfolio", "Price drop %", "Position increase %", "IR Search"]
    detail_cols = ["Summary", "Other holders"]

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
            _render_results(df, "funds_analysis.csv", compact_cols, detail_cols, tab_key="funds", has_fund=True)

    if not ran_now:
        last_run = cache.load_latest_timestamp("funds")
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run("funds")
            if cached_df is not None:
                _render_results(cached_df, "funds_analysis.csv", compact_cols, detail_cols, tab_key="funds", has_fund=True)
        else:
            st.info("No run yet — click \"Run Funds Analysis\" above to get started.")

else:
    st.write("Shows companies from the Magic Formula Investing screener with market cap over $1B.")
    st.caption("Data source: [Magic Formula Investing](https://www.magicformulainvesting.com)")

    compact_cols = ["Company", "Market cap ($M)", "IR Search"]
    detail_cols = ["Summary", "Funds holding it"]

    ran_now = False
    if st.button("Update Magic Formula Analysis", type="primary"):
        ran_now = True
        progress = st.progress(0.0, text="Starting...")

        def _scrape_update(i: int, total: int, name: str):
            progress.progress(i / total, text=f"({i}/{total}) Fetching fund list: {name}")

        def _update(i: int, total: int, label: str):
            progress.progress(i / total, text=f"({i}/{total}) {label}")

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
            _render_results(df, "magic_formula_analysis.csv", compact_cols, detail_cols, tab_key="magicformula", has_fund=False)

    if not ran_now:
        last_run = cache.load_latest_timestamp("magicformula")
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run("magicformula")
            if cached_df is not None:
                _render_results(
                    cached_df, "magic_formula_analysis.csv", compact_cols, detail_cols, tab_key="magicformula", has_fund=False
                )
        else:
            st.info("No run yet — click \"Update Magic Formula Analysis\" above to get started.")
