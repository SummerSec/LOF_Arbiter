"""
LOF Arbiter - Streamlit Dashboard

Web-based visualization for LOF arbitrage opportunities.
Run: streamlit run scripts/dashboard.py
"""

import sys
import os

# Ensure project root in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date

from scripts.query import (
    get_lof_data,
    get_premium_top,
    get_discount_top,
    get_limited_premium_top,
    get_fund_by_code,
    format_onsite_subscribe_limit,
    format_onsite_subscribe_min,
    DEFAULT_DB_PATH,
)
from scripts.jisilu import get_jisilu_latest, get_jisilu_data


st.set_page_config(
    page_title="LOF Arbitrage Dashboard",
    page_icon="📊",
    layout="wide",
)

# Auto refresh every 5 minutes via meta tag
import streamlit.components.v1 as components
components.html(
    "<meta http-equiv='refresh' content='300'>",
    height=0,
)


@st.cache_data(ttl=300)
def load_lof_data():
    return get_lof_data(db_path=DEFAULT_DB_PATH)


@st.cache_data(ttl=300)
def load_jisilu_latest():
    try:
        return get_jisilu_latest(db_path=DEFAULT_DB_PATH)
    except Exception:
        return pd.DataFrame()


def compute_signals(df: pd.DataFrame, window: int = 7) -> str:
    """Compute BUY/SELL/HOLD signal based on rolling stats."""
    if df.empty or "discount_rt" not in df.columns:
        return "N/A"
    if len(df) < window:
        return "HOLD (insufficient data)"

    recent = df["discount_rt"].head(window)
    mean = recent.mean()
    std = recent.std()
    current = recent.iloc[0]

    if pd.isna(current) or pd.isna(mean) or pd.isna(std) or std == 0:
        return "HOLD"

    if current < mean - std:
        return "BUY (undervalued)"
    elif current > mean + std:
        return "SELL (overvalued)"
    else:
        return "HOLD"


def signal_color(signal: str) -> str:
    if signal.startswith("BUY"):
        return "green"
    elif signal.startswith("SELL"):
        return "red"
    return "gray"


# ---- Sidebar ----
st.sidebar.title("LOF Arbitrage Dashboard")
st.sidebar.markdown("---")

# Data source selection
data_source = st.sidebar.radio(
    "Data Source",
    ["Local (akshare + estimator)", "Jisilu"],
    index=0,
)

# Load data
df_all = load_lof_data()
df_jisilu = load_jisilu_latest()

if data_source == "Jisilu":
    primary_df = df_jisilu
    code_col = "fund_code"
    premium_col = "discount_rt"
    price_col = "price"
    nav_col = "net_value"
else:
    primary_df = df_all
    code_col = "fund_code"
    premium_col = "premium_rate"
    price_col = "price"
    nav_col = "nav"

# Fund code filter
available_codes = []
if not primary_df.empty:
    available_codes = sorted(primary_df[code_col].astype(str).unique())

selected_codes = st.sidebar.multiselect(
    "Select LOF Codes",
    available_codes,
    default=available_codes[:5] if len(available_codes) >= 5 else available_codes,
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    f"**Last update:** {date.today().strftime('%Y-%m-%d')}"
)
st.sidebar.markdown(
    f"**Data source:** {data_source} "
    f"({len(primary_df)} records)"
)

# ---- Main Content ----
st.title("LOF Premium Arbitrage Dashboard")

col1, col2, col3 = st.columns(3)

# ---- Column 1: Trading Signals ----
with col1:
    st.subheader("Signals")
    if not selected_codes:
        st.info("Select LOF codes in sidebar")
    else:
        for code in selected_codes:
            if data_source == "Jisilu":
                fund_df = get_jisilu_data(code, db_path=DEFAULT_DB_PATH)
                if fund_df is None or fund_df.empty:
                    continue
                fund_df = fund_df.sort_values("price_dt", ascending=False)
                latest = fund_df.iloc[0] if not fund_df.empty else None
                signal = compute_signals(fund_df)
            else:
                row = get_fund_by_code(code, db_path=DEFAULT_DB_PATH)
                if row:
                    latest = row
                    # For local data, use premium_rate for signal
                    fund_data = df_all[df_all[code_col].astype(str) == code]
                    if not fund_data.empty:
                        signal = compute_signals(
                            pd.DataFrame({"discount_rt": [fund_data.iloc[0].get("premium_rate", 0)] * 7})
                        )
                    else:
                        signal = "N/A"
                else:
                    continue

            if latest is None:
                continue

            premium = latest.get(premium_col, 0) or 0
            price = latest.get(price_col, 0) or 0
            nav = latest.get(nav_col, 0) or 0

            with st.expander(f"{code} - {signal}", expanded=True):
                col_s = signal_color(signal)
                st.markdown(f"**:{col_s}[{signal}]**")
                st.metric("Premium Rate", f"{premium:.2f}%")
                st.metric("Price", f"{price:.4f}")
                st.metric("NAV", f"{nav:.4f}")
                if data_source == "Local (akshare + estimator)":
                    fund_code = latest.get("fund_code_full") or latest.get("fund_code") or code
                    col_a, col_b = st.columns(2)
                    col_a.metric("On-Exchange Min", format_onsite_subscribe_min(fund_code))
                    col_b.metric("On-Exchange Max", format_onsite_subscribe_limit(latest.get("daily_limit")))

