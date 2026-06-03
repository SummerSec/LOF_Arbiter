"""
LOF Arbiter - 跟踪标的实时涨跌幅抓取

按 source_func 分组批量调用 akshare，一次 API 调用覆盖所有同类标的。
"""

import time
import pandas as pd
from datetime import date, timedelta
from typing import Dict, Optional, Callable, Any

from scripts.config import FundTrackingConfig

# 配置里的汇率对 → akshare 符号 / 备用数据源映射
FOREX_PAIR_SOURCES = {
    "USDCNH": {
        "spot_keys": ["USDCNH", "美元离岸", "离岸人民币"],
        "hist_symbol": "USDCNH",
        "boc_symbol": "美元",
    },
    "HKDCNY": {
        "spot_keys": ["HKDCNY", "HKDCNYC", "港元兑", "港币兑"],
        "hist_symbol": "HKDCNYC",
        "boc_symbol": "港币",
    },
}


def _retry_call(
    func: Callable[[], Any],
    *,
    retries: int = 3,
    delay: float = 1.5,
    label: str = "",
) -> Any:
    """对 transient 网络错误进行重试。"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return func()
        except Exception as e:
            last_err = e
            if attempt < retries:
                wait = delay * attempt
                print(f"[tracker] {label} 第 {attempt} 次失败，{wait:.1f}s 后重试: {e}")
                time.sleep(wait)
    raise last_err


def fetch_benchmark_data(configs: Dict[str, FundTrackingConfig]) -> Dict[str, Optional[float]]:
    """
    批量获取 R(T-1)——用于 T 日基准预估净值。

    Returns: {fund_code: change_pct}  change_pct 为百分比值（如 1.5 表示 +1.5%）
    """
    t1, _ = fetch_benchmark_changes(configs)
    return t1


def fetch_benchmark_changes(
    configs: Dict[str, FundTrackingConfig],
) -> tuple:
    """
    分别获取 R(T-1) 与 R(T) 两组基准涨跌幅。

    - R(T-1) benchmark_t1: 用于 T 日基准预估净值 = N(T-2) × (1+R(T-1))
    - R(T)   benchmark_t0: 用于 T+1 预估（港股同步类）

    语义按标的类型区分：
      us_etf / 美股指数  → t1=前一晚美股收盘涨跌（spot 实时值），t0=None
      global_index 港股  → t1=昨日指数涨跌（历史），t0=今日实时涨跌
      domestic_index     → t1=t0=当日 A 股指数实时涨跌
    """
    if not configs:
        return {}, {}

    groups: Dict[str, list] = {}
    for code, cfg in configs.items():
        func = cfg.tracking_target.source_func
        groups.setdefault(func, []).append(code)

    t1_result: Dict[str, Optional[float]] = {}
    t0_result: Dict[str, Optional[float]] = {}

    for func_name, fund_codes in groups.items():
        try:
            df = _call_source_func(func_name, fund_codes, configs)
            if df is None or df.empty:
                for code in fund_codes:
                    t1_result[code] = None
                    t0_result[code] = None
                continue

            for code in fund_codes:
                cfg = configs[code]
                spot_change = _extract_change(df, cfg)
                t1, t0 = _resolve_benchmark_pair(cfg, spot_change)
                t1_result[code] = t1
                t0_result[code] = t0
        except Exception as e:
            print(f"[tracker] {func_name} 获取失败: {e}")
            for code in fund_codes:
                t1_result[code] = None
                t0_result[code] = None

    return t1_result, t0_result


def _resolve_benchmark_pair(
    cfg: FundTrackingConfig,
    spot_change: Optional[float],
) -> tuple:
    """
    将 spot 实时涨跌幅映射为 (R(T-1), R(T))。
    """
    from scripts.config import get_qdii_sync_class

    sync_class = get_qdii_sync_class(cfg)
    target_type = cfg.tracking_target.type

    if sync_class == "HK_SYNC":
        prev_change = _fetch_prev_global_index_change(cfg.tracking_target.name)
        t1 = prev_change if prev_change is not None else spot_change
        t0 = spot_change
        return t1, t0

    if sync_class == "US_LAGGED" or target_type == "us_etf":
        # A 股盘中 spot ≈ 前一晚美股收盘涨跌 = R(T-1)
        return spot_change, None

    if sync_class == "DOMESTIC":
        return spot_change, spot_change

    # MIXED / 其他
    return spot_change, spot_change


def _fetch_prev_global_index_change(index_name: str) -> Optional[float]:
    """
    获取全球指数上一交易日涨跌幅（R(T-1)）。
    优先尝试 akshare 历史接口，失败时返回 None。
    """
    if not index_name:
        return None

    try:
        import akshare as ak

        if "恒生" in index_name:
            df = ak.stock_hk_index_daily_em(symbol="HSI")
            if df is not None and len(df) >= 2:
                return _pct_from_last_two_rows(df, "close", "收盘")

        df = ak.index_global_hist_em(symbol=index_name)
        if df is not None and len(df) >= 2:
            for col in ("涨跌幅", "pct_chg", "change_pct"):
                if col in df.columns:
                    val = df.iloc[-2][col]
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        pass
            return _pct_from_last_two_rows(df, "close", "收盘", "最新价")
    except Exception as e:
        print(f"[tracker] 历史指数 {index_name} 获取失败: {e}")

    return None


def _pct_from_last_two_rows(df, *price_cols) -> Optional[float]:
    """由最近两个收盘价计算涨跌幅(%)"""
    col = None
    for c in price_cols:
        if c in df.columns:
            col = c
            break
    if col is None:
        for c in df.columns:
            if any(k in str(c) for k in ("收盘", "close", "最新")):
                col = c
                break
    if col is None:
        return None
    try:
        prev = float(df.iloc[-2][col])
        curr = float(df.iloc[-1][col])
        if prev == 0:
            return None
        return (curr - prev) / prev * 100
    except (ValueError, TypeError, IndexError):
        return None


def fetch_forex_data(configs: Dict[str, FundTrackingConfig]) -> Dict[str, Optional[float]]:
    """
    获取所有 QDII 基金需要的汇率涨跌幅。

    优先东方财富实时接口；502/网络失败时自动降级到历史 K 线或新浪中行牌价。

    Returns: {currency_pair: change_pct}
    """
    pairs = set()
    for cfg in configs.values():
        if cfg.currency_pair:
            pairs.add(cfg.currency_pair)

    if not pairs:
        return {}

    result: Dict[str, Optional[float]] = {pair: None for pair in pairs}

    # 1) 东方财富实时（带重试）
    fx_df = None
    try:
        import akshare as ak

        fx_df = _retry_call(
            ak.forex_spot_em,
            retries=3,
            delay=2.0,
            label="forex_spot_em",
        )
    except Exception as e:
        print(f"[tracker] 汇率实时数据获取失败: {e}")

    if fx_df is not None and not fx_df.empty:
        for pair in pairs:
            change = _find_fx_in_spot(fx_df, pair)
            if change is not None:
                result[pair] = change

    # 2) 逐对备用源
    for pair in pairs:
        if result[pair] is not None:
            continue
        change, source = _fetch_fx_change_fallback(pair)
        if change is not None:
            result[pair] = change
            print(f"[tracker] 汇率 {pair} 使用备用源: {source} → {change:+.4f}%")
        else:
            print(f"[tracker] 汇率 {pair} 所有数据源均不可用")

    available = sum(1 for v in result.values() if v is not None)
    if available < len(pairs):
        print(f"[tracker] 汇率数据: {available}/{len(pairs)} 可用")
    return result


def _find_fx_in_spot(df: pd.DataFrame, pair: str) -> Optional[float]:
    """在东方财富实时汇率表中查找涨跌幅。"""
    meta = FOREX_PAIR_SOURCES.get(pair, {})
    keys = meta.get("spot_keys", [pair])

    for key in keys:
        for filter_col in ("名称", "代码", "name", "code"):
            change = _find_change_in_df(df, key, filter_col=filter_col, change_col="涨跌幅")
            if change is not None:
                return change
    return None


def _fetch_fx_change_fallback(pair: str) -> tuple:
    """单汇率对备用获取，返回 (change_pct, source_name)。"""
    meta = FOREX_PAIR_SOURCES.get(pair, {"spot_keys": [pair]})

    hist_symbol = meta.get("hist_symbol", pair)
    change = _fetch_fx_change_from_hist(hist_symbol)
    if change is not None:
        return change, f"forex_hist_em({hist_symbol})"

    boc_symbol = meta.get("boc_symbol")
    if boc_symbol:
        change = _fetch_fx_change_from_boc(boc_symbol)
        if change is not None:
            return change, f"currency_boc_sina({boc_symbol})"

    return None, ""


def _fetch_fx_change_from_hist(symbol: str) -> Optional[float]:
    """由东方财富历史 K 线最近两日收盘价计算涨跌幅。"""
    try:
        import akshare as ak

        df = _retry_call(
            lambda: ak.forex_hist_em(symbol=symbol),
            retries=2,
            delay=1.5,
            label=f"forex_hist_em({symbol})",
        )
        return _pct_from_last_two_rows(df, "最新价", "close", "收盘")
    except Exception as e:
        print(f"[tracker] forex_hist_em({symbol}) 失败: {e}")
        return None


def _fetch_fx_change_from_boc(symbol: str) -> Optional[float]:
    """由新浪中行牌价最近两个交易日折算价计算涨跌幅。"""
    try:
        import akshare as ak

        end = date.today()
        start = end - timedelta(days=21)

        def _fetch():
            return ak.currency_boc_sina(
                symbol=symbol,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )

        df = _retry_call(_fetch, retries=2, delay=1.5, label=f"currency_boc_sina({symbol})")
        if df is None or df.empty:
            return None
        df = df.sort_values("日期")
        return _pct_from_last_two_rows(df, "中行折算价", "央行中间价")
    except Exception as e:
        print(f"[tracker] currency_boc_sina({symbol}) 失败: {e}")
        return None


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
