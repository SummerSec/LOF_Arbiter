"""
LOF Arbiter - 实时估算净值计算引擎

QDII 溢价采用 T-2 锚定 + T-1 落地涨跌模型：
  T日基准预估净值 = N(T-2) × (1 + R(T-1)/100 × 仓位) × (1 + 汇率(T-1)/100)
  当日溢价率     = (现价 - 基准预估净值) / 基准预估净值 × 100%
  T+1预估净值    = 基准 × (1 + R(T)/100 × 仓位)   ← 仅港股同步类可信

降级策略：
  TRACKING_HK  - 港股同步 QDII，当日/次日溢价均可较精准
  TRACKING_US  - 美股 QDII，仅当日溢价可信
  TRACKING_DOM - 国内 LOF，当日估算
  LEGACY       - 无配置或无基准 → 用最新官方净值
  NONE         - 无净值数据
"""

import pandas as pd
from typing import Dict, Optional, Tuple

from scripts.config import FundTrackingConfig, get_qdii_sync_class


def estimate_all(
    lof_df: pd.DataFrame,
    tracking_config: Dict[str, FundTrackingConfig],
    benchmark_data: Dict[str, Optional[float]],
    fx_data: Dict[str, Optional[float]],
    benchmark_t0: Optional[Dict[str, Optional[float]]] = None,
) -> pd.DataFrame:
    """
    批量计算所有 LOF 基金的实时估算净值和溢价率。

    benchmark_data : R(T-1)，用于 T 日基准预估净值
    benchmark_t0   : R(T)，用于 T+1 预估（港股同步类）；缺省则与 benchmark_data 相同
    """
    result_df = lof_df.copy()
    t0_data = benchmark_t0 if benchmark_t0 is not None else benchmark_data

    if result_df.empty:
        return result_df

    rows = [
        _estimate_row(row, tracking_config, benchmark_data, t0_data, fx_data)
        for _, row in result_df.iterrows()
    ]
    for key in rows[0]:
        result_df[key] = [r[key] for r in rows]

    return result_df


def _estimate_row(
    row,
    tracking_config: Dict[str, FundTrackingConfig],
    benchmark_t1: Dict[str, Optional[float]],
    benchmark_t0: Dict[str, Optional[float]],
    fx_data: Dict[str, Optional[float]],
) -> dict:
    code = row.get("fund_code_clean", "")
    price = _safe_float(row.get("price"))
    nav = _safe_float(row.get("nav"))
    prev_nav = _safe_float(row.get("prev_nav"))
    nav_date = row.get("nav_date")
    prev_nav_date = row.get("prev_nav_date")

    cfg = tracking_config.get(code)
    bm_t1 = benchmark_t1.get(code)
    bm_t0 = benchmark_t0.get(code)
    fx_pair = cfg.currency_pair if cfg else None
    fx_change = fx_data.get(fx_pair) if fx_pair else None

    result = _estimate_with_degradation(
        price=price,
        nav=nav,
        prev_nav=prev_nav,
        nav_date=nav_date,
        prev_nav_date=prev_nav_date,
        fund_config=cfg,
        benchmark_t1=bm_t1,
        benchmark_t0=bm_t0,
        fx_change=fx_change,
    )
    return result


def estimate_single(
    anchor_nav: float,
    fund_config: Optional[FundTrackingConfig],
    benchmark_change: Optional[float],
    fx_change: Optional[float],
) -> Optional[float]:
    """
    由锚定净值 + 单日海外/标的涨跌，计算预估净值。

    公式：
      国内: nav = anchor × (1 + change/100 × position_ratio)
      QDII: nav = anchor × (1 + change/100 × position_ratio) × (1 + fx/100)
    """
    if anchor_nav is None or anchor_nav <= 0:
        return None
    if fund_config is None or benchmark_change is None:
        return None

    ratio = fund_config.position_ratio
    est = anchor_nav * (1 + benchmark_change / 100 * ratio)

    if fund_config.fund_type == "QDII" and fx_change is not None:
        est *= 1 + fx_change / 100

    return est


def resolve_anchor_nav(
    nav: Optional[float],
    prev_nav: Optional[float],
    nav_date: Optional[str],
    prev_nav_date: Optional[str],
    nav_lag_days: int = 0,
) -> Tuple[Optional[float], Optional[str]]:
    """
    解析 T-2 锚定净值 N(T-2)。

    nav_lag_days=0 → 使用最新官方净值（QDII 通常已是 T-2 数据）
    nav_lag_days≥1 → 再回退一档 prev_nav
    """
    if nav_lag_days >= 1 and prev_nav is not None and prev_nav > 0:
        return prev_nav, prev_nav_date
    if nav is not None and nav > 0:
        return nav, nav_date
    if prev_nav is not None and prev_nav > 0:
        return prev_nav, prev_nav_date
    return None, None


