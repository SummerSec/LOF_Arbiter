"""
LOF Arbiter - 实时估算净值计算引擎

根据跟踪标的实时涨跌幅，计算每只 LOF 的估算净值和溢价率。
支持三级降级策略：
  TRACKING: 有配置 + 有基准数据 → 完整公式
  LEGACY:   无配置或无基准 → 原 (price-nav)/nav 公式
  NONE:     无净值数据 → premium_rate = NULL
"""

import pandas as pd
from typing import Dict, Optional

from scripts.config import FundTrackingConfig


def estimate_all(
    lof_df: pd.DataFrame,
    tracking_config: Dict[str, FundTrackingConfig],
    benchmark_data: Dict[str, Optional[float]],
    fx_data: Dict[str, Optional[float]],
) -> pd.DataFrame:
    """
    批量计算所有 LOF 基金的实时估算净值和溢价率。

    Parameters
    ----------
    lof_df : DataFrame
        融合后的 LOF 数据，需含 fund_code_clean, price, nav, prev_nav 列
    tracking_config : dict
        {fund_code: FundTrackingConfig} 配置映射
    benchmark_data : dict
        {fund_code: change_pct} 跟踪标的涨跌幅（%）
    fx_data : dict
        {currency_pair: change_pct} 汇率涨跌幅（%）

    Returns
    -------
    DataFrame，新增列：
        estimated_nav, benchmark_change_pct, fx_change_pct,
        premium_rate, premium_rate_legacy, estimation_method
    """
    result_df = lof_df.copy()

    estimated_navs = []
    benchmark_changes = []
    fx_changes = []
    premium_rates = []
    premium_legacies = []
    est_methods = []

    for _, row in result_df.iterrows():
        code = row.get("fund_code_clean", "")
        price = _safe_float(row.get("price"))
        nav = _safe_float(row.get("nav"))
        prev_nav = _safe_float(row.get("prev_nav"))
        last_nav = nav if nav is not None else prev_nav

        cfg = tracking_config.get(code)
        bm_change = benchmark_data.get(code)
        fx_pair = cfg.currency_pair if cfg else None
        fx_change = fx_data.get(fx_pair) if fx_pair else None

        # 计算估算净值
        est_nav, method = _estimate_with_degradation(
            last_nav=last_nav,
            price=price,
            fund_config=cfg,
            benchmark_change=bm_change,
            fx_change=fx_change,
        )

        # 计算溢价率
        new_premium = None
        legacy_premium = None
        if price is not None and est_nav is not None and est_nav > 0:
            new_premium = (price - est_nav) / est_nav * 100
        if price is not None and last_nav is not None and last_nav > 0:
            legacy_premium = (price - last_nav) / last_nav * 100

        estimated_navs.append(est_nav)
        benchmark_changes.append(bm_change)
        fx_changes.append(fx_change)
        premium_rates.append(new_premium)
        premium_legacies.append(legacy_premium)
        est_methods.append(method)

    result_df["estimated_nav"] = estimated_navs
    result_df["benchmark_change_pct"] = benchmark_changes
    result_df["fx_change_pct"] = fx_changes
    result_df["premium_rate"] = premium_rates
    result_df["premium_rate_legacy"] = premium_legacies
    result_df["estimation_method"] = est_methods

    return result_df


def estimate_single(
    last_nav: float,
    fund_config: Optional[FundTrackingConfig],
    benchmark_change: Optional[float],
    fx_change: Optional[float],
) -> Optional[float]:
    """
    为单只基金计算实时估算净值（不处理降级）。

    公式：
      国内: est_nav = last_nav × (1 + benchmark_change / 100 × position_ratio)
      QDII: est_nav = last_nav × (1 + benchmark_change / 100 × position_ratio)
                      × (1 + fx_change / 100)

    Returns None if critical input missing.
    """
    if last_nav is None or last_nav <= 0:
        return None
    if fund_config is None or benchmark_change is None:
        return None

    ratio = fund_config.position_ratio
    nav = last_nav * (1 + benchmark_change / 100 * ratio)

    if fund_config.fund_type == "QDII" and fx_change is not None:
        nav = nav * (1 + fx_change / 100)

    return nav


def _estimate_with_degradation(
    last_nav: Optional[float],
    price: Optional[float],
    fund_config: Optional[FundTrackingConfig],
    benchmark_change: Optional[float],
    fx_change: Optional[float],
) -> tuple:
    """
    三级降级估算。

    Returns: (estimated_nav, estimation_method)
    """
    # Tier 1: 完整跟踪公式
    if fund_config is not None and benchmark_change is not None and last_nav is not None and last_nav > 0:
        est_nav = estimate_single(last_nav, fund_config, benchmark_change, fx_change)
        if est_nav is not None:
            return (est_nav, "TRACKING")

    # Tier 2: 降级为原公式
    if last_nav is not None and last_nav > 0:
        return (last_nav, "LEGACY")

    # Tier 3: 无可用数据
    return (None, "NONE")


def _safe_float(val) -> Optional[float]:
    """安全转换为 float"""
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
    """统计各估算方式的基金数量"""
    if "estimation_method" not in df.columns:
        return {}
    return df["estimation_method"].value_counts().to_dict()
