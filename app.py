"""Market research tool — fund 13F analysis (Dataroma) and Magic Formula Investing."""
import datetime as dt
import os
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src import (
    background_task,
    cache,
    dataroma_client,
    magicformula_analysis,
    market_data,
    funds_analysis,
    status_store,
)

load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent

st.set_page_config(page_title="Market Research Tool", layout="wide")

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Inter', -apple-system, sans-serif; }

h1 { font-weight: 700 !important; letter-spacing: -0.02em; }

/* Buttons: rounder, a bit of lift on hover instead of the flat default look */
div.stButton > button, div.stDownloadButton > button {
    border-radius: 10px;
    font-weight: 600;
    transition: transform 0.12s ease, box-shadow 0.12s ease;
}
div.stButton > button:hover, div.stDownloadButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(108, 92, 231, 0.18);
}

/* Segmented control (the tab switcher) pills */
button[data-testid^="stBaseButton-segmented_control"] {
    border-radius: 10px !important;
}
</style>
""",
    unsafe_allow_html=True,
)

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
TAB_WIDGET_KEY = "active_view_tab"

STATUS_FILTER_OPTIONS = [
    "All (hide archived)",
    "Unread only",
    "⭐ Starred only",
    "🗑 Archived only",
    "Everything",
]

COLUMN_CONFIG = {
    "IR Search": st.column_config.LinkColumn("IR Search", display_text="Search ↗", width="small"),
    "Position Value ($)": st.column_config.NumberColumn("Position Value ($)", format="dollar"),
}


def _holdings_by_stock(progress_callback=None) -> dict[str, set[str]]:
    """Build a stock -> {fund names} index from Dataroma, used to cross-reference tab 2."""
    holdings_by_stock: dict[str, set[str]] = defaultdict(set)
    funds = dataroma_client.get_superinvestors()
    all_holdings, _ = dataroma_client.get_all_holdings(funds, progress_callback=progress_callback)
    for h in all_holdings:
        holdings_by_stock[h.stock_ticker].add(h.fund_name)
    return holdings_by_stock


def _render_price_chart(ticker: str, fund_ticker: str, fund_name: str, other_names: list[str], other_tickers: list[str]):
    """5y price line with a marker per quarter; hovering a marker lists what every
    tracked fund holding this stock did that quarter (portfolio %, activity, value)."""
    import calendar

    import plotly.graph_objects as go

    try:
        hist = market_data.get_price_history(ticker, period="5y")
    except market_data.MarketDataError as exc:
        st.caption(f"Price chart unavailable: {exc}")
        return

    fund_pairs = [(fund_ticker, fund_name)] + list(zip(other_tickers, other_names))
    with st.spinner("Loading quarterly fund activity for the chart..."):
        histories = dataroma_client.get_stock_histories([fp[0] for fp in fund_pairs], ticker)

    quarter_dates: dict[tuple[int, int], dt.date] = {}
    for records in histories.values():
        for r in records:
            end_day = calendar.monthrange(r.year, r.quarter * 3)[1]
            quarter_dates[(r.year, r.quarter)] = dt.date(r.year, r.quarter * 3, end_day)

    hist_start = hist.index.min().date()
    marker_x, marker_y, hover_texts = [], [], []
    for (year, q), qdate in sorted(quarter_dates.items(), key=lambda kv: kv[1]):
        if qdate < hist_start:
            continue
        sub = hist[hist.index.date <= qdate]
        if sub.empty:
            continue
        marker_x.append(sub.index[-1])
        marker_y.append(float(sub["Close"].iloc[-1]))

        lines = [f"<b>{year} Q{q}</b>"]
        for ftkr, fname in fund_pairs:
            rec = next((r for r in histories.get(ftkr, []) if (r.year, r.quarter) == (year, q)), None)
            if rec is None:
                continue
            if not rec.activity_direction:
                activity = "No change"
            elif rec.activity_pct is not None:
                activity = f"{rec.activity_direction} {rec.activity_pct:.1f}%"
            else:
                activity = rec.activity_direction
            value = rec.shares * rec.reported_price if rec.shares and rec.reported_price else None
            pct_str = f"{rec.pct_of_portfolio:.1f}%" if rec.pct_of_portfolio is not None else "n/a"
            value_str = f"${value:,.0f}" if value else "n/a"
            lines.append(f"{fname}: {pct_str} of portfolio, {activity}, value {value_str}")
        hover_texts.append("<br>".join(lines))

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist.index, y=hist["Close"], mode="lines", name="Price", line=dict(color="#6C5CE7")))
    fig.add_trace(
        go.Scatter(
            x=marker_x,
            y=marker_y,
            mode="markers",
            name="Quarter-end",
            marker=dict(size=8, color="#FF6B6B"),
            text=hover_texts,
            hovertemplate="%{text}<extra></extra>",
        )
    )
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), showlegend=False, title="5-year price")
    st.plotly_chart(fig, use_container_width=True)


def _render_results(
    df, csv_name: str, compact_cols: list[str], detail_cols: list[str], tab_key: str, has_fund: bool
):
    """Filters + a compact grid; pick a company below it to see its full detail and mark it."""
    if df.empty:
        st.info("No results to show.")
        return

    status = status_store.load_status(tab_key)

    def key_of(row) -> str:
        return status_store.row_key(row["Company"], row["Fund"] if has_fund else None)

    with st.container(border=True):
        filter_cols = st.columns([2, 2, 3] if has_fund else [2, 2])
        with filter_cols[0]:
            status_filter = st.selectbox("🔎 Show", STATUS_FILTER_OPTIONS, key=f"{tab_key}_status_filter")
        with filter_cols[1]:
            search = st.text_input("Search", placeholder="Search company...", key=f"{tab_key}_search")
        fund_filter: list[str] = []
        if has_fund:
            with filter_cols[2]:
                fund_filter = st.multiselect(
                    "Filter by fund", sorted(df["Fund"].unique()), key=f"{tab_key}_fund_filter"
                )

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
    export_df = df[[c for c in df.columns if not c.startswith("_")]]
    st.download_button(
        "Export full detail to CSV", export_df.to_csv(index=False).encode("utf-8-sig"), csv_name, "text/csv"
    )

    if filtered.empty:
        st.info("No rows match the current filters.")
        return

    filtered["Status"] = filtered["_status"].apply(lambda s: status_store.STATUS_LABELS[s])
    filtered = filtered.reset_index(drop=True)

    ROW_PX = 35
    st.dataframe(
        filtered[["Status"] + compact_cols],
        width="stretch",
        height=ROW_PX * (len(filtered) + 1) + 3,  # +1 header row; show every row, no inner scrollbar
        row_height=ROW_PX,
        column_config=COLUMN_CONFIG,
        hide_index=True,
        key=f"{tab_key}_grid",
    )

    # Streamlit's interactive grid only registers clicks on its own selection
    # checkbox, not on a cell's text (e.g. the company name) — this picker is a
    # reliable substitute for opening the same detail view from any cell's name.
    if has_fund:
        labels = [f"{r['Company']} ({r['Fund']})" for _, r in filtered.iterrows()]
    else:
        labels = filtered["Company"].tolist()
    chosen = st.selectbox("🔍 Inspect a company", ["—"] + labels, key=f"{tab_key}_inspect")
    if chosen == "—":
        return
    row = filtered.iloc[labels.index(chosen)]
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

        if has_fund and str(row.get("_ticker", "")):
            other_names = [n for n in str(row.get("_other_holder_names", "")).split("|") if n]
            other_tickers = [t for t in str(row.get("_other_holder_tickers", "")).split("|") if t]
            _render_price_chart(row["_ticker"], row["_fund_ticker"], row["Fund"], other_names, other_tickers)


def _run_or_show_results(
    task_key: str, csv_name: str, compact_cols, detail_cols, tab_key: str, has_fund: bool, resort_fn=None
):
    """Show progress for a background task, its result once done, or the last cached run."""
    task = background_task.get_task(task_key)

    if task and task["status"] == "running":
        i, total, label = task["progress"]
        st.progress(i / total if total else 0.0, text=f"({i}/{total}) {label}")
        st.caption("This keeps running even if you switch to the other tab and come back.")
        time.sleep(1)
        st.rerun()
    elif task and task["status"] == "error":
        st.error(f"Run failed: {task['error']}")
        background_task.clear_task(task_key)
    elif task and task["status"] == "done":
        st.caption("Last updated: just now")
        df = resort_fn(task["result"]) if resort_fn else task["result"]
        _render_results(df, csv_name, compact_cols, detail_cols, tab_key=tab_key, has_fund=has_fund)
    else:
        last_run = cache.load_latest_timestamp(tab_key)
        if last_run:
            st.caption(f"Last updated: {last_run}")
            cached_df = cache.load_latest_run(tab_key)
            if cached_df is not None:
                if resort_fn:
                    cached_df = resort_fn(cached_df)
                _render_results(cached_df, csv_name, compact_cols, detail_cols, tab_key=tab_key, has_fund=has_fund)
        else:
            st.info("No run yet — click the button above to get started.")


# Seed the tab widget's state once (from the URL, e.g. after a fresh page load or a
# direct link like ?tab=magicformula) and let the widget itself own it from then on.
# Recomputing `default=` from st.query_params on every rerun — instead of only
# seeding session_state once — made the segmented control need two clicks to
# switch, since its identity (and therefore its remembered value) shifted under it
# on the very next rerun.
if TAB_WIDGET_KEY not in st.session_state:
    seeded = st.query_params.get("tab", "funds")
    st.session_state[TAB_WIDGET_KEY] = seeded if seeded in TAB_LABELS else "funds"

st.segmented_control(
    "View", options=list(TAB_LABELS.keys()), format_func=lambda k: TAB_LABELS[k], key=TAB_WIDGET_KEY
)
selected_tab = st.session_state[TAB_WIDGET_KEY]
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

    compact_cols = [
        "Company", "Fund", "% of Portfolio", "Quarter Price Move", "YTD Price Move",
        "Position increase %", "Previous Position Change", "Position Value ($)", "IR Search",
    ]
    detail_cols = ["Sector", "Industry", "Company Market Cap ($M)", "Summary", "Other holders"]

    if st.button("Run Funds Analysis", type="primary"):
        limit, drop, add = fund_limit or None, min_drop_pct, min_add_pct

        def _job(progress_cb):
            df = funds_analysis.run_funds_analysis(
                progress_callback=progress_cb, fund_limit=limit, min_drop_pct=drop, min_add_pct=add
            )
            cache.save_run("funds", df)
            return df

        if not background_task.start_task("funds", _job):
            st.warning("A funds analysis run is already in progress.")

    with st.container(border=True):
        weigh_ownership = st.toggle(
            "🔀 Also weigh position size vs. the company's own market cap "
            "(top = big in both the fund's portfolio AND the company; bottom = small in both)",
            key="funds_weigh_ownership",
        )

    def _resort(df):
        if not weigh_ownership or df.empty or "Company Market Cap ($M)" not in df.columns:
            return df
        df = df.copy()
        has_cap = df["Company Market Cap ($M)"].notna() & df["Position Value ($)"].notna()
        ownership_pct = pd.Series(0.0, index=df.index)
        ownership_pct[has_cap] = df.loc[has_cap, "Position Value ($)"] / (
            df.loc[has_cap, "Company Market Cap ($M)"] * 1_000_000
        )
        portfolio_rank = df["% of Portfolio"].rank(ascending=False, method="min")
        drop_rank = df["Quarter Price Move %"].fillna(0).rank(ascending=True, method="min")
        add_rank = df["Position increase %"].rank(ascending=False, method="min")
        ownership_rank = ownership_pct.rank(ascending=False, method="min")
        combined = portfolio_rank + drop_rank + add_rank + ownership_rank
        return df.assign(_combined=combined).sort_values("_combined").drop(columns="_combined").reset_index(drop=True)

    _run_or_show_results(
        "funds", "funds_analysis.csv", compact_cols, detail_cols, tab_key="funds", has_fund=True, resort_fn=_resort
    )

else:
    st.write("Shows companies from the Magic Formula Investing screener with market cap over $1B.")
    st.caption("Data source: [Magic Formula Investing](https://www.magicformulainvesting.com)")

    with st.expander("ℹ️ How these results are chosen and ordered", expanded=False):
        st.markdown(
            "**Which companies appear:** pulled from the Magic Formula Investing stock "
            "screener (ranks companies by combined Earnings Yield + Return on Invested "
            "Capital), restricted to the top 50 companies with a market cap of at least "
            "$1 billion.\n\n"
            "**How they're ordered:** the screener is re-run several more times with "
            "the minimum market cap lowered step by step (e.g. $750M, $500M, ... down "
            "to $50M), each time still asking for the top 50. Lowering the minimum lets "
            "more small-cap companies compete for those 50 spots. A company that keeps "
            "its spot even against that extra small-cap competition has a stronger "
            "underlying rank — a bigger gap between its price and its estimated value — "
            "than one that only makes the top 50 once small caps are excluded. So "
            "companies are sorted by the **lowest** minimum-market-cap level at which "
            "they still hold a top-50 spot: the ones that survive the most competition "
            "are listed first. Ties are broken by (1) whether a fund from your Funds "
            "Analysis list already holds the company, then (2) market cap."
        )

    compact_cols = ["Company", "Market cap ($M)", "IR Search"]
    detail_cols = ["Sector", "Industry", "Summary", "Funds holding it"]

    if st.button("Update Magic Formula Analysis", type="primary"):

        def _job(progress_cb):
            def _scrape_progress(i, total, name):
                progress_cb(i, total, f"Fetching fund list: {name}")

            holdings_index = _holdings_by_stock(progress_callback=_scrape_progress)
            df = magicformula_analysis.run_magic_formula_analysis(
                holdings_by_stock=holdings_index, progress_callback=progress_cb
            )
            cache.save_run("magicformula", df)
            return df

        if not background_task.start_task("magicformula", _job):
            st.warning("A Magic Formula run is already in progress.")

    _run_or_show_results(
        "magicformula",
        "magic_formula_analysis.csv",
        compact_cols,
        detail_cols,
        tab_key="magicformula",
        has_fund=False,
    )