def _estimate_with_degradation(
    price: Optional[float],
    nav: Optional[float],
    prev_nav: Optional[float],
    nav_date: Optional[str],
    prev_nav_date: Optional[str],
    fund_config: Optional[FundTrackingConfig],
    benchmark_t1: Optional[float],
    benchmark_t0: Optional[float],
    fx_change: Optional[float],
) -> dict:
    """完整估算，返回所有输出字段。"""
    empty = {
        "anchor_nav": None,
        "anchor_nav_date": None,
        "estimated_nav": None,
        "estimated_nav_tomorrow": None,
        "benchmark_change_pct": benchmark_t1,
        "benchmark_change_t0": benchmark_t0,
        "fx_change_pct": fx_change,
        "premium_rate": None,
        "premium_tomorrow_est": None,
        "premium_rate_legacy": None,
        "premium_confidence": None,
        "estimation_method": "NONE",
    }

    last_nav = nav if nav is not None else prev_nav
    legacy_premium = None
    if price is not None and last_nav is not None and last_nav > 0:
        legacy_premium = (price - last_nav) / last_nav * 100
    empty["premium_rate_legacy"] = legacy_premium

    if fund_config is None or benchmark_t1 is None:
        if last_nav is not None and last_nav > 0:
            empty["estimated_nav"] = last_nav
            empty["estimation_method"] = "LEGACY"
            empty["premium_confidence"] = "LEGACY"
            if price is not None:
                empty["premium_rate"] = legacy_premium
        return empty

    sync_class = get_qdii_sync_class(fund_config)
    anchor, anchor_date = resolve_anchor_nav(
        nav, prev_nav, nav_date, prev_nav_date, fund_config.nav_lag_days
    )
    if anchor is None:
        empty["estimation_method"] = "NONE"
        return empty

    baseline = estimate_single(anchor, fund_config, benchmark_t1, fx_change)
    if baseline is None:
        empty["estimation_method"] = "LEGACY"
        empty["premium_confidence"] = "LEGACY"
        empty["estimated_nav"] = last_nav
        if price is not None and last_nav:
            empty["premium_rate"] = legacy_premium
        return empty

    premium_today = None
    if price is not None and baseline > 0:
        premium_today = (price - baseline) / baseline * 100

    # T+1 预估（仅港股同步类输出可信值）
    est_tomorrow = None
    premium_tomorrow = None
    confidence = "HIGH"

    if sync_class == "HK_SYNC" and benchmark_t0 is not None:
        est_tomorrow = estimate_single(baseline, fund_config, benchmark_t0, fx_change)
        if est_tomorrow is not None and price is not None and est_tomorrow > 0:
            premium_tomorrow = (price - est_tomorrow) / est_tomorrow * 100
        confidence = "HIGH"
    elif sync_class == "US_LAGGED":
        confidence = "HIGH"  # 当日收盘溢价可信
        # 美股次日溢价不做数值预估（时差导致无法精准）
        premium_tomorrow = None
        est_tomorrow = None
    elif sync_class == "DOMESTIC":
        if benchmark_t0 is not None:
            est_tomorrow = estimate_single(baseline, fund_config, benchmark_t0, None)
            if est_tomorrow and price and est_tomorrow > 0:
                premium_tomorrow = (price - est_tomorrow) / est_tomorrow * 100
        confidence = "HIGH"
    else:
        confidence = "ESTIMATE"
        if benchmark_t0 is not None:
            est_tomorrow = estimate_single(baseline, fund_config, benchmark_t0, fx_change)
            if est_tomorrow and price and est_tomorrow > 0:
                premium_tomorrow = (price - est_tomorrow) / est_tomorrow * 100

    method_map = {
        "HK_SYNC": "TRACKING_HK",
        "US_LAGGED": "TRACKING_US",
        "DOMESTIC": "TRACKING_DOM",
        "MIXED": "TRACKING",
    }

    return {
        "anchor_nav": anchor,
        "anchor_nav_date": anchor_date,
        "estimated_nav": baseline,
        "estimated_nav_tomorrow": est_tomorrow,
        "benchmark_change_pct": benchmark_t1,
        "benchmark_change_t0": benchmark_t0,
        "fx_change_pct": fx_change,
        "premium_rate": premium_today,
        "premium_tomorrow_est": premium_tomorrow,
        "premium_rate_legacy": legacy_premium,
        "premium_confidence": confidence,
        "estimation_method": method_map.get(sync_class, "TRACKING"),
    }


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        v = float(val)
        if pd.isna(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


def get_estimation_summary(df: pd.DataFrame) -> Dict[str, int]:
    if "estimation_method" not in df.columns:
        return {}
    return df["estimation_method"].value_counts().to_dict()
