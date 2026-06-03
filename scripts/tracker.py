"""
LOF Arbiter - 跟踪标的实时涨跌幅抓取

按 source_func 分组批量调用 akshare，一次 API 调用覆盖所有同类标的。
"""

import pandas as pd
from typing import Dict, Optional

from scripts.config import FundTrackingConfig


def fetch_benchmark_data(configs: Dict[str, FundTrackingConfig]) -> Dict[str, Optional[float]]:
    """
    批量获取所有基金的跟踪标的涨跌幅。

    按 source_func 分组，每种数据源只调用一次 akshare，
    然后从返回的 DataFrame 中提取每只基金的跟踪标的涨跌幅。

    Returns: {fund_code: change_pct}  change_pct 为百分比值（如 1.5 表示 +1.5%）
    """
    if not configs:
        return {}

    # 按 source_func 分组
    groups: Dict[str, list] = {}
    for code, cfg in configs.items():
        func = cfg.tracking_target.source_func
        groups.setdefault(func, []).append(code)

    result: Dict[str, Optional[float]] = {}

    for func_name, fund_codes in groups.items():
        try:
            df = _call_source_func(func_name, fund_codes, configs)
            if df is None or df.empty:
                for code in fund_codes:
                    result[code] = None
                continue

            # 从 DataFrame 中提取每只基金的涨跌幅
            for code in fund_codes:
                cfg = configs[code]
                change = _extract_change(df, cfg)
                result[code] = change
        except Exception as e:
            print(f"[tracker] {func_name} 获取失败: {e}")
            for code in fund_codes:
                result[code] = None

    return result


def fetch_forex_data(configs: Dict[str, FundTrackingConfig]) -> Dict[str, Optional[float]]:
    """
    获取所有 QDII 基金需要的汇率涨跌幅。

    按汇率对去重，一次 forex_spot_em 调用获取全部汇率。

    Returns: {currency_pair: change_pct}
    """
    pairs = set()
    for cfg in configs.values():
        if cfg.currency_pair:
            pairs.add(cfg.currency_pair)

    if not pairs:
        return {}

    result: Dict[str, Optional[float]] = {}
    try:
        import akshare as ak
        fx_df = ak.forex_spot_em()

        if fx_df is None or fx_df.empty:
            for pair in pairs:
                result[pair] = None
            return result

        for pair in pairs:
            change = _find_change_in_df(fx_df, pair, filter_col="名称", change_col="涨跌幅")
            result[pair] = change
    except Exception as e:
        print(f"[tracker] 汇率数据获取失败: {e}")
        for pair in pairs:
            result[pair] = None

    return result


def _call_source_func(func_name: str, fund_codes: list, configs: Dict[str, FundTrackingConfig]):
    """根据函数名调用对应的 akshare 函数，返回 DataFrame"""
    import akshare as ak

    if func_name == "stock_us_spot_em":
        return ak.stock_us_spot_em()

    elif func_name == "index_global_spot_em":
        return ak.index_global_spot_em()

    elif func_name == "stock_zh_index_spot_em":
        # 收集所有需要的 arg 参数，去重调用
        args = set()
        for code in fund_codes:
            cfg = configs[code]
            args.add(cfg.tracking_target.source_func_arg or "沪深重要指数")

        dfs = []
        for arg in args:
            try:
                df = ak.stock_zh_index_spot_em(symbol=arg)
                dfs.append(df)
            except Exception as e:
                print(f"[tracker] stock_zh_index_spot_em({arg}) 失败: {e}")

        if dfs:
            return pd.concat(dfs, ignore_index=True)
        return None

    elif func_name == "forex_spot_em":
        return ak.forex_spot_em()

    elif func_name == "futures_zh_realtime_sina":
        return ak.futures_zh_realtime_sina()

    else:
        print(f"[tracker] 未知数据源函数: {func_name}")
        return None


def _extract_change(df: pd.DataFrame, cfg: FundTrackingConfig) -> Optional[float]:
    """从 DataFrame 中提取特定跟踪标的的涨跌幅"""
    target = cfg.tracking_target
    filter_col = target.source_filter
    symbol = target.symbol

    # 根据 filter 类型确定匹配值
    if filter_col == "code":
        match_val = symbol
    elif filter_col == "name":
        match_val = target.name
    elif filter_col == "symbol":
        match_val = symbol
    else:
        match_val = symbol

    return _find_change_in_df(df, match_val, filter_col=filter_col)


def _find_change_in_df(
    df: pd.DataFrame,
    match_val: str,
    filter_col: str = "代码",
    change_col: Optional[str] = None
) -> Optional[float]:
    """
    在 DataFrame 中查找目标行并提取涨跌幅。

    自动识别涨跌幅列名（支持中英文列名）。
    """
    if df is None or df.empty:
        return None

    # 查找匹配行
    col_map = _detect_columns(df)

    actual_filter = col_map.get(filter_col, filter_col)
    if actual_filter not in df.columns:
        # 尝试模糊匹配列名
        for c in df.columns:
            if filter_col in str(c):
                actual_filter = c
                break
        else:
            return None

    # 匹配行
    mask = df[actual_filter].astype(str).str.contains(match_val, na=False)
    matched = df[mask]
    if matched.empty:
        return None

    row = matched.iloc[0]

    # 提取涨跌幅
    if change_col:
        change_cols = [change_col]
    else:
        change_cols = ["涨跌幅", "change_pct", "pct_chg", "变动", "change", "涨跌", "涨幅"]

    for cc in change_cols:
        actual_cc = col_map.get(cc, cc)
        if actual_cc in row.index:
            val = row[actual_cc]
        else:
            # 尝试模糊匹配
            found = None
            for c in df.columns:
                if cc in str(c):
                    found = c
                    break
            if found:
                val = row[found]
            else:
                continue

        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    return None


def _detect_columns(df: pd.DataFrame) -> Dict[str, str]:
    """建立标准列名到实际列名的映射（处理中英文差异）"""
    mapping = {}
    for col in df.columns:
        col_lower = str(col).lower().strip()
        if "代码" in col or col_lower in ("code", "symbol", "ticker"):
            mapping["代码"] = col
            mapping["code"] = col
            mapping["symbol"] = col
        if "名称" in col or col_lower in ("name", "sec_name", "secname"):
            mapping["名称"] = col
            mapping["name"] = col
        if "涨跌幅" in col or col_lower in ("change_pct", "pct_chg", "pctchange", "changepercent"):
            mapping["涨跌幅"] = col
            mapping["change_pct"] = col
            mapping["pct_chg"] = col
        if "涨跌额" in col or col_lower in ("change", "change_amt"):
            mapping["涨跌"] = col
            mapping["change"] = col
        if "最新价" in col or col_lower in ("price", "last", "close", "最新"):
            mapping["最新价"] = col
            mapping["price"] = col
    return mapping