# ---- Column 2: Premium Ranking ----
with col2:
    st.subheader("Ranking")
    if primary_df.empty:
        st.info("No data available")
    else:
        # Filter selected codes
        if selected_codes:
            rank_df = primary_df[
                primary_df[code_col].astype(str).isin(selected_codes)
            ]
        else:
            rank_df = primary_df

        if not rank_df.empty and premium_col in rank_df.columns:
            rank_df = rank_df.sort_values(premium_col, ascending=False)
            display_cols = [code_col, premium_col]
            display_names = ["Code", "Premium (%)"]
            if data_source == "Local (akshare + estimator)" and "daily_limit" in rank_df.columns:
                rank_df = rank_df.copy()
                code_series = rank_df["fund_code_full"].fillna(rank_df[code_col]) if "fund_code_full" in rank_df.columns else rank_df[code_col]
                rank_df["onsite_min_display"] = code_series.apply(format_onsite_subscribe_min)
                rank_df["onsite_max_display"] = rank_df["daily_limit"].apply(format_onsite_subscribe_limit)
                display_cols.extend(["onsite_min_display", "onsite_max_display"])
                display_names.extend(["On-Exchange Min", "On-Exchange Max"])
            display = rank_df[display_cols].copy()
            display.columns = display_names
            display = display.reset_index(drop=True)
            st.dataframe(
                display.style.background_gradient(subset=["Premium (%)"], cmap="RdYlGn"),
                use_container_width=True,
                height=400,
            )
        else:
            st.info("No premium data")

# ---- Column 3: System Status ----
with col3:
    st.subheader("Status")
    total_lofs = len(primary_df[code_col].unique()) if not primary_df.empty else 0
    total_records = len(primary_df)

    st.metric("Total LOFs", total_lofs)
    st.metric("Total Records", total_records)

    if data_source == "Local (akshare + estimator)" and "estimation_method" in df_all.columns:
        st.markdown("**Estimation Methods:**")
        summary = df_all["estimation_method"].value_counts()
        for method, count in summary.items():
            st.text(f"  {method}: {count}")

    if not df_jisilu.empty:
        st.metric("Jisilu Records", len(df_jisilu))
        jisilu_codes = len(df_jisilu["fund_code"].unique())
        st.metric("Jisilu LOFs", jisilu_codes)

    st.markdown("---")
    st.caption(
        "Real-time premium is calculated using estimated NAV. "
        "T-day actual NAV is confirmed before market open on T+1."
    )

# ---- Bottom: Trend Chart ----
st.markdown("---")
st.subheader("Premium Trend")

if selected_codes:
    chart_code = st.selectbox("Select code for trend chart", selected_codes, key="trend_select")

    if data_source == "Jisilu":
        trend_df = get_jisilu_data(chart_code, db_path=DEFAULT_DB_PATH)
        if trend_df is not None and not trend_df.empty:
            trend_df = trend_df.sort_values("price_dt")
            trend_df["discount_rt"] = pd.to_numeric(trend_df["discount_rt"], errors="coerce")
            y_col = "discount_rt"
            x_col = "price_dt"
        else:
            trend_df = pd.DataFrame()
    else:
        df = df_all[df_all[code_col].astype(str) == chart_code]
        if not df.empty:
            trend_df = df.copy()
            trend_df["premium_rate"] = pd.to_numeric(trend_df["premium_rate"], errors="coerce")
            y_col = "premium_rate"
            x_col = "trade_date"
        else:
            trend_df = pd.DataFrame()

    if not trend_df.empty and y_col in trend_df.columns:
        valid = trend_df[trend_df[y_col].notna()]
        if len(valid) > 1:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=valid[x_col],
                y=valid[y_col],
                mode="lines+markers",
                name="Premium Rate",
                line=dict(color="blue", width=1.5),
            ))

            # 7-day moving average
            valid_vals = valid[y_col].dropna()
            if len(valid_vals) >= 7:
                ma = valid_vals.rolling(window=7).mean()
                fig.add_trace(go.Scatter(
                    x=valid[x_col],
                    y=ma,
                    mode="lines",
                    name="7-day MA",
                    line=dict(color="red", width=1, dash="dash"),
                ))

            fig.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.5)
            fig.update_layout(
                title=f"{chart_code} Premium Trend",
                xaxis_title="Date",
                yaxis_title="Premium Rate (%)",
                height=400,
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Not enough valid data points for chart")
    else:
        st.info("No trend data available for selected code")
else:
    st.info("Select LOF codes in the sidebar to view trend charts")
